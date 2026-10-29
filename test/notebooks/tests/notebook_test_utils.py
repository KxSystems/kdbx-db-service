import json
import os
import re
import shutil
import sys
import textwrap
import time

import nbformat
from nbformat.v4 import new_code_cell

VALIDATION_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "validation"))
if VALIDATION_DIR not in sys.path:
    sys.path.insert(0, VALIDATION_DIR)

from core import (
    INCONSISTENT_FIELDS,
    INCONSISTENT_ROWS,
    JSON_PARSE_ERROR,
    assert_job_completed_or_poll,
    classify_operation,
    colored,
    extract_import_paths_from_source,
    extract_import_status,
    extract_job_id,
    extract_table_param_from_source,
    field_names,
    import_job_failure_message,
    is_import_cell,
    list_tables_rest,
    operation_display_name,
    parse_json_text,
    parse_python_literal,
    payload_fields,
    payload_indicates_failure,
    poll_import_status_rest,
    query_shape,
    reset_dbservice_tables,
    result_type_name,
    row_count,
    short_text,
    validation_check_text,
    validation_verbose,
    write_junit_report,
)


QCLIENT_QUERY_ERROR_HELPER_SOURCE = """
def _test_assert_q_query_success(result_type, fields, lookup):
    field_names = {str(field) for field in (fields or [])}
    if result_type != 99 or not {"status", "error"}.issubset(field_names):
        return

    status = lookup("status")
    error = lookup("error")
    details = lookup("details") if "details" in field_names else None
    parts = ["qClient query returned an error response"]
    if status not in (None, ""):
        parts.append("status=%s" % status)
    if error not in (None, ""):
        parts.append("error=%s" % error)
    if details not in (None, ""):
        parts.append("details=%s" % details)
    raise RuntimeError(" - FAIL: " + "; ".join(parts))
""".strip()


def notebook_path(name: str) -> str:
    return os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "notebooks", name
    )


def load_notebook(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return nbformat.read(f, as_version=4)


def should_show_notebook_progress() -> bool:
    value = os.getenv("NOTEBOOK_PROGRESS", "1").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    return True


def is_test_helper_cell(cell) -> bool:
    source = cell.get("source", "")
    tags = cell.get("metadata", {}).get("tags", [])
    if "test-helper" in tags:
        return True
    return "# TEST_HELPER_CELL" in source


def detect_client(path: str) -> str:
    name = os.path.basename(path).replace("_executed", "")
    lower = name.lower()
    if lower.startswith("curl_"):
        return "curl"
    if lower.startswith("python_"):
        return "python"
    if lower.startswith("qclient_"):
        return "qclient"
    raise AssertionError(colored(f" - FAIL: Unknown notebook client for {name}", "red"))


def notebook_progress_prefix(path_or_name: str) -> str:
    return f"notebook:{detect_client(path_or_name)}"


class NotebookValidationError(AssertionError):
    """A notebook failure already formatted for concise console output."""


def format_notebook_failure(
    notebook_file: str,
    cell_index,
    operation: str,
    source: str,
    error: str,
    executed_file: str = "",
    report_file: str = "",
) -> str:
    repo_root = os.path.abspath(infer_repo_root(notebook_file))
    notebook_display = os.path.relpath(os.path.abspath(notebook_file), repo_root)
    source_text = str(source or "").strip() or "(source unavailable)"
    if len(source_text) > 3000:
        source_text = source_text[:2997] + "..."
    error_text = short_text(str(error or "unknown error"), limit=1200)
    lines = [
        "NOTEBOOK VALIDATION FAILED",
        f"File: {notebook_display}",
        f"Cell: {cell_index} (position in the original notebook)",
        f"Operation: {operation}",
        "Command/source:",
        textwrap.indent(source_text, "  "),
        f"Error: {error_text}",
    ]
    if executed_file:
        lines.append(f"Executed copy: {executed_file}")
    if report_file:
        lines.append(f"JUnit report: {report_file}")
    return "\n".join(lines)


def cell_outputs(cell):
    return cell.get("outputs", [])


def has_error_output(cell):
    return any(out.get("output_type") == "error" for out in cell_outputs(cell))


def has_output(cell):
    for out in cell_outputs(cell):
        out_type = out.get("output_type")
        if out_type == "error":
            return True
        if out_type == "stream":
            text = out.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            if str(text).strip():
                return True
        if out_type in ("execute_result", "display_data"):
            data = out.get("data", {})
            if data:
                return True
    return False


def output_text(cell):
    chunks = []
    previous_stream = None
    for out in cell_outputs(cell):
        out_type = out.get("output_type")
        if out_type == "stream":
            text = out.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            if text:
                stream = out.get("name", "stdout")
                if chunks and stream == previous_stream:
                    chunks[-1] += str(text)
                else:
                    chunks.append(str(text))
                previous_stream = stream
        elif out_type in ("execute_result", "display_data"):
            previous_stream = None
            data = out.get("data", {})
            if "text/plain" in data:
                text = data["text/plain"]
                if isinstance(text, list):
                    text = "".join(text)
                chunks.append(str(text))
            elif "application/json" in data:
                chunks.append(json.dumps(data["application/json"]))
        elif out_type == "error":
            previous_stream = None
            chunks.append(str(out.get("evalue", "")))
    return "\n".join(c for c in chunks if c).strip()


def helper_failure_message(error_text, display_idx, label):
    for line in (error_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- FAIL:"):
            detail = stripped[len("- FAIL:") :].strip()
            if detail.startswith("cell "):
                return f" - FAIL: {detail}"
            return f" - FAIL: cell {display_idx} ({label}) {detail}"

    legacy_import_match = re.search(
        r"Import job failed(?: after polling)?:\s*(\{.*\})",
        error_text or "",
        flags=re.DOTALL,
    )
    if legacy_import_match:
        payload = parse_python_literal(legacy_import_match.group(1))
        if isinstance(payload, dict):
            job_id = extract_job_id(payload, error_text)
            status = extract_import_status(payload, error_text)
            return import_job_failure_message(
                display_idx,
                label,
                job_id,
                status,
                payload=payload,
                text=error_text,
            )

    legacy_timeout_match = re.search(
        r"Timed out waiting for import job(?:\s+|:\s*)(.*)",
        error_text or "",
    )
    if legacy_timeout_match:
        job_id = short_text(legacy_timeout_match.group(1), limit=80) or "-"
        return f" - FAIL: cell {display_idx} ({label}) job timed out: status=unknown job={job_id}"

    return (
        f" - FAIL: cell {display_idx} ({label}) helper produced an error output. "
        f"output={short_text(error_text)}"
    )


def has_html_output(cell):
    for out in cell_outputs(cell):
        if out.get("output_type") in ("execute_result", "display_data"):
            data = out.get("data", {})
            if "text/html" in data:
                return True
    return False


def html_output_text(cell):
    chunks = []
    for out in cell_outputs(cell):
        if out.get("output_type") in ("execute_result", "display_data"):
            html = out.get("data", {}).get("text/html")
            if isinstance(html, list):
                html = "".join(html)
            if html:
                chunks.append(str(html))
    return "\n".join(chunks)


def split_path_env(value: str):
    if not value:
        return []
    parts = [p.strip() for p in value.split(os.pathsep)]
    return [p for p in parts if p]


def infer_repo_root(notebook_file: str):
    notebooks_dir = os.path.dirname(notebook_file)
    return os.path.normpath(os.path.join(notebooks_dir, ".."))


def infer_staging_dir():
    return os.path.expanduser(
        os.getenv("NOTEBOOK_IMPORT_STAGING_DIR", "~/.kx/db-service/data/imports")
    )


def infer_source_dirs(notebook_file: str):
    repo_root = infer_repo_root(notebook_file)
    notebooks_dir = os.path.dirname(notebook_file)
    configured = [
        os.path.expanduser(p)
        for p in split_path_env(os.getenv("NOTEBOOK_IMPORT_SOURCE_DIRS", ""))
    ]
    defaults = [
        notebooks_dir,
        os.path.join(repo_root, "samples"),
    ]
    deduped = []
    seen = set()
    for d in configured + defaults:
        if d and d not in seen:
            seen.add(d)
            deduped.append(d)
    return deduped


def should_auto_stage_import_files():
    return os.getenv("NOTEBOOK_AUTO_STAGE_IMPORTS", "1") == "1"


def stage_notebook_import_files(notebook_file: str):
    nb = load_notebook(notebook_file)
    staging_dir = infer_staging_dir()
    source_dirs = infer_source_dirs(notebook_file)
    required = []

    for cell in nb["cells"]:
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if not is_import_cell(source):
            continue
        for rel_path in extract_import_paths_from_source(source):
            if rel_path not in required:
                required.append(rel_path)

    if not required:
        return []

    os.makedirs(staging_dir, exist_ok=True)
    staged = []
    missing = []
    for rel_path in required:
        target = os.path.join(staging_dir, os.path.basename(rel_path))
        if os.path.exists(target):
            continue

        src = None
        for base in source_dirs:
            candidate = os.path.normpath(os.path.join(base, rel_path))
            if os.path.exists(candidate):
                src = candidate
                break
            # Fall back to basename match under source dir.
            candidate2 = os.path.join(base, os.path.basename(rel_path))
            if os.path.exists(candidate2):
                src = candidate2
                break

        if not src:
            missing.append(rel_path)
            continue

        shutil.copy2(src, target)
        staged.append((src, target))

    if missing:
        raise AssertionError(
            colored(
                " - FAIL: Could not stage required import file(s): "
                + ", ".join(sorted(set(missing)))
                + f". Searched source dirs: {', '.join(source_dirs)}. "
                + f"Expected staging dir: {staging_dir}",
                "red",
            )
        )
    return staged


def assert_notebook_import_paths_exist(notebook_file: str):
    nb = load_notebook(notebook_file)
    staging_dir = infer_staging_dir()
    missing = []

    for cell in nb["cells"]:
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if not is_import_cell(source):
            continue
        for rel_path in extract_import_paths_from_source(source):
            staged = os.path.join(staging_dir, os.path.basename(rel_path))
            if not os.path.exists(staged):
                missing.append(rel_path)

    if missing:
        raise AssertionError(
            colored(
                " - FAIL: Notebook import files are not in DB Service staging dir "
                f"({staging_dir}): " + ", ".join(sorted(set(missing))),
                "red",
            )
        )


def validate_cell_runtime(client: str, op: str, idx: int, cell):
    one = {op: [(idx, op, cell)]}
    if client == "curl":
        from notebook_contracts_curl import validate_curl_operations

        validate_curl_operations(one)
    elif client == "python":
        from notebook_contracts_python import validate_python_operations

        validate_python_operations(one)
    elif client == "qclient":
        from notebook_contracts_qclient import validate_qclient_operations

        validate_qclient_operations(one)


def infer_notebook_endpoint(nb, default="http://localhost:8080"):
    pattern = re.compile(r"https?://[A-Za-z0-9._:-]+")
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        m = pattern.search(cell.get("source", ""))
        if m:
            return m.group(0)
    return default


def rewrite_notebook_endpoint(nb):
    endpoint = os.getenv("NOTEBOOK_IMPORT_STATUS_ENDPOINT")
    if not endpoint:
        return

    endpoint = endpoint.rstrip("/")
    bare_endpoint = re.sub(r"^https?://", "", endpoint)
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        source = source.replace("http://localhost:8080", endpoint)
        source = source.replace("localhost:8080", bare_endpoint)
        source = source.replace(
            "dbs.Session()",
            f'dbs.Session(endpoint="{bare_endpoint}")',
        )
        source = source.replace(
            "dbs.createSession[]",
            f'dbs.createSession([endpoint:"{bare_endpoint}"])',
        )
        cell["source"] = source


def harden_curl_notebook_http_errors(nb):
    """Make HTTP errors fail curl cells in the temporary executed notebook.

    The source notebook is left untouched. ``pipefail`` is required because
    the examples pipe curl output through jq or tee, which would otherwise
    hide curl's non-zero exit status.
    """
    prelude = 'set -o pipefail\ncurl() { command curl --fail-with-body "$@"; }\n'
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if not re.search(r"\bcurl\b", source) or not re.search(r"/api/v[0-9]+(?:/|\b)", source):
            continue
        tags = cell.get("metadata", {}).get("tags", [])
        if "validation-allow-http-error" in tags:
            continue
        first_line, separator, remainder = source.partition("\n")
        if first_line.strip().startswith("%%bash"):
            cell["source"] = first_line + "\n" + prelude + remainder
        else:
            cell["source"] = prelude + source


def execute_notebook(path: str):
    try:
        from nbclient import NotebookClient
    except Exception as exc:
        raise RuntimeError(
            "nbclient is required to execute notebooks. Install notebook test dependencies."
        ) from exc
    from notebook_contracts_python import capture_async_job_cell_source
    from notebook_contracts_qclient import capture_q_async_job_cell_source, capture_q_query_cell_source

    nb = load_notebook(path)
    rewrite_notebook_endpoint(nb)
    for source_cell_index, cell in enumerate(nb["cells"], start=1):
        if cell.get("cell_type") != "code":
            continue
        metadata = dict(cell.get("metadata", {}))
        metadata["test-source-cell-index"] = source_cell_index
        metadata["test-source-cell"] = cell.get("source", "")
        cell["metadata"] = metadata
    if os.path.basename(path) == "curl_notebook.ipynb":
        harden_curl_notebook_http_errors(nb)
    augmented_cells = []
    is_python_notebook = os.path.basename(path) == "python_notebook.ipynb"
    is_qclient_notebook = os.path.basename(path) == "qClient_notebook.ipynb"
    if is_python_notebook:
        augmented_cells.append(
            new_code_cell(
                source="""
# TEST_HELPER_CELL
import time

def _test_python_job_id(job):
    if isinstance(job, dict):
        return job.get("name") or job.get("job_id") or job.get("jobId") or job.get("id")
    return job

def _test_compact_failure_value(value, limit=260):
    if value in (None, "", [], {}, ()):
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_test_compact_failure_value(item, limit=limit) for item in value[:3]]
        parts = [part for part in parts if part]
        if not parts:
            return None
        suffix = " ..." if len(value) > 3 else ""
        text = "; ".join(parts) + suffix
        return text if len(text) <= limit else text[: limit - 3] + "..."
    if isinstance(value, dict):
        parts = []
        for key in ("error", "message", "text", "title", "code"):
            part = _test_compact_failure_value(value.get(key), limit=limit)
            if part:
                parts.append(f"{key}={part}")
        if parts:
            text = " ".join(parts)
        else:
            text = str(value)
        return text if len(text) <= limit else text[: limit - 3] + "..."
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."

def _test_find_first_key(data, keys):
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _test_find_first_key(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = _test_find_first_key(item, keys)
            if found not in (None, ""):
                return found
    return None

def _test_import_job_failure_message(job_id, state, status):
    detail = _test_compact_failure_value(_test_find_first_key(status, ("error", "message", "text", "title")))
    if not detail and isinstance(status, dict):
        warnings = _test_compact_failure_value(status.get("warnings"))
        details = _test_compact_failure_value(status.get("details"))
        if warnings:
            detail = f"warnings={warnings}"
        elif details:
            detail = f"details={details}"
    if not detail and status not in (None, {}, []):
        detail = f"payload={_test_compact_failure_value(status)}"
    if not detail:
        detail = "no error details returned by DB Service"
    return f" - FAIL: job failed: status={state or 'unknown'} job={job_id or '-'} error={detail}"

def _wait_for_import_job(session, job_name, timeout_s=120, poll_s=1):
    job_name = _test_python_job_id(job_name)
    if not job_name:
        raise RuntimeError(f"Could not determine import job id from: {job_name}")
    start = time.time()
    errored_observations = 0
    while True:
        status = session.get_import(job_id=job_name)
        raw_state = str(status.get("state") or status.get("status") or "").lower()
        state = raw_state.replace(" ", "").replace("-", "_")
        if state in {"completed", "complete", "success", "succeeded", "finished", "done"}:
            return status
        if state in {"failed", "error", "errored", "cancelled", "canceled"}:
            errored_observations += 1
            if errored_observations >= 2:
                raise RuntimeError(_test_import_job_failure_message(job_name, state, status))
            time.sleep(poll_s)
            continue
        if (time.time() - start) > timeout_s:
            raise TimeoutError(f" - FAIL: job timed out: status={raw_state or 'unknown'} job={job_name}")
        time.sleep(poll_s)
"""
                ,
                metadata={"tags": ["test-helper"]},
            )
        )
    q_endpoint = os.getenv(
        "NOTEBOOK_IMPORT_STATUS_ENDPOINT",
        infer_notebook_endpoint(nb, default="http://localhost:8080"),
    )
    q_wait_helper_source = f"""
# TEST_HELPER_CELL
import json
import time
import urllib.request

_TEST_Q_ENDPOINT = {q_endpoint!r}

{QCLIENT_QUERY_ERROR_HELPER_SOURCE}

def _test_q_to_python(value):
    try:
        value = value.py()
    except Exception:
        pass
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, (list, tuple)):
        normalized = [_test_q_to_python(item) for item in value]
        if normalized and all(isinstance(item, str) and len(item) == 1 for item in normalized):
            return "".join(normalized)
        return normalized
    return value

def _test_q_lookup(name, key):
    try:
        return _test_q_to_python(kx.q(f"string {{name}}`{{key}}"))
    except Exception:
        return None

def _test_extract_job_id(job):
    if isinstance(job, str):
        for key in ("jobId", "name", "job_id", "id"):
            job_id = _test_q_lookup(job, key)
            if job_id:
                return str(job_id)
    job = _test_q_to_python(job)
    if isinstance(job, dict):
        for key in ("jobId", "name", "job_id", "id"):
            if key in job and job[key]:
                return str(job[key])
    if isinstance(job, (str, int, float)):
        return str(job)
    raise RuntimeError(f"Could not determine import job id from: {{job}}")

def _test_compact_failure_value(value, limit=260):
    if value in (None, "", [], {{}}, ()):
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_test_compact_failure_value(item, limit=limit) for item in value[:3]]
        parts = [part for part in parts if part]
        if not parts:
            return None
        suffix = " ..." if len(value) > 3 else ""
        text = "; ".join(parts) + suffix
        return text if len(text) <= limit else text[: limit - 3] + "..."
    if isinstance(value, dict):
        parts = []
        for key in ("error", "message", "text", "title", "code"):
            part = _test_compact_failure_value(value.get(key), limit=limit)
            if part:
                parts.append("%s=%s" % (key, part))
        if parts:
            text = " ".join(parts)
        else:
            text = str(value)
        return text if len(text) <= limit else text[: limit - 3] + "..."
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."

def _test_find_first_key(data, keys):
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _test_find_first_key(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = _test_find_first_key(item, keys)
            if found not in (None, ""):
                return found
    return None

def _test_import_job_failure_message(job_id, state, status):
    detail = _test_compact_failure_value(_test_find_first_key(status, ("error", "message", "text", "title")))
    if not detail and isinstance(status, dict):
        warnings = _test_compact_failure_value(status.get("warnings"))
        details = _test_compact_failure_value(status.get("details"))
        if warnings:
            detail = "warnings=" + warnings
        elif details:
            detail = "details=" + details
    if not detail and status not in (None, {{}}, []):
        detail = "payload=" + (_test_compact_failure_value(status) or "unavailable")
    if not detail:
        detail = "no error details returned by DB Service"
    return " - FAIL: job failed: status=%s job=%s error=%s" % (state or "unknown", job_id or "-", detail)

def _test_job_status(job):
    if isinstance(job, str):
        state = _test_q_lookup(job, "status") or _test_q_lookup(job, "state")
        if state:
            return str(state).lower().replace(" ", "").replace("-", "_")
    job = _test_q_to_python(job)
    if isinstance(job, dict):
        state = job.get("status") or job.get("state")
        if state:
            return str(state).lower().replace(" ", "").replace("-", "_")
    return None

def _test_fetch_json(path):
    url = _TEST_Q_ENDPOINT.rstrip("/") + path
    req = urllib.request.Request(url, headers={{"Accept": "application/json"}})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _test_wait_for_import_job_rest(job, timeout_s=120, poll_s=1):
    initial_state = _test_job_status(job)
    if initial_state in {{"completed", "complete", "success", "succeeded", "finished", "done"}}:
        return {{"name": _test_extract_job_id(job), "status": initial_state}}
    if initial_state in {{"failed", "error", "errored", "cancelled", "canceled"}}:
        raise RuntimeError(_test_import_job_failure_message(_test_extract_job_id(job), initial_state, _test_q_to_python(job)))
    job_id = _test_extract_job_id(job)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = _test_fetch_json(f"/api/v0/imports/{{job_id}}")
        state = str(status.get("status") or status.get("state") or "").lower()
        state = state.replace(" ", "").replace("-", "_")
        if state in {{"completed", "complete", "success", "succeeded", "finished", "done"}}:
            return status
        if state in {{"failed", "error", "errored", "cancelled", "canceled"}}:
            raise RuntimeError(_test_import_job_failure_message(job_id, state, status))
        time.sleep(poll_s)
    raise TimeoutError(" - FAIL: job timed out: status=unknown job=%s" % job_id)

def _test_list_tables_rest():
    payload = _test_fetch_json("/api/v0/tables")
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("tables"), list):
        return payload["tables"]
    return []

def _test_q_value(name, expr):
    try:
        return _test_q_to_python(kx.q(f"{{expr}} {{name}}"))
    except Exception:
        return None

def _test_q_query_summary(name):
    result_type = _test_q_value(name, "type")
    rows = _test_q_value(name, "count")
    fields = _test_q_value(name, "cols")
    if isinstance(fields, str):
        fields = [fields]
    if isinstance(fields, list):
        fields = [str(field) for field in fields]
    _test_assert_q_query_success(
        result_type,
        fields,
        lambda key: _test_q_lookup(name, key),
    )
    return rows, fields
"""
    q_wait_helper_inserted = False
    for cell in nb["cells"]:
        if (
            is_python_notebook
            and cell.get("cell_type") == "code"
            and not is_test_helper_cell(cell)
        ):
            cell_index = len(augmented_cells)
            source, capture_name, _capture_op = capture_async_job_cell_source(
                cell.get("source", ""),
                cell_index,
            )
            if capture_name:
                table = extract_table_param_from_source(cell.get("source", ""))
                source_cell_index = cell.get("metadata", {}).get("test-source-cell-index", cell_index)
                source_cell = cell.get("metadata", {}).get("test-source-cell", cell.get("source", ""))
                helper_lines = [
                    "# TEST_HELPER_CELL",
                    f"_test_async_job = _test_python_job_id({capture_name})",
                    "_test_async_status = _wait_for_import_job(session, _test_async_job)",
                    "_test_async_state = _test_async_status.get('status') or _test_async_status.get('state') or 'completed'",
                ]
                if table:
                    helper_lines.extend(
                        [
                            f"_test_expected_table = {table!r}",
                            "if _test_expected_table not in session.list_tables():",
                            "    raise AssertionError(f\"Expected table {_test_expected_table} to exist after async job completed\")",
                            "print(f\"check: status={_test_async_state} table={_test_expected_table} job={_test_async_job}\")",
                        ]
                    )
                else:
                    helper_lines.append("print(f\"check: status={_test_async_state} job={_test_async_job}\")")
                cell["source"] = source
                metadata = dict(cell.get("metadata", {}))
                tags = list(metadata.get("tags", []))
                if "test-async-captured" not in tags:
                    tags.append("test-async-captured")
                metadata["tags"] = tags
                cell["metadata"] = metadata
                augmented_cells.append(cell)
                augmented_cells.append(
                    new_code_cell(
                        source="\n".join(helper_lines),
                        metadata={
                            "tags": ["test-helper"],
                            "test-helper-op": _capture_op,
                            "test-helper-display-index": source_cell_index,
                            "test-source-cell-index": source_cell_index,
                            "test-source-cell": source_cell,
                        },
                    )
                )
                continue
        if (
            is_qclient_notebook
            and cell.get("cell_type") == "code"
            and not q_wait_helper_inserted
            and "import pykx as kx" in cell.get("source", "")
        ):
            augmented_cells.append(cell)
            augmented_cells.append(
                new_code_cell(
                    source=q_wait_helper_source,
                    metadata={"tags": ["test-helper"]},
                )
            )
            q_wait_helper_inserted = True
            continue
        if (
            is_qclient_notebook
            and cell.get("cell_type") == "code"
            and not is_test_helper_cell(cell)
        ):
            cell_index = len(augmented_cells)
            source, capture_name, _capture_op = capture_q_async_job_cell_source(
                cell.get("source", ""),
                cell_index,
            )
            if capture_name:
                table = extract_table_param_from_source(cell.get("source", ""))
                source_cell_index = cell.get("metadata", {}).get("test-source-cell-index", cell_index)
                source_cell = cell.get("metadata", {}).get("test-source-cell", cell.get("source", ""))
                helper_lines = [
                    "# TEST_HELPER_CELL",
                    f"_test_async_status = _test_wait_for_import_job_rest({capture_name!r})",
                    "_test_async_job = _test_async_status.get('name') if isinstance(_test_async_status, dict) else None",
                    "_test_async_state = _test_async_status.get('status') if isinstance(_test_async_status, dict) else None",
                ]
                if table:
                    helper_lines.extend(
                        [
                            f"_test_expected_table = {table!r}",
                            "if _test_expected_table not in _test_list_tables_rest():",
                            "    raise AssertionError(f\"Expected table {_test_expected_table} to exist after async job completed\")",
                            "print(f\"check: status={_test_async_state} table={_test_expected_table} job={_test_async_job}\")",
                        ]
                    )
                cell["source"] = source
                metadata = dict(cell.get("metadata", {}))
                tags = list(metadata.get("tags", []))
                if "test-async-captured" not in tags:
                    tags.append("test-async-captured")
                metadata["tags"] = tags
                cell["metadata"] = metadata
                augmented_cells.append(cell)
                augmented_cells.append(
                    new_code_cell(
                        source="\n".join(helper_lines),
                        metadata={
                            "tags": ["test-helper"],
                            "test-helper-op": _capture_op,
                            "test-helper-display-index": source_cell_index,
                            "test-source-cell-index": source_cell_index,
                            "test-source-cell": source_cell,
                        },
                    )
                )
                continue
            source, capture_name, capture_op = capture_q_query_cell_source(
                cell.get("source", ""),
                cell_index,
            )
            if capture_name:
                source_cell_index = cell.get("metadata", {}).get("test-source-cell-index", cell_index)
                source_cell = cell.get("metadata", {}).get("test-source-cell", cell.get("source", ""))
                cell["source"] = source
                metadata = dict(cell.get("metadata", {}))
                tags = list(metadata.get("tags", []))
                if "test-query-captured" not in tags:
                    tags.append("test-query-captured")
                metadata["tags"] = tags
                cell["metadata"] = metadata
                augmented_cells.append(cell)
                augmented_cells.append(
                    new_code_cell(
                        source="\n".join(
                            [
                                "# TEST_HELPER_CELL",
                                f"_test_query_rows, _test_query_fields = _test_q_query_summary({capture_name!r})",
                                "if _test_query_rows is None:",
                                "    raise AssertionError('Expected q query result to expose a row count')",
                                "if not _test_query_fields:",
                                "    raise AssertionError('Expected q query result to expose column fields')",
                                "print(f\"check: response=q_table rows={_test_query_rows} fields={_test_query_fields}\")",
                            ]
                        ),
                        metadata={
                            "tags": ["test-helper"],
                            "test-helper-op": capture_op,
                            "test-helper-display-index": source_cell_index,
                            "test-helper-kind": "query",
                            "test-source-cell-index": source_cell_index,
                            "test-source-cell": source_cell,
                        },
                    )
                )
                continue
        augmented_cells.append(cell)
    nb["cells"] = augmented_cells

    client_name = detect_client(path)
    notebook_prefix = notebook_progress_prefix(path)
    runtime_state = {
        "import_jobs": [],
        "drop_precheck": {},
        "endpoint": os.getenv(
            "NOTEBOOK_IMPORT_STATUS_ENDPOINT",
            infer_notebook_endpoint(nb, default="http://localhost:8080"),
        ),
        "current_check": None,
        "current_cell": None,
        "check_details": {},
        "failure": None,
    }
    summary = {"passed": 0, "failed": 0, "skipped": 0}
    counted_checks = set()
    test_cases = []

    client_kwargs = {
        "timeout": 600,
        "kernel_name": "python3",
    }
    if not validation_verbose():
        client_kwargs["extra_arguments"] = ["--IPKernelApp.log_level=ERROR"]

    show_progress = should_show_notebook_progress()

    def _emit(msg):
        if show_progress:
            print(f"[{notebook_prefix}] {msg}", flush=True)

    def _check_key(idx, label):
        return f"{idx}:{label}"

    def _remember_check(idx, label, check):
        runtime_state["check_details"][_check_key(idx, label)] = str(check)
        if validation_verbose():
            _emit(f"cell {idx} ({label}) check: {check}")

    def _source_cell_index(cell, fallback="?"):
        metadata = cell.get("metadata", {})
        return metadata.get("test-source-cell-index", metadata.get("test-helper-display-index", fallback))

    def _source_cell_text(cell):
        return cell.get("metadata", {}).get("test-source-cell", cell.get("source", ""))

    def _remember_failure(idx, label, cell, error):
        runtime_state["failure"] = {
            "idx": idx,
            "label": label,
            "source": _source_cell_text(cell),
            "error": error,
        }

    def _case_name(idx, label):
        return f"cell {idx} ({label})"

    def _case_time(started_at=None):
        if not started_at:
            return 0.0
        return max(time.time() - started_at, 0.0)

    def _planned_checks():
        planned = []
        seen = set()
        for cell_index, cell in enumerate(nb["cells"]):
            if cell.get("cell_type") != "code":
                continue
            helper_op = cell.get("metadata", {}).get("test-helper-op")
            if helper_op:
                idx = _source_cell_index(cell, cell_index)
                label = operation_display_name(client_name, helper_op)
            elif is_test_helper_cell(cell):
                continue
            else:
                op = classify_operation(cell.get("source", ""))
                if not op:
                    continue
                idx = _source_cell_index(cell, cell_index)
                label = operation_display_name(client_name, op)
            key = _check_key(idx, label)
            if key in seen:
                continue
            seen.add(key)
            planned.append((idx, label))
        return planned

    expected_checks = _planned_checks()

    def _record_case(idx, label, status, message="", output="", failure_type="AssertionError"):
        key = _check_key(idx, label)
        if key in counted_checks:
            return False
        started_at = None
        current = runtime_state.get("current_check")
        if current and current[0] == idx and current[1] == label:
            started_at = current[2]
        test_cases.append(
            {
                "classname": notebook_prefix,
                "name": _case_name(idx, label),
                "status": status,
                "message": message,
                "output": output,
                "failure_type": failure_type,
                "time": _case_time(started_at),
            }
        )
        counted_checks.add(key)
        return True

    def _record_pass(idx, label, output=""):
        recorded = _record_case(idx, label, "passed", output=output)
        if recorded:
            summary["passed"] += 1
        runtime_state["current_check"] = None
        runtime_state["current_cell"] = None
        check = runtime_state["check_details"].pop(_check_key(idx, label), "")
        if not recorded:
            return
        if validation_verbose():
            _emit(f"cell {idx} ({label}) PASS")
        else:
            completed = len(counted_checks)
            detail = f" | {check}" if check else ""
            _emit(f"[{completed}/{len(expected_checks)}] PASS cell {idx} ({label}){detail}")

    def _record_fail(idx=None, label=None, message="", failure_type="AssertionError"):
        if idx is None or label is None:
            current = runtime_state.get("current_check")
            idx, label = (current[0], current[1]) if current else ("?", "unknown")
        if _record_case(idx, label, "failed", message=message, failure_type=failure_type):
            summary["failed"] += 1
        runtime_state["current_check"] = None

    def _record_unrun_checks(message="not run because notebook execution stopped before this cell"):
        for idx, label in expected_checks:
            key = _check_key(idx, label)
            if key in counted_checks:
                continue
            test_cases.append(
                {
                    "classname": notebook_prefix,
                    "name": _case_name(idx, label),
                    "status": "skipped",
                    "message": message,
                    "time": 0.0,
                }
            )
            counted_checks.add(key)
            summary["skipped"] += 1

    def _summary_line():
        total = summary["passed"] + summary["failed"] + summary["skipped"]
        return (
            f"summary: passed={summary['passed']} failed={summary['failed']} "
            f"skipped={summary['skipped']} total={total}"
        )

    def _parse_payload(cell):
        text = output_text(cell)
        if client_name == "curl":
            payload = parse_json_text(text)
            if payload is JSON_PARSE_ERROR:
                payload = {}
        elif client_name == "python":
            payload = parse_python_literal(text)
            if payload is None:
                payload = {}
        else:
            payload = {}
        return text, payload

    def _resolve_job_completion(op, idx, payload, text):
        job_id = extract_job_id(payload, text)
        if job_id and op in {"create_table", "import_file", "import_data", "import_database"}:
            runtime_state["import_jobs"].append(job_id)
        label = operation_display_name(client_name, op)
        return assert_job_completed_or_poll(
            op,
            idx,
            payload,
            text,
            runtime_state["endpoint"],
            fallback_jobs=runtime_state["import_jobs"],
            emit=_emit,
            display_op=label,
        )

    def _metadata_payload(text, payload):
        if client_name == "python":
            return payload
        if client_name == "qclient":
            return None
        if isinstance(payload, str):
            nested = parse_json_text(payload)
            if nested is not JSON_PARSE_ERROR:
                return nested
        return payload

    def _html_row_count(cell):
        html = html_output_text(cell)
        if not html:
            return None
        body_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, flags=re.IGNORECASE | re.DOTALL)
        target = body_match.group(1) if body_match else html
        rows = re.findall(r"<tr\b", target, flags=re.IGNORECASE)
        return len(rows) if rows else None

    def _html_fields(cell):
        html = html_output_text(cell)
        if not html:
            return []
        head_match = re.search(r"<thead[^>]*>(.*?)</thead>", html, flags=re.IGNORECASE | re.DOTALL)
        target = head_match.group(1) if head_match else html
        headers = re.findall(r"<th[^>]*>(.*?)</th>", target, flags=re.IGNORECASE | re.DOTALL)
        fields = []
        for header in headers:
            text = re.sub(r"<[^>]+>", "", header).strip()
            if text and text not in fields:
                fields.append(text)
        # Pandas includes a blank/index header before actual data columns.
        return [field for field in fields if field.lower() not in {"", "index"}]

    def _emit_cell_metadata(op, idx, label, source_text, text, payload, table, cell, drop_after=None):
        data = _metadata_payload(text, payload)
        if op == "list_tables":
            check = validation_check_text(
                op=op,
                client_name=client_name,
                payload=data,
                endpoint=runtime_state["endpoint"],
            )
            _remember_check(idx, label, check)
            return

        if op == "describe_table":
            table_name = table or extract_table_param_from_source(source_text)
            check = validation_check_text(
                op=op,
                client_name=client_name,
                payload=data,
                text=text,
                table=table_name,
                endpoint=runtime_state["endpoint"],
            )
            _remember_check(idx, label, check)
            return

        if op in {"create_table", "import_file", "import_data", "import_database"}:
            check = validation_check_text(
                op=op,
                client_name=client_name,
                payload=data,
                text=text,
                table=table,
                endpoint=runtime_state["endpoint"],
            )
            _remember_check(idx, label, check)
            return

        if op == "import_status":
            job_id = extract_job_id(data, text)
            if not job_id and runtime_state["import_jobs"]:
                job_id = runtime_state["import_jobs"][-1]
            check = validation_check_text(
                op=op,
                client_name=client_name,
                payload=data,
                text=text,
                endpoint=runtime_state["endpoint"],
                fallback_job_id=job_id,
            )
            _remember_check(idx, label, check)
            return

        if op in {"query_simple", "query_preview", "query_sql", "query_q"}:
            if client_name == "curl" and op in {"query_simple", "query_preview", "query_sql"}:
                if not isinstance(data, dict) or not isinstance(data.get("payload"), list):
                    keys = list(data.keys()) if isinstance(data, dict) else []
                    raise AssertionError(
                        colored(
                            f" - FAIL: cell {idx} ({label}) expected DB Service JSON envelope "
                            f"with a payload array. parsed_shape={query_shape(data)} "
                            f"keys={keys} output={short_text(text)}",
                            "red",
                        )
                    )
            rows = row_count(data)
            if rows is INCONSISTENT_ROWS:
                raise AssertionError(
                    colored(
                        f" - FAIL: cell {idx} ({label}) returned table fields with inconsistent row counts.",
                        "red",
                    )
                )
            if rows is None:
                rows = _html_row_count(cell)
            result_type = result_type_name(data, client_name, op, has_html_output(cell))
            rows_text = rows if rows is not None else "unknown"
            fields = payload_fields(data)
            if fields is INCONSISTENT_FIELDS:
                raise AssertionError(
                    colored(
                        f" - FAIL: cell {idx} ({label}) returned payload rows with inconsistent fields.",
                        "red",
                    )
                )
            if fields is None:
                fields = field_names(data)
            if fields is INCONSISTENT_FIELDS:
                raise AssertionError(
                    colored(
                        f" - FAIL: cell {idx} ({label}) returned rows with inconsistent fields.",
                        "red",
                    )
                )
            if not fields and has_html_output(cell):
                fields = _html_fields(cell)
            check = validation_check_text(
                op=op,
                client_name=client_name,
                payload=data,
                text=text,
                endpoint=runtime_state["endpoint"],
                has_html=has_html_output(cell),
                html_rows=rows,
                html_fields=fields,
            )
            _remember_check(idx, label, check)
            return

        if op == "drop_table":
            pre = runtime_state["drop_precheck"].get(idx)
            if pre:
                tbl, existed_before = pre
                after_present = bool(drop_after and tbl in drop_after)
                before = "present" if existed_before else "absent"
                after = "present" if after_present else "absent"
                check = validation_check_text(
                    op="drop_table_state",
                    client_name=client_name,
                    table=tbl,
                    drop_before=before,
                    drop_after=after,
                )
                _remember_check(idx, label, check)

    def _on_cell_start(**kwargs):
        cell = kwargs.get("cell", {})
        idx = _source_cell_index(cell, kwargs.get("cell_index", "?"))
        if cell.get("cell_type") != "code":
            return
        helper_op = cell.get("metadata", {}).get("test-helper-op")
        if helper_op:
            return
        if is_test_helper_cell(cell):
            return
        op = classify_operation(cell.get("source", "")) or "code"
        label = operation_display_name(client_name, op)
        if op == "drop_table":
            table = extract_table_param_from_source(cell.get("source", ""))
            if table:
                before_tables = list_tables_rest(runtime_state["endpoint"])
                runtime_state["drop_precheck"][idx] = (table, table in before_tables)
        if op != "code":
            runtime_state["current_check"] = (idx, label, time.time())
            runtime_state["current_cell"] = {
                "idx": idx,
                "label": label,
                "source": _source_cell_text(cell),
            }
        if validation_verbose():
            _emit(f"cell {idx} ({label}) RUN")

    def _on_cell_executed(**kwargs):
        cell = kwargs.get("cell", {})
        idx = _source_cell_index(cell, kwargs.get("cell_index", "?"))
        if cell.get("cell_type") != "code":
            return
        helper_op = cell.get("metadata", {}).get("test-helper-op")
        if helper_op:
            display_idx = _source_cell_index(cell, idx)
            label = operation_display_name(client_name, helper_op)
            if has_error_output(cell):
                error_text = output_text(cell)
                message = helper_failure_message(error_text, display_idx, label)
                _remember_failure(display_idx, label, cell, message)
                _record_fail(display_idx, label, message=message)
                raise AssertionError(colored(message, "red"))
            helper_text = output_text(cell)
            if helper_op in {"create_table", "import_file", "import_data", "import_database"}:
                job_id = extract_job_id({}, helper_text)
                if job_id and job_id not in runtime_state["import_jobs"]:
                    runtime_state["import_jobs"].append(job_id)
            for line in helper_text.splitlines():
                detail = line.strip()
                if detail.startswith("check:"):
                    _remember_check(display_idx, label, detail.removeprefix("check:").strip())
                elif detail and validation_verbose():
                    _emit(f"cell {display_idx} ({label}) {detail}")
            _record_pass(display_idx, label)
            return
        if is_test_helper_cell(cell):
            return

        op = classify_operation(cell.get("source", ""))
        if not op:
            if validation_verbose():
                _emit(f"cell {idx} (code) DONE")
            return
        label = operation_display_name(client_name, op)

        if has_error_output(cell):
            error_text = output_text(cell)
            message = (
                f"cell {idx} ({label}) produced an error output. "
                f"output={short_text(error_text)}"
            )
            _remember_failure(idx, label, cell, error_text)
            _record_fail(idx, label, message=message)
            raise AssertionError(
                colored(
                    f" - FAIL: {message}",
                    "red",
                )
            )

        if "validation-allow-http-error" in cell.get("metadata", {}).get("tags", []):
            _remember_check(idx, label, "intentional HTTP error allowed by cell metadata")
            _record_pass(idx, label, output=output_text(cell))
            return

        source_text = cell.get("source", "")
        text, payload = _parse_payload(cell)
        # Special handling for curl notebook placeholder status endpoint (/imports/NAME):
        # poll the actual last import job id instead of trusting placeholder output.
        if client_name == "curl" and op == "import_status" and "/imports/NAME" in source_text:
            if not runtime_state["import_jobs"]:
                raise AssertionError(
                    colored(
                        f" - FAIL: cell {idx} ({label}) uses /imports/NAME placeholder but no prior import job id was captured.",
                        "red",
                    )
                )
            poll_job = runtime_state["import_jobs"][-1]
            final_status, final_payload = poll_import_status_rest(
                job_id=poll_job,
                endpoint=runtime_state["endpoint"],
                timeout_s=60,
                poll_s=2,
            )
            if final_status != "completed":
                raise AssertionError(
                    colored(
                        import_job_failure_message(idx, label, poll_job, final_status, payload=final_payload),
                        "red",
                    )
                )
            _emit_cell_metadata(op, idx, label, source_text, text, final_payload, None, cell)
            _record_pass(idx, label)
            return

        validate_cell_runtime(client_name, op, idx, cell)
        is_async_captured = (
            "test-async-captured" in cell.get("metadata", {}).get("tags", [])
            and op in {"create_table", "import_file", "import_data", "import_database"}
        )
        if is_async_captured:
            final_job_status, final_job_payload = None, payload
        else:
            final_job_status, final_job_payload = _resolve_job_completion(op, idx, payload, text)
        table = extract_table_param_from_source(source_text)
        drop_tables_after = None
        if op == "create_table" and table and not is_async_captured:
            tables_after = list_tables_rest(runtime_state["endpoint"])
            if table not in tables_after:
                raise AssertionError(
                    colored(f" - FAIL: cell {idx} ({label}) did not result in table `{table}` being present.", "red")
                )
        if op in {"import_file", "import_data", "import_database"} and table and not is_async_captured:
            tables_after = list_tables_rest(runtime_state["endpoint"])
            if table not in tables_after:
                raise AssertionError(
                    colored(f" - FAIL: cell {idx} ({label}) expected table `{table}` to exist after import.", "red")
                )
        if op == "drop_table":
            pre = runtime_state["drop_precheck"].get(idx)
            if pre:
                tbl, existed_before = pre
                if not existed_before:
                    raise AssertionError(
                        colored(f" - FAIL: cell {idx} ({label}) expected `{tbl}` to exist before drop.", "red")
                    )
                drop_tables_after = list_tables_rest(runtime_state["endpoint"])
                if tbl in drop_tables_after:
                    raise AssertionError(
                        colored(f" - FAIL: cell {idx} ({label}) expected `{tbl}` to be absent after drop.", "red")
                    )

        if op in {"query_simple", "query_preview", "query_sql", "query_q"}:
            parsed = parse_python_literal(text) if client_name == "python" else parse_json_text(text)
            is_empty = False
            if isinstance(parsed, list) and len(parsed) == 0:
                is_empty = True
            if isinstance(parsed, dict) and len(parsed) == 0:
                is_empty = True
            if is_empty:
                _emit(f"cell {idx} ({label}) WARN: returned an empty table/result")
        metadata_payload = final_job_payload if final_job_status == "completed" else payload
        async_capture = is_async_captured
        qclient_query_capture = (
            client_name == "qclient"
            and "test-query-captured" in cell.get("metadata", {}).get("tags", [])
            and op in {"query_simple", "query_preview", "query_sql", "query_q"}
        )
        if not async_capture and not qclient_query_capture:
            _emit_cell_metadata(
                op,
                idx,
                label,
                source_text,
                text,
                metadata_payload,
                table,
                cell,
                drop_after=drop_tables_after,
            )
        if not async_capture and not qclient_query_capture:
            _record_pass(idx, label)

    def _on_cell_error(**kwargs):
        cell = kwargs.get("cell", {})
        idx = _source_cell_index(cell, kwargs.get("cell_index", "?"))
        helper_op = cell.get("metadata", {}).get("test-helper-op")
        if helper_op:
            op = helper_op
        else:
            op = classify_operation(cell.get("source", "")) or "code"
        label = operation_display_name(client_name, op)
        error_text = output_text(cell)
        _remember_failure(idx, label, cell, error_text)
        if op != "code":
            message = f"cell {idx} ({label}) failed during notebook execution. output={short_text(error_text)}"
            _record_fail(idx, label, message=message)
        if validation_verbose():
            _emit(f"cell {idx} ({label}) ERROR")

    client_kwargs.update(
        {
            "on_cell_start": _on_cell_start,
            "on_cell_executed": _on_cell_executed,
            "on_cell_error": _on_cell_error,
        }
    )

    def _write_executed_copy():
        os.makedirs("executed-notebook", exist_ok=True)
        executed_path = os.path.join(
            "executed-notebook", os.path.basename(path).replace(".ipynb", "_executed.ipynb")
        )
        with open(executed_path, "w", encoding="utf-8") as handle:
            nbformat.write(nb, handle)
        return executed_path

    client = NotebookClient(nb, **client_kwargs)
    try:
        client.execute()
    except Exception as exc:
        failure = runtime_state.get("failure") or runtime_state.get("current_cell") or {}
        failure_idx = failure.get("idx", "?")
        failure_label = failure.get("label", "unknown")
        failure_source = failure.get("source", "")
        failure_error = failure.get("error") or str(exc)
        if runtime_state.get("current_check") or summary["failed"] == 0:
            _record_fail(
                failure_idx,
                failure_label,
                message=short_text(failure_error, limit=1000),
                failure_type=type(exc).__name__,
            )
        _record_unrun_checks()
        out_path = _write_executed_copy()
        report_path = write_junit_report(notebook_prefix, test_cases, summary)
        _emit(_summary_line())
        raise NotebookValidationError(
            format_notebook_failure(
                path,
                failure_idx,
                failure_label,
                failure_source,
                failure_error,
                executed_file=out_path,
                report_file=report_path,
            )
        ) from None

    out_path = _write_executed_copy()
    report_path = write_junit_report(notebook_prefix, test_cases, summary)
    return {
        "path": out_path,
        "report_path": report_path,
        "summary": dict(summary),
        "summary_line": _summary_line(),
    }
