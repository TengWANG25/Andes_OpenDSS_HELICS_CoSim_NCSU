Andes_OpenDSS_HELICS_CoSim_NCSU

IEEE118-8500 systems co-simulation with Andes and OpenDSS

Preliminary: The OpenDSS, OpenDSS python interface, ANDES, HELICS should be installed with LINUX environment. The necessary python packages are imported and should be installed as well.

To run the co-simulation, use `./run.sh` in ternimal.

To plot the transmission-side bus voltage after a run:

```bash
python3 plot_from_logs.py --log transmission.log
```

To plot the distribution-side bus voltage from a feeder log:

```bash
python3 plot_distribution_from_logs.py --log feeder_1.log
```

`Distribution.py` now logs the OpenDSS bus `regxfmr_hvmv_sub_lsb` by default. If you want a different distribution-side bus, set `DIST_VOLTAGE_BUS` before running the co-simulation, for example:

```bash
DIST_VOLTAGE_BUS=_hvmv_sub_lsb ./run.sh
```

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
