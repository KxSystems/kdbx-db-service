#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DASHBOARD_ASSETS=$(cd "$SCRIPT_DIR/../../../workflows/streaming-pnl/dashboard/kx" && pwd)
GATEWAY=${PNLDASH_GW_CONTAINER:-pnl-dashboard-gw}
DATA_DIR=/opt/kx/app/data

gateway_ready=false
for _ in $(seq 1 30); do
  if [ "$(docker inspect -f '{{.State.Running}}' "$GATEWAY" 2>/dev/null || true)" = "true" ] \
    && docker exec "$GATEWAY" test -d "$DATA_DIR/dashboards" \
    && docker exec "$GATEWAY" test -d "$DATA_DIR/connections"; then
    gateway_ready=true
    break
  fi
  sleep 2
done

if [ "$gateway_ready" != "true" ]; then
  echo "$GATEWAY did not become ready. Check: docker logs $GATEWAY" >&2
  exit 1
fi

dashboard_id=$(python3 -c "import json; print(json.load(open('$DASHBOARD_ASSETS/pnl-dashboard.json'))['id'])")

echo "Removing the bundled sample dashboard definitions"
docker exec "$GATEWAY" sh -c "
  cd '$DATA_DIR' || exit 1
  find dashboards -name '*.json' ! -name '$dashboard_id.json' -delete 2>/dev/null
  find connections -name '*.json' ! -name 'pnl.json' -delete 2>/dev/null
  : > k8s_mode
"

echo "Installing the P&L connection and dashboard"
docker cp "$DASHBOARD_ASSETS/pnl.connection.json" "$GATEWAY:$DATA_DIR/connections/pnl.json"
docker cp "$DASHBOARD_ASSETS/pnl-dashboard.json" "$GATEWAY:$DATA_DIR/dashboards/$dashboard_id.json"

gateway_uid=$(docker exec "$GATEWAY" id -u)
gateway_gid=$(docker exec "$GATEWAY" id -g)
docker exec -u 0 "$GATEWAY" sh -c "
  chown '$gateway_uid:$gateway_gid' '$DATA_DIR/connections/pnl.json' '$DATA_DIR/dashboards/$dashboard_id.json'
  chmod 664 '$DATA_DIR/connections/pnl.json' '$DATA_DIR/dashboards/$dashboard_id.json'
"

echo "Restarting $GATEWAY"
docker restart "$GATEWAY" >/dev/null

for _ in $(seq 1 30); do
  if docker logs --since 1m "$GATEWAY" 2>&1 | grep -q "gateway script is running"; then
    echo "Dashboard installed. Open http://localhost:${DS_PORT_PNLDASH:-9090}."
    exit 0
  fi
  sleep 2
done

echo "$GATEWAY did not report ready within 60 seconds. Check: docker logs $GATEWAY" >&2
exit 1
