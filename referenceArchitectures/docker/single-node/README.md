# Single-Node DB Service Reference Architecture

## Overview

This reference architecture deploys one DB Service database on a single machine using Docker Compose. The deployment includes the database components, Reliable Transport (RT) for streaming data, and a gateway for table, import, and query APIs.

Deploy DB Service once, then choose a workflow. You can import and query a small inline dataset through REST, or run the streaming profit-and-loss (P&L) workflow using Python or q. The P&L workflow also includes an optional KX Dashboard extension.

## Contents

- [Architecture](#architecture)
- [Before you begin](#before-you-begin)
- [Deploy DB Service](#deploy-db-service)
- [Choose a workflow](#choose-a-workflow)
- [Workflow A: Import inline data using REST](#workflow-a-import-inline-data-using-rest)
- [Workflow B: Stream data and calculate P&L](#workflow-b-stream-data-and-calculate-pl)
  - [Step 1: Configure the workflow](#step-1-configure-the-workflow)
  - [Step 2: Create the streaming tables](#step-2-create-the-streaming-tables)
  - [Step 3: Build and start the streaming workloads](#step-3-build-and-start-the-streaming-workloads)
  - [Step 4: Verify prices, trades, and P&L](#step-4-verify-prices-trades-and-pl)
  - [Optional: View P&L in KX Dashboard](#optional-view-pl-in-kx-dashboard)
- [Manage the deployment](#manage-the-deployment)
- [Related resources](#related-resources)

## Architecture

The root [`docker-compose.yaml`](../../../docker-compose.yaml) deploys the following components on the `kx-db` Docker network:

| Component | Compose services | Purpose |
|---|---|---|
| RT | `kx-db-rt` | Receives and distributes streaming data. |
| Database | `kx-db-sm`, `kx-db-da` | Ingests, persists, and serves data. |
| Gateway | `kx-db-rc`, `kx-db-agg`, `kx-db-gw` | Routes table, import, and query requests. |

## Before you begin

Complete the prerequisites in the root [DB Service quickstart](../../../README.md#prerequisites). You need Docker with Compose, registry access for the required KX images, a valid KDB-X license in the root `.env`, `curl`, `jq`, and `python3`.

Run every command in this guide from the repository root, which contains `docker-compose.yaml`.

## Deploy DB Service

For a new checkout, initialize the data directories before starting DB Service:

```bash
./init-db.sh
docker compose up -d

# Confirm that the containers are running.
docker compose ps

# Confirm that the gateway is available.
export GW_URL=http://localhost:8080
curl -s "$GW_URL/api/v0/tables" | jq
```

## Choose a workflow

After deploying DB Service, choose the workflow that matches what you want to try. Each workflow includes its own table setup, ingestion, and verification; you do not need to run another workflow first.

| Workflow | Choose this to... |
|---|---|
| [Workflow A: Import inline data using REST](#workflow-a-import-inline-data-using-rest) | Create a table, import three JSON rows, and query a small, fixed dataset through the REST API. |
| [Workflow B: Stream data and calculate P&L](#workflow-b-stream-data-and-calculate-pl) | Run the price, trade, and P&L workloads in Python or q, then query the results. |

## Workflow A: Import inline data using REST

This workflow creates the `fxquote` table, imports three JSON rows, and queries the results through the REST API.

**Prerequisite:** Complete [Deploy DB Service](#deploy-db-service).

Set the gateway URL if you are continuing in a new terminal:

```bash
export GW_URL=http://localhost:8080
```

### Step 1: Create the `fxquote` table

```bash
curl -s -X POST "$GW_URL/api/v0/tables/fxquote" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "partitioned",
    "prtnCol": "ts",
    "columns": [
      {"name": "trddate", "type": "date"},
      {"name": "ts", "type": "timestamp"},
      {"name": "sym", "type": "symbol"},
      {"name": "bid", "type": "float"},
      {"name": "ask", "type": "float"}
    ]
  }' | jq
```

### Step 2: Import three rows

The import request returns a job ID. Use it to check the import status, and wait for `completed` before running the query.

```bash
IMPORT_JOB_ID=$(curl -s -X POST "$GW_URL/api/v0/imports/data" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "table": "fxquote",
    "data": [
      ["2026-01-21", "2026-01-21T10:00:00.000", "EURUSD", 1.16035, 1.16085],
      ["2026-01-21", "2026-01-21T10:00:01.000", "GBPUSD", 1.34040, 1.34120],
      ["2026-01-21", "2026-01-21T10:00:02.000", "USDJPY", 157.975, 158.025]
    ],
    "columnNames": ["trddate", "ts", "sym", "bid", "ask"]
  }' | jq -r '.jobId')

# Repeat until status is completed.
curl -s "$GW_URL/api/v0/imports/$IMPORT_JOB_ID" | jq
```

### Step 3: Query the imported rows

```bash
curl -s -X POST "$GW_URL/api/v0/query/simple" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "table": "fxquote",
    "limit": 5
  }' | jq
```

The query should return the three imported rows.

## Workflow B: Stream data and calculate P&L

This workflow runs three workloads that generate prices, create trades, and calculate running P&L. Choose either Python or q for all three workloads.

**Prerequisite:** Complete [Deploy DB Service](#deploy-db-service).

![DB Service streaming P&L workflow](../../img/db-service-streaming-pnl-flow.png)

| Workload | Purpose | Output table |
|---|---|---|
| Market-data simulator | Generates live prices. | `mdquote` |
| OMS simulator | Creates trades using current prices. | `omstrade` |
| P&L RTE | Maintains positions and calculates running P&L. | `pnlstate` |

### Step 1: Configure the workflow

Set the gateway URL if you are continuing in a new terminal.

```bash
export GW_URL=http://localhost:8080
```

#### Select an implementation

Choose either Python or q. Each option implements all three workloads: market data, OMS, and P&L. Do not run both implementations at the same time.

<details>
<summary>Use Python</summary>

```bash
export STREAMING_IMPLEMENTATION=python
export STREAMING_COMPOSE=referenceArchitectures/docker/single-node/compose/docker-compose.streaming-pnl-python.yaml
```

The Python implementation builds one image for each workload.

</details>

<details>
<summary>Use q</summary>

```bash
export STREAMING_IMPLEMENTATION=q
export STREAMING_COMPOSE=referenceArchitectures/docker/single-node/compose/docker-compose.streaming-pnl-q.yaml
```

The q implementation builds one shared image containing the q runtime and all three q workloads. It also requires the RT q client package (`rt.qpk`). By default, the build downloads and unpacks `rt.qpk` version `1.18.1` from the KX portal; set `RT_QPK_DIR` to use an existing unpacked copy.

</details>

### Step 2: Create the streaming tables

The setup script is idempotent. Existing `mdquote`, `omstrade`, or `pnlstate` tables are left unchanged.

```bash
referenceArchitectures/workflows/streaming-pnl/setup-tables.sh

# Confirm that all three tables exist.
curl -s "$GW_URL/api/v0/tables" | jq
```

### Step 3: Build and start the streaming workloads

```bash
# Create separate writable RT replay-log directories for each workload.
mkdir -p data/rt-client-md data/rt-client-oms data/rt-client-pnl
chmod 777 data/rt-client-md data/rt-client-oms data/rt-client-pnl

# Build the selected implementation from the shared workflow source.
IMAGE_TAG=local \
STREAMING_IMPLEMENTATION="$STREAMING_IMPLEMENTATION" \
referenceArchitectures/workflows/streaming-pnl/build-images.sh docker

# Start the selected streaming workloads alongside the existing DB Service deployment.
docker compose \
  -f docker-compose.yaml \
  -f "$STREAMING_COMPOSE" \
  up -d

# Confirm that all three workloads are running and inspect their output.
docker compose \
  -f docker-compose.yaml \
  -f "$STREAMING_COMPOSE" \
  ps sim-marketdata sim-oms sim-pnl

docker compose \
  -f docker-compose.yaml \
  -f "$STREAMING_COMPOSE" \
  logs --tail=20 sim-marketdata sim-oms sim-pnl
```

### Step 4: Verify prices, trades, and P&L

Allow several seconds for the first events to flow through RT, then query each table:

```bash
sleep 10

# Generated prices.
curl -s -X POST "$GW_URL/api/v0/query/simple" \
  -H "Content-Type: application/json" \
  -d '{"table":"mdquote","limit":5}' | jq

# Trades created from current prices.
curl -s -X POST "$GW_URL/api/v0/query/simple" \
  -H "Content-Type: application/json" \
  -d '{"table":"omstrade","limit":5}' | jq

# Running P&L calculated from prices and trades.
curl -s -X POST "$GW_URL/api/v0/query/simple" \
  -H "Content-Type: application/json" \
  -d '{"table":"pnlstate","limit":5}' | jq
```

### Optional: View P&L in KX Dashboard

The Dashboard extension adds a web interface, a Dashboard gateway, and a q shim that exposes `pnlstate` to the Dashboard. Complete the streaming workflow, confirm that `pnlstate` contains data, and confirm that your registry account can pull `dash-web` and `kxi-gui-gateway` before continuing.

```bash
# Start the Dashboard services alongside DB Service and the selected workflow.
# A one-shot service writes the existing DB Service license into a private,
# Docker-managed volume for the Dashboard gateway and q shim.
docker compose \
  -f docker-compose.yaml \
  -f "$STREAMING_COMPOSE" \
  -f referenceArchitectures/docker/single-node/compose/docker-compose.pnl-dashboard.yaml \
  up -d

# Install the P&L connection and dashboard definition.
referenceArchitectures/docker/single-node/utils/install-dashboard.sh

# Verify the three Dashboard services.
docker compose \
  -f docker-compose.yaml \
  -f "$STREAMING_COMPOSE" \
  -f referenceArchitectures/docker/single-node/compose/docker-compose.pnl-dashboard.yaml \
  ps pnl-dashboard pnl-dashboard-gw pnl-dashboard-shim
```

If Docker is running locally, open `http://localhost:9090` and select **P&L by instrument and book**.

## Manage the deployment

Each code block lists alternative operations; run only the command for the action you want to perform.

### Streaming P&L workflow

If you are continuing in a new terminal, repeat [Select an implementation](#select-an-implementation) to set `STREAMING_COMPOSE`.

```bash
# Stop the streaming workloads.
docker compose -f docker-compose.yaml -f "$STREAMING_COMPOSE" stop sim-marketdata sim-oms sim-pnl

# Start the streaming workloads.
docker compose -f docker-compose.yaml -f "$STREAMING_COMPOSE" start sim-marketdata sim-oms sim-pnl

# Stop and remove the streaming workload containers (DB Service data and streaming tables are retained).
docker compose -f docker-compose.yaml -f "$STREAMING_COMPOSE" rm --stop --force sim-marketdata sim-oms sim-pnl

# q only: remove the completed licence-initialization container.
docker compose -f docker-compose.yaml -f "$STREAMING_COMPOSE" rm --force streaming-pnl-q-license-init
```

### P&L Dashboard

```bash
# Stop the Dashboard services.
docker compose -f docker-compose.yaml -f referenceArchitectures/docker/single-node/compose/docker-compose.pnl-dashboard.yaml stop pnl-dashboard pnl-dashboard-gw pnl-dashboard-shim

# Start the Dashboard services.
docker compose -f docker-compose.yaml -f referenceArchitectures/docker/single-node/compose/docker-compose.pnl-dashboard.yaml start pnl-dashboard pnl-dashboard-gw pnl-dashboard-shim

# Stop and remove the Dashboard containers.
docker compose -f docker-compose.yaml -f referenceArchitectures/docker/single-node/compose/docker-compose.pnl-dashboard.yaml rm --stop --force pnl-dashboard-license-init pnl-dashboard pnl-dashboard-gw pnl-dashboard-shim
```

### Complete DB Service deployment

Manage the streaming workflow and Dashboard separately using the commands above. Remove them before resetting DB Service.

```bash
# Stop DB Service without deleting data.
docker compose stop

# Start DB Service.
docker compose start

# Remove DB Service and delete its persisted data.
./reset-db.sh
```

## Related resources

- [Streaming P&L workflow](../../workflows/streaming-pnl/) - shared Python and q workloads, image builds, table setup, and Dashboard assets.
- [Single-database Helm architecture](../../helm/single-database/README.md#workflow-b-stream-data-and-calculate-pl) - Kubernetes deployment of the same workflow.
