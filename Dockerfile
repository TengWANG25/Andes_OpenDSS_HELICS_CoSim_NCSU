# syntax=docker/dockerfile:1.7

FROM mambaorg/micromamba:1.5.10
ARG MAMBA_DOCKERFILE_ACTIVATE=1

USER root

# Runtime tools used by scripts (bash + ss command in run.sh)
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash iproute2 tini \
    && rm -rf /var/lib/apt/lists/*

# Install Python from conda-forge.
# NOTE: the old `gmlc-helics` conda channel now returns 404.
RUN micromamba install -y -n base -c conda-forge \
      python=3.11 \
      pip \
    && micromamba clean --all --yes

RUN python -m pip install --no-cache-dir \
      helics \
      helics-apps \
      andes \
      opendssdirect.py \
      pandas

WORKDIR /workspace/Andes_OpenDSS_HELICS_CoSim_NCSU

# Copy the full repository into the image.
COPY . .

RUN chmod +x run.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/_entrypoint.sh"]
CMD ["bash", "./run.sh"]
