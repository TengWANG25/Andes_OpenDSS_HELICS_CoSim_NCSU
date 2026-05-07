# ANDES + OpenDSS + HELICS Co-Simulation

This folder runs a coupled IEEE 118-bus transmission model in ANDES with an
IEEE 8500-node OpenDSS feeder through HELICS. The default study applies a
balanced three-phase-to-ground fault at transmission bus 2, which is also the
transmission distribution interface bus.

## Requirements

Use a Linux environment with Python, HELICS, ANDES, OpenDSSDirect.py, pandas,
and NumPy installed. The scripts currently default to:

```bash
/home/teng/miniforge3/envs/cosim/bin/python
```

Override that path with `PYTHON=/path/to/python ./run.sh` if needed.

## Run

From this directory:

```bash
./run.sh
```

`run.sh` starts the HELICS broker, transmission federate, and feeder federate,
then writes:

- `broker.log`
- `transmission.log`
- `feeder_1.log`
- `transmission_timeseries.csv`

The launcher uses broker port `23407` by default and passes
`HELICS_BROKER_URL=tcp://127.0.0.1:23407` to the federates. HELICS opens the
adjacent ZMQ core port internally, so do not point federates directly at
`23408`.

## Default Study

The default bus-fault settings are defined in `Transmission.py`:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `TX_FAULT_BUS` | `2` | Faulted transmission bus |
| `TX_FAULT_TIME` | `1.0` | Fault application time in seconds |
| `TX_FAULT_DURATION` | `0.08` | Fault duration in seconds |
| `TX_FAULT_RF` | `0.0` | Fault resistance |
| `TX_FAULT_XF` | `0.05` | Fault reactance |

`TX_FAULT_XF` is intentionally nonzero because an exact `rf=0, xf=0` fault
causes a divide-by-zero in the ANDES `Fault` model for this case.

The co-simulation step defaults are:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `TARGET_TIME` | `10.0` | End time in seconds |
| `FINE_DT` | `0.03` | HELICS/OpenDSS co-simulation step before `COARSE_START` |
| `COARSE_DT` | `0.03` | HELICS/OpenDSS co-simulation step after `COARSE_START` |
| `COARSE_START` | `1.60` | Time to switch to `COARSE_DT` |
| `TX_TDS_STEP` | `0.033` | ANDES internal TDS step |

Examples:

```bash
TARGET_TIME=1.2 ./run.sh
TX_FAULT_XF=0.10 ./run.sh
TX_FAULT_TIME=2.0 TX_FAULT_DURATION=0.12 ./run.sh
FINE_DT=0.005 COARSE_DT=0.02 ./run.sh
```

## OpenDSS Case

The feeder uses `8500Node/Master.dss`. If that case is not inside this folder,
`run.sh` looks for `../8500Node/Master.dss`. Override the path explicitly with:

```bash
DIST_MASTER_DSS=/path/to/8500Node/Master.dss ./run.sh
```

`Distribution.py` monitors `regxfmr_hvmv_sub_lsb` by default. To monitor a
different OpenDSS bus:

```bash
DIST_VOLTAGE_BUS=_hvmv_sub_lsb ./run.sh
```

## Plotting

Transmission-side plots:

```bash
python3 plot_from_logs.py --log transmission.log
```

Distribution-side feeder plots:

```bash
python3 plot_distribution_from_logs.py --log feeder_1.log
```

`plot_from_logs.py` prefers `transmission_timeseries.csv` when it is present.
It can still read legacy detailed logs, and sparse transmission logs can be
combined with `feeder_*.log` files to reconstruct the interface time series.

## Containers

Build and run with Docker:

```bash
docker build -t andes-opendss-helics-cosim .
docker run --rm -it andes-opendss-helics-cosim
```

Mount the working tree for development:

```bash
docker run --rm -it \
  -v "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU \
  andes-opendss-helics-cosim
```

Build and run with Apptainer:

```bash
apptainer build andes-opendss-helics-cosim.sif Apptainer.def
apptainer run --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif
```

Open an Apptainer shell:

```bash
apptainer shell --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif
cd /workspace/Andes_OpenDSS_HELICS_CoSim_NCSU
./run.sh
```
