# Distribution.py
import math
import sys
import os
import helics as h
import opendssdirect as dss
from pathlib import Path

ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}

feeder_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1
DEFAULT_DIST_VOLTAGE_BUS = "regxfmr_hvmv_sub_lsb"
dist_voltage_bus = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.environ.get("DIST_VOLTAGE_BUS", DEFAULT_DIST_VOLTAGE_BUS)
)


def get_target_time() -> float:
    value = os.environ.get("SIM_TARGET_TIME", "10.0")
    try:
        target = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid SIM_TARGET_TIME='{value}'. Expected a positive float in seconds."
        ) from exc
    if target <= 0.0:
        raise ValueError(
            f"Invalid SIM_TARGET_TIME='{value}'. Expected a positive float in seconds."
        )
    return target


def get_positive_env_float(name: str, default: float) -> float:
    value = os.environ.get(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a positive float in seconds."
        ) from exc
    if parsed <= 0.0:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a positive float in seconds."
        )
    return parsed


def get_broker_url() -> str:
    return os.environ.get("HELICS_BROKER_URL", "tcp://127.0.0.1:23407")


def get_distribution_case_path(script_dir: Path) -> Path:
    case_value = os.environ.get("DIST_MASTER_DSS") or os.environ.get(
        "OPENDSS_MASTER_DSS"
    )
    if case_value:
        case_path = Path(case_value)
        if not case_path.is_absolute():
            case_path = script_dir / case_path
        return case_path.resolve()

    for candidate in (
        script_dir / "8500Node" / "Master.dss",
        script_dir.parent / "8500Node" / "Master.dss",
    ):
        if candidate.exists():
            return candidate.resolve()

    return (script_dir / "8500Node" / "Master.dss").resolve()


def get_cosim_step_config():
    fine_dt = get_positive_env_float("SIM_FINE_DT", 0.01)
    coarse_dt = get_positive_env_float("SIM_COARSE_DT", 0.05)
    coarse_start = get_positive_env_float("SIM_COARSE_START", 2.05)
    if coarse_dt < fine_dt:
        raise ValueError(
            f"Invalid co-simulation step schedule: SIM_COARSE_DT={coarse_dt} "
            f"must be >= SIM_FINE_DT={fine_dt}."
        )
    return fine_dt, coarse_dt, coarse_start

def _phase_value(phase_map, phase: int) -> float:
    return phase_map.get(phase, math.nan)

def get_bus_voltage_snapshot(bus_name: str) -> dict:
    dss.Circuit.SetActiveBus(bus_name)
    active_bus = dss.Bus.Name()
    if active_bus.lower() != bus_name.lower():
        raise RuntimeError(
            f"Requested distribution voltage bus '{bus_name}' but OpenDSS activated '{active_bus}'."
        )

    pu_mag_angle = dss.Bus.puVmagAngle()
    nodes = dss.Bus.Nodes()

    phase_mags = {}
    phase_angles = {}
    for idx, node in enumerate(nodes):
        mag_idx = 2 * idx
        ang_idx = mag_idx + 1
        if ang_idx >= len(pu_mag_angle):
            continue
        phase_mags[node] = pu_mag_angle[mag_idx]
        phase_angles[node] = pu_mag_angle[ang_idx]

    present_phase_mags = [phase_mags[node] for node in sorted(phase_mags)]
    avg_mag = sum(present_phase_mags) / len(present_phase_mags) if present_phase_mags else math.nan

    return {
        "bus": active_bus,
        "avg_mag": avg_mag,
        "phase_mags": phase_mags,
        "phase_angles": phase_angles,
    }

def solve_distribution_from_source(V_complex):
    Vmag = abs(V_complex)
    Vangle = math.degrees(math.atan2(V_complex.imag, V_complex.real))
    dss.Text.Command(f"Edit Vsource.Source pu={Vmag:.6f} angle={Vangle:.6f}")
    dss.Solution.Solve()

    dist_bus_snapshot = get_bus_voltage_snapshot(dist_voltage_bus)
    totalPQ = dss.Circuit.TotalPower()  # kW, kvar
    P_pu = -totalPQ[0] / 100000.0
    Q_pu = -totalPQ[1] / 100000.0

    return complex(P_pu, Q_pu), totalPQ, dist_bus_snapshot, Vmag, Vangle

# 1. HELICS setup
SCRIPT_DIR = Path(__file__).resolve().parent
master_dss = get_distribution_case_path(SCRIPT_DIR)
if not master_dss.exists():
    raise FileNotFoundError(
        f"Distribution case not found: {master_dss}. Set DIST_MASTER_DSS to "
        "the OpenDSS Master.dss path."
    )

broker_url = get_broker_url()
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(
    fedinfo, f"--federates=1 --broker={broker_url}"
)
fine_dt, coarse_dt, coarse_start = get_cosim_step_config()
h.helicsFederateInfoSetTimeProperty(
    fedinfo, h.HELICS_PROPERTY_TIME_DELTA, min(fine_dt, coarse_dt)
)
h.helicsFederateInfoSetFlagOption(fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, True)
dist_fed = h.helicsCreateValueFederate(f"Feeder{feeder_index}", fedinfo)

subV = h.helicsFederateRegisterSubscription(dist_fed, "Bus2Voltage", "")
pubS = h.helicsFederateRegisterGlobalPublication(
    dist_fed, f"Feeder{feeder_index}_Power", h.HELICS_DATA_TYPE_COMPLEX, ""
)
h.helicsPublicationSetOption(pubS, h.HELICS_HANDLE_OPTION_ONLY_TRANSMIT_ON_CHANGE, 1)

print(
    f"Feeder {feeder_index}: HELICS interfaces created "
    f"(broker={broker_url}, case={master_dss})."
)

# 2. OpenDSS setup
dss.Basic.ClearAll()
dss.Text.Command(f'Compile "{master_dss}"')
dss.Text.Command("set controlmode=off")
dss.Text.Command("set mode=snap")
dss.Text.Command("set maxcontroliter=100")

PROFILE_24 = [1] * 24

def loadmult_from_time(t_sec: float) -> float:
    t_day = t_sec % 86400.0
    hour = t_day / 3600.0
    i0 = int(math.floor(hour)) % 24
    i1 = (i0 + 1) % 24
    frac = hour - math.floor(hour)
    return PROFILE_24[i0] * (1.0 - frac) + PROFILE_24[i1] * frac

last_time_applied = -1.0
last_loadmult = loadmult_from_time(0.0)
dss.Text.Command(f"set loadmult={last_loadmult:.4f}")

# ---- HELICS initialization handshake at t = 0 ----
max_init = 20
tol_init_v = 1e-6
tol_init_s = 1e-6

V_last = 1.0 + 0.0j
V_prev = None

S_prev, totalPQ, dist_bus_snapshot, Vmag, Vangle = solve_distribution_from_source(V_last)

print(
    f"Feeder {feeder_index}: initial guess "
    f"V={Vmag:.6f} pu ang={Vangle:.6f} deg "
    f"P={S_prev.real:.6f} Q={S_prev.imag:.6f}"
)

h.helicsFederateEnterInitializingMode(dist_fed)
print(f"Feeder {feeder_index}: entered HELICS initialization mode.")


# Publish initial feeder power guess before first iterative entry
h.helicsPublicationPublishComplex(pubS, S_prev)

for k in range(max_init):
    init_state = h.helicsFederateEnterExecutingModeIterative(
        dist_fed, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    if h.helicsInputIsUpdated(subV):
        V_last = h.helicsInputGetComplex(subV)

    S_new, totalPQ, dist_bus_snapshot, Vmag, Vangle = solve_distribution_from_source(V_last)

    dV = float("inf") if V_prev is None else abs(V_last - V_prev)
    dS = abs(S_new - S_prev)

    print(
        f"[Feeder{feeder_index:02d} init {k+1:02d}] "
        f"V={Vmag:.6f} pu ang={Vangle:.6f} deg "
        f"P={S_new.real:.6f} Q={S_new.imag:.6f} "
        f"dV={dV:.3e} dS={dS:.3e}"
    )

    if dV > tol_init_v or dS > tol_init_s:
        h.helicsPublicationPublishComplex(pubS, S_new)

    V_prev = V_last
    S_prev = S_new

    if init_state == h.HELICS_ITERATION_RESULT_NEXT_STEP:
        break
else:
    raise RuntimeError(f"Feeder {feeder_index}: initialization handshake did not converge.")

print(f"Feeder {feeder_index}: initialization handshake converged.")

# 3. Normal time loop starts here
target_time = get_target_time()
print(f"Feeder {feeder_index}: target simulation time = {target_time:.3f} s")
print(
    f"Feeder {feeder_index}: co-simulation step schedule = "
    f"{fine_dt:.3f}s until t={coarse_start:.3f}s, then {coarse_dt:.3f}s"
)
current_time = 0.0
iter_count = 0

while current_time < target_time:
    current_dt = fine_dt if current_time + 1e-9 < coarse_start else coarse_dt
    next_time = min(current_time + current_dt, target_time)
    granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
        dist_fed, next_time, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    current_time = granted_time
    iter_count += 1

    updated = h.helicsInputIsUpdated(subV)
    if updated:
        V_last = h.helicsInputGetComplex(subV)

    if current_time > last_time_applied + 1e-9:
        last_loadmult = loadmult_from_time(current_time)
        dss.Text.Command(f"set loadmult={last_loadmult:.4f}")
        last_time_applied = current_time

    S_new, totalPQ, dist_bus_snapshot, Vmag, Vangle = solve_distribution_from_source(V_last)
    h.helicsPublicationPublishComplex(pubS, S_new)

    state_str = ITER_STATE_NAME.get(iteration_state, str(iteration_state))
    print(
        f"[Feeder{feeder_index:02d}] "
        f"iter={iter_count:06d} "
        f"t_granted={current_time:.3f}s (t_req={next_time:.3f}s, dt={current_dt:.3f}s) "
        f"state={state_str} | "
        f"Vupdate={updated} V={Vmag:.6f} pu ang={Vangle:.6f} deg | "
        f"DistBus={dist_bus_snapshot['bus']} "
        f"Vavg={dist_bus_snapshot['avg_mag']:.6f} pu "
        f"Va={_phase_value(dist_bus_snapshot['phase_mags'], 1):.6f} pu "
        f"Vb={_phase_value(dist_bus_snapshot['phase_mags'], 2):.6f} pu "
        f"Vc={_phase_value(dist_bus_snapshot['phase_mags'], 3):.6f} pu | "
        f"TotalPower={totalPQ[0]:.2f} kW, {totalPQ[1]:.2f} kvar | "
        f"Pub={S_new.real:.6f}+j{S_new.imag:.6f} pu "
        f"LoadMult={last_loadmult:.4f}"
    )

h.helicsFederateDisconnect(dist_fed)
h.helicsFederateFree(dist_fed)
print(f"Feeder {feeder_index}: Finished.")
