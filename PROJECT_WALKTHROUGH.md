# Project Walkthrough

This file is intended as a compact guide for the 15-minute project video. It
explains what to show first, where the entry points are, and how data moves
through the co-simulation.

## Goal

This project studies fault-induced delayed voltage recovery (FIDVR) in a
transmission-distribution co-simulation. ANDES provides the transmission-side
dynamic system, OpenDSS provides the IEEE 13-node distribution feeder, and HELICS
exchanges the interface voltage and feeder power at each co-simulation time.

The current FIDVR path uses one physically interpretable distribution load
model: the WECC/LD1PAC Motor D representation implemented as controlled OpenDSS
load injections. The older surrogate and companion-load paths are not part of
the active workflow.

## Entry Points

- `./run.sh`: launches the HELICS broker, the transmission federate, and one or
  more feeder federates with a selected FIDVR profile.
- `Transmission.py`: ANDES federate. It applies the transmission fault, receives
  feeder `P/Q`, advances the dynamic simulation, and publishes the interface
  bus voltage.
- `Distribution.py`: OpenDSS feeder federate. It receives the transmission
  voltage, updates Motor D/capacitor/regulator states, solves the feeder, and
  publishes feeder `P/Q`.
- `plot_from_logs.py`: transmission-side plots and alert summaries.
- `plot_distribution_from_logs.py`: distribution-side voltage, FIDVR trajectory,
  and feeder alert summaries.
- `fidvr_alerts.py`, `fidvr_controls.py`, `fidvr_load_restoration.py`: small
  helper modules for alert detection, delayed shunt controls, and long-delay
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
   - thermal state follows the WECC stall-heating logic
   - optional long-delay thermal load restoration is applied only when enabled
5. The same `Load.weccmd_*` elements are edited to represent running, stalled,
   tripped, and restoring behavior. The network element does not change.
6. OpenDSS solves a snapshot and publishes total feeder `P/Q` back to ANDES.

The most important timing detail is that `FINE_DT` is the co-simulation/control
update step. It determines how often the feeder receives voltage and updates
Motor D timers. `TX_TDS_STEP` is the internal ANDES TDS step. For short Motor D
stall timers such as `Tstall=0.033 s`, `FINE_DT=0.003 s` is much safer than
`0.03 s`.

## Recommended Demonstration Run

For a compact FIDVR demonstration:

```bash
TARGET_TIME=60 FIDVR_ENABLE=1 FIDVR_PROFILE=weak_bus14 \
FIDVR_MOTOR_SHARE=1.0 FINE_DT=0.003 COARSE_DT=0.03 TX_TDS_STEP=0.003 ./run.sh
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

## Suggested Video Storyline

1. State the goal: reproduce and analyze FIDVR in a coupled transmission and
   distribution simulator.
2. Open `run.sh` and explain the chosen profile and key knobs:
   `FIDVR_PROFILE`, `FIDVR_MOTOR_SHARE`, `FINE_DT`, `COARSE_DT`, `TX_TDS_STEP`,
   and fault settings.
3. Open `Transmission.py` and show the ANDES fault and HELICS voltage
   publication loop.
4. Open `Distribution.py` and show the Motor D data model, the Motor D state
   update, and the OpenDSS solve/power publication loop.
5. Run or replay the plotting commands.
6. Explain the result: fault sag, Motor D stall/high reactive demand, delayed
   recovery, and optional capacitor/regulator/thermal-restoration extensions.
7. Close with limitations: current feeder is IEEE 13-node, Motor D parameters
   are WECC defaults, and calibration/sensitivity studies are still needed for
   a fully validated system-specific study.

## Package Contents To Share

Include source code, cases, tests, and a small set of representative outputs:

- `run.sh`, `broker.py`, `Transmission.py`, `Distribution.py`
- `fidvr_alerts.py`, `fidvr_controls.py`, `fidvr_load_restoration.py`
- `plot_from_logs.py`, `plot_distribution_from_logs.py`
- `tests/`
- `13Bus/`
- `ieee14_fault.xlsx`
- `README.md`, `PROJECT_WALKTHROUGH.md`, `FIDVR_REDESIGN_GUIDE.md`
- `WECC_MOTOR_D_TRACEABILITY.md`
- representative logs/plots from the final run

Avoid packaging generated caches such as `__pycache__/` and old exploratory
replay folders unless they are needed to support the final video.
