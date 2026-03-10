# Transmission.py
import helics as h
import andes
import math
from pathlib import Path
import pandas as pd

# 1. HELICS Federate setup
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(
    fedinfo, "--federates=1 --broker=tcp://127.0.0.1:23406"
) # Match broker settings with Distribution.py
dt = 300.0 # Time step in seconds
h.helicsFederateInfoSetTimeProperty(fedinfo, h.HELICS_PROPERTY_TIME_DELTA, dt)
h.helicsFederateInfoSetFlagOption(fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, True)
trans_fed = h.helicsCreateValueFederate("TransmissionFed", fedinfo)

# Publications and Subscriptions
pubV = h.helicsFederateRegisterGlobalPublication(trans_fed, "Bus2Voltage", h.HELICS_DATA_TYPE_COMPLEX, "") # Publish voltage at Bus 2
h.helicsPublicationSetOption(
    pubV, h.HELICS_HANDLE_OPTION_ONLY_TRANSMIT_ON_CHANGE, 1
) # Only publish on change
feeder_subs = []
for i in range(1, 11):
    sub = h.helicsFederateRegisterSubscription(trans_fed, f"Feeder{i}_Power", "")
    feeder_subs.append(sub)

h.helicsFederateEnterExecutingMode(trans_fed)
print("Transmission: Connected and ready.")

# 2. Load ANDES
SCRIPT_DIR = Path(__file__).resolve().parent  # Get current script directory
CASE_XLSX = (SCRIPT_DIR / "IEEE_118_final_conversion.xlsx").resolve() # Path to the case file
ss = andes.load(str(CASE_XLSX), setup=False, default_config=True) # Load the system without setup

ss.PQ.add(idx="DistLoad", name="DistLoad", bus=2, p0=0.0, q0=0.0) # Add a PQ load at bus 2 for distribution load

ss.setup() # Setup the system
ss.PFlow.run() # Run power flow to initialize

# Read Voltage In ANDES
bus2_idx = ss.Bus.idx2uid(2)  # Get internal index for bus 2
# Use dae.y to access variables v=magnitude, a=angle
Vmag = ss.dae.y[ss.Bus.v.a[bus2_idx]] # Voltage magnitude at bus 2
V_angle_rad = ss.dae.y[ss.Bus.a.a[bus2_idx]] # Voltage angle at bus 2 in radians
bus2_voltage_complex = Vmag * complex(math.cos(V_angle_rad), math.sin(V_angle_rad))

# 3. Iteration Loop
current_time = 0.0
target_time = 86400.0 # 24 hours in seconds

h.helicsPublicationPublishComplex(pubV, bus2_voltage_complex) # Publish initial value

#Print initial voltage
print(f"Bus2 Initial Voltage: {bus2_voltage_complex:.6f}") # Initial Voltage print
print(f"Bus2 Initial Voltage Mag: {Vmag:.6f}") 
print(f"Bus2 Initial Voltage Angle: {V_angle_rad:.6f}")

# Print initial load
# Get initial load at bus 2
pq_df = pd.DataFrame({
    "idx": list(ss.PQ.idx.v),
    "bus": list(ss.PQ.bus.v),
    "p0":  list(ss.PQ.p0.v),
    "q0":  list(ss.PQ.q0.v),
}) # Create DataFrame of PQ loads

bus2_pq = pq_df[pq_df["bus"] == 2] # Filter loads at bus 2

orig_bus2 = bus2_pq[bus2_pq["idx"] != "DistLoad"] # Original loads at bus 2
dist_bus2 = bus2_pq[bus2_pq["idx"] == "DistLoad"] # Distribution load at bus 2

P0_orig = float(orig_bus2["p0"].sum()) # Original load P0
Q0_orig = float(orig_bus2["q0"].sum()) # Original load Q0

P0_dist = float(dist_bus2["p0"].sum()) # Distribution load P0
Q0_dist = float(dist_bus2["q0"].sum()) # Distribution load Q0

P0_total = P0_orig + P0_dist # Total load P0
Q0_total = Q0_orig + Q0_dist # Total load Q0

print(f"Bus2 Initial Original Load:   P0={P0_orig:.6f}, Q0={Q0_orig:.6f}") # Original load print
print(f"Bus2 Initial Distribution Load:  P0={P0_dist:.6f}, Q0={Q0_dist:.6f}") # Distribution load print
print(f"Bus2 Initial TOTAL Load:      P0={P0_total:.6f}, Q0={Q0_total:.6f}") # Total load print

# Iteration State Name Mapping
ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}

iter_count = 0 # Initialize iteration counter
last_s = [0.0 + 0.0j] * len(feeder_subs) # Last known S (power) values for feeders

tolV = 1e-5 # Voltage tolerance for iteration convergence
alpha = 0.2 # Smoothing factor for voltage updates
Vprev = bus2_voltage_complex # Previous voltage for convergence check
max_outer = 20 # Maximum outer iterations to prevent infinite loops
while current_time < target_time:

    next_time = min(current_time + dt, target_time) # Next time step
    iter_req = h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    for k in range(max_outer):
        granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
            trans_fed, next_time, iter_req)
        current_time = granted_time # Update current time
        iter_count += 1

        # Header line: shows iteration, time, requested time, and iterative state
        state_str = ITER_STATE_NAME.get(iteration_state, str(iteration_state))
        print(
            f"[iter={iter_count:06d}] "
            f"t_granted={current_time:.3f}s "
            f"(t_req={next_time:.3f}s, dt={dt:.3f}s) "
            f"state={state_str}"
        ) # Log iteration header

        # Calculate total Load from 10 feeders
        total_P = 0.0
        total_Q = 0.0
        updated_cnt = 0
        for i, sub in enumerate(feeder_subs):
            if h.helicsInputIsUpdated(sub):
                s_val = h.helicsInputGetComplex(sub) # Read only when updated
                last_s[i] = s_val # Update last known value                      
                updated_cnt += 1 # Count updated feeders
            else:
                s_val = last_s[i] # Use last known value if not updated         
            total_P += s_val.real
            total_Q += s_val.imag
        print(f"[iter={iter_count:06d} t={current_time:.3f}s] "
            f"Total Distribution Load P={total_P:.4f}, Q={total_Q:.4f} (updated={updated_cnt}/{len(feeder_subs)})") # Log total load from feeders

        # Update ANDES
        ss.PQ.alter('p0', 'DistLoad', total_P) # Update distribution load P
        ss.PQ.alter('q0', 'DistLoad', total_Q) # Update distribution load Q
        ss.PFlow.run()

        # Get new Voltage
        Vmag_new = ss.dae.y[ss.Bus.v.a[bus2_idx]] # New Voltage magnitude at bus 2
        V_angle_rad_new = ss.dae.y[ss.Bus.a.a[bus2_idx]] # New Voltage angle at bus 2 in radians
        bus2_voltage_complex_new = Vmag_new * complex(math.cos(V_angle_rad_new), math.sin(V_angle_rad_new)) 
        # Apply smoothing to voltage updates
        bus2_voltage_complex_new = alpha * bus2_voltage_complex_new + (1 - alpha) * Vprev

        # Check the new voltage
        print(f"[iter={iter_count:06d} t={current_time:.3f}s] Bus2 V={bus2_voltage_complex_new:.6f}") # Log new voltage
        print(f"[iter={iter_count:06d} t={current_time:.3f}s] Bus2 |V|={Vmag_new:.6f}, angle(rad)={V_angle_rad_new:.6f}") # Log new voltage magnitude and angle

        h.helicsPublicationPublishComplex(pubV, bus2_voltage_complex_new) # Publish updated voltage
        bus2_voltage_complex = bus2_voltage_complex_new # Update for next iteration

        # Check for convergence: if voltage change is within tolerance, we can stop iterating
        if abs(bus2_voltage_complex_new - Vprev) < tolV:
            iter_req = h.HELICS_ITERATION_REQUEST_NO_ITERATION
        else:
            iter_req = h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED

        Vprev = bus2_voltage_complex_new # Update previous voltage for next iteration

        if iteration_state != h.HELICS_ITERATION_RESULT_ITERATING:
            break # Exit outer loop if not iterating
    
    else:
        print(f"[TX] WARN: hit max_outer={max_outer} at t={current_time:.3f}s; forcing advance")


h.helicsFederateDisconnect(trans_fed) # Disconnect federate
print("Transmission: Finished.")