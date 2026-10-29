# Image for the MARKET DATA simulator only  ->  sim-marketdata:local
#
# Build context is the python parent, not this directory, so that
# common/rt_common.py is reachable: Docker tars the context and will not
# follow symlinks out of it, so a shared file must live inside it. Each feed
# still owns its own directory, Dockerfile and requirements.
#
#   context: referenceArchitectures/workflows/streaming-pnl/python
#   dockerfile: marketdata/Dockerfile
#
# Python 3.12 specifically: kxi-rtpy ships version-specific CPython wheels
# (cp312 at the time of writing) - a different minor version will fail to
# resolve a wheel from the KX package index.
FROM python:3.12-slim

# librt_helper.so (bundled in the kxi-rtpy wheel) links against libstdc++;
# ca-certificates is needed to reach the KX package index over HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first, for layer caching. Note: no DB Service client here.
# Both kept at their original relative positions, because pip resolves a
# `-r` path relative to the file containing it: marketdata/requirements.txt says
# `-r ../common/requirements.txt`, so it must sit one level below /app.
COPY common/requirements.txt ./common/requirements.txt
COPY marketdata/requirements.txt ./marketdata/requirements.txt
RUN pip install --no-cache-dir -r marketdata/requirements.txt

COPY common/rt_common.py ./
COPY marketdata/sim_marketdata.py ./

# Unbuffered so container logs show ticks live.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "sim_marketdata.py"]
