#!/usr/bin/env bash
#
# setup-tables.sh - create the tables the simulators publish into.
#
# Idempotent: a table that already exists is left alone. Safe to run before
# every simulator start.
#
#   mdquote  <- market-data workload   market data: bid/ask/last  (append-only)
#   omstrade <- OMS workload           trades: side/qty/price/book (append-only)
#   pnlstate <- P&L RTE                running P&L                 (KEYED/upsert)
#
# Both feed tables deliberately share the trddate/ts/sym column names so an
# as-of join needs no renaming:
#   aj[`sym`ts; select from omstrade ...; select from mdquote ...]
#
# NOTE: the trade price column is `lastpx`, not `last` - `last` is a q
# reserved word and the DB Service rejects it outright.
#
# pnlstate is the odd one out: it is a KEYED table, so each publish upserts
# the row for that key instead of appending. Three things that had to be
# established by testing, all of them easy to get wrong:
#   * `primaryKeys` columns MUST be listed first in `columns`, in the same
#     order as primaryKeys, or every import into the table fails.
#   * the type CANNOT be `partitioned`: that is rejected with
#     "primaryKeys MUST include prtnCol 'ts' if specified", and keying on a
#     tick timestamp would defeat upsert. `splayed_mem` keeps it in memory
#     and visible immediately; `basic`/`splayed` hold updates until
#     end-of-interval write-down, so they are not visible straight away.
#   * a keyed table is NOT readable through /api/v0/query/q (it errors with
#     "Unexpected error (<table>) encountered executing .query.q").
#     Use /api/v0/query/simple.

set -euo pipefail

if [ -n "${GW_URL:-}${DBS_GATEWAY:-}" ]; then
    GW="${GW_URL:-${DBS_GATEWAY:-}}"
else
    GW=http://localhost:8080
    echo "==> GW_URL is not set, so using the Docker Compose default $GW" >&2
    echo "    For Kubernetes, forward the gateway and export GW_URL first." >&2
fi
WAIT_SECS="${DBS_WAIT_SECS:-90}"
CREATE_TIMEOUT="${DBS_CREATE_TIMEOUT:-90}"

# --- wait for the gateway ----------------------------------------------------
# The gateway answers before the Coordinator and Storage Manager register, so
# a create issued too early can return 500. Poll until /tables responds, then
# retry transient 500 responses while registration completes.
echo "==> waiting for $GW (up to ${WAIT_SECS}s)"
deadline=$(( $(date +%s) + WAIT_SECS ))
until [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$GW/api/v0/tables" 2>/dev/null)" = "200" ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "error: gateway did not become ready within ${WAIT_SECS}s" >&2
        echo "  check the gateway port-forward and DB Service pod logs" >&2
        exit 1
    fi
    sleep 3
done
echo "    ready: $(curl -s -m 5 "$GW/api/v0/tables")"

existing="$(curl -s -m 10 "$GW/api/v0/tables")"

create() {
    local name="$1" body="$2"
    if printf '%s' "$existing" | grep -q "\"$name\""; then
        echo "    $name: already exists, skipping"
        return 0
    fi
    local out retry=0
    while [ "$retry" -lt 5 ]; do
        out="$(curl -s -m "$CREATE_TIMEOUT" -X POST "$GW/api/v0/tables/$name" \
            -H 'Accept: application/json' -H 'Content-Type: application/json' \
            -d "$body")"
        if printf '%s' "$out" | grep -q '"status":"completed"'; then
            if [ "$retry" -gt 0 ]; then
                echo "    $name: created after $retry retries"
            else
                echo "    $name: created"
            fi
            return 0
        fi
        if printf '%s' "$out" | grep -q '"code":"500"'; then
            retry=$((retry + 1))
            if [ "$retry" -lt 5 ]; then
                echo "    $name: retrying ($retry/5) after $((2 * retry))s..." >&2
                sleep $((2 * retry))
                continue
            fi
        fi
        echo "    $name: FAILED -> $out" >&2
        return 1
    done
}

echo "==> creating tables"

# Market data. lastpx/lastsize rather than last/lastsize - see NOTE above.
create mdquote '{
  "type": "partitioned", "prtnCol": "ts",
  "sortColsDisk": ["sym"], "sortColsOrd": ["sym"],
  "columns": [
    {"name": "trddate",   "type": "date",      "description": "Trade date"},
    {"name": "ts",        "type": "timestamp", "attrDisk": "parted", "description": "Tick timestamp (UTC)"},
    {"name": "sym",       "type": "symbol",    "attrMem": "grouped", "attrDisk": "parted", "description": "Instrument symbol"},
    {"name": "ticker", "type": "symbol",    "description": "vendor-style ticker"},
    {"name": "exch",      "type": "symbol",    "description": "Exchange / composite code"},
    {"name": "ccy",       "type": "symbol",    "description": "Quote currency"},
    {"name": "bid",       "type": "float",     "description": "Best bid price"},
    {"name": "bidsize",   "type": "long",      "description": "Best bid size"},
    {"name": "ask",       "type": "float",     "description": "Best ask price"},
    {"name": "asksize",   "type": "long",      "description": "Best ask size"},
    {"name": "lastpx",    "type": "float",     "description": "Last trade price"},
    {"name": "lastsize",  "type": "long",      "description": "Last trade size"},
    {"name": "volume",    "type": "long",      "description": "Cumulative session volume"}
  ]
}'

# Trades. Same trddate/ts/sym as mdquote so aj joins cleanly.
create omstrade '{
  "type": "partitioned", "prtnCol": "ts",
  "sortColsDisk": ["sym"], "sortColsOrd": ["sym"],
  "columns": [
    {"name": "trddate", "type": "date",      "description": "Trade date"},
    {"name": "ts",      "type": "timestamp", "attrDisk": "parted", "description": "Execution timestamp (UTC)"},
    {"name": "sym",     "type": "symbol",    "attrMem": "grouped", "attrDisk": "parted", "description": "Instrument symbol"},
    {"name": "tradeid", "type": "guid",      "description": "Unique trade identifier"},
    {"name": "side",    "type": "symbol",    "description": "buy or sell"},
    {"name": "qty",     "type": "long",      "description": "Executed quantity"},
    {"name": "price",   "type": "float",     "description": "Execution price"},
    {"name": "book",    "type": "symbol",    "description": "Account / book tag"},
    {"name": "exch",    "type": "symbol",    "description": "Exchange / composite code"},
    {"name": "ccy",     "type": "symbol",    "description": "Settlement currency"}
  ]
}'

# Running P&L state - KEYED on (sym, book), so P&L publishes upsert.
# Verified: publishing the same key twice leaves one row, holding the second
# value. Key columns are first, in primaryKeys order, as required.
create pnlstate '{
  "type": "splayed_mem",
  "primaryKeys": ["sym", "book"],
  "columns": [
    {"name": "sym",        "type": "symbol", "description": "Instrument symbol (key)"},
    {"name": "book",       "type": "symbol", "description": "Account / book tag (key)"},
    {"name": "position",   "type": "long",   "description": "Net signed position; + long, - short"},
    {"name": "avgcost",    "type": "float",  "description": "Weighted-average cost of the open position"},
    {"name": "markpx",     "type": "float",  "description": "Mark price last used for unrealized"},
    {"name": "realized",   "type": "float",  "description": "Cumulative realized P&L; never restated"},
    {"name": "unrealized", "type": "float",  "description": "Unrealized P&L at markpx"},
    {"name": "totalpnl",   "type": "float",  "description": "realized + unrealized"},
    {"name": "trades",     "type": "long",   "description": "Fills applied to this key"},
    {"name": "lastupd",    "type": "timestamp", "description": "Last recalculation (UTC)"}
  ]
}'

echo "==> tables now: $(curl -s -m 10 "$GW/api/v0/tables")"
