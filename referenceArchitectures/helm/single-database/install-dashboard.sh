#!/usr/bin/env bash
#
# install-dashboard.sh - load the P&L dashboard into the KX Dashboards gateway
# running on the cluster.
#
# Optional add-on: run it once the pnl-dashboard chart is installed (see the
# single-database README). Idempotent - safe to re-run after editing the
# shared dashboard/kx/*.json files.
#
# The gateway seeds its data directory from its own image on first start, which
# brings in KX's 23 sample dashboards. This evicts them, copies in our dashboard
# and connection, and creates the `k8s_mode` sentinel: dash.q re-copies conf/*
# over the data directory unless connections/, dashboards/, url,
# globalProperties and k8s_mode all exist, and k8s_mode ships in none of them.
# The gateway caches its dashboard list at startup, so it is restarted last.
#
#   NAMESPACE      default db-service
#   RELEASE_NAME   the DB Service release the dashboard was installed for
#                  (dbService.release in the chart), default db-service-single.
#                  The dashboard's objects are named after it.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

NAMESPACE=${NAMESPACE:-db-service}
RELEASE_NAME=${RELEASE_NAME:-db-service-single}
GW_DEPLOY="${RELEASE_NAME}-pnl-dashboard-gw"
DATA=/opt/kx/app/data
KX=../../workflows/streaming-pnl/dashboard/kx

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for tool in kubectl jq; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required but was not found in PATH."
done

for file in "$KX/pnl-dashboard.json" "$KX/pnl.connection.json"; do
  [ -f "$file" ] || fail "$file was not found."
done

kubectl get deployment "$GW_DEPLOY" -n "$NAMESPACE" >/dev/null 2>&1 || \
  fail "Deployment $GW_DEPLOY was not found in namespace $NAMESPACE. Install the pnl-dashboard add-on first (single-database README, Workflow 2 dashboard extension)."

DASHBOARD_ID=$(jq -er .id "$KX/pnl-dashboard.json")

# Files are written through stdin rather than `kubectl cp`, which needs tar in
# the image, and they are created by the gateway's own user so no chown is needed.
gw() {
  kubectl exec -i -n "$NAMESPACE" "deployment/$GW_DEPLOY" -- "$@"
}

wait_ready() {
  local _
  for _ in $(seq 1 60); do
    if kubectl logs -n "$NAMESPACE" "deployment/$GW_DEPLOY" --tail=500 2>&1 \
        | grep -q "gateway script is running"; then
      return 0
    fi
    sleep 2
  done
  fail "The gateway did not report ready. Check: kubectl logs -n $NAMESPACE deployment/$GW_DEPLOY"
}

echo "==> waiting for $GW_DEPLOY"
kubectl rollout status "deployment/$GW_DEPLOY" -n "$NAMESPACE" --timeout=5m
wait_ready

echo "==> evicting sample dashboards and connections"
gw sh -c "cd $DATA \
  && find dashboards -name '*.json' ! -name '$DASHBOARD_ID.json' -delete \
  && find connections -name '*.json' ! -name 'pnl.json' -delete \
  && : > k8s_mode"
echo "    k8s_mode sentinel in place - samples will not return"

echo "==> installing the dashboard and connection"
gw sh -c "cat > $DATA/connections/pnl.json" < "$KX/pnl.connection.json"
echo "    connection: pnl -> pnl-dashboard-shim:5030"
gw sh -c "cat > $DATA/dashboards/$DASHBOARD_ID.json" < "$KX/pnl-dashboard.json"
echo "    dashboard : $DASHBOARD_ID.json"

echo "==> restarting $GW_DEPLOY"
kubectl rollout restart "deployment/$GW_DEPLOY" -n "$NAMESPACE" >/dev/null
kubectl rollout status "deployment/$GW_DEPLOY" -n "$NAMESPACE" --timeout=5m
wait_ready

COUNT=$(gw sh -c "ls -1 $DATA/dashboards/*.json | wc -l" | tr -d '[:space:]')
echo "    ready - dashboards present: $COUNT"

cat <<NEXT

==> done. Expose the UI and open it:

  kubectl port-forward svc/pnl-dashboard 9091:8080 -n $NAMESPACE &
  http://localhost:9091  ->  "P&L by instrument and book"

If the grid is empty, check the shim is serving:
  kubectl logs -n $NAMESPACE deployment/${RELEASE_NAME}-pnl-dashboard-shim --tail=5
and what the gateway made of the query:
  kubectl logs -n $NAMESPACE deployment/$GW_DEPLOY --tail=20
NEXT
