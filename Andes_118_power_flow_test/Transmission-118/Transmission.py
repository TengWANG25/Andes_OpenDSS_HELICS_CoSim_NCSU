# Transmission.py
# HELICS federate for transmission system using ANDES"
import helics as h
import andes

# 1. HELICS Federate setup for transmission
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")           # using ZMQ core for local testing
h.helicsFederateInfoSetCoreInitString(fedinfo, "--federates=11")    # expecting 11 federates (1 transmission + 10 distribution)
h.helicsFederateInfoSetTimeProperty(fedinfo, h.HELICS_PROPERTY_TIME_DELTA, 1.0)  # 1s time step
h.helicsFederateInfoSetFlagOption(fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, True)  # allow iterative timing
trans_fed = h.helicsCreateValueFederate("TransmissionFed", fedinfo)

# Register publication for Bus 2 voltage global so feeders can find it
pubV = h.helicsFederateRegisterGlobalPublication(trans_fed, "Bus2Voltage", h.HELICS_DATA_TYPE_COMPLEX, "") 

# Register subscriptions for each feeder's power
feeder_subs = []
for i in range(1, 11):
    sub = h.helicsFederateRegisterSubscription(trans_fed, f"Feeder{i}_Power", "")  # subscribe to complex power P+jQ
    feeder_subs.append(sub)

# Enter execution mode
h.helicsFederateEnterExecutingMode(trans_fed)

# 2. Load ANDES case and prepare model
ss = andes.load("r\"118_converted_from_UNCC_to_PSSE_format_MATCH_RAW_VN_ALLSHEETS_TAP_FROM_RAW.xlsx\"", setup=False, default_config=True)

# Add distribution aggregate load at Bus 2
ss.PQ.add(name="DistLoad", bus=2, p0=0.0, q0=0.0)

# Ensure constant PQ behavior
ss.PQ.config.p2p = ss.PQ.config.q2q = 1.0
ss.PQ.config.p2i = ss.PQ.config.q2i = 0.0
ss.PQ.config.p2z = ss.PQ.config.q2z = 0.0

ss.setup()
ss.PFlow.run()  # initial solve with zero distribution load

# Get Bus 2 initial voltage
bus2_idx = ss.Bus.get_index(name=2)
Vmag = ss.Bus.V[bus2_idx]       # per-unit magnitude
V_angle_rad = ss.Bus.theta[bus2_idx] # angle in radians
bus2_voltage_complex = Vmag * (complex(math.cos(V_angle_rad), math.sin(V_angle_rad)))

# 3. Co-simulation iterative loop at time 0
time_current = 0.0
iterating = True
iteration_count = 0
# First publish initial voltage before requesting time
h.helicsPublicationPublishComplex(pubV, bus2_voltage_complex)

while iterating:
    iteration_count += 1
    # Request time advance with iteration
    result_time, iteration_state = h.helicsFederateRequestTimeIterative(
        trans_fed, time_current, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    # Sum feeder loads
    total_P = 0.0
    total_Q = 0.0
    for sub in feeder_subs:
        feeder_S = h.helicsInputGetComplex(sub)    # complex number P+jQ in pu
        total_P += feeder_S.real
        total_Q += feeder_S.imag

    # Update the Bus 2 load in ANDES
    # Ppf/Qpf might be the fields to alter for the PQ load
    ss.PQ.alter('p0', 'DistLoad', total_P)
    ss.PQ.alter('q0', 'DistLoad', total_Q)

    ss.PFlow.run()  # solve power flow with updated load

    # Get new Bus 2 voltage
    Vmag_new = ss.Bus.V[bus2_idx]
    V_angle_rad_new = ss.Bus.theta[bus2_idx]
    bus2_voltage_complex_new = Vmag_new * complex(math.cos(V_angle_rad_new), math.sin(V_angle_rad_new))

    # Check convergence: compare the new and old voltages
    deltaV = abs(abs(bus2_voltage_complex_new) - abs(bus2_voltage_complex))
    deltaAng = abs(V_angle_rad_new - V_angle_rad)
    if deltaV < 1e-5 and deltaAng < 1e-5:
        # Converged, no further iteration needed
        iterating = False
        # Publish the final voltage one last time so feeders have final value
        h.helicsPublicationPublishComplex(pubV, bus2_voltage_complex_new)
        # Advance time to next step without iteration to finalize
        h.helicsFederateRequestTime(trans_fed, time_current + 1.0)
    else:
        # Not converged yet, publish updated voltage and continue
        bus2_voltage_complex = bus2_voltage_complex_new
        Vang = V_angle_rad_new  # store for next iteration
        h.helicsPublicationPublishComplex(pubV, bus2_voltage_complex_new)
        # The loop will request another iterative time advance
        continue

# 4. Finalize federate
h.helicsFederateFinalize(trans_fed)
print(f"Converged in {iteration_count} iterations. Bus2 Voltage={Vmag_new:.4f}∠{V_angle_rad_new:.2f}, P_total={total_P*100:.2f} MW")
