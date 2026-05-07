# Distribution.py
import math
import sys
import os
import helics as h
import opendssdirect as dss
from pathlib import Path

feeder_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1
DEFAULT_DIST_VOLTAGE_BUS = "regxfmr_hvmv_sub_lsb"
dist_voltage_bus = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.environ.get("DIST_VOLTAGE_BUS", DEFAULT_DIST_VOLTAGE_BUS)  # feeder-head bus voltage
)

# Read the phase voltage from a dictionary, return NaN if the phase is not present
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
    avg_mag = (
        sum(present_phase_mags) / len(present_phase_mags)
        if present_phase_mags
        else math.nan
    )

    return {
        "bus": active_bus,
        "avg_mag": avg_mag,
        "phase_mags": phase_mags,
        "phase_angles": phase_angles,
    }

# 1. HELICS Setup
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(
    fedinfo, "--federates=1 --broker=tcp://127.0.0.1:23406"
) # Match broker settings with Transmission.py
dt = 0.01 # Time step in seconds
h.helicsFederateInfoSetTimeProperty(fedinfo, h.HELICS_PROPERTY_TIME_DELTA, dt)
h.helicsFederateInfoSetFlagOption(fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, True)
dist_fed = h.helicsCreateValueFederate(f"Feeder{feeder_index}", fedinfo)
subV = h.helicsFederateRegisterSubscription(dist_fed, "Bus2Voltage", "")
pubS = h.helicsFederateRegisterGlobalPublication(dist_fed, f"Feeder{feeder_index}_Power", h.HELICS_DATA_TYPE_COMPLEX, "")
h.helicsPublicationSetOption(
    pubS, h.HELICS_HANDLE_OPTION_ONLY_TRANSMIT_ON_CHANGE, 1
) # Only publish on change
h.helicsFederateEnterExecutingMode(dist_fed)
print(f"Feeder {feeder_index}: Connected.")

# 2. OpenDSS Setup
SCRIPT_DIR = Path(__file__).resolve().parent # Get current script directory
master_dss = (SCRIPT_DIR / "8500Node" / "Master.dss").resolve() # Path to Master DSS file
dss.Basic.ClearAll() # Clear previous DSS data
dss.Text.Command(f'Compile "{master_dss}"') # Compile the DSS file
dss.Text.Command("set controlmode=off")   # freeze controls (regulators/caps)
dss.Text.Command("set mode=snap")         # single snapshot power flow
dss.Text.Command("set maxcontroliter=100")  # optional safety
dss.Solution.Solve()
initial_dist_bus_snapshot = get_bus_voltage_snapshot(dist_voltage_bus)
print(
    f"Feeder {feeder_index}: Tracking distribution bus "
    f"'{initial_dist_bus_snapshot['bus']}' "
    f"(Vavg={initial_dist_bus_snapshot['avg_mag']:.4f} pu, "
    f"Va={_phase_value(initial_dist_bus_snapshot['phase_mags'], 1):.4f} pu, "
    f"Vb={_phase_value(initial_dist_bus_snapshot['phase_mags'], 2):.4f} pu, "
    f"Vc={_phase_value(initial_dist_bus_snapshot['phase_mags'], 3):.4f} pu)."
)

#### Load Profile Definition
#PROFILE_24 = [
#   0.65, 0.60, 0.58, 0.57, 0.58, 0.62,
#   0.70, 0.80, 0.90, 0.95, 0.98, 1.00,
#   0.98, 0.95, 0.92, 0.95, 1.00, 1.08,
#   1.12, 1.05, 0.95, 0.85, 0.75, 0.68
#]


PROFILE_24 = [
    1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1,
    ]

def loadmult_from_time(t_sec: float) -> float:
    t_day = t_sec % 86400.0
    hour = t_day / 3600.0
    i0 = int(math.floor(hour)) % 24
    i1 = (i0 + 1) % 24
    frac = hour - math.floor(hour)
    return PROFILE_24[i0] * (1.0 - frac) + PROFILE_24[i1] * frac

last_time_applied = -1.0
last_loadmult = 1.0
#####

# 3. Iteration Loop
target_time = 10.0 # 24 hours in seconds
current_time = 0.0

# Define iteration state names for logging
ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}

# Initialize iteration counter
iter_count = 0

V_last = 1.0 + 0.0j  # Initial voltage guess
while current_time < target_time:

    next_time = min(current_time + dt, target_time) # Next time step

    granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
        dist_fed, next_time, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    current_time = granted_time # Update current time
    iter_count += 1 # Increment iteration counter

    # Get Voltage from Transmission
    updated = h.helicsInputIsUpdated(subV)     
    if updated:
        V_last = h.helicsInputGetComplex(subV) # read only when updated

    V = V_last  # Use last known voltage if not updated

    # Update OpenDSS Source
    Vmag = abs(V)
    Vangle = math.degrees(math.atan2(V.imag, V.real)) # Convert to degrees for OpenDSS

    if current_time > last_time_applied + 1e-9:
        last_loadmult = loadmult_from_time(current_time)
        dss.Text.Command(f"set loadmult={last_loadmult:.4f}")
        last_time_applied = current_time
    
    dss.Text.Command(
        f"Edit Vsource.Source pu={Vmag:.4f} angle={Vangle:.4f}"
    ) # Update source voltage with enough precision to avoid quantization flip-flop
    dss.Solution.Solve()
    dist_bus_snapshot = get_bus_voltage_snapshot(dist_voltage_bus)

    # Calculate Power in pu
    totalPQ = dss.Circuit.TotalPower()  # kW/kvar
    P_pu = - totalPQ[0] / 100000.0      # 100 MVA base
    Q_pu = - totalPQ[1] / 100000.0      # Sign adjustment for load convention

    h.helicsPublicationPublishComplex(pubS, complex(P_pu, Q_pu)) # Publish power to Transmission

    state_str = ITER_STATE_NAME.get(iteration_state, str(iteration_state)) # `state_str` definition
    print(
        f"[Feeder{feeder_index:02d}] "
        f"iter={iter_count:06d} "
        f"t_granted={current_time:.3f}s (t_req={next_time:.3f}s, dt={dt:.3f}s) "
        f"state={state_str} | "
        f"Vupdate={updated} V={Vmag:.6f} pu ang={Vangle:.6f} deg | "
        f"DistBus={dist_bus_snapshot['bus']} "
        f"Vavg={dist_bus_snapshot['avg_mag']:.6f} pu "
        f"Va={_phase_value(dist_bus_snapshot['phase_mags'], 1):.6f} pu "
        f"Vb={_phase_value(dist_bus_snapshot['phase_mags'], 2):.6f} pu "
        f"Vc={_phase_value(dist_bus_snapshot['phase_mags'], 3):.6f} pu | "
        f"TotalPower={totalPQ[0]:.2f} kW, {totalPQ[1]:.2f} kvar | "
        f"Pub={P_pu:.6f}+j{Q_pu:.6f} pu "
        f"LoadMult={last_loadmult:.4f} | "
    ) # Log iteration details


h.helicsFederateDisconnect(dist_fed) # Disconnect federate
h.helicsFederateFree(dist_fed) # Free federate resources
print(f"Feeder {feeder_index}: Finished.")
