set -eu

export NOTEBOOK_IMPORT_STATUS_ENDPOINT="${NOTEBOOK_IMPORT_STATUS_ENDPOINT:-http://kx-db-gw:8080}"
export QLIC="${QLIC:-/tmp/qlic}"
export PYTHON="${PYTHON:-/tmp/dbservice-notebook-venv/bin/python}"
venv_dir=$(dirname "$(dirname "$PYTHON")")

is_verbose() {
  case "${VERBOSE:-0}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

section() {
  if is_verbose; then
    printf '\n== %s ==\n' "$1"
  fi
}

step() {
  if is_verbose; then
    printf '%s\n' "-> $1"
  fi
}

git_fetch_failed() {
  failed_repo_url="$1"
  failed_repo_ref="$2"

  printf 'Unable to fetch validation dependency: %s (ref %s)\n' "$failed_repo_url" "$failed_repo_ref" >&2
  case "$failed_repo_url" in
    https://gitlab.com/*)
      if [ -z "${CI_JOB_TOKEN:-}" ]; then
        printf '%s\n' 'Private GitLab HTTPS fetches need CI_JOB_TOKEN in CI, or a local checkout mounted into the runner.' >&2
        printf '%s\n' 'For local runs, prefer CODE_QCLIENT=/path/to/dbservice-qclient or CODE_PYCLIENT=/path/to/dbservice-python-client.' >&2
      fi
      ;;
    git@gitlab.com:*|ssh://git@gitlab.com/*)
      printf '%s\n' 'SSH URLs only work if SSH credentials are available inside the runner container.' >&2
      ;;
  esac
  exit 2
}

fetch_git_ref() {
  target_dir="$1"
  repo_url="$2"
  repo_ref="$3"

  rm -rf "$target_dir"
  git -c init.defaultBranch=main init -q "$target_dir"
  git -C "$target_dir" remote add origin "$repo_url"
  case "$repo_url" in
    https://gitlab.com/*)
      if [ -n "${CI_JOB_TOKEN:-}" ]; then
        GIT_TERMINAL_PROMPT=0 git -C "$target_dir" \
          -c "url.https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.com/.insteadOf=https://gitlab.com/" \
          fetch -q --depth 1 origin "$repo_ref" || git_fetch_failed "$repo_url" "$repo_ref"
      else
        GIT_TERMINAL_PROMPT=0 git -C "$target_dir" fetch -q --depth 1 origin "$repo_ref" || git_fetch_failed "$repo_url" "$repo_ref"
      fi
      ;;
    *)
      GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}" \
        GIT_TERMINAL_PROMPT=0 \
        git -C "$target_dir" fetch -q --depth 1 origin "$repo_ref" || git_fetch_failed "$repo_url" "$repo_ref"
      ;;
  esac
  git -C "$target_dir" checkout -q --detach FETCH_HEAD
}

notebook_deps_available() {
  "$PYTHON" - <<'PY' >/dev/null 2>&1
import bash_kernel
import dbservice_client
import ipykernel
import nbclient
import nbformat
import pykx
import requests
PY
}

selected_client() {
  selection="${TEST_FILTER:-${NOTEBOOK_UNDER_TEST:-}}"
  if [ -n "${NOTEBOOK_TEST_SOURCE:-}" ]; then
    source_name=$(basename "$NOTEBOOK_TEST_SOURCE" | tr '[:upper:]' '[:lower:]')
    case "$source_name" in
      *qclient*|*q-client*|*q_client*) selection=qclient ;;
      *python*) selection=python ;;
      *curl*) selection=curl ;;
    esac
  fi
  normalized_selection=$(printf '%s' "$selection" | tr '[:upper:]' '[:lower:]')
  case "$normalized_selection" in
    python|python_notebook|python_notebook.ipynb) printf 'python' ;;
    q|qclient|q_client|q-client|qclient_notebook|qclient_notebook.ipynb) printf 'qclient' ;;
    curl|curl_notebook|curl_notebook.ipynb) printf 'curl' ;;
    *) printf '' ;;
  esac
}

needs_q_client() {
  case "$(selected_client)" in
    ""|qclient) return 0 ;;
    *) return 1 ;;
  esac
}

needs_python_client() {
  case "$(selected_client)" in
    ""|python) return 0 ;;
    *) return 1 ;;
  esac
}

is_client_readme_validation() {
  case "${TEST_SUITE:-}" in
    client_readmes) return 0 ;;
    *) return 1 ;;
  esac
}

python_client_source_requested() {
  case "${DBSERVICE_PYTHON_CLIENT_MODE:-source}" in
    package|released|pip|"") return 1 ;;
    source|git|repo) return 0 ;;
    *)
      printf 'Unsupported DBSERVICE_PYTHON_CLIENT_MODE: %s\n' "$DBSERVICE_PYTHON_CLIENT_MODE" >&2
      printf 'Expected package or source.\n' >&2
      exit 2
      ;;
  esac
}

if ! is_verbose; then
  printf '%s\n' '__VALIDATION_OUTPUT_BEGIN__'
fi

section "Runner Setup"

if [ -n "${KDB_K4LICENSE_B64:-}" ]; then
  step "Installing KDB-X license"
  mkdir -p "$QLIC" "$HOME/.qp.licenses"
  printf '%s' "$KDB_K4LICENSE_B64" | base64 -d > "$QLIC/k4.lic"
  cp "$QLIC/k4.lic" "$HOME/.qp.licenses/k4.lic"
fi

if [ ! -x "$PYTHON" ]; then
  step "Creating Python virtual environment"
  python3 -m venv "$venv_dir"
fi

python_client_dir=""
using_python_client_source=0
if needs_python_client; then
  python_client_dir="${DBSERVICE_PYTHON_CLIENT_DIR:-}"
  if [ -n "$python_client_dir" ]; then
    mounted_python_client_dir="/workspace/.external/dbservice-python-client"
    if [ -f "$mounted_python_client_dir/src/dbservice_client/__init__.py" ]; then
      python_client_dir="$mounted_python_client_dir"
    fi
    if [ ! -f "$python_client_dir/src/dbservice_client/__init__.py" ]; then
      printf 'Local Python client source is not available: %s\n' "$python_client_dir" >&2
      printf 'Expected to find: %s\n' "$python_client_dir/src/dbservice_client/__init__.py" >&2
      exit 2
    fi
    export DBSERVICE_PYTHON_CLIENT_DIR="$python_client_dir"
    export PYTHONPATH="$python_client_dir/src${PYTHONPATH:+:$PYTHONPATH}"
    using_python_client_source=1
  elif is_client_readme_validation || python_client_source_requested; then
    python_client_dir="${DBSERVICE_PYTHON_CLIENT_CLONE_DIR:-/tmp/kdbx-db-service-python-client}"
    python_client_url="${DBSERVICE_PYTHON_CLIENT_URL:-https://gitlab.com/kxdev/kxinsights/data-services/dbservice-python-client.git}"
    python_client_ref="${DBSERVICE_PYTHON_CLIENT_REF:-main}"
    step "Fetching DB Service Python client"
    fetch_git_ref "$python_client_dir" "$python_client_url" "$python_client_ref"
    if [ ! -f "$python_client_dir/src/dbservice_client/__init__.py" ]; then
      printf 'Fetched Python client source is not available: %s\n' "$python_client_dir" >&2
      printf 'Expected to find: %s\n' "$python_client_dir/src/dbservice_client/__init__.py" >&2
      exit 2
    fi
    export DBSERVICE_PYTHON_CLIENT_DIR="$python_client_dir"
    export PYTHONPATH="$python_client_dir/src${PYTHONPATH:+:$PYTHONPATH}"
    using_python_client_source=1
  fi
fi

if ! notebook_deps_available; then
  step "Installing Python notebook dependencies"
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -r test/notebooks/requirements.txt
else
  step "Python notebook dependencies already available"
fi

if ! needs_python_client; then
  step "Skipping Python client setup for this validation selection"
elif [ "$using_python_client_source" = "1" ]; then
  step "Using DB Service Python client source"
else
  step "Using packaged DB Service Python client"
fi

if needs_q_client; then
  step "Installing DB Service q client"
  q_client_local_dir="${DBSERVICE_Q_CLIENT_LOCAL_DIR:-}"
  if [ -n "$q_client_local_dir" ]; then
    mounted_q_client_dir="/workspace/.external/dbservice-qclient"
    if [ -f "$mounted_q_client_dir/dbservice_client.q" ]; then
      q_client_dir="$mounted_q_client_dir"
    else
      q_client_dir="$q_client_local_dir"
    fi
    if [ ! -f "$q_client_dir/dbservice_client.q" ]; then
      printf 'Local qClient source is not available: %s\n' "$q_client_dir" >&2
      printf 'Expected to find: %s\n' "$q_client_dir/dbservice_client.q" >&2
      exit 2
    fi
    export DBSERVICE_Q_CLIENT_LOCAL_DIR="$q_client_dir"
  else
    q_client_dir="${DBSERVICE_Q_CLIENT_DIR:-/tmp/kdbx-db-service-q-client}"
    q_client_url="${DBSERVICE_Q_CLIENT_URL:-https://gitlab.com/kxdev/kxinsights/data-services/dbservice-qclient.git}"
    q_client_ref="${DBSERVICE_Q_CLIENT_REF:-main}"
    fetch_git_ref "$q_client_dir" "$q_client_url" "$q_client_ref"
  fi

  q_module_dir=$(
    "$PYTHON" - <<'PY'
import pathlib
import site

print(pathlib.Path(site.getsitepackages()[0]) / "pykx" / "lib" / "mod" / "kx")
PY
  )
  mkdir -p "$q_module_dir" "$HOME/.kx/mod/kx" /opt/kx/mod/kx
  cp "$q_client_dir/dbservice_client.q" "$q_module_dir/dbservice_client.q"
  cp "$q_client_dir/dbservice_client.q" "$HOME/.kx/mod/kx/dbservice_client.q"
  cp "$q_client_dir/dbservice_client.q" /opt/kx/mod/kx/dbservice_client.q

  printf '%s\n' 'dbs:use`kx.dbservice_client' 'show key dbs' '\\' > /tmp/verify-kdbx.q
  if ! q /tmp/verify-kdbx.q >/tmp/verify-kdbx.out 2>&1; then
    cat /tmp/verify-kdbx.out
    exit 1
  fi
else
  step "Skipping q client setup for this validation selection"
fi

if is_verbose && needs_python_client; then
  if [ "$using_python_client_source" = "1" ]; then
    python_commit=""
    if [ -d "$python_client_dir/.git" ]; then
      python_commit=$(git -C "$python_client_dir" rev-parse HEAD 2>/dev/null || true)
    fi
    printf 'Resolved Python client: source=%s%s\n' \
      "$python_client_dir" "${python_commit:+ commit=$python_commit}"
  else
    "$PYTHON" - <<'PY'
import importlib.metadata
import dbservice_client

try:
    version = importlib.metadata.version("kdbx-db-service-client")
except importlib.metadata.PackageNotFoundError:
    version = "unknown"
print(f"Resolved Python client: package=kdbx-db-service-client version={version} path={dbservice_client.__file__}")
PY
  fi
fi

if is_verbose && needs_q_client; then
  q_commit=""
  if [ -d "$q_client_dir/.git" ]; then
    q_commit=$(git -C "$q_client_dir" rev-parse HEAD 2>/dev/null || true)
  fi
  printf 'Resolved qClient: source=%s%s file=%s\n' \
    "$q_client_dir" "${q_commit:+ commit=$q_commit}" "$q_client_dir/dbservice_client.q"
fi

section "Validation Tests"
export NOTEBOOK_PROGRESS="${NOTEBOOK_PROGRESS:-1}"

if [ -n "${VALIDATION_COMMAND:-}" ]; then
  sh -c "$VALIDATION_COMMAND"
else
  if is_verbose; then
    "$PYTHON" test/notebooks/tests/run_notebook_validation.py --verbose
  else
    "$PYTHON" test/notebooks/tests/run_notebook_validation.py ${NOTEBOOK_TEST_FLAGS:-}
  fi
fi
