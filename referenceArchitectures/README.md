# DB Service Reference Architectures

These examples show how to deploy DB Service in several common configurations. Choose a deployment architecture, then follow its README from start to finish.

## Deployment architectures

| Architecture | Technology | Use it to |
|---|---|---|
| [Single node](docker/single-node/) | Docker Compose | Run one DB Service database on a single machine. |
| [Single-node hot/hot](docker/single-node-hot-hot/) | Docker Compose | Run two DB Service instances behind a load balancer. |
| [Single database](helm/single-database/) | Kubernetes and Helm | Run one DB Service database on Minikube or an existing Kubernetes cluster. |
| [Sharded databases](helm/sharded-databases/) | Kubernetes and Helm | Run multiple database shards behind a shared gateway. |

## Reusable workflows

Workflows add complete data-processing examples to supported deployments.

| Workflow | Purpose |
|---|---|
| [Streaming P&L](workflows/streaming-pnl/) | Generate prices and trades, calculate P&L using Python or q, and optionally display the results in KX Dashboard. |

The Kubernetes architectures use the reusable Helm charts under [`kxCharts`](kxCharts/).
