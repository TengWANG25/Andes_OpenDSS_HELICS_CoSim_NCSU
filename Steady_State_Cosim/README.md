# ANDES + OpenDSS + HELICS Steady-State Co-Simulation

IEEE118-8500 systems co-simulation with Andes and OpenDSS

Use Linux or WSL with Bash and `ss` (provided by `iproute2`), plus Python,
HELICS, ANDES, OpenDSSDirect.py, pandas, NumPy, matplotlib, and openpyxl.
See the [repository quick start](../README.md#quick-start) for environment setup.
The launcher uses `python3` from your active environment; override it with
`PYTHON=/absolute/path/to/python bash ./run.sh` if needed.

From the repository root:

```bash
cd Steady_State_Cosim
bash ./run.sh
```

This launcher starts ten feeder federates on broker port `23406`.
The shared feeder data is in `../8500Node/`. `Distribution.py` checks its
local folder, then the repository root, or accepts an explicit
`DIST_MASTER_DSS=/absolute/path/to/Master.dss`.

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

Build from the **repository root**, rather than this scenario folder, so shared
OpenDSS case folders are included:

```bash
docker build -f Steady_State_Cosim/Dockerfile -t andes-opendss-helics-cosim .
```

Run co-simulation:

```bash
docker run --rm -it andes-opendss-helics-cosim
```

To keep logs and plots on your computer, mount the entire repository root:

```bash
docker run --rm -it \
  -v "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU \
  andes-opendss-helics-cosim
```


## Apptainer setup

Build the image from the **repository root**:

```bash
apptainer build andes-opendss-helics-cosim.sif Steady_State_Cosim/Apptainer.def
```

Run the co-simulation:
```bash
apptainer run --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif
```

Run with an explicit command:

```bash
apptainer exec --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif bash /workspace/Andes_OpenDSS_HELICS_CoSim_NCSU/Steady_State_Cosim/run.sh
```

Open an interactive shell:

```bash
apptainer shell --bind "$PWD":/workspace/Andes_OpenDSS_HELICS_CoSim_NCSU andes-opendss-helics-cosim.sif
```

Inside shell:

```bash
cd /workspace/Andes_OpenDSS_HELICS_CoSim_NCSU/Steady_State_Cosim
bash ./run.sh
```

> Apptainer build permissions depend on the host configuration; use the method
> supported by your cluster or local installation.
