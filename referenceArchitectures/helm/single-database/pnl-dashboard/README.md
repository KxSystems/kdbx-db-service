# P&L Dashboard Helm Chart

This optional chart adds a KX Dashboard to an existing single-database DB Service deployment. It reads the `pnlstate` table produced by the streaming P&L workflow.

## Requirements

- The [single-database architecture](../) is running.
- The streaming P&L workflow has created and populated `pnlstate`.
- The namespace contains the DB Service image-pull and license secrets.
- Your registry account can pull the KX Dashboard images configured in `values.yaml`.

## Configuration

| Value | Purpose |
|---|---|
| `dbService.release` | Identifies the DB Service release that the Dashboard reads from. |
| `license` | Selects the Kubernetes secret and license filename used by the q shim. |
| `persistence` | Configures storage for Dashboard connections and definitions. |
| `shim` | Configures the q shim that exposes `pnlstate` to the Dashboard. |
| `webImage` and `gatewayImage` | Select the Dashboard container images. |

Follow the parent runbook's [optional Dashboard instructions](../README.md#optional-view-pl-in-kx-dashboard) to install, verify, and remove this chart.
