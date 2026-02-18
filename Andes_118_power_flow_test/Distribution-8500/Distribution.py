# Distribution.py for 10 feeders co-simulated with a transmission system using HELICS and OpenDSS
import math
import sys
import helics as h
import opendssdirect as dss

int(sys.argv[1])  # Feeder index from command line argument

# 1. HELICS Federate setup for distribution feeder
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(fedinfo, "--federates=11")
h.helicsFederateInfoSetTimeProperty(fedinfo, h.HELICS_PROPERTY_TIME_DELTA, 1.0)
h.helicsFederateInfoSetFlagOption(fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, True)
dist_fed = h.helicsCreateValueFederate(f"Feeder{feeder_index}", fedinfo)

# Register subscription to the Bus2 voltage
subV = h.helicsFederateRegisterSubscription(dist_fed, "Bus2Voltage", "")  # Complex number
# Register publication for this feeder's power
pubS = h.helicsFederateRegisterGlobalPublication(dist_fed, f"Feeder{feeder_index}_Power", h.HELICS_DATA_TYPE_COMPLEX, "")

h.helicsFederateEnterExecutingMode(dist_fed)

# 2. Load and initialize the OpenDSS feeder model
dss.Text.Command(f"8500node_master.dss")  # Load the feeder model file but need to be adjusted per feeder and the updated name
# Set substation source to nominal 1.0 pu, 0 deg initially
dss.Text.Command("Edit Vsource.Source pu=1.0 angle=0")
dss.Solution.Solve()
# After initial solve, we may publish an initial power but we actually should wait for voltage from transmission

# 3. Co-simulation loop
time_current = 0.0
converged = False
while not converged:
    # Request the next time step
    result_time = h.helicsFederateRequestTime(dist_fed, time_current)

    # Get updated Bus 2 voltage from transmission
    V = h.helicsInputGetComplex(subV)  # complex number pu
    # Extract magnitude and angle
    Vmag = abs(V)
    Vangle = 0.0 # Set the default angle
    if Vmag > 1e-9: # Avoid division by zero
        # Calculate angle of V, it is in radians then convert to degrees required by OpenDSS
        Vangle = math.degrees(math.atan2(V.imag, V.real))
    # Set the feeder source voltage to this value
    # OpenDSS Vsource uses "pu" relative to its base and angle to degrees accordingly.
    cmd = f"Edit Vsource.Source pu={Vmag:.4f} angle={Vangle:.2f}"
    dss.Text.Command(cmd)

    # Solve the feeder power flow with the new source voltage
    dss.Solution.Solve()

    # Get total feeder power
    totalPQ = dss.Circuit.TotalPower()  # P(kW), Q(kvar)
    P_kW = totalPQ[0] # Get the real power in kW
    Q_kvar = totalPQ[1] # Get the reactive power in kvar
    # Convert to per-unit on transmission base, 100 MVA base = 100000 kW, use 100 MVA baseas system base
    P_pu = P_kW / 100000.0
    Q_pu = Q_kvar / 100000.0
    # Publish this feeder's power consumption in per-unit
    feeder_S = complex(P_pu, Q_pu)
    h.helicsPublicationPublishComplex(pubS, feeder_S)

    # Check iteration status to follow the transmission federate's progress
    granted_time = h.helicsFederateGetCurrentTime(dist_fed)
    if granted_time >= 1.0:
        # If time has advanced to 1.0, that implies convergence, which means the transmission federation has moved on to the next loop.
        converged = True

# 4. Finalize federate
h.helicsFederateFinalize(dist_fed)
