# Reference Workflows

Reference workflows demonstrate reusable DB Service scenarios across supported deployment environments.

| Workflow | Purpose | Deployment guides |
|---|---|---|
| [`streaming-pnl`](streaming-pnl/) | Generates prices and trades, calculates P&L using Python or q, and provides an optional P&L dashboard. | [Docker Compose](../docker/single-node/README.md#workflow-b-stream-data-and-calculate-pl), [Kubernetes with Helm](../helm/single-database/README.md#workflow-b-stream-data-and-calculate-pl) |

Shared workflow source and assets are maintained here. Deployment-specific configuration and instructions remain under `docker/` and `helm/`.
