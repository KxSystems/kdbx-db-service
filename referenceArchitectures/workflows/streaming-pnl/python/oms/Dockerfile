# Image for the OMS TRADE simulator only  ->  sim-oms:local
#
# Build context is the python parent - see marketdata/Dockerfile for why.
#
#   context: referenceArchitectures/workflows/streaming-pnl/python
#   dockerfile: oms/Dockerfile
#
# This image additionally carries the DB Service Python client, because
# sim_oms reads live quote prices back from the gateway to price its trades.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Both kept at their original relative positions, because pip resolves a
# `-r` path relative to the file containing it: oms/requirements.txt says
# `-r ../common/requirements.txt`, so it must sit one level below /app.
COPY common/requirements.txt ./common/requirements.txt
COPY oms/requirements.txt ./oms/requirements.txt
RUN pip install --no-cache-dir --pre -r oms/requirements.txt

COPY common/rt_common.py ./
COPY oms/sim_oms.py ./

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "sim_oms.py"]
