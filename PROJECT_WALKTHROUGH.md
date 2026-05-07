# Project Walkthrough

## Goal

This project studies fault induced delayed voltage recovery (FIDVR) in a
transmission-distribution co-simulation. ANDES solves the transmission-side
dynamic system, OpenDSS provides the IEEE 13-node distribution feeder, and HELICS
exchanges the interface voltage and feeder power at each co-simulation time step.

The current FIDVR path uses one physically interpretable distribution load
model: the WECC/LD1PAC Motor D representation implemented as controlled OpenDSS
load injections.

## Entry Points

- `./run.sh`: launches the HELICS broker, the transmission federate, and one or
  more feeder federates with a selected FIDVR profile.
- `Transmission.py`: ANDES federate. It applies the transmission fault, receives
  feeder `P/Q`, advances the dynamic simulation, and publishes the interface
  bus voltage.
- `Distribution.py`: OpenDSS feeder federate. It receives the transmission
  voltage, updates Motor D/capacitor/regulator states, solves the feeder, and
  publishes feeder `P/Q`.
- `plot_from_logs.py`: transmission side plots and alert summaries.
- `plot_distribution_from_logs.py`: distribution side voltage, FIDVR trajectory,
  and feeder alert summaries.
- `fidvr_alerts.py`, `fidvr_controls.py`, `fidvr_load_restoration.py`: small
  helper modules for alert detection, delayed shunt controls, and long delay
  thermal load restoration.
- `WECC_MOTOR_D_TRACEABILITY.md`: maps each implemented Motor D parameter and
  equation block to pages in the local WECC report.

## Runtime Data Flow

```text
run.sh
  |
  +-- broker.py / helics_broker
  |
  +-- Transmission.py  (ANDES dynamic model)
  |      |
  |      | receives Feeder1_Power, Feeder2_Power, ...
  |      | updates DistLoad Ppf/Qpf in ANDES
  |      | advances TDS to the granted HELICS time
  |      v
  |   publishes TxInterfaceVoltage
  |
  +-- Distribution.py  (one process per feeder)
         |
         | receives TxInterfaceVoltage
         | edits OpenDSS Vsource
         | updates Motor D, capacitor, and regulator states
         | solves OpenDSS snapshot
         v
      publishes FeederN_Power
```

The feedback loop is:

```text
Transmission voltage -> feeder voltage response -> feeder P/Q -> transmission
load injection -> next transmission voltage
```

HELICS iteration is used at each granted time so the interface voltage and
feeder power can converge before the simulation advances to the next time step.

## FIDVR Model Flow In `Distribution.py`

1. Load, regulator, and capacitor data are read from the OpenDSS IEEE 13-node
   case.
2. When `FIDVR_ENABLE=1`, selected single-phase loads are split into:
   - remaining static OpenDSS load
   - `Load.weccmd_*` Motor D injection
3. At each co-simulation time, the feeder reads the latest transmission voltage.
4. Motor D state timers are updated using the monitored motor terminal voltage:
   - voltage below `Vstall` for `Tstall` arms stall
   - contactor dropout/reclose follows the WECC voltage curves
   - thermal state follows the WECC stall heating logic
   - optional long-delay thermal load restoration is applied only when enabled
5. The same `Load.weccmd_*` elements are edited to represent running, stalled,
   tripped, and restoring behavior. The network element does not change.
6. OpenDSS solves a snapshot and publishes total feeder `P/Q` back to ANDES.

## Recommended Demonstration Run

For a compact FIDVR demonstration:

```bash
TARGET_TIME=60 FIDVR_ENABLE=1 FIDVR_PROFILE=weak_bus14 \
FIDVR_MOTOR_SHARE=1.0 ./run.sh
```

Then generate plots:

```bash
python3 plot_from_logs.py --log transmission.log
python3 plot_distribution_from_logs.py --log feeder_1.log
```

Important outputs to show:

- `transmission.log`: applied fault, interface voltage, feeder `P/Q` feedback
- `feeder_1.log`: Motor D state counts, `MotorP`, `MotorQ`, FIDVR stage
- `total_pq_and_bus14_voltage_vs_time.png`: transmission-side voltage and load
- `feeder_1_distribution_bus_voltage_vs_time.png`: distribution bus recovery
- `feeder_1_fidvr_trajectory.png`: Motor D and control-stage trajectory


