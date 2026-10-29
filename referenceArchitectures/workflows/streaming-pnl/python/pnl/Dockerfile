# Image for the P&L analytic (sim_pnl.py) ONLY  ->  sim-pnl:local
#
# Build context is the python parent so common/rt_common.py is
# reachable - see marketdata/Dockerfile for the full reasoning.
#
#   context: referenceArchitectures/workflows/streaming-pnl/python
#   dockerfile: pnl/Dockerfile
#
# Python 3.12 specifically: kxi-rtpy ships version-specific CPython wheels
# (cp312 at the time of writing).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kept at their original relative positions, because pip resolves a `-r` path
# relative to the file containing it.
COPY common/requirements.txt ./common/requirements.txt
COPY pnl/requirements.txt ./pnl/requirements.txt
RUN pip install --no-cache-dir -r pnl/requirements.txt

COPY common/rt_common.py ./
COPY pnl/sim_pnl.py ./

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "sim_pnl.py"]
