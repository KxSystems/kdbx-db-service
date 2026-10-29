set -eu

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

is_verbose() {
  case "${VERBOSE:-0}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

test_suite="${TEST_SUITE:-notebooks}"
test_source="${TEST_SOURCE:-}"
test_filter="${TEST_FILTER:-}"
code_profile="${CODE_PROFILE:-main}"
code_dbs="${CODE_DBS:-$code_profile}"
code_qclient="${CODE_QCLIENT:-$code_profile}"
code_pyclient="${CODE_PYCLIENT:-$code_profile}"
fail_fast="${FAIL_FAST:-0}"
compose_cmd="${NOTEBOOK_TEST_COMPOSE:-docker compose -f docker-compose.yaml -f test/compose/docker-compose.notebook-test-runner.yaml}"
repo_root=$(pwd -P)

normalized_filter=$(printf '%s' "$test_filter" | tr '[:upper:]' '[:lower:]')
case "$normalized_filter" in
  ""|curl|curl_notebook|curl_notebook.ipynb|python|python_notebook|python_notebook.ipynb|q|qclient|q_client|q-client|qclient_notebook|qclient_notebook.ipynb) ;;
  *)
    printf 'Unsupported TEST_FILTER: %s\n' "$test_filter" >&2
    printf '%s\n' 'Use curl, python, or qClient.' >&2
    exit 2
    ;;
esac

case "$test_source" in
  "$repo_root"/*) test_source=${test_source#"$repo_root"/} ;;
  /*)
    printf 'TEST_SOURCE must be inside this repository: %s\n' "$repo_root" >&2
    printf 'Received: %s\n' "$test_source" >&2
    exit 2
    ;;
esac

if [ -n "$test_source" ] && [ ! -f "$repo_root/$test_source" ]; then
  printf 'TEST_SOURCE does not exist: %s\n' "$repo_root/$test_source" >&2
  exit 2
fi

external_source_dir="${DBSERVICE_VALIDATION_EXTERNAL_DIR:-$repo_root/.external/validation-sources}"
log_dir="${VALIDATION_LOG_DIR:-validation-logs}"
case "$log_dir" in
  /*) ;;
  *) log_dir="$repo_root/$log_dir" ;;
esac
build_log="$log_dir/validation-runner-build.log"
runner_log="$log_dir/validation-runner.log"
prepared_source_section=0
resolved_python_client=""
resolved_q_client=""
runner_fifo=""
default_q_client_url="https://gitlab.com/kxdev/kxinsights/data-services/dbservice-qclient.git"
default_python_client_url="https://gitlab.com/kxdev/kxinsights/data-services/dbservice-python-client.git"

mkdir -p "$log_dir"
: >"$build_log"
: >"$runner_log"

selected_client() {
  if [ "$test_suite" = "notebooks" ] && [ -n "$test_source" ]; then
    source_name=$(basename "$test_source" | tr '[:upper:]' '[:lower:]')
    case "$source_name" in
      *python*) printf 'python'; return ;;
      *qclient*|*q-client*|*q_client*) printf 'qclient'; return ;;
      *curl*) printf 'curl'; return ;;
    esac
  fi

  normalized_filter=$(printf '%s' "$test_filter" | tr '[:upper:]' '[:lower:]')
  case "$normalized_filter" in
    python|python_notebook|python_notebook.ipynb) printf 'python' ;;
    q|qclient|q_client|q-client|qclient_notebook|qclient_notebook.ipynb) printf 'qclient' ;;
    curl|curl_notebook|curl_notebook.ipynb) printf 'curl' ;;
    *) printf '' ;;
  esac
}

if [ "$test_suite" = "client_readmes" ]; then
  case "$(printf '%s' "$test_filter" | tr '[:upper:]' '[:lower:]')" in
    "")
      if [ -n "$test_source" ]; then
        printf '%s\n' 'TEST_SOURCE for client_readmes requires TEST_FILTER=python or TEST_FILTER=qClient.' >&2
        exit 2
      fi
      ;;
    python|q|qclient|q_client|q-client) ;;
    *)
      printf '%s\n' 'client_readmes supports TEST_FILTER=python or TEST_FILTER=qClient.' >&2
      exit 2
      ;;
  esac
fi

needs_python_client() {
  case "$test_suite" in
    notebooks|spec_samples|client_readmes) ;;
    *) return 1 ;;
  esac

  client_selection=$(selected_client)
  case "$(printf '%s' "$client_selection" | tr '[:upper:]' '[:lower:]')" in
    ""|python|python_notebook|python_notebook.ipynb) return 0 ;;
    *) return 1 ;;
  esac
}

needs_q_client() {
  case "$test_suite" in
    notebooks|spec_samples|client_readmes) ;;
    *) return 1 ;;
  esac

  client_selection=$(selected_client)
  case "$(printf '%s' "$client_selection" | tr '[:upper:]' '[:lower:]')" in
    ""|q|qclient|q_client|q-client|qclient_notebook|qclient_notebook.ipynb) return 0 ;;
    *) return 1 ;;
  esac
}

is_gitlab_url() {
  case "$1" in
    https://gitlab.com/*|git@gitlab.com:*|ssh://git@gitlab.com/*) return 0 ;;
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

start_source_section() {
  if [ "$prepared_source_section" = "0" ]; then
    section "Setup: Validation Sources"
    prepared_source_section=1
  fi
}

stage_git_ref_from_host_checkout() {
  source_name="$1"
  checkout_dir="$2"
  target_dir="$3"
  repo_ref="$4"
  required_file="$5"
  source_key="$6"

  if [ ! -d "$checkout_dir/.git" ]; then
    printf 'Default local validation needs a host checkout for %s: %s\n' "$source_name" "$checkout_dir" >&2
    printf '%s\n' 'Create the sibling checkout, or pass CODE_QCLIENT=/path/to/dbservice-qclient or CODE_PYCLIENT=/path/to/dbservice-python-client.' >&2
    exit 2
  fi

  start_source_section
  step "Fetching $source_name on host from $checkout_dir (ref $repo_ref)"
  if ! GIT_TERMINAL_PROMPT=0 git -C "$checkout_dir" fetch -q --depth 1 origin "$repo_ref"; then
    printf 'Unable to fetch %s ref %s from host checkout: %s\n' "$source_name" "$repo_ref" "$checkout_dir" >&2
    printf '%s\n' 'Check that the checkout can fetch from GitLab with your host SSH or Git credentials.' >&2
    exit 2
  fi

  tmp_dir="$target_dir.tmp.$$"
  archive_file="$tmp_dir/source.tar"
  rm -rf "$tmp_dir" "$target_dir"
  mkdir -p "$tmp_dir"
  git -C "$checkout_dir" archive --format=tar --output="$archive_file" FETCH_HEAD
  tar -xf "$archive_file" -C "$tmp_dir"
  rm -f "$archive_file"

  if [ ! -f "$tmp_dir/$required_file" ]; then
    printf 'Fetched %s source does not contain expected file: %s\n' "$source_name" "$required_file" >&2
    exit 2
  fi

  mv "$tmp_dir" "$target_dir"
  resolved_ref=$(git -C "$checkout_dir" rev-parse --short=12 FETCH_HEAD)
  checkout_abs=$(cd "$checkout_dir" && pwd -P)
  case "$source_key" in
    python) resolved_python_client="GitLab ${DBSERVICE_PYTHON_CLIENT_REF:-main} @ $resolved_ref | fetched via $checkout_abs" ;;
    qclient) resolved_q_client="GitLab ${DBSERVICE_Q_CLIENT_REF:-main} @ $resolved_ref | fetched via $checkout_abs" ;;
  esac
  step "Prepared $source_name source at $target_dir"
}

if [ -z "${CI_JOB_TOKEN:-}" ] && [ -z "${DBSERVICE_PYTHON_CLIENT_DIR:-}" ] && needs_python_client && python_client_source_requested && is_gitlab_url "${DBSERVICE_PYTHON_CLIENT_URL:-$default_python_client_url}"; then
  DBSERVICE_PYTHON_CLIENT_DIR="$external_source_dir/dbservice-python-client"
  export DBSERVICE_PYTHON_CLIENT_DIR
  stage_git_ref_from_host_checkout \
    "DB Service Python client" \
    "${DBSERVICE_PYTHON_CLIENT_SIBLING_DIR:-../dbservice-python-client}" \
    "$DBSERVICE_PYTHON_CLIENT_DIR" \
    "${DBSERVICE_PYTHON_CLIENT_REF:-main}" \
    "pyproject.toml" \
    "python"
fi

if [ -z "${CI_JOB_TOKEN:-}" ] && [ -z "${DBSERVICE_Q_CLIENT_LOCAL_DIR:-}" ] && needs_q_client && is_gitlab_url "${DBSERVICE_Q_CLIENT_URL:-$default_q_client_url}"; then
  DBSERVICE_Q_CLIENT_LOCAL_DIR="$external_source_dir/dbservice-qclient"
  export DBSERVICE_Q_CLIENT_LOCAL_DIR
  stage_git_ref_from_host_checkout \
    "DB Service q client" \
    "${DBSERVICE_Q_CLIENT_SIBLING_DIR:-../dbservice-qclient}" \
    "$DBSERVICE_Q_CLIENT_LOCAL_DIR" \
    "${DBSERVICE_Q_CLIENT_REF:-main}" \
    "dbservice_client.q" \
    "qclient"
fi

if needs_python_client && [ -n "${DBSERVICE_PYTHON_CLIENT_DIR:-}" ]; then
  if [ ! -f "$DBSERVICE_PYTHON_CLIENT_DIR/pyproject.toml" ]; then
    printf 'Local Python client path is not a Python package: %s\n' "$DBSERVICE_PYTHON_CLIENT_DIR" >&2
    printf 'Expected to find: %s\n' "$DBSERVICE_PYTHON_CLIENT_DIR/pyproject.toml" >&2
    exit 2
  fi
  compose_cmd="$compose_cmd -f test/compose/docker-compose.python-client-local.yaml"
fi

if needs_q_client && [ -n "${DBSERVICE_Q_CLIENT_LOCAL_DIR:-}" ]; then
  if [ ! -f "$DBSERVICE_Q_CLIENT_LOCAL_DIR/dbservice_client.q" ]; then
    printf 'Local qClient path is not a qClient checkout: %s\n' "$DBSERVICE_Q_CLIENT_LOCAL_DIR" >&2
    printf 'Expected to find: %s\n' "$DBSERVICE_Q_CLIENT_LOCAL_DIR/dbservice_client.q" >&2
    exit 2
  fi
  compose_cmd="$compose_cmd -f test/compose/docker-compose.q-client-local.yaml"
fi

describe_checkout() {
  checkout_path="$1"
  checkout_abs=$(cd "$checkout_path" && pwd -P)
  if git -C "$checkout_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    checkout_branch=$(git -C "$checkout_path" symbolic-ref --short -q HEAD || printf 'detached')
    checkout_commit=$(git -C "$checkout_path" rev-parse --short=12 HEAD)
    checkout_dirty=""
    if [ -n "$(git -C "$checkout_path" status --porcelain --untracked-files=no)" ]; then
      checkout_dirty=" (modified)"
    fi
    printf '%s @ %s%s | %s' "$checkout_branch" "$checkout_commit" "$checkout_dirty" "$checkout_abs"
  else
    printf '%s' "$checkout_abs"
  fi
}

notebook_examples() {
  case "$(printf '%s' "$test_filter" | tr '[:upper:]' '[:lower:]')" in
    curl|curl_notebook|curl_notebook.ipynb) printf '%s/notebooks/curl_notebook.ipynb' "$repo_root" ;;
    python|python_notebook|python_notebook.ipynb) printf '%s/notebooks/python_notebook.ipynb' "$repo_root" ;;
    q|qclient|q_client|q-client|qclient_notebook|qclient_notebook.ipynb) printf '%s/notebooks/qClient_notebook.ipynb' "$repo_root" ;;
    "") printf '%s/notebooks/curl_notebook.ipynb, %s/notebooks/python_notebook.ipynb, %s/notebooks/qClient_notebook.ipynb' "$repo_root" "$repo_root" "$repo_root" ;;
    *) printf '%s/notebooks/%s' "$repo_root" "$test_filter" ;;
  esac
}

test_source_description() {
  if [ -n "$test_source" ]; then
    case "$test_source" in
      /*) printf '%s' "$test_source" ;;
      *) printf '%s/%s' "$repo_root" "$test_source" ;;
    esac
    return
  fi

  case "$test_suite" in
    notebooks) notebook_examples ;;
    spec_samples)
      printf '%s/openapi/dbservice.yaml' "$repo_root"
      ;;
    client_readmes)
      printf 'README.md from the selected %s client source' "${test_filter:-Python and qClient}"
      ;;
  esac
}

if needs_python_client; then
  if [ -n "$resolved_python_client" ]; then
    python_description="$resolved_python_client"
  elif [ -n "${DBSERVICE_PYTHON_CLIENT_DIR:-}" ]; then
    python_description=$(describe_checkout "$DBSERVICE_PYTHON_CLIENT_DIR")
  elif python_client_source_requested; then
    python_description="$code_pyclient | ${DBSERVICE_PYTHON_CLIENT_URL:-$default_python_client_url} @ ${DBSERVICE_PYTHON_CLIENT_REF:-main}"
  else
    python_description="$code_pyclient | installed package"
  fi
else
  python_description="not used"
fi

if needs_q_client; then
  if [ -n "$resolved_q_client" ]; then
    q_description="$resolved_q_client"
  elif [ -n "${DBSERVICE_Q_CLIENT_LOCAL_DIR:-}" ]; then
    q_description=$(describe_checkout "$DBSERVICE_Q_CLIENT_LOCAL_DIR")
  else
    q_description="$code_qclient | ${DBSERVICE_Q_CLIENT_URL:-$default_q_client_url} @ ${DBSERVICE_Q_CLIENT_REF:-main}"
  fi
else
  q_description="not used"
fi

if [ -n "${DS_REGISTRY:-}" ]; then
  dbservice_images="${DS_REGISTRY}*:${DS_TAG:-}"
else
  dbservice_images="local Docker images (*:${DS_TAG:-})"
fi

rt_registry=${DS_RT_REGISTRY:-${DS_REGISTRY:-}}
if [ -n "$rt_registry" ]; then
  runtime_image="${rt_registry}kxi-rt:${DS_RT_TAG:-}"
else
  runtime_image="local Docker image (kxi-rt:${DS_RT_TAG:-})"
fi

printf '\nConfiguration\n'
printf '%s\n' '-------------'
printf '  %-13s %s\n' 'Test suite' "$test_suite"
printf '  %-13s %s\n' 'Test filter' "${test_filter:-all}"
printf '  %-13s %s\n' 'Test source' "$(test_source_description)"
printf '  %-13s %s\n' 'Code profile' "$code_profile"
printf '  %-13s %s | %s\n' 'DB Service' "$code_dbs" "$dbservice_images"
printf '  %-13s %s | %s\n' 'RT' "${DS_RT_TAG:-}" "$runtime_image"
printf '  %-13s %s\n' 'qClient' "$q_description"
printf '  %-13s %s\n' 'Python client' "$python_description"
printf '  %-13s host=%s | runner=http://kx-db-gw:8080\n' 'Endpoint' "${NOTEBOOK_IMPORT_STATUS_ENDPOINT:-http://localhost:8080}"
if is_verbose; then
  printf '  %-13s %s\n' 'Output' 'verbose (streamed to terminal)'
else
  printf '  %-13s %s\n' 'Full logs' "$log_dir"
fi

section "Setup: Validation Runner"
VERBOSE="${VERBOSE:-0}" sh test/validation/run_step.sh \
  "Building validation runner image" "$build_log" \
  $compose_cmd build notebook-test-runner

step "Removing stale validation runner container"
docker rm -f notebook-test-runner-ci >/dev/null 2>&1 || true

show_runner_setup_failure() {
  printf '\nVALIDATION RUNNER FAILED DURING SETUP\n' >&2
  printf 'Full output: %s\n' "$runner_log" >&2
  tail -n 40 "$runner_log" >&2 || true
}

cleanup_runner_fifo() {
  if [ -n "$runner_fifo" ]; then
    rm -f "$runner_fifo"
  fi
}

trap cleanup_runner_fifo EXIT HUP INT TERM

run_validation_container() {
  if is_verbose; then
    "$@"
    return $?
  fi

  runner_fifo="$log_dir/.validation-runner-output.$$"
  rm -f "$runner_fifo"
  mkfifo "$runner_fifo"
  : >"$runner_log"

  "$@" >"$runner_fifo" 2>&1 &
  runner_pid=$!
  runner_output_started=0
  while IFS= read -r runner_line || [ -n "$runner_line" ]; do
    printf '%s\n' "$runner_line" >>"$runner_log"
    if [ "$runner_output_started" = "1" ]; then
      printf '%s\n' "$runner_line"
    elif [ "$runner_line" = "__VALIDATION_OUTPUT_BEGIN__" ]; then
      runner_output_started=1
    fi
  done <"$runner_fifo"

  if wait "$runner_pid"; then
    runner_rc=0
  else
    runner_rc=$?
  fi
  cleanup_runner_fifo
  runner_fifo=""

  if [ "$runner_output_started" = "0" ]; then
    show_runner_setup_failure
  fi
  return "$runner_rc"
}

running_tests_header() {
  printf '\nTest progress\n'
  printf '%s\n' '-------------'
}

case "$test_suite" in
  notebooks)
    running_tests_header
    export NOTEBOOK_UNDER_TEST="$test_filter"
    export NOTEBOOK_TEST_SOURCE="$test_source"
    run_validation_container $compose_cmd run --name notebook-test-runner-ci --no-deps notebook-test-runner
    ;;

  spec_samples)
    running_tests_header
    export VALIDATION_COMMAND="SPEC_SAMPLES_RUN=1 TEST_SOURCE=\"$test_source\" TEST_FILTER=\"$test_filter\" FAIL_FAST=\"$fail_fast\" PYTHON=\"\$PYTHON\" NOTEBOOK_IMPORT_STATUS_ENDPOINT=\"\$NOTEBOOK_IMPORT_STATUS_ENDPOINT\" \"\$PYTHON\" test/validation/validate.py --suite spec_samples --filter \"$test_filter\" --test-source \"$test_source\" --run-samples --fail-fast \"$fail_fast\""
    run_validation_container $compose_cmd run --name notebook-test-runner-ci --no-deps notebook-test-runner
    ;;

  client_readmes)
    running_tests_header
    export VALIDATION_COMMAND="TEST_SOURCE=\"$test_source\" TEST_FILTER=\"$test_filter\" FAIL_FAST=\"$fail_fast\" PYTHON=\"\$PYTHON\" NOTEBOOK_IMPORT_STATUS_ENDPOINT=\"\$NOTEBOOK_IMPORT_STATUS_ENDPOINT\" \"\$PYTHON\" test/validation/validate.py --suite client_readmes --filter \"$test_filter\" --test-source \"$test_source\" --run-samples --fail-fast \"$fail_fast\""
    run_validation_container $compose_cmd run --name notebook-test-runner-ci --no-deps notebook-test-runner
    ;;

  *)
    printf 'Unsupported compose test suite: %s\n' "$test_suite" >&2
    exit 2
    ;;
esac
