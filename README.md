Andes-OpenDSS-HELICS CoSimulation -- NCSU

Transmission-distribution co-simulation with ANDES, OpenDSS, and HELICS.

Preliminary: The OpenDSS, OpenDSS python interface, ANDES, HELICS should be installed with LINUX environment. The necessary python packages are imported and should be installed as well.

Please nevigate to each session 'Dynamic_Cosim', 'FIDVR', ANDES_118_power_flow_test' to explore differernt scenarios. 

Once down, to run the co-simulation, use `./run.sh` in terminal, please feel free to explore the options of using docker as you might encouter some package issues.


The default `run.sh` configuration is a small case baseline:

- Transmission case: `ieee14_fault.xlsx`
- Distribution case: `13Bus/IEEE13Nodeckt.dss`
- Transmission interface bus: `2`
- Distribution monitor bus: `650`
- Shared co-simulation base: `100 MVA`
- Defaulted Disturbance: disabled by default for the baseline handshake run

To plot the transmission side bus voltage after a run:

```bash
python3 plot_from_logs.py --log transmission.log
```

To plot the distribution side bus voltage from a feeder log:

```bash
python3 plot_distribution_from_logs.py --log feeder_1.log
```

`Distribution.py` logs the OpenDSS bus `650` by default for the IEEE13 feeder. If you want a different distribution side bus, set `DIST_VOLTAGE_BUS` before running the co-simulation, for example:

```bash
DIST_VOLTAGE_BUS=632 ./run.sh
```

Key configuration knobs for the small case setup are:

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

The fault driven FIDVR workflow uses a bus Fault as the primary event.
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
When `FIDVR_ENABLE=1`, the feeder uses one FIDVR motor path: the WECC/LD1PAC
Motor D model. Each converted compressor group is represented as one continuously controlled OpenDSS
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
  controls for sensitivity testing with faster time run.
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
distribution load scale.

The active Motor D defaults are traceable to the WECC Air Conditioner Motor
Model test report `ld1pac` settings and stalling parameter section:

```text
CompPF=0.97, Vstall=0.70, Rstall=0.114, Xstall=0.124, Tstall=0.033,
Frst=0.20, Vrst=0.90, Trst=0.40,
Vc1off=0.45, Vc2off=0.35, Vc1on=0.50, Vc2on=0.40,
Tth=10.0, Th1t=1.30, Th2t=4.30,
Fuvr=0.00, UVtr1=0.80, Ttr1=0.20, UVtr2=0.90, Ttr2=5.00
```

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
terminal voltage exceeds `Vrst` for `Trst`.

These parameters mean:

- `FIDVR_THERMAL_RESTORE_VOLTAGE_PU`: minimum voltage required to begin the
  restoration ramp; default `0.90 pu` aligns with the WECC Motor D `Vrst`
  default.
- `FIDVR_THERMAL_RESTORE_DROPOUT_VOLTAGE_PU`: voltage below which the restored
  fraction is reset; default `0.70 pu` aligns with the WECC Motor D `Vstall`
  default.
- `FIDVR_THERMAL_RESTORE_FRACTION`: fraction of the thermally tripped Motor D
  load allowed to return in the study window.



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
