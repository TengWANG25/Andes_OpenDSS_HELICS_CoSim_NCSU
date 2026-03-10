# Distribution.py
import math
import sys
import os
import helics as h
import opendssdirect as dss
from pathlib import Path

feeder_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1

# 1. HELICS Setup
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(
    fedinfo, "--federates=1 --broker=tcp://127.0.0.1:23406"
) # Match broker settings with Transmission.py
dt = 300.0 # Time step in seconds
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

#### Load Profile Definition
PROFILE_24 = [
   0.65, 0.60, 0.58, 0.57, 0.58, 0.62,
   0.70, 0.80, 0.90, 0.95, 0.98, 1.00,
   0.98, 0.95, 0.92, 0.95, 1.00, 1.08,
   1.12, 1.05, 0.95, 0.85, 0.75, 0.68
]

#PROFILE_24 = [
#    1, 1, 1, 1, 1, 1,
#    1, 1, 1, 1, 1, 1,
#    1, 1, 1, 1, 1, 1,
#    1, 1, 1, 1, 1, 1,
#    ]

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
target_time = 86400.0 # 24 hours in seconds
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
    
    dss.Text.Command(f"Edit Vsource.Source pu={Vmag:.4f} angle={Vangle:.2f}") # Update source voltage
    dss.Solution.Solve()

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
        f"Vupdate={updated} V={Vmag:.4f} pu ang={Vangle:.2f} deg | "
        f"TotalPower={totalPQ[0]:.2f} kW, {totalPQ[1]:.2f} kvar | "
        f"Pub={P_pu:.6f}+j{Q_pu:.6f} pu"
        f"LoadMult={last_loadmult:.4f} | "
    ) # Log iteration details


h.helicsFederateDisconnect(dist_fed) # Disconnect federate
h.helicsFederateFree(dist_fed) # Free federate resources
print(f"Feeder {feeder_index}: Finished.")