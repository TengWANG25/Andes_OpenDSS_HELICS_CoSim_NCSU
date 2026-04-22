# Transmission.py
import helics as h
import andes
import math
import os
from pathlib import Path
import pandas as pd
import numpy as np


TRANSMISSION_CSV_COLUMNS = [
    "iter",
    "t_granted",
    "state",
    "updated",
    "P_total",
    "Q_total",
    "Vmag",
    "Vang_rad",
    "event_line_idx",
    "event_bus1",
    "event_bus2",
    "event_line_status",
    "event_bus1_vmag",
    "event_bus1_vang_rad",
    "event_bus2_vmag",
    "event_bus2_vang_rad",
    "event_bus_angle_diff_deg",
    "delta_min_deg",
    "delta_max_deg",
    "delta_spread_deg",
    "delta_min_idx",
    "delta_min_bus",
    "delta_max_idx",
    "delta_max_bus",
    "omega_min_pu",
    "omega_max_pu",
    "omega_max_dev",
    "omega_min_idx",
    "omega_min_bus",
    "omega_max_idx",
    "omega_max_bus",
    "vf_min_pu",
    "vf_max_pu",
    "vf_min_idx",
    "vf_min_bus",
    "vf_max_idx",
    "vf_max_bus",
]

ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}


# --- Disturbance configuration ---
# Use a stronger event close to the feeder interface so the distribution side
# actually sees the transmission disturbance. Bus 2 only has two incident lines
# (1-2 and 2-12); opening the lower-impedance 2-12 branch produces a much more
# visible voltage sag at the interface than a remote meshed-line trip.
LINE_TRIP_IDX = "Line_3"  # IEEE118 line 2-12, adjacent to the co-sim interface
LINE_TRIP_TIME = 1.0
LINE_OUTAGE_DURATION = 0.20      # seconds; use None for a permanent trip
LINE_RECLOSE_TIME = (
    None if LINE_OUTAGE_DURATION is None else LINE_TRIP_TIME + LINE_OUTAGE_DURATION
)
LINE_TRIP_EVENT_IDX = f"Trip_{LINE_TRIP_IDX}"
LINE_RECLOSE_EVENT_IDX = f"Reclose_{LINE_TRIP_IDX}"

# --- GENROU_5 baseline parameters ---
# Keep the study on the original workbook values unless we intentionally
# enable a tuning experiment again later.
GENROU5_TUNING = {
    "genrou_d": 0.0,
    "exst1_ka": 50.0,
    "exst1_vrmax": 9999.0,
    "exst1_vrmin": -9999.0,
    "tgov1_r": 0.005,
    "tgov1_dt": 0.0,
    "tgov1_vmax": 999.0,
    "tgov1_vmin": -999.0,
}


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


def get_cosim_step_config():
    fine_dt = get_positive_env_float("SIM_FINE_DT", 0.01)
    coarse_dt = get_positive_env_float("SIM_COARSE_DT", 0.05)
    coarse_start_default = (
        (LINE_RECLOSE_TIME if LINE_RECLOSE_TIME is not None else LINE_TRIP_TIME) + 1.0
    )
    coarse_start = get_positive_env_float("SIM_COARSE_START", coarse_start_default)
    if coarse_dt < fine_dt:
        raise ValueError(
            f"Invalid co-simulation step schedule: SIM_COARSE_DT={coarse_dt} "
            f"must be >= SIM_FINE_DT={fine_dt}."
        )
    return fine_dt, coarse_dt, coarse_start


def apply_genrou5_tuning(ss):
    ss.GENROU.alter("D", "GENROU_5", GENROU5_TUNING["genrou_d"])

    ss.EXST1.alter("KA", "EXST1_5", GENROU5_TUNING["exst1_ka"])
    ss.EXST1.alter("VRMAX", "EXST1_5", GENROU5_TUNING["exst1_vrmax"])
    ss.EXST1.alter("VRMIN", "EXST1_5", GENROU5_TUNING["exst1_vrmin"])

    ss.TGOV1.alter("R", "TGOV1_5", GENROU5_TUNING["tgov1_r"])
    ss.TGOV1.alter("Dt", "TGOV1_5", GENROU5_TUNING["tgov1_dt"])
    ss.TGOV1.alter("VMAX", "TGOV1_5", GENROU5_TUNING["tgov1_vmax"])
    ss.TGOV1.alter("VMIN", "TGOV1_5", GENROU5_TUNING["tgov1_vmin"])


def split_line_for_parallel_trip(ss, base_idx: str, parallel_idx: str):
    # Optional helper kept for future studies where a synthetic parallel
    # branch is useful; the current contingency uses an existing double-circuit
    # line and does not call this path.
    base_uid = ss.Line.idx2uid(base_idx)

    def _value(field):
        return getattr(ss.Line, field).v[base_uid]

    base_name = str(_value("name"))
    line_params = {
        "idx": parallel_idx,
        "name": f"{base_name} parallel",
        "u": int(_value("u")),
        "bus1": int(_value("bus1")),
        "bus2": int(_value("bus2")),
        "Sn": float(_value("Sn")),
        "fn": float(_value("fn")),
        "Vn1": float(_value("Vn1")),
        "Vn2": float(_value("Vn2")),
        "r": float(_value("r")) * 2.0,
        "x": float(_value("x")) * 2.0,
        "b": float(_value("b")) / 2.0,
        "g": float(_value("g")) / 2.0,
        "b1": float(_value("b1")) / 2.0,
        "g1": float(_value("g1")) / 2.0,
        "b2": float(_value("b2")) / 2.0,
        "g2": float(_value("g2")) / 2.0,
        "trans": int(_value("trans")),
        "tap": float(_value("tap")),
        "phi": float(_value("phi")),
        "rate_a": float(_value("rate_a")),
        "rate_b": float(_value("rate_b")),
        "rate_c": float(_value("rate_c")),
    }

    ss.Line.r.v[base_uid] = line_params["r"]
    ss.Line.x.v[base_uid] = line_params["x"]
    ss.Line.b.v[base_uid] = line_params["b"]
    ss.Line.g.v[base_uid] = line_params["g"]
    ss.Line.b1.v[base_uid] = line_params["b1"]
    ss.Line.g1.v[base_uid] = line_params["g1"]
    ss.Line.b2.v[base_uid] = line_params["b2"]
    ss.Line.g2.v[base_uid] = line_params["g2"]

    ss.add("Line", line_params)

    return {
        "base_idx": base_idx,
        "base_name": base_name,
        "parallel_idx": parallel_idx,
        "bus1": line_params["bus1"],
        "bus2": line_params["bus2"],
        "r_each": line_params["r"],
        "x_each": line_params["x"],
        "b_each": line_params["b"],
    }


def get_total_feeder_power(subs, last_s):
    total_P = 0.0
    total_Q = 0.0
    updated_cnt = 0
    for i, sub in enumerate(subs):
        if h.helicsInputIsUpdated(sub):
            last_s[i] = h.helicsInputGetComplex(sub)
            updated_cnt += 1
        s_val = last_s[i]
        total_P += s_val.real
        total_Q += s_val.imag
    return total_P, total_Q, updated_cnt

def get_tx_bus_voltage(ss, bus_uid):
    vmag = ss.dae.y[ss.Bus.v.a[bus_uid]]
    vang = ss.dae.y[ss.Bus.a.a[bus_uid]]
    v = vmag * complex(math.cos(vang), math.sin(vang))
    return v, vmag, vang


def wrap_angle_deg(angle_deg):
    return (angle_deg + 180.0) % 360.0 - 180.0


def get_event_line_diagnostics(
    ss,
    line_uid,
    line_idx,
    bus1,
    bus2,
    bus1_uid,
    bus2_uid,
):
    _, bus1_vmag, bus1_vang = get_tx_bus_voltage(ss, bus1_uid)
    _, bus2_vmag, bus2_vang = get_tx_bus_voltage(ss, bus2_uid)

    return {
        "event_line_idx": str(line_idx),
        "event_bus1": int(bus1),
        "event_bus2": int(bus2),
        "event_line_status": float(ss.Line.u.v[line_uid]),
        "event_bus1_vmag": float(bus1_vmag),
        "event_bus1_vang_rad": float(bus1_vang),
        "event_bus2_vmag": float(bus2_vmag),
        "event_bus2_vang_rad": float(bus2_vang),
        "event_bus_angle_diff_deg": float(
            wrap_angle_deg(math.degrees(bus1_vang - bus2_vang))
        ),
    }


def get_genrou_diagnostics(ss):
    diag = {
        "delta_min_deg": math.nan,
        "delta_max_deg": math.nan,
        "delta_spread_deg": math.nan,
        "delta_min_idx": None,
        "delta_min_bus": math.nan,
        "delta_max_idx": None,
        "delta_max_bus": math.nan,
        "omega_min_pu": math.nan,
        "omega_max_pu": math.nan,
        "omega_max_dev": math.nan,
        "omega_min_idx": None,
        "omega_min_bus": math.nan,
        "omega_max_idx": None,
        "omega_max_bus": math.nan,
        "vf_min_pu": math.nan,
        "vf_max_pu": math.nan,
        "vf_min_idx": None,
        "vf_min_bus": math.nan,
        "vf_max_idx": None,
        "vf_max_bus": math.nan,
    }

    if not hasattr(ss, "GENROU") or getattr(ss.GENROU, "n", 0) <= 0:
        return diag

    def _values(var):
        if not hasattr(var, "v"):
            return np.asarray([], dtype=float)
        return np.asarray(var.v, dtype=float)

    delta = _values(ss.GENROU.delta)
    omega = _values(ss.GENROU.omega)
    vf = _values(ss.GENROU.vf)
    gen_ids = np.asarray(ss.GENROU.idx.v, dtype=object)
    gen_buses = np.asarray(ss.GENROU.bus.v, dtype=int)

    if delta.size:
        delta_deg = np.rad2deg(delta)
        delta_min_pos = int(np.argmin(delta_deg))
        delta_max_pos = int(np.argmax(delta_deg))
        diag["delta_min_deg"] = float(delta_deg[delta_min_pos])
        diag["delta_max_deg"] = float(delta_deg[delta_max_pos])
        diag["delta_spread_deg"] = float(delta_deg[delta_max_pos] - delta_deg[delta_min_pos])
        diag["delta_min_idx"] = str(gen_ids[delta_min_pos])
        diag["delta_min_bus"] = int(gen_buses[delta_min_pos])
        diag["delta_max_idx"] = str(gen_ids[delta_max_pos])
        diag["delta_max_bus"] = int(gen_buses[delta_max_pos])

    if omega.size:
        omega_min_pos = int(np.argmin(omega))
        omega_max_pos = int(np.argmax(omega))
        diag["omega_min_pu"] = float(omega[omega_min_pos])
        diag["omega_max_pu"] = float(omega[omega_max_pos])
        diag["omega_max_dev"] = float(np.max(np.abs(omega - 1.0)))
        diag["omega_min_idx"] = str(gen_ids[omega_min_pos])
        diag["omega_min_bus"] = int(gen_buses[omega_min_pos])
        diag["omega_max_idx"] = str(gen_ids[omega_max_pos])
        diag["omega_max_bus"] = int(gen_buses[omega_max_pos])

    if vf.size:
        vf_min_pos = int(np.argmin(vf))
        vf_max_pos = int(np.argmax(vf))
        diag["vf_min_pu"] = float(vf[vf_min_pos])
        diag["vf_max_pu"] = float(vf[vf_max_pos])
        diag["vf_min_idx"] = str(gen_ids[vf_min_pos])
        diag["vf_min_bus"] = int(gen_buses[vf_min_pos])
        diag["vf_max_idx"] = str(gen_ids[vf_max_pos])
        diag["vf_max_bus"] = int(gen_buses[vf_max_pos])

    return diag


def make_timeseries_row(
    iteration,
    t_granted,
    state,
    updated,
    total_p,
    total_q,
    vmag,
    vang_rad,
    diagnostics=None,
):
    row = {
        "iter": int(iteration),
        "t_granted": float(t_granted),
        "state": state,
        "updated": int(updated),
        "P_total": float(total_p),
        "Q_total": float(total_q),
        "Vmag": float(vmag),
        "Vang_rad": float(vang_rad),
    }
    if diagnostics:
        row.update(diagnostics)
    return row


def write_transmission_timeseries(csv_path, rows):
    if rows:
        df = pd.DataFrame(rows, columns=TRANSMISSION_CSV_COLUMNS)
    else:
        df = pd.DataFrame(columns=TRANSMISSION_CSV_COLUMNS)
    df.to_csv(csv_path, index=False)
    print(f"Transmission: saved timeseries CSV to {csv_path}")

# 1. HELICS Federate setup
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(
    fedinfo, "--federates=1 --broker=tcp://127.0.0.1:23406"
)
fine_dt, coarse_dt, coarse_start = get_cosim_step_config()
h.helicsFederateInfoSetTimeProperty(
    fedinfo, h.HELICS_PROPERTY_TIME_DELTA, min(fine_dt, coarse_dt)
)
h.helicsFederateInfoSetFlagOption(fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, True)
trans_fed = h.helicsCreateValueFederate("TransmissionFed", fedinfo)

pubV = h.helicsFederateRegisterGlobalPublication(
    trans_fed, "Bus2Voltage", h.HELICS_DATA_TYPE_COMPLEX, ""
)
h.helicsPublicationSetOption(pubV, h.HELICS_HANDLE_OPTION_ONLY_TRANSMIT_ON_CHANGE, 1)

feeder_subs = []
for i in range(1, 2):
    feeder_subs.append(
        h.helicsFederateRegisterSubscription(trans_fed, f"Feeder{i}_Power", "")
    )

print("Transmission: HELICS interfaces created.")

# 2. Load ANDES
SCRIPT_DIR = Path(__file__).resolve().parent
CASE_XLSX = (SCRIPT_DIR / "IEEE118_from_PDF.xlsx").resolve()
TRANSMISSION_CSV_PATH = SCRIPT_DIR / "transmission_timeseries.csv"
ss = andes.load(str(CASE_XLSX), setup=False, default_config=True)
apply_genrou5_tuning(ss)
ss.PQ.add(idx="DistLoad", name="DistLoad", bus=2, p0=0.0, q0=0.0)
ss.add(
    "Toggle",
    {
        "idx": LINE_TRIP_EVENT_IDX,
        "model": "Line",
        "dev": LINE_TRIP_IDX,
        "t": LINE_TRIP_TIME,
    },
)
if LINE_RECLOSE_TIME is not None:
    ss.add(
        "Toggle",
        {
            "idx": LINE_RECLOSE_EVENT_IDX,
            "model": "Line",
            "dev": LINE_TRIP_IDX,
            "t": LINE_RECLOSE_TIME,
        },
    )


# Constant-power behavior during TDS
ss.PQ.config.p2p = 1.0
ss.PQ.config.p2i = 0.0
ss.PQ.config.p2z = 0.0
ss.PQ.config.q2q = 1.0
ss.PQ.config.q2i = 0.0
ss.PQ.config.q2z = 0.0

ss.setup()
ss.PFlow.run()

line_uid = ss.Line.idx2uid(LINE_TRIP_IDX)
line_bus1 = int(ss.Line.bus1.v[line_uid])
line_bus2 = int(ss.Line.bus2.v[line_uid])
line_bus1_uid = ss.Bus.idx2uid(line_bus1)
line_bus2_uid = ss.Bus.idx2uid(line_bus2)
print(
    f"Transmission event: line trip on {LINE_TRIP_IDX} "
    f"({line_bus1}-{line_bus2}) at t={LINE_TRIP_TIME:.3f}s"
)
print(
    "Transmission: applied GENROU_5 tuning "
    f"(D={GENROU5_TUNING['genrou_d']}, "
    f"KA={GENROU5_TUNING['exst1_ka']}, "
    f"VRMAX/VRMIN={GENROU5_TUNING['exst1_vrmax']}/{GENROU5_TUNING['exst1_vrmin']}, "
    f"R={GENROU5_TUNING['tgov1_r']}, "
    f"Dt={GENROU5_TUNING['tgov1_dt']}, "
    f"VMAX/VMIN={GENROU5_TUNING['tgov1_vmax']}/{GENROU5_TUNING['tgov1_vmin']})"
)
if LINE_RECLOSE_TIME is not None:
    print(
        f"Transmission event: line reclose on {LINE_TRIP_IDX} "
        f"at t={LINE_RECLOSE_TIME:.3f}s"
    )
print(
    "Transmission: co-simulation step schedule = "
    f"{fine_dt:.3f}s until t={coarse_start:.3f}s, then {coarse_dt:.3f}s"
)

bus2_uid = ss.Bus.idx2uid(2)
last_s = [0.0 + 0.0j] * len(feeder_subs)

# ---- HELICS initialization handshake at t = 0 ----
max_init = 20
tol_init_v = 1e-6
tol_init_s = 1e-6

bus2_voltage_complex, Vmag, V_angle_rad = get_tx_bus_voltage(ss, bus2_uid)
V_prev = bus2_voltage_complex
S_prev = None

h.helicsFederateEnterInitializingMode(trans_fed)
print("Transmission: entered HELICS initialization mode.")

# Publish the initial transmission voltage guess before the first iterative entry
h.helicsPublicationPublishComplex(pubV, bus2_voltage_complex)

for k in range(max_init):
    init_state = h.helicsFederateEnterExecutingModeIterative(
        trans_fed, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    total_P, total_Q, updated_cnt = get_total_feeder_power(feeder_subs, last_s)

    # During the initialization handshake, update the static PF load
    ss.PQ.alter("p0", "DistLoad", total_P)
    ss.PQ.alter("q0", "DistLoad", total_Q)
    ss.PFlow.run()

    bus2_voltage_new, Vmag_new, V_angle_rad_new = get_tx_bus_voltage(ss, bus2_uid)
    S_new = complex(total_P, total_Q)

    dV = abs(bus2_voltage_new - V_prev)
    dS = float("inf") if S_prev is None else abs(S_new - S_prev)

    print(
        f"[TX init {k+1:02d}] updated={updated_cnt}/{len(feeder_subs)} "
        f"P={total_P:.6f} Q={total_Q:.6f} "
        f"|V|={Vmag_new:.6f} ang={V_angle_rad_new:.6f} "
        f"dV={dV:.3e} dS={dS:.3e}"
    )

    # Publish only if our output changed meaningfully
    if dV > tol_init_v or dS > tol_init_s:
        h.helicsPublicationPublishComplex(pubV, bus2_voltage_new)

    V_prev = bus2_voltage_new
    S_prev = S_new

    if init_state == h.HELICS_ITERATION_RESULT_NEXT_STEP:
        break
else:
    raise RuntimeError("Transmission initialization handshake did not converge.")

print("Transmission: initialization handshake converged.")

# ---- NOW initialize TDS from the converged static equilibrium ----
TDS_INTERNAL_STEP = 0.033
ss.TDS.config.tstep = TDS_INTERNAL_STEP
ss.TDS.config.tf = 0.0
ss.TDS.run()

bus2_voltage_complex, Vmag, V_angle_rad = get_tx_bus_voltage(ss, bus2_uid)
Vprev = bus2_voltage_complex

print(f"Bus2 Initial Voltage: {bus2_voltage_complex:.6f}")
print(f"Bus2 Initial Voltage Mag: {Vmag:.6f}")
print(f"Bus2 Initial Voltage Angle: {V_angle_rad:.6f}")

# Optional: print the now-consistent initialized DistLoad
uid = ss.PQ.idx2uid("DistLoad")
print(f"DistLoad p0={ss.PQ.p0.v[uid]:.6f}, q0={ss.PQ.q0.v[uid]:.6f}")

timeseries_rows = [
    make_timeseries_row(
        iteration=0,
        t_granted=0.0,
        state="INITIALIZED",
        updated=len(feeder_subs),
        total_p=ss.PQ.p0.v[uid],
        total_q=ss.PQ.q0.v[uid],
        vmag=Vmag,
        vang_rad=V_angle_rad,
        diagnostics={
            **get_event_line_diagnostics(
                ss,
                line_uid,
                LINE_TRIP_IDX,
                line_bus1,
                line_bus2,
                line_bus1_uid,
                line_bus2_uid,
            ),
            **get_genrou_diagnostics(ss),
        },
    )
]

# 3. Dynamic loop starts here, same idea as your current code
current_time = 0.0
target_time = get_target_time()
print(f"Transmission: target simulation time = {target_time:.3f} s")

iter_count = 0
tolV = 1e-5
alpha = 1
max_outer = 20
tx_failed = False
tx_progress_interval = 0.5
next_tx_progress_time = tx_progress_interval

try:
    while current_time < target_time and not tx_failed:
        current_dt = fine_dt if current_time + 1e-9 < coarse_start else coarse_dt
        next_time = min(current_time + current_dt, target_time)
        iter_req = h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED

        for k in range(max_outer):
            granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
                trans_fed, next_time, iter_req
            )
            current_time = granted_time
            iter_count += 1

            total_P, total_Q, updated_cnt = get_total_feeder_power(feeder_subs, last_s)

            # During TDS, update Ppf/Qpf, not p0/q0
            ss.PQ.set(src="Ppf", idx="DistLoad", attr="v", value=total_P)
            ss.PQ.set(src="Qpf", idx="DistLoad", attr="v", value=total_Q)

            ss.TDS.config.tf = granted_time
            ss.TDS.run(no_summary=True)

            if ss.dae.t + 1e-9 < granted_time:
                print(
                    f"[TX] TDS failed to reach requested time: "
                    f"dae.t={ss.dae.t:.6f}, requested={granted_time:.6f}"
                )
                print(
                    f"[TX progress] t={ss.dae.t:.3f}s/{target_time:.3f}s "
                    f"state=FAILED updated={updated_cnt}/{len(feeder_subs)} "
                    f"P={total_P:.6f} Q={total_Q:.6f}"
                )
                tx_failed = True
                break

            bus2_voltage_new, Vmag_new, V_angle_rad_new = get_tx_bus_voltage(ss, bus2_uid)
            bus2_voltage_pub = alpha * bus2_voltage_new + (1 - alpha) * Vprev

            h.helicsPublicationPublishComplex(pubV, bus2_voltage_pub)

            if abs(bus2_voltage_pub - Vprev) < tolV:
                iter_req = h.HELICS_ITERATION_REQUEST_NO_ITERATION
            else:
                iter_req = h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED

            Vprev = bus2_voltage_pub
            state_name = ITER_STATE_NAME.get(iteration_state, str(iteration_state))

            if iteration_state != h.HELICS_ITERATION_RESULT_ITERATING:
                timeseries_rows.append(
                    make_timeseries_row(
                        iteration=iter_count,
                        t_granted=granted_time,
                        state=state_name,
                        updated=updated_cnt,
                        total_p=total_P,
                        total_q=total_Q,
                        vmag=Vmag_new,
                        vang_rad=V_angle_rad_new,
                        diagnostics={
                            **get_event_line_diagnostics(
                                ss,
                                line_uid,
                                LINE_TRIP_IDX,
                                line_bus1,
                                line_bus2,
                                line_bus1_uid,
                                line_bus2_uid,
                            ),
                            **get_genrou_diagnostics(ss),
                        },
                    )
                )
                if (
                    granted_time + 1e-9 >= next_tx_progress_time
                    or granted_time + 1e-9 >= target_time
                    or iteration_state in (
                        h.HELICS_ITERATION_RESULT_ERROR,
                        h.HELICS_ITERATION_RESULT_HALTED,
                    )
                ):
                    print(
                        f"[TX progress] t={granted_time:.3f}s/{target_time:.3f}s "
                        f"state={state_name} updated={updated_cnt}/{len(feeder_subs)} "
                        f"P={total_P:.6f} Q={total_Q:.6f} "
                        f"|V|={Vmag_new:.6f} ang={V_angle_rad_new:.6f} "
                        f"dt={current_dt:.3f}s"
                    )
                    while next_tx_progress_time <= granted_time + 1e-9:
                        next_tx_progress_time += tx_progress_interval
                if iteration_state in (
                    h.HELICS_ITERATION_RESULT_ERROR,
                    h.HELICS_ITERATION_RESULT_HALTED,
                ):
                    tx_failed = True
                break
        else:
            print(f"[TX] WARN: hit max_outer={max_outer} at t={current_time:.3f}s")
finally:
    write_transmission_timeseries(TRANSMISSION_CSV_PATH, timeseries_rows)
    try:
        h.helicsFederateDisconnect(trans_fed)
    except Exception as exc:
        print(f"Transmission: HELICS disconnect warning: {exc}")
    print("Transmission: Finished.")
