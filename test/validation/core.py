"""Shared DB Service validation core.

This module intentionally avoids notebook/Jupyter concepts. It contains the
operation names, parsing, REST polling, cleanup, and failure-detection helpers
that can be reused by notebooks, README examples, OpenAPI examples, generated
scenarios, and future collectors.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

JSON_PARSE_ERROR = object()
INCONSISTENT_ROWS = object()
INCONSISTENT_FIELDS = object()

try:
    from termcolor import colored
except Exception:

    def colored(text, *_args, **_kwargs):
        return text


def short_text(text: str, limit: int = 240):
    txt = (text or "").strip().replace("\n", " ")
    if len(txt) <= limit:
        return txt
    return txt[: limit - 3] + "..."


def validation_verbose() -> bool:
    return os.getenv("VERBOSE", "0").strip().lower() in {"1", "true", "yes", "on"}


def validation_report_dir() -> str:
    return os.getenv("VALIDATION_REPORT_DIR", "validation-reports")


def write_junit_report(name: str, test_cases: list[dict], summary: dict, report_dir: str | None = None) -> str:
    target_dir = report_dir or validation_report_dir()
    os.makedirs(target_dir, exist_ok=True)
    total = int(summary.get("passed", 0)) + int(summary.get("failed", 0)) + int(summary.get("skipped", 0))
    suite = ET.Element(
        "testsuite",
        {
            "name": name,
            "tests": str(total),
            "failures": str(summary.get("failed", 0)),
            "errors": str(summary.get("errors", 0)),
            "skipped": str(summary.get("skipped", 0)),
            "time": f"{sum(float(case.get('time', 0.0)) for case in test_cases):.3f}",
        },
    )
    for case in test_cases:
        testcase = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": str(case["classname"]),
                "name": str(case["name"]),
                "time": f"{float(case.get('time', 0.0)):.3f}",
            },
        )
        if case["status"] == "failed":
            failure = ET.SubElement(
                testcase,
                "failure",
                {
                    "message": short_text(str(case.get("message", "Validation check failed"))),
                    "type": str(case.get("failure_type", "AssertionError")),
                },
            )
            failure.text = str(case.get("message", "Validation check failed"))
        elif case["status"] == "skipped":
            skipped = ET.SubElement(testcase, "skipped")
            if case.get("message"):
                skipped.text = str(case["message"])
        if case.get("output"):
            system_out = ET.SubElement(testcase, "system-out")
            system_out.text = str(case["output"])

    filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "validation"
    path = os.path.join(target_dir, f"{filename}.xml")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def is_import_cell(source: str) -> bool:
    markers = [
        "import_files",
        "import_data",
        "import_database",
        "importFiles",
        "importData",
        "importDatabase",
        "/imports/",
    ]
    return any(marker in source for marker in markers)


def classify_operation(source: str):
    sl = source.lower()

    if "query_preview(" in source or "session.querypreview" in sl or "/api/v0/query/preview" in sl:
        return "query_preview"
    if "query_simple(" in source or "session.querysimple" in sl or "/api/v0/query/simple" in sl:
        return "query_simple"
    if "query_sql(" in source or "session.querysql" in sl or "/api/v0/query/sql" in sl:
        return "query_sql"
    if "query_q(" in source or "session.queryq" in sl or "/api/v0/query/q" in sl:
        return "query_q"

    if "import_files(" in source or "session.importfiles" in sl or "/api/v0/imports/files" in sl:
        return "import_file"
    if "import_data(" in source or "session.importdata" in sl or "/api/v0/imports/data" in sl:
        return "import_data"
    if (
        "import_database(" in source
        or "session.importdatabase" in sl
        or "session.importkdb" in sl
        or "/api/v0/imports/database" in sl
        or "/api/v0/imports/kdb" in sl
    ):
        return "import_database"
    if "get_import(" in source or "session.getimport" in sl or re.search(r"/api/v0/imports/[^\"\s]+", sl):
        return "import_status"

    if "delete_rows(" in source or "session.deleterows" in sl or (
        "-x post" in sl and "/api/v0/deletes" in sl
    ):
        return "delete_start"
    if "get_delete(" in source or "session.getdelete" in sl or (
        "-x get" in sl and re.search(r"/api/v0/deletes/[^\"\s]+", sl)
    ):
        return "delete_status"
    if "cancel_delete(" in source or "session.canceldelete" in sl or (
        "-x delete" in sl and re.search(r"/api/v0/deletes/[^\"\s]+", sl)
    ):
        return "delete_clear"

    if "drop_table(" in source or "session.droptable" in sl or (
        "-x delete" in sl and "/api/v0/tables/" in sl
    ):
        return "drop_table"
    if "create_table(" in source or "session.createtable" in sl or (
        "-x post" in sl and "/api/v0/tables/" in sl
    ):
        return "create_table"
    if "describe_table(" in source or "session.describetable" in sl or (
        "-x get" in sl and "/api/v0/tables/" in sl
    ):
        return "describe_table"
    if "list_tables(" in source or "session.listtables" in sl or (
        "-x get" in sl and "/api/v0/tables" in sl
    ):
        return "list_tables"

    # Keep new DB Service endpoints visible even before operation-specific
    # validation has been added for them.
    if re.search(r"/api/v[0-9]+(?:/|\b)", sl):
        return "api_call"

    return None


def operation_display_name(client: str, op: str) -> str:
    names = {
        "python": {
            "list_tables": "list_tables",
            "create_table": "create_table",
            "describe_table": "describe_table",
            "import_file": "import_files",
            "import_data": "import_data",
            "import_database": "import_database",
            "import_status": "get_import",
            "query_simple": "query_simple",
            "query_preview": "query_preview",
            "query_sql": "query_sql",
            "query_q": "query_q",
            "drop_table": "drop_table",
            "delete_start": "delete_rows",
            "delete_status": "get_delete",
            "delete_clear": "cancel_delete",
        },
        "qclient": {
            "list_tables": "listTables",
            "create_table": "createTable",
            "describe_table": "describeTable",
            "import_file": "importFiles",
            "import_data": "importData",
            "import_database": "importDatabase",
            "import_status": "getImport",
            "query_simple": "querySimple",
            "query_preview": "queryPreview",
            "query_sql": "querySQL",
            "query_q": "queryQ",
            "drop_table": "dropTable",
            "delete_start": "deleteRows",
            "delete_status": "getDelete",
            "delete_clear": "cancelDelete",
        },
        "curl": {
            "list_tables": "GET /api/v0/tables",
            "create_table": "POST /api/v0/tables/{table}",
            "describe_table": "GET /api/v0/tables/{table}",
            "import_file": "POST /api/v0/imports/files",
            "import_data": "POST /api/v0/imports/data",
            "import_database": "POST /api/v0/imports/database",
            "import_status": "GET /api/v0/imports/{job}",
            "query_simple": "POST /api/v0/query/simple",
            "query_preview": "POST /api/v0/query/preview",
            "query_sql": "POST /api/v0/query/sql",
            "query_q": "POST /api/v0/query/q",
            "drop_table": "DELETE /api/v0/tables/{table}",
            "delete_start": "POST /api/v0/deletes",
            "delete_status": "GET /api/v0/deletes/{job}",
            "delete_clear": "DELETE /api/v0/deletes/{job}",
        },
    }
    return names.get(client, {}).get(op, op)


def parse_json_text(text: str):
    text = text.strip()
    if not text:
        return JSON_PARSE_ERROR
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass

    def _balanced_json_from(start):
        if start < 0 or start >= len(text) or text[start] not in "{[":
            return None
        stack = []
        in_string = False
        escaped = False
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if not stack or ch != stack.pop():
                    return None
                if not stack:
                    try:
                        return json.loads(text[start:pos + 1])
                    except Exception:
                        return None
        return None

    payload_pos = text.find('"payload"')
    if payload_pos != -1:
        start = text.rfind("{", 0, payload_pos)
        while start != -1:
            candidate = _balanced_json_from(start)
            if isinstance(candidate, dict) and isinstance(candidate.get("payload"), list):
                return candidate
            start = text.rfind("{", 0, start)

    decoder = json.JSONDecoder()
    candidates = []
    for pos, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            payload, end = decoder.raw_decode(text[pos:])
            candidates.append((payload, end))
        except Exception:
            continue

    for payload, _end in candidates:
        if isinstance(payload, dict) and isinstance(payload.get("payload"), list):
            return payload
    for payload, _end in candidates:
        if isinstance(payload, dict) and "payload" in payload:
            return payload
    if candidates:
        return max(candidates, key=lambda item: item[1])[0]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line_candidates = []
    for line in lines:
        try:
            line_candidates.append(json.loads(line))
        except Exception:
            continue
    for payload in line_candidates:
        if isinstance(payload, dict) and isinstance(payload.get("payload"), list):
            return payload
    if line_candidates:
        return line_candidates[0]
    return JSON_PARSE_ERROR


def parse_python_literal(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return ast.literal_eval(text)
    except Exception:
        return None


def extract_import_paths_from_source(source: str):
    paths = []

    for m in re.finditer(r"path\s*=\s*[\"']([^\"']+)[\"']", source):
        paths.append(m.group(1))
    for m in re.finditer(r'"path"\s*:\s*"([^"]+)"', source):
        paths.append(m.group(1))
    for m in re.finditer(r'path!\([^;]+;"([^"]+)"\)', source):
        paths.append(m.group(1))
    if is_import_cell(source):
        for m in re.finditer(r"[\"']([^\"']+\.(?:csv(?:\.gz)?|parquet|json))[\"']", source):
            paths.append(m.group(1))

    deduped = []
    seen = set()
    for path in paths:
        if "://" in path or path.startswith("/"):
            continue
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def extract_table_param_from_source(source: str):
    """Return a literal table name used by a runnable example, when available.

    The harness uses this only for state checks and targeted pre-test cleanup.
    It supports the public Python, curl, and qClient call shapes; callers must
    still tolerate ``None`` because examples may calculate a table dynamically.
    """
    patterns = [
        r"table\s*=\s*[\"']([^\"']+)[\"']",
        r'"table"\s*:\s*"([^"]+)"',
        r"/tables/([A-Za-z0-9_.-]+)",
        r"\btable\s*:\s*`([A-Za-z0-9_.-]+)",
        r'\btable\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, source)
        if m:
            table = (m.group(1) or "").strip()
            if table and not table.lower().startswith("http"):
                return table
    table = extract_q_positional_table_param(source)
    if table:
        return table
    table = extract_q_bang_dict_table_param(source)
    if table:
        return table
    return None


def extract_q_positional_table_param(source: str):
    """Extract the first positional table argument from table-oriented q APIs.

    qClient's variadic API takes the table as its first argument, for example
    ``session.createTable[`fxquote; options]``. Requiring the argument to end
    at ``;`` or ``]`` prevents a keyed dictionary such as
    ```table`type!(...)`` from being mistaken for the table named ``table``.
    """
    table_methods = (
        "createTable",
        "describeTable",
        "dropTable",
        "importFiles",
        "importData",
        "querySimple",
        "queryPreview",
        "deleteRows",
    )
    methods = "|".join(table_methods)
    match = re.search(
        rf"\bsession\.(?:{methods})\s*\[\s*(?P<table>`[A-Za-z0-9_.-]+|\"[^\"]+\")\s*(?=;|\])",
        source,
    )
    return extract_q_literal_table_name(match.group("table")) if match else None


def extract_q_bang_dict_table_param(source: str):
    for match in re.finditer(r"((?:`[A-Za-z_][A-Za-z0-9_]*)+)!\(", source):
        keys = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)", match.group(1))
        if "table" not in keys:
            continue

        end = find_matching_q_paren(source, match.end() - 1)
        if end is None:
            continue

        values = split_q_tuple_values(source[match.end():end])
        table_index = keys.index("table")
        if table_index >= len(values):
            continue

        table = extract_q_literal_table_name(values[table_index])
        if table and not table.lower().startswith("http"):
            return table
    return None


def find_matching_q_paren(source: str, open_index: int):
    depth = 0
    in_string = False
    idx = open_index
    while idx < len(source):
        char = source[idx]
        if in_string:
            if char == '"':
                if idx + 1 < len(source) and source[idx + 1] == '"':
                    idx += 1
                else:
                    in_string = False
        elif char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return None


def split_q_tuple_values(values: str):
    parts = []
    start = 0
    depth = 0
    in_string = False
    idx = 0
    while idx < len(values):
        char = values[idx]
        if in_string:
            if char == '"':
                if idx + 1 < len(values) and values[idx + 1] == '"':
                    idx += 1
                else:
                    in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ";" and depth == 0:
            parts.append(values[start:idx].strip())
            start = idx + 1
        idx += 1
    parts.append(values[start:].strip())
    return parts


def extract_q_literal_table_name(value: str):
    value = value.strip()
    match = re.match(r"`([A-Za-z0-9_.-]+)\b", value)
    if match:
        return match.group(1)
    match = re.match(r'"([^"]+)"', value)
    if match:
        return match.group(1)
    return None


def normalize_import_status(value):
    if value is None:
        return None
    token = str(value).strip().lower()
    if not token:
        return None
    pending = {"pending", "queued", "running", "processing", "inprogress", "in_progress", "submitted"}
    completed = {"completed", "complete", "success", "succeeded", "finished", "done"}
    errored = {"errored", "error", "failed", "failure", "cancelled", "canceled"}

    token_norm = token.replace(" ", "").replace("-", "_")
    if token in pending or token_norm in pending:
        return "pending"
    if token in completed or token_norm in completed:
        return "completed"
    if token in errored or token_norm in errored:
        return "errored"
    return None


def find_first_key(data, keys):
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = find_first_key(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_first_key(item, keys)
            if found not in (None, ""):
                return found
    return None


def extract_job_id(payload, text: str):
    job = find_first_key(payload, ["job_id", "jobId", "name", "id", "job"])
    if isinstance(job, (str, int, float)):
        return str(job)

    text = text or ""
    m = re.search(r'"(?:name|id|job_id|jobId|job)"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r"(?:^|\s)job=([^\s]+)", text)
    if m:
        job_id = m.group(1).strip(".,;")
        if job_id.lower() not in {"none", "null", "-"}:
            return job_id
    return None


def extract_import_status(payload, text: str):
    raw = find_first_key(payload, ["status", "state"])
    normalized = normalize_import_status(raw)
    if normalized:
        return normalized

    lower = (text or "").lower()
    for token in ["completed", "complete", "success", "succeeded", "finished", "done"]:
        if token in lower:
            return "completed"
    for token in ["pending", "queued", "running", "processing", "submitted", "in_progress"]:
        if token in lower:
            return "pending"
    for token in ["errored", "error", "failed", "failure", "cancelled", "canceled"]:
        if token in lower:
            return "errored"
    return None


def payload_indicates_failure(payload, text: str) -> bool:
    lower_text = (text or "").lower()

    status = extract_import_status(payload, text)
    if status in {"pending", "completed"}:
        return False
    if status == "errored":
        return True

    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, int) and code >= 400:
            return True
        if isinstance(code, str) and code.isdigit() and int(code) >= 400:
            return True

        err = payload.get("error")
        if err not in (None, "", [], {}, ()):
            return True

    if any(tok in lower_text for tok in ["not found", "no such file", "unknown table", "traceback", "exception"]):
        return True
    if re.search(r"\b(failed|failure|errored|cancelled|canceled)\b", lower_text):
        return True

    return False


def _compact_failure_value(value, limit: int = 260):
    if value in (None, "", [], {}, ()):
        return None
    if isinstance(value, str):
        return short_text(" ".join(value.split()), limit=limit)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_compact_failure_value(item, limit=limit) for item in value[:3]]
        parts = [part for part in parts if part]
        if not parts:
            return None
        suffix = " ..." if len(value) > 3 else ""
        return short_text("; ".join(parts) + suffix, limit=limit)
    if isinstance(value, dict):
        parts = []
        for key in ("error", "message", "text", "title", "code"):
            part = _compact_failure_value(value.get(key), limit=limit)
            if part:
                parts.append(f"{key}={part}")
        if parts:
            return short_text(" ".join(parts), limit=limit)
        return short_text(json.dumps(value, default=str), limit=limit)
    return short_text(str(value), limit=limit)


def import_job_failure_message(idx, label, job_id, status, payload=None, text: str = ""):
    detail = find_first_key(payload, ["error", "message", "text", "title"]) if payload is not None else None
    detail = _compact_failure_value(detail)
    if not detail and isinstance(payload, dict):
        warnings = _compact_failure_value(payload.get("warnings"))
        details = _compact_failure_value(payload.get("details"))
        if warnings:
            detail = f"warnings={warnings}"
        elif details:
            detail = f"details={details}"
    if not detail and text:
        detail = _compact_failure_value(text)
    if not detail and payload not in (None, {}, []):
        detail = f"payload={_compact_failure_value(payload)}"
    if not detail:
        detail = "no error details returned by DB Service"
    return f" - FAIL: cell {idx} ({label}) job failed: status={status or 'unknown'} job={job_id or '-'} error={detail}"


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    parsed = parse_json_text(body)
    if parsed is JSON_PARSE_ERROR:
        return {}
    return parsed


def list_tables_rest(endpoint: str):
    url = endpoint.rstrip("/") + "/api/v0/tables"
    payload = _fetch_json(url)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        tables = payload.get("tables")
        if isinstance(tables, list):
            return tables
    return []


def poll_import_status_rest(job_id: str, endpoint: str, timeout_s=60, poll_s=2):
    if not job_id:
        return None, None

    deadline = time.time() + timeout_s
    last_payload = None
    while time.time() < deadline:
        url = endpoint.rstrip("/") + f"/api/v0/imports/{job_id}"
        try:
            payload = _fetch_json(url)
        except urllib.error.HTTPError as exc:
            payload = {"code": exc.code, "error": str(exc)}
        except Exception as exc:
            payload = {"error": str(exc)}

        last_payload = payload
        status = extract_import_status(payload, json.dumps(payload))
        if status == "completed":
            return "completed", payload
        if status == "errored":
            time.sleep(poll_s)
            continue
        time.sleep(poll_s)

    final_status = extract_import_status(last_payload or {}, json.dumps(last_payload or {}))
    return final_status or "unknown", last_payload


def assert_job_completed_or_poll(op, idx, payload, text, endpoint, fallback_jobs=None, emit=None, display_op=None):
    if op not in {"create_table", "import_file", "import_data", "import_database", "import_status"}:
        return None, payload

    label = display_op or op
    job_id = extract_job_id(payload, text)
    status = extract_import_status(payload, text)

    if status == "completed":
        return "completed", payload

    if status == "errored":
        raise AssertionError(
            colored(
                import_job_failure_message(idx, label, job_id, status, payload=payload, text=text),
                "red",
            )
        )

    poll_job = job_id or (fallback_jobs[-1] if fallback_jobs else None)
    if not poll_job:
        if op == "import_status":
            raise AssertionError(
                colored(f" - FAIL: cell {idx} ({label}) had no job id to poll for terminal status.", "red")
            )
        return status, payload

    final_status, final_payload = poll_import_status_rest(
        job_id=str(poll_job),
        endpoint=endpoint,
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
    return final_status, final_payload


def reset_dbservice_tables(endpoint=None, tables=None, show_progress=False, notebook_name="db-service"):
    """Delete tables between test batches without assuming their names.

    By default the current table list is read from DB Service. Supplying
    ``tables`` keeps targeted cleanup available to callers that know exactly
    which state they own.
    """
    base = endpoint or os.getenv("NOTEBOOK_IMPORT_STATUS_ENDPOINT", "http://localhost:8080")
    if tables is None:
        try:
            tables = list_tables_rest(base)
        except Exception as exc:
            if show_progress:
                print(f"[{notebook_name}] cleanup list tables err={exc}", flush=True)
            return
    for table in tables:
        url = base.rstrip("/") + f"/api/v0/tables/{table}"
        req = urllib.request.Request(url, method="DELETE", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10):
                if show_progress:
                    print(f"[{notebook_name}] cleanup dropped table={table}", flush=True)
        except urllib.error.HTTPError as exc:
            if show_progress and exc.code != 404:
                print(f"[{notebook_name}] cleanup delete table={table} http={exc.code}", flush=True)
        except Exception as exc:
            if show_progress:
                print(f"[{notebook_name}] cleanup delete table={table} err={exc}", flush=True)


def dict_has_numeric_keys(data):
    return bool(data) and all(str(key).isdigit() for key in data.keys())


def row_count(data):
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        payload = data.get("payload")
        if isinstance(payload, list):
            return len(payload)
        if dict_has_numeric_keys(data):
            return len(data)
        for key in ("rows", "data", "result", "records"):
            value = data.get(key)
            if key == "rows" and isinstance(value, int):
                return value
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                nested_count = row_count(value)
                if nested_count is not None:
                    return nested_count
        columns = data.get("columns")
        if isinstance(columns, dict):
            lengths = [len(v) for v in columns.values() if isinstance(v, list)]
            if lengths:
                return lengths[0] if len(set(lengths)) == 1 else INCONSISTENT_ROWS
        column_lengths = [len(v) for v in data.values() if isinstance(v, list)]
        if column_lengths:
            return column_lengths[0] if len(set(column_lengths)) == 1 else INCONSISTENT_ROWS
        dict_values = [v for v in data.values() if isinstance(v, dict)]
        if dict_values and len(dict_values) == len(data):
            if dict_has_numeric_keys(data):
                return len(data)
            nested_counts = [row_count(v) for v in dict_values]
            if INCONSISTENT_ROWS in nested_counts:
                return INCONSISTENT_ROWS
            nested_counts = [count for count in nested_counts if count is not None]
            if nested_counts:
                return nested_counts[0] if len(set(nested_counts)) == 1 else INCONSISTENT_ROWS
    return None


def query_shape(data):
    if isinstance(data, dict) and isinstance(data.get("payload"), list):
        return "header_payload"
    if isinstance(data, list):
        return "row_array"
    if isinstance(data, dict):
        if any(isinstance(v, list) for v in data.values()):
            return "column_object"
        if any(isinstance(v, dict) for v in data.values()):
            return "nested_object"
        return "scalar_object"
    return type(data).__name__ if data is not JSON_PARSE_ERROR else "unknown"


def payload_fields(data):
    if not isinstance(data, dict):
        return None
    payload = data.get("payload")
    if not isinstance(payload, list):
        return None
    rows = [row for row in payload if isinstance(row, dict)]
    if not rows:
        return []
    fields = list(rows[0].keys())
    expected = set(fields)
    if any(set(row.keys()) != expected for row in rows[1:]):
        return INCONSISTENT_FIELDS
    return fields


def field_names(data):
    if isinstance(data, list) and data and all(isinstance(row, dict) for row in data):
        fields = list(data[0].keys())
        expected = set(fields)
        if any(set(row.keys()) != expected for row in data[1:]):
            return INCONSISTENT_FIELDS
        return fields
    if not isinstance(data, dict):
        return []
    columns = data.get("columns")
    if isinstance(columns, list):
        names = []
        for column in columns:
            if isinstance(column, dict) and column.get("name"):
                names.append(str(column["name"]))
            elif isinstance(column, str):
                names.append(column)
        if names:
            return names
    for key in ("data", "rows", "result", "records"):
        value = data.get(key)
        if isinstance(value, dict):
            return list(value.keys())
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return list(value[0].keys())
    fields = data.get("fields")
    if isinstance(fields, list):
        return fields
    return list(data.keys())


def result_type_name(data, client_name: str, op: str, has_html=False):
    if client_name == "curl":
        if isinstance(data, dict) and isinstance(data.get("payload"), list):
            return "JSON object (header, payload)"
        if isinstance(data, dict):
            return "JSON object"
        if isinstance(data, list):
            return "JSON array"
        if isinstance(data, str):
            return "JSON string"
        if isinstance(data, bool):
            return "JSON boolean"
        if isinstance(data, (int, float)):
            return "JSON number"
        if data is None:
            return "JSON null"
        if data is JSON_PARSE_ERROR:
            return "unknown"
    if client_name == "python" and has_html:
        return "dataframe"
    if client_name == "qclient" and op in {"query_simple", "query_preview", "query_sql", "query_q"}:
        return "q_table"
    return type(data).__name__ if data is not JSON_PARSE_ERROR else "unknown"


def validation_check_text(
    *,
    op: str,
    client_name: str,
    payload=None,
    text: str = "",
    table: str | None = None,
    endpoint: str | None = None,
    fallback_job_id: str | None = None,
    status_override: str | None = None,
    drop_before: str | None = None,
    drop_after: str | None = None,
    has_html: bool = False,
    html_rows=None,
    html_fields=None,
) -> str:
    """Return a consistent progress check string by DB Service API operation type."""
    if op == "list_tables":
        tables = payload if isinstance(payload, list) else list_tables_rest(endpoint or "http://localhost:8080")
        return f"tables={len(tables)} names={tables}"

    if op == "describe_table":
        schema = payload if isinstance(payload, dict) else {}
        columns = schema.get("columns") if isinstance(schema, dict) else None
        table_type = schema.get("type") if isinstance(schema, dict) else None
        column_count = len(columns) if isinstance(columns, list) else "unknown"
        return f"table={table or 'unknown'} type={table_type or 'unknown'} columns={column_count}"

    if op in {"create_table", "import_file", "import_data", "import_database", "drop_table"}:
        job_id = extract_job_id(payload, text) or fallback_job_id
        status = status_override or extract_import_status(payload, text) or "completed"
        parts = [f"status={status}"]
        if table:
            parts.append(f"table={table}")
        if job_id:
            parts.append(f"job={job_id}")
        return " ".join(parts)

    if op == "import_status":
        job_id = extract_job_id(payload, text) or fallback_job_id
        status = status_override or extract_import_status(payload, text) or "unknown"
        parts = [f"status={status}"]
        if job_id:
            parts.append(f"job={job_id}")
        return " ".join(parts)

    if op in {"query_simple", "query_preview", "query_sql", "query_q"}:
        rows = row_count(payload)
        if rows is None:
            rows = html_rows
        response = result_type_name(payload, client_name, op, has_html)
        rows_text = rows if rows is not None else "unknown"
        fields = payload_fields(payload)
        if fields is None:
            fields = field_names(payload)
        if not fields:
            fields = html_fields
        fields_text = f" fields={fields}" if fields else ""
        return f"response={response} rows={rows_text}{fields_text}"

    if op == "drop_table_state":
        parts = []
        if table:
            parts.append(f"table={table}")
        if drop_before:
            parts.append(f"before={drop_before}")
        if drop_after:
            parts.append(f"after={drop_after}")
        return " ".join(parts) if parts else "status=completed"

    return "status=completed"
