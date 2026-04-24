Andes_OpenDSS_HELICS_CoSim_NCSU

Transmission-distribution co-simulation with ANDES, OpenDSS, and HELICS.

Preliminary: The OpenDSS, OpenDSS python interface, ANDES, HELICS should be installed with LINUX environment. The necessary python packages are imported and should be installed as well.

To run the co-simulation, use `./run.sh` in terminal.

The default `run.sh` configuration is a small-case baseline:

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
when it is present beside `transmission.log`. Legacy detailed transmission logs
are still supported, and sparse transmission logs can still be plotted if the
matching `feeder_*.log` files are present so the interface time series can be
reconstructed.

To plot the distribution-side bus voltage from a feeder log:

```bash
python3 plot_distribution_from_logs.py --log feeder_1.log
```

Both plotters now also export FIDVR alert summaries using the three
FNET/GridEye-style indices:

- `Alert.1`: voltage dip `>= 0.20 pu` within `3` cycles
- `Alert.2`: undervoltage duration `>= 5 s`
- `Alert.3`: overvoltage duration `>= 1 s` within `120 s` after `Alert.2`

The alert CSVs are written beside the logs as:

- `bus<interface_bus>_fidvr_alerts.csv`
- `feeder_<n>_fidvr_alerts.csv`

For feeder buses that do not sit exactly at `1.0 pu` pre-fault, the alert logic
uses the pre-fault bus voltage as the local reference so the thresholds remain
meaningful on deeper distribution buses.

`Distribution.py` logs the OpenDSS bus `650` by default for the IEEE13 feeder. If you want a different distribution-side bus, set `DIST_VOLTAGE_BUS` before running the co-simulation, for example:

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
SIM_COARSE_DT=0.02
TX_TDS_STEP=0.001
```

If you want to turn the transmission disturbance back on for later studies:

```bash
TX_ENABLE_DISTURBANCE=1 ./run.sh
```

The fault-driven FIDVR workflow now uses a true ANDES `Fault` as the primary event.
The default stable fault settings are:

```bash
TX_FAULT_TIME=1.0
TX_FAULT_DURATION=0.08
TX_FAULT_RF=0.0
TX_FAULT_XF=0.3
SIM_FINE_DT=0.005
SIM_COARSE_DT=0.02
TX_TDS_STEP=0.001
```

Optional post-fault topology stress can be added after the fault clears with:

```bash
TX_POSTFAULT_LINE=Line_13
# or
TX_POSTFAULT_LINES=Line_4,Line_7
TX_POSTFAULT_TRIP_DELAY=0.01
```

When `FIDVR_ENABLE=1`, `run.sh` supports two FIDVR presets:

- `FIDVR_PROFILE=scaled` keeps the previously validated scaled-feeder case
  as the default FIDVR setup.
  - `FEEDER_COUNT=1`
  - `DIST_LOAD_SCALE=8.0`
  - `FIDVR_MOTOR_MODEL=surrogate`
  - `FIDVR_MOTOR_SHARE=0.45` unless you override it
  - default monitor bus `632`
  - fault at transmission bus `4`
  - feeder regulator and capacitor controls frozen by default
- `FIDVR_PROFILE=weak_bus14` switches to an exploratory no-scale topology
  stress case:
  - transmission interface bus `14`
  - `1` IEEE13 feeder
  - monitor bus `675`
  - fault at transmission bus `14`
  - `DIST_LOAD_SCALE=1.0`

Use the exploratory weak-interface profile like this:

```bash
FIDVR_ENABLE=1 FIDVR_PROFILE=weak_bus14 ./run.sh
python3 plot_from_logs.py --log transmission.log
python3 plot_distribution_from_logs.py --log feeder_1.log
```

The `weak_bus14` preset is useful for testing a transmission-driven deep sag
without scaling the stock IEEE13 feeder, but it does not yet reproduce the full
stalled-motor delayed-recovery trajectory by itself. The `scaled` preset remains
the working FIDVR demonstrator in this repository.

The current feeder motor path is an explicitly staged surrogate, not a native
OpenDSS dynamic induction-machine model. That is intentional: the repo now
labels it honestly and exports the motor state counts, regulator taps, and
control state so the delayed-recovery behavior can be validated as a surrogate.

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
- `dynamics_enabled`

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
