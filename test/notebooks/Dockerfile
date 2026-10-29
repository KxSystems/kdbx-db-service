ARG KDBX_IMAGE=dev-registry.kxi-dev.kx.com/kxi-kdbx-base:1.20.0
FROM ${KDBX_IMAGE} AS kdbx

FROM python:3.11-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq \
    && rm -rf /var/lib/apt/lists/*

COPY --from=kdbx /opt/kx /opt/kx

ENV QHOME=/opt/kx
ENV PATH=/opt/kx/l64:/opt/kx:${PATH}
ENV LD_LIBRARY_PATH=/opt/kx/l64
ENV VIRTUAL_ENV=/tmp/dbservice-notebook-venv
ENV PYTHON=/tmp/dbservice-notebook-venv/bin/python

WORKDIR /workspace
COPY notebooks/requirements.txt /workspace/notebooks/requirements.txt
COPY test/notebooks/requirements.txt /workspace/test/notebooks/requirements.txt
WORKDIR /workspace/test/notebooks
RUN python3 -m venv "$VIRTUAL_ENV" \
    && "$PYTHON" -m pip install --upgrade pip \
    && "$PYTHON" -m pip install -r requirements.txt

WORKDIR /workspace
COPY . /workspace
CMD ["sh", "test/notebooks/run_notebook_tests.sh"]
