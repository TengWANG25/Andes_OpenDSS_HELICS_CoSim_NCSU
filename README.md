Andes_OpenDSS_HELICS_CoSim_NCSU

Transmission-distribution co-simulation with ANDES, OpenDSS, and HELICS.

Preliminary: The OpenDSS, OpenDSS python interface, ANDES, HELICS should be installed with LINUX environment. The necessary python packages are imported and should be installed as well.

To run the co-simulation, use `./run.sh` in terminal.


The default `run.sh` configuration is a small case baseline:

- Transmission case: `ieee14_fault.xlsx`
- Distribution case: `13Bus/IEEE13Nodeckt.dss`
- Transmission interface bus: `2`
- Distribution monitor bus: `650`
- Shared co-simulation base: `100 MVA`
- Disturbance: disabled by default for the baseline handshake run
- Embedded workbook events: disabled by default for the baseline run

To plot the transmission-side bus voltage after a run:

```bash
python3 plot_from_logs.py --log transmission.log
```

`plot_from_logs.py` now prefers the structured file `transmission_timeseries.csv`
when it is present beside `transmission.log`.

To plot the distribution-side bus voltage from a feeder log:

```bash
python3 plot_distribution_from_logs.py --log feeder_1.log
```

Both plotters now also export FIDVR alert summaries using the three
FNET indices:

- `Alert.1`: voltage dip `>= 0.20 pu` within `3` cycles
- `Alert.2`: undervoltage duration `>= 5 s`
- `Alert.3`: overvoltage duration `>= 1 s` within `120 s` after `Alert.2`

The alert CSVs are written beside the logs as:

- `bus<interface_bus>_fidvr_alerts.csv`
- `feeder_<n>_fidvr_alerts.csv`

`Distribution.py` logs the OpenDSS bus `650` by default for the IEEE13 feeder. If you want a different distribution side bus, set `DIST_VOLTAGE_BUS` before running the co-simulation, for example:

```bash
DIST_VOLTAGE_BUS=632 ./run.sh
```

Key configuration knobs for the small-case setup are:

```bash
TX_CASE_XLSX=ieee14_fault.xlsx
DIST_MASTER_DSS=13Bus/IEEE13Nodeckt.dss
TX_INTERFACE_BUS=2
DIST_VOLTAGE_BUS=650
COSIM_BASE_MVA=100.0
TX_ENABLE_DISTURBANCE=0
TX_KEEP_BUILTIN_EVENTS=0
SIM_FINE_DT=0.005
SIM_COARSE_DT=0.03
TX_TDS_STEP=0.03
```

If you want to turn the transmission disturbance back on for later studies:

```bash
TX_ENABLE_DISTURBANCE=1 ./run.sh
```

The fault driven FIDVR workflowuses a bus Fault as the primary event.
The older primary line-trip/reclose compatibility mode has been removed from the
active run path; use `TX_FAULT_*` for the initiating event and `TX_POSTFAULT_*`
only for optional topology stress after the fault clears.
The default stable fault settings are:

```bash
TX_FAULT_TIME=1.0
TX_FAULT_DURATION=0.08
TX_FAULT_RF=0.0
TX_FAULT_XF=0.3
SIM_FINE_DT=0.005
SIM_COARSE_DT=0.02
TX_TDS_STEP=0.03
```

Optional post-fault topology stress can be added after the fault clears with:

```bash
TX_POSTFAULT_LINE=Line_13
# or
TX_POSTFAULT_LINES=Line_4,Line_7
TX_POSTFAULT_TRIP_DELAY=0.01
```

When `FIDVR_ENABLE=1`, the feeder uses one FIDVR motor path: the WECC/LD1PAC
Motor D model. Each converted compressor
group is represented as one continuously controlled OpenDSS
`Load.weccmd_*` terminal injection. The original OpenDSS load is reduced by the
Motor D fraction, and the Motor D injection remains the same network element for
running, stalling, contactor dropout, undervoltage relay trip, thermal trip, and
restart behaviour.

`run.sh` supports these FIDVR presets:

- `FIDVR_PROFILE=scaled`: stronger scaled feeder demonstrator
  (`FEEDER_COUNT=1`, `DIST_LOAD_SCALE=8.0`, monitor bus `632`,
  `FIDVR_MOTOR_SHARE=0.45`).
- `FIDVR_PROFILE=fault_only`: cleaner calibration case without feeder controls
  (`FEEDER_COUNT=2`, `DIST_LOAD_SCALE=1.5`, monitor bus `675`,
  `FIDVR_MOTOR_SHARE=0.50`, `TX_FAULT_DURATION=0.10`, `TX_FAULT_XF=0.28`).
- `FIDVR_PROFILE=weak_bus14`: exploratory weak interface case at transmission
  bus `14` with one IEEE13 feeder, monitor bus `675`, and
  `FIDVR_MOTOR_SHARE=0.80`.
- `FIDVR_PROFILE=alerts`: stronger alert calibration setup for
  `Alert.1/2/3`.
- `FIDVR_PROFILE=second_half`: baseline for capacitor/regulator interaction
  with bus `14`, monitor bus `675`, `FIDVR_MOTOR_SHARE=1.00`, and feeder
  regulator/capacitor controls enabled.
- `FIDVR_PROFILE=second_half_fast_controls`: same case with faster regulator
  controls for sensitivity testing.
- `FIDVR_PROFILE=second_half_capscale`: same case with the existing capacitor
  kvar scaled by `FIDVR_CAPACITOR_KVAR_SCALE=2.0` unless overridden.

Useful runs:

```bash
FIDVR_ENABLE=1 FIDVR_PROFILE=weak_bus14 ./run.sh
python3 plot_from_logs.py --log transmission.log
python3 plot_distribution_from_logs.py --log feeder_1.log

FIDVR_ENABLE=1 FIDVR_PROFILE=second_half ./run.sh
python3 plot_from_logs.py --log transmission.log
python3 plot_distribution_from_logs.py --log feeder_1.log
```

`FIDVR_MOTOR_SHARE` is the Motor D composition fraction. To increase the FIDVR
forcing before tuning internal Motor D parameters, increase this fraction or the
distribution load scale. Splitting the same Motor D MVA into more OpenDSS load
objects only increases bookkeeping granularity; it does not make the response
stronger unless total Motor D kW/kVA increases.

The active Motor D defaults are traceable to the WECC Air Conditioner Motor
Model test report `ld1pac` settings and stalling-parameter section:

```text
CompPF=0.97, Vstall=0.70, Rstall=0.114, Xstall=0.124, Tstall=0.033,
Frst=0.20, Vrst=0.90, Trst=0.40,
Vc1off=0.45, Vc2off=0.35, Vc1on=0.50, Vc2on=0.40,
Tth=10.0, Th1t=1.30, Th2t=4.30,
Fuvr=0.00, UVtr1=0.80, Ttr1=0.20, UVtr2=0.90, Ttr2=5.00
```

Note: the report is internally inconsistent for `Rstall`/`Xstall`. Appendix 1
and Appendix 3 list `Rstall=0.124, Xstall=0.114`, while Section 9 states
`rSTALL=0.114, xSTALL=0.124`. The current code follows the Section 9 order
because that section discusses the stalling parameter validation cases. See
`WECC_MOTOR_D_TRACEABILITY.md` for the map.

Primary references:

- WECC, "Air Conditioner Motor Model Test Report", Appendix 3, `ld1pac`
  defaults: https://www.wecc.org/sites/default/files/documents/meeting/2024/WECC%20Air%20Conditioner%20Motor%20Model%20Test%20Report--%20Final.pdf
- WECC, "Composite Load Model Specification", Motor D section:
  https://www.wecc.org/sites/default/files/documents/meeting/2024/WECC%20Comp%20Load%20Model%20Specification_final.pdf
- PowerWorld LD1PAC model description and equations:
  https://www.powerworld.com/WebHelp/Content/TransientModels_PDF/Load/Load_Characteristic/Load%20Characteristic%20LD1PAC.pdf

### Thermal load restoration extension

The strict WECC Motor D model has a short restart path controlled by
`Frst/Vrst/Trst`: only the restartable fraction returns after the stalled motor
terminal voltage exceeds `Vrst` for `Trst`. The longer "A/C load coming back"
part of the full FIDVR narrative is modeled separately and is off by default.
Enable it only when the study needs post-thermal-reset customer load return:

```bash
TARGET_TIME=360 FIDVR_ENABLE=1 FIDVR_PROFILE=second_half \
FIDVR_ENABLE_THERMAL_LOAD_RESTORATION=1 \
FIDVR_THERMAL_RESTORE_MIN_DELAY_S=180 \
FIDVR_THERMAL_RESTORE_MAX_DELAY_S=300 \
FIDVR_THERMAL_RESTORE_RAMP_S=30 \
FIDVR_THERMAL_RESTORE_VOLTAGE_PU=0.90 \
FIDVR_THERMAL_RESTORE_FRACTION=1.0 ./run.sh
```

These parameters mean:

- `FIDVR_ENABLE_THERMAL_LOAD_RESTORATION`: turns on the long-delay restoration
  extension while keeping the same `Load.weccmd_*` Motor D network elements.
- `FIDVR_THERMAL_RESTORE_MIN_DELAY_S` and
  `FIDVR_THERMAL_RESTORE_MAX_DELAY_S`: deterministic per-motor delay window
  after thermal trip. The default `180-300 s` follows PNNL/SCE guidance that
  single-phase compressor motors typically may not restart for about three
  minutes or more, with some cases described as three to five minutes.
- `FIDVR_THERMAL_RESTORE_RAMP_S`: aggregate diversity ramp for the returning
  compressor load once the individual delay has elapsed.
- `FIDVR_THERMAL_RESTORE_VOLTAGE_PU`: minimum voltage required to begin the
  restoration ramp; default `0.90 pu` aligns with the WECC Motor D `Vrst`
  default.
- `FIDVR_THERMAL_RESTORE_DROPOUT_VOLTAGE_PU`: voltage below which the restored
  fraction is reset; default `0.70 pu` aligns with the WECC Motor D `Vstall`
  default.
- `FIDVR_THERMAL_RESTORE_FRACTION`: fraction of the thermally tripped Motor D
  load allowed to return in the study window.

When enabled, feeder logs include `ThermalRestore=<fraction>` in addition to
`Restore=<fraction>`. `Restore` is the total connected/running Motor D fraction;
`ThermalRestore` is only the long-delay post-thermal-reset contribution. The
plotter includes both traces in the staged FIDVR trajectory plot.

Reference context:

- WECC Composite Load Model Specification: Motor D transitions from stall to
  run when the restartable fraction sees voltage above `Vrst` for `Trst`, while
  the non-restartable portion remains stalled for the simulation once stalled.
- PNNL, "ARRA Interconnection Planning - Load Modeling Activities"
  (`PNNL-24468`): single-phase compressor motors stall quickly around
  `60-70%` voltage and typically may not restart after tripping until roughly
  three minutes or more:
  https://www.pnnl.gov/main/publications/external/technical_reports/PNNL-24468.pdf
- PNNL, "Load Modeling Transmission Research" (`PNNL-24425`): after capacitors
  trip off and load-tripped air conditioners reset, voltage can drop below
  nominal again in the late FIDVR sequence:
  https://www.pnnl.gov/main/publications/external/technical_reports/PNNL-24425.pdf

### Shunt capacitor and regulator controls

The second-half FIDVR experiment uses the physical IEEE13 feeder devices already
in `13Bus/IEEE13Nodeckt.dss`; it does not create additional artificial
capacitors:

- `Capacitor.Cap1`: bus `675`, three-phase, `600 kvar`, `4.16 kV`
- `Capacitor.Cap2`: bus `611.3`, single-phase, `100 kvar`, `2.4 kV`
- `Reg1`-`Reg3`: single-phase regulators with the IEEE13 `regcontrol`
  settings and initial taps from the feeder file

For co-simulation, `Distribution.py` keeps OpenDSS native `controlmode=off` and
applies an external delayed controller after the transmission fault clears. This
makes each switching/tap action explicit in the feeder log while keeping the
network elements physical OpenDSS capacitors and transformers.

Capacitor controls are enabled with `FIDVR_ENABLE_CAP_CONTROL=1`. The default
research starting point is an overvoltage trip at `1.03 pu` after `2.0 s` and a
low-voltage reclose at `0.97 pu` after `10.0 s`; set
`FIDVR_CAPACITOR_LOCKOUT_AFTER_OPEN=1` when you want to reproduce the common
FIDVR narrative where capacitors trip off on overvoltage and stay off for the
study window. These thresholds are scenario assumptions, not universal validated
device settings, so sweeps should report them explicitly.
The feeder log reports `Caps=on`, `Caps=partial`, or `Caps=off` plus
`CapFrac` and per-bank state/voltage tokens such as `Cap2=off Cap2Lock=1`.
If `FIDVR_CAPACITOR_KVAR_SCALE` is greater than `1.0`, the run is an explicit
aggregation sensitivity study: the same named IEEE13 capacitor banks are edited
to larger kvar values and the applied scale is printed in the feeder log.

```bash
FIDVR_ENABLE=1 FIDVR_PROFILE=weak_bus14 \
FIDVR_MOTOR_SHARE=1.00 FIDVR_ENABLE_CAP_CONTROL=1 \
FIDVR_CAPACITOR_OFF_VOLTAGE_PU=1.03 FIDVR_CAPACITOR_OFF_DELAY_S=2.0 \
FIDVR_CAPACITOR_ON_VOLTAGE_PU=0.97 FIDVR_CAPACITOR_ON_DELAY_S=10.0 \
FIDVR_CAPACITOR_LOCKOUT_AFTER_OPEN=1 ./run.sh
```

Regulator controls are enabled separately with `FIDVR_ENABLE_REG_CONTROL=1`.
The external regulator controller uses the same physical transformer taps, with
configurable voltage band and delay:

```bash
FIDVR_ENABLE_REG_CONTROL=1 FIDVR_REGULATOR_MONITOR_BUS=675 \
FIDVR_REGULATOR_LOW_VOLTAGE_PU=0.99 FIDVR_REGULATOR_HIGH_VOLTAGE_PU=1.03 \
FIDVR_REGULATOR_DELAY_S=15.0 FIDVR_REGULATOR_TAP_DELAY_S=2.0 ./run.sh
```

Control references:

- IEEE PES Distribution System Analysis Subcommittee, radial distribution test
  feeders, IEEE13 capacitor/regulator data:
  https://ewh.ieee.org/soc/pes/dsacom/testfeeders/testfeeders.pdf
- EPRI OpenDSS `RegControl` documentation:
  https://opendss.epri.com/RegControl.html
- EPRI OpenDSS capacitor-control technical note:
  https://opendss.epri.com/TechNoteOpenDSSCapControlPhasing.html
- EPRI OpenDSS control queue interface:
  https://opendss.epri.com/OpenDSSCtrlQueueInterface.html
- NERC transient voltage response white paper for the delayed recovery,
  post-recovery overvoltage, and switching-action context:
  https://www.nerc.com/comm/RSTC_Reliability_Guidelines/Whitepaper_on_Transient_Voltage_Response_Criteria_12_06_2022.pdf

If you want to sweep the forcing manually, the main knobs are:

```bash
FIDVR_ENABLE=1 FIDVR_PROFILE=scaled FIDVR_MOTOR_SHARE=0.60 ./run.sh
FIDVR_ENABLE=1 FIDVR_PROFILE=scaled DIST_LOAD_SCALE=9.0 ./run.sh
FIDVR_ENABLE=1 FIDVR_PROFILE=scaled TX_POSTFAULT_LINE=Line_13 ./run.sh
```

For the current IEEE14-IEEE13 fault-driven FIDVR test, a useful starting point is:

```bash
FIDVR_ENABLE=1 TARGET_TIME=3.0 ./run.sh
python3 plot_from_logs.py --log transmission.log --bus 4
python3 plot_distribution_from_logs.py --log feeder_1.log
```

The feeder CSV exported by `plot_distribution_from_logs.py` now includes:

- source magnitude/angle from transmission
- monitored feeder bus `Vavg` and `Vpos`
- FIDVR stage and motor `P/Q` scaling
- motor state counts (`running/stalled/tripped/restoring`)
- regulator tap average and per-regulator taps
- capacitor fraction

## Docker setup

A containerized environment is provided via `Dockerfile` so the project can run with all required tools (HELICS broker/apps, Python, ANDES, OpenDSSDirect, pandas).

Build image:

```bash
docker build -t andes-opendss-helics-cosim .
```

Run co-simulation:

```bash
docker run --rm -it andes-opendss-helics-cosim
```

To mount your local working tree for iterative development:

```bash
docker run --rm -it \
  -v "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU \
  andes-opendss-helics-cosim
```


## Apptainer setup

Build the image:

```bash
apptainer build andes-opendss-helics-cosim.sif Apptainer.def
```

Run the co-simulation:
```bash
apptainer run --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif
```

Run with an explicit command:

```bash
apptainer exec --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif bash /workspace/Andes_OpenDSS_HELICS_CoSim_NCSU/run.sh
```

Open an interactive shell:

```bash
apptainer shell --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif
```

Inside shell:

```bash
cd /workspace/Andes_OpenDSS_HELICS_CoSim_NCSU
./run.sh
```

> Note: `apptainer build` typically needs root (`sudo`)
