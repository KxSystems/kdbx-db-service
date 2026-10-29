set -eu

if [ -z "${KDB_K4LICENSE_B64:-}" ]; then
  echo "Missing KDB_K4LICENSE_B64. DB Service validation requires the k4 license variable."
  exit 1
fi

if [ "${VERBOSE:-0}" = "1" ]; then
  echo "Using KDB_K4LICENSE_B64 for DB Service validation"
fi
unset KDB_LICENSE_B64

export QLIC="${QLIC:-$HOME/qlic}"
mkdir -p "$QLIC" "$HOME/.qp.licenses"
printf '%s' "$KDB_K4LICENSE_B64" | base64 -d > "$QLIC/k4.lic"
cp "$QLIC/k4.lic" "$HOME/.qp.licenses/k4.lic"

mkdir -p \
  data/db \
  data/db/idb \
  data/db/hdb \
  data/imports \
  data/logs \
  data/rt/in \
  data/rt/out \
  data/rt/state \
  data/rt/session \
  executed-notebook \
  validation-reports

if [ -d samples/data ]; then
  cp -R samples/data/. data/imports/
fi
if [ -f samples/data/fxquote ]; then
  mkdir -p data/imports/fxquote-hdb
  cp samples/data/fxquote data/imports/fxquote-hdb/fxquote
fi

export NOTEBOOK_TEST_COMPOSE="${NOTEBOOK_TEST_COMPOSE:-docker compose -f docker-compose.yaml -f test/compose/docker-compose.notebook-test-runner.yaml}"
export DBSERVICE_COMPOSE="${DBSERVICE_COMPOSE:-docker compose -f docker-compose.yaml}"

make validate \
  PYTHON="${VALIDATION_HOST_PYTHON:-python3}" \
  TEST_SUITE="${TEST_SUITE:-notebooks}" \
  TEST_SOURCE="${TEST_SOURCE:-}" \
  TEST_FILTER="${TEST_FILTER:-}" \
  CODE_PROFILE="${CODE_PROFILE:-main}" \
  FAIL_FAST="${FAIL_FAST:-0}" \
  VERBOSE="${VERBOSE:-0}"
