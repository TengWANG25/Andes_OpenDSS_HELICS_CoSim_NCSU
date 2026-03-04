Andes_OpenDSS_HELICS_CoSim_NCSU

IEEE118-8500 systems co-simulation with Andes and OpenDSS

Preliminary: The OpenDSS, OpenDSS python interface, ANDES, HELICS should be installed with LINUX environment. The necessary python packages are imported and should be installed as well.

To run the co-simulation, use `./run.sh` in ternimal.

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
