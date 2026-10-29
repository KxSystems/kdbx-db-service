# Streaming P&L Workflow

This directory contains reusable Python and q workloads that demonstrate how to calculate profit and loss (P&L) from live prices and trades. Deployment-specific configuration remains with each supported reference architecture. The workflow can run with either the single-node Docker Compose architecture or the single-database Kubernetes architecture deployed with Helm.

```text
Market-data simulator -- prices --> RT --> DB Service: mdquote
                                                  |
                                                  +-- latest prices --> OMS simulator

OMS simulator -- trades --> RT --> DB Service: omstrade

RT (mdquote + omstrade) --> P&L RTE --> RT --> DB Service: pnlstate
```

| Directory | Contents |
|---|---|
| `python/` | Python versions of the market-data simulator, OMS simulator, and P&L calculator, plus shared code for communicating with RT |
| `q/` | q versions of the market-data simulator, OMS simulator, and P&L RTE, plus shared code for communicating with RT |
| `dashboard/` | Files used by the optional P&L Dashboard for the Docker Compose and Helm deployments |

`setup-tables.sh` creates the three DB Service tables used by the workflow: `mdquote` for prices, `omstrade` for trades, and `pnlstate` for calculated P&L. The Python and q implementations use the same data flow, simulation settings, weighted-average-cost calculation, and table schemas.

`build-images.sh` builds the Python or q container images required by the workflow. The q image includes the q workloads and the [`rt.qpk`](https://code.kx.com/insights/1.18/microservices/rt/sdks/rt-qpk.html) package used to communicate with RT.

`q/pnl.q` is the q P&L real-time engine (RTE). It subscribes to `mdquote` and `omstrade`, maintains P&L state for each instrument and book, and publishes the calculated results to `pnlstate` through RT.

The [single-node Docker Compose runbook](../../docker/single-node/README.md#workflow-b-stream-data-and-calculate-pl) and [single-database Helm runbook](../../helm/single-database/README.md#workflow-b-stream-data-and-calculate-pl) explain how to deploy the workflow and its optional Dashboard extension. These workloads demonstrate the integration and are not intended as production market-data, order-management, or risk systems.
