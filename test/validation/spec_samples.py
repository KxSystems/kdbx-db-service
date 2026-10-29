"""Collect request/code samples embedded in the OpenAPI spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import yaml

from core import (
    INCONSISTENT_FIELDS,
    INCONSISTENT_ROWS,
    JSON_PARSE_ERROR,
    extract_import_paths_from_source,
    extract_import_status,
    extract_job_id,
    extract_table_param_from_source,
    find_matching_q_paren,
    field_names,
    find_first_key,
    list_tables_rest,
    parse_json_text,
    payload_indicates_failure,
    payload_fields,
    reset_dbservice_tables,
    row_count,
    short_text,
    split_q_tuple_values,
    validation_check_text,
    validation_verbose,
    write_junit_report,
)


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

CLIENT_ALIASES = {
    "curl": "curl",
    "python": "python",
    "py": "python",
    "q": "qclient",
    "qclient": "qclient",
    "q_client": "qclient",
    "q-client": "qclient",
}


@dataclass(frozen=True)
class SpecSample:
    method: str
    path: str
    operation_id: str
    tags: tuple[str, ...]
    client: str
    label: str
    source: str

    @property
    def operation_key(self) -> str:
        return self.operation_id or f"{self.method} {self.path}"


DELETE_START_OPERATION = "deleteStart"
DELETE_STATUS_OPERATION = "deleteStatus"
DELETE_CLEAR_OPERATION = "deleteDelete"
DELETE_JOB_EXAMPLE_ID = "54f5cf87-dbc2-4336-d109-173ed56c0b86"


@dataclass(frozen=True)
class DeleteScenario:
    """Client-neutral prerequisites extracted from one delete-start example."""

    table: str
    assembly: str | None = None
    start_ts: str | None = None
    end_ts: str | None = None
    filter_values: tuple[tuple[str, Any], ...] = ()


@dataclass
class DeleteWorkflowState:
    """Delete jobs and fixtures kept separately for each DB Service assembly."""

    jobs: dict[str, str] = field(default_factory=dict)
    fixtures: dict[tuple[str, str], DeleteScenario] = field(default_factory=dict)

    @staticmethod
    def key(assembly: str | None) -> str:
        return assembly or "__default__"

    def remember(self, scenario: DeleteScenario, job_id: str) -> None:
        key = self.key(scenario.assembly)
        self.track_fixture(scenario)
        self.jobs[key] = job_id

    def track_fixture(self, scenario: DeleteScenario) -> None:
        self.fixtures[(self.key(scenario.assembly), scenario.table)] = scenario

    def job_for(self, assembly: str | None) -> str | None:
        return self.jobs.get(self.key(assembly))

    def all_fixtures(self) -> list[DeleteScenario]:
        return list(self.fixtures.values())


def _python_delete_call_values(source: str) -> dict[str, Any]:
    try:
        module = ast.parse(source)
    except SyntaxError:
        return {}

    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr.lower().replace("_", "") != "deleterows":
            continue
        values: dict[str, Any] = {}
        if node.args:
            try:
                values["table"] = ast.literal_eval(node.args[0])
            except (ValueError, TypeError):
                pass
        for keyword in node.keywords:
            if not keyword.arg:
                continue
            try:
                values[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                continue
        return values
    return {}


def _curl_json_body(source: str) -> dict[str, Any]:
    match = re.search(
        r"(?:^|\s)(?:-d|--data(?:-raw)?)\s+'(\{.*?\})'",
        source,
        flags=re.DOTALL,
    )
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_assembly(source: str, client: str) -> str | None:
    if client == "python":
        values = _python_delete_call_values(source)
        assembly = values.get("assembly")
        if isinstance(assembly, str) and assembly:
            return assembly
    if client == "qclient":
        assembly = _q_bang_dict_values(source).get("assembly", "").strip()
        if assembly.startswith('"') and assembly.endswith('"'):
            return assembly[1:-1]
        if assembly.startswith("`"):
            return assembly[1:]

    patterns = (
        r"[?&]assembly=([A-Za-z0-9_.-]+)",
        r"\bassembly\s*=\s*[\"']([^\"']+)[\"']",
        r'\bassembly\s*:\s*"([^"]+)"',
        r'"assembly"\s*:\s*"([^"]+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return None


def _filter_values(value: Any) -> tuple[tuple[str, Any], ...]:
    found: list[tuple[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            if len(item) >= 3 and isinstance(item[1], str):
                column = item[1]
                if column and column not in {name for name, _ in found}:
                    found.append((column, item[2]))
                return
            for child in item:
                visit(child)

    visit(value)
    return tuple(found)


def _q_bang_dict_values(source: str) -> dict[str, str]:
    for match in re.finditer(r"((?:`[A-Za-z_][A-Za-z0-9_]*)+)!\(", source):
        keys = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)", match.group(1))
        end = find_matching_q_paren(source, match.end() - 1)
        if end is None:
            continue
        values = split_q_tuple_values(source[match.end():end])
        if len(values) < len(keys):
            continue
        return dict(zip(keys, values))
    return {}


def _q_filter_values(source: str) -> tuple[tuple[str, Any], ...]:
    filter_match = re.search(
        r'(?:enlist\s*)?\(\s*"[^"]+"\s*;\s*(?:"([^"]+)"|`([A-Za-z0-9_.-]+))\s*;\s*("[^"]*"|`[A-Za-z0-9_.-]+|-?\d+(?:\.\d+)?)\s*\)',
        source,
    )
    if not filter_match:
        return ()
    raw_value = filter_match.group(3)
    if raw_value.startswith('"'):
        value: Any = raw_value[1:-1]
    elif raw_value.startswith("`"):
        value = raw_value[1:]
    elif "." in raw_value:
        value = float(raw_value)
    else:
        value = int(raw_value)
    return ((filter_match.group(1) or filter_match.group(2), value),)


def _q_delete_values(source: str) -> tuple[str | None, str | None, tuple[tuple[str, Any], ...]]:
    start_match = re.search(r"\bstartTS\s*:\s*([^;\])]+)", source)
    end_match = re.search(r"\bendTS\s*:\s*([^;\])]+)", source)
    bang_values = _q_bang_dict_values(source)
    start_ts = start_match.group(1).strip() if start_match else bang_values.get("startTS")
    end_ts = end_match.group(1).strip() if end_match else bang_values.get("endTS")
    filter_source = bang_values.get("filter", source)
    return (
        start_ts,
        end_ts,
        _q_filter_values(filter_source),
    )


def extract_delete_scenario(sample: SpecSample) -> DeleteScenario | None:
    """Extract a delete fixture from curl, Python, or qClient source."""
    if sample.operation_id != DELETE_START_OPERATION:
        return None

    python_values = _python_delete_call_values(sample.source) if sample.client == "python" else {}
    table = sample_table(sample) or python_values.get("table")
    if not table:
        return None

    assembly = _extract_assembly(sample.source, sample.client)
    start_ts: str | None = None
    end_ts: str | None = None
    filters: tuple[tuple[str, Any], ...] = ()

    if sample.client == "python":
        values = python_values
        start_ts = str(values["startTS"]) if values.get("startTS") is not None else None
        end_ts = str(values["endTS"]) if values.get("endTS") is not None else None
        filters = _filter_values(values.get("filter"))
    elif sample.client == "curl":
        values = _curl_json_body(sample.source)
        start_ts = str(values["startTS"]) if values.get("startTS") is not None else None
        end_ts = str(values["endTS"]) if values.get("endTS") is not None else None
        filters = _filter_values(values.get("filter"))
    elif sample.client == "qclient":
        start_ts, end_ts, filters = _q_delete_values(sample.source)

    return DeleteScenario(
        table=table,
        assembly=assembly,
        start_ts=start_ts,
        end_ts=end_ts,
        filter_values=filters,
    )


def default_spec_file(repo_root: Path | None = None) -> Path:
    root = repo_root or Path.cwd()
    return root / "openapi" / "dbservice.yaml"


def normalize_client(value: str) -> str:
    token = str(value).strip().lower()
    return CLIENT_ALIASES.get(token, token)


def load_openapi_spec(spec_file: str | Path | None = None) -> dict[str, Any]:
    path = Path(spec_file) if spec_file else default_spec_file()
    if not path.exists():
        raise FileNotFoundError(f"OpenAPI spec file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)

    if not isinstance(spec, dict):
        raise ValueError(f"OpenAPI spec must be a YAML object: {path}")
    if not spec.get("openapi"):
        raise ValueError(f"OpenAPI spec is missing the 'openapi' field: {path}")
    if not isinstance(spec.get("paths"), dict):
        raise ValueError(f"OpenAPI spec is missing a valid 'paths' object: {path}")
    return spec


def collect_spec_samples(spec: dict[str, Any]) -> list[SpecSample]:
    samples: list[SpecSample] = []
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            method_l = str(method).lower()
            if method_l not in HTTP_METHODS or not isinstance(operation, dict):
                continue

            tags = tuple(str(tag) for tag in operation.get("tags", []) if tag is not None)
            operation_id = str(operation.get("operationId") or "")
            for raw_sample in operation.get("x-codeSamples", []) or []:
                if not isinstance(raw_sample, dict):
                    continue
                source = raw_sample.get("source")
                if not source:
                    continue
                client = normalize_client(raw_sample.get("lang") or raw_sample.get("label") or "")
                samples.append(
                    SpecSample(
                        method=method_l.upper(),
                        path=str(path),
                        operation_id=operation_id,
                        tags=tags,
                        client=client,
                        label=str(raw_sample.get("label") or raw_sample.get("lang") or client),
                        source=str(source).strip(),
                    )
                )
    return samples


def sample_matches(sample: SpecSample, test_filter: str) -> bool:
    if not test_filter:
        return True

    needle = test_filter.strip().lower()
    if not needle:
        return True

    client = normalize_client(needle)
    if needle in CLIENT_ALIASES or needle in {"curl", "python", "qclient"}:
        return sample.client == client

    haystacks = [
        sample.method,
        sample.path,
        sample.operation_id,
        sample.label,
        sample.client,
        *sample.tags,
    ]
    return any(needle in str(value).lower() for value in haystacks)


def filter_spec_samples(samples: list[SpecSample], test_filter: str = "") -> list[SpecSample]:
    return [sample for sample in samples if sample_matches(sample, test_filter)]


def print_spec_sample_summary(
    samples: list[SpecSample],
    spec_file: str | Path,
    test_filter: str = "",
) -> None:
    filter_text = f" filter={test_filter}" if test_filter else ""
    print(f"validation: suite=spec_samples source={spec_file}{filter_text}")
    print(f"Collected {len(samples)} OpenAPI code sample(s)")

    by_client: dict[str, int] = {}
    for sample in samples:
        by_client[sample.client] = by_client.get(sample.client, 0) + 1
    if by_client:
        counts = ", ".join(f"{client}={count}" for client, count in sorted(by_client.items()))
        print(f"Client samples: {counts}")

    for sample in samples:
        tags = ",".join(sample.tags) if sample.tags else "-"
        print(
            f"- {sample.client:7} {sample.method:6} {sample.path:28} "
            f"op={sample.operation_key} label={sample.label} tags={tags}"
        )


PHASES = {
    "configTableCreate": 10,
    "listTables": 20,
    "configTable": 30,
    "importFiles": 40,
    "importData": 45,
    "ingestStart": 47,
    "ingestStatus": 50,
    "simple": 60,
    "preview": 60,
    "sql": 60,
    "qsql": 60,
    DELETE_START_OPERATION: 70,
    DELETE_STATUS_OPERATION: 75,
    DELETE_CLEAR_OPERATION: 80,
    "ingestDelete": 90,
    "configTableDelete": 90,
}

ASYNC_JOB_OPERATIONS = {
    "configTableCreate",
    "configTableDelete",
    "importFiles",
    "importData",
    "ingestStart",
    "ingestStatus",
    "ingestDelete",
}

QUERY_OPERATIONS = {"simple", "preview", "sql", "qsql"}

# cancel_import needs an import that remains active long enough to cancel. The
# suite documents that deliberate skip until it owns such a fixture; every
# other operation must execute at least one sample for the selected client.
COVERAGE_EXEMPT_OPERATIONS = {"ingestDelete"}


QCLIENT_CAPTURE_OPERATIONS = {
    "configTable",
    "configTableCreate",
    "configTableDelete",
    "importFiles",
    "importData",
    "ingestStart",
    "ingestStatus",
    DELETE_START_OPERATION,
    DELETE_STATUS_OPERATION,
    DELETE_CLEAR_OPERATION,
    *QUERY_OPERATIONS,
}


def sample_phase(sample: SpecSample) -> int:
    return PHASES.get(sample.operation_id, 80)


def sample_variant_order(sample: SpecSample) -> int:
    label = (sample.label or "").lower()
    if "splayed" in label:
        return 0
    if "minimal" in label:
        return 1
    if "full" in label:
        return 2
    if "standard file ingest" in label:
        return 2
    if "postparse" in label:
        return 3
    return 3


def order_spec_samples(samples: list[SpecSample]) -> list[SpecSample]:
    return sorted(samples, key=lambda sample: (sample_phase(sample), sample.path, sample_variant_order(sample), sample.label))


def clients_in_samples(samples: list[SpecSample]) -> set[str]:
    return {sample.client for sample in samples}


def sample_table(sample: SpecSample) -> str | None:
    """Return a sample's literal table name for state checks and cleanup."""
    return extract_table_param_from_source(sample.source)


def _source_has_create_table(source: str) -> bool:
    return any(
        re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        for pattern in (
            r"\bcreateTable\s*=\s*True\b",
            r'"createTable"\s*:\s*(true|1|"1")',
            r"\bcreateTable\s*:\s*(1b|true|1|\"1\")\b",
            r"`createTable[^!]*![^\n]*1b",
        )
    )


def _sample_creates_table(sample: SpecSample) -> bool:
    if sample.operation_id == "configTableCreate":
        return True
    if sample.operation_id in {"importFiles", "importData", "ingestStart"}:
        return _source_has_create_table(sample.source)
    return False


def _query_required_tables(sample: SpecSample) -> list[str]:
    tables: list[str] = []
    table = sample_table(sample)
    if table:
        tables.append(table)

    for match in re.finditer(r"\bfrom\s+[`\"]?([A-Za-z_][A-Za-z0-9_.-]*)", sample.source, flags=re.IGNORECASE):
        table = match.group(1)
        if table and table not in tables:
            tables.append(table)
    return tables


def _sample_required_tables(sample: SpecSample) -> list[str]:
    if sample.operation_id in QUERY_OPERATIONS:
        return _query_required_tables(sample)

    table = sample_table(sample)
    if not table:
        return []

    if sample.operation_id in {"configTable", "configTableDelete"}:
        return [table]

    if sample.operation_id in {"importFiles", "importData", "ingestStart"} and not _sample_creates_table(sample):
        return [table]

    return []


def _safe_table_set(endpoint: str) -> set[str] | None:
    try:
        return set(list_tables_rest(endpoint))
    except Exception:
        return None


def _with_assembly(path: str, assembly: str | None) -> str:
    if not assembly:
        return path
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{urllib.parse.urlencode({'assembly': assembly})}"


def _rest_json(
    endpoint: str,
    path: str,
    *,
    method: str = "GET",
    payload: Any = None,
    assembly: str | None = None,
) -> Any:
    url = endpoint.rstrip("/") + _with_assembly(path, assembly)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        detail = _clean_message(text) or str(exc)
        raise SpecSampleExecutionError(f"HTTP {exc.code} {detail}") from exc
    parsed = parse_json_text(text)
    return None if parsed is JSON_PARSE_ERROR else parsed


def _tables_for_assembly(endpoint: str, assembly: str | None) -> set[str]:
    payload = _rest_json(endpoint, "/api/v0/tables", assembly=assembly)
    if isinstance(payload, list):
        return {str(table) for table in payload}
    if isinstance(payload, dict) and isinstance(payload.get("tables"), list):
        return {str(table) for table in payload["tables"]}
    return set()


def _assembly_available(endpoint: str, assembly: str | None) -> bool:
    if not assembly:
        return True
    try:
        _tables_for_assembly(endpoint, assembly)
    except SpecSampleExecutionError as exc:
        message = str(exc).lower()
        if ("http 400" in message or "http 404" in message) and (
            "no such database" in message or "unknown assembly" in message
        ):
            return False
        raise
    return True


def _wait_for_table_absent_in_assembly(
    endpoint: str,
    table: str,
    assembly: str | None,
    timeout_s: int = 60,
) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if table not in _tables_for_assembly(endpoint, assembly):
            return
        time.sleep(1)
    raise SpecSampleExecutionError(
        f"table {table!r} still exists after fixture cleanup"
    )


def _fixture_time(start_ts: str | None) -> tuple[str, str]:
    text = str(start_ts or "")
    match = re.search(
        r"(?P<year>\d{4})[.-](?P<month>\d{2})[.-](?P<day>\d{2})"
        r"(?:[DT](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}))?",
        text,
    )
    if match:
        values = {name: int(value or 0) for name, value in match.groupdict().items()}
        timestamp = datetime(
            values["year"],
            values["month"],
            values["day"],
            values["hour"],
            values["minute"],
            values["second"],
        ) + timedelta(seconds=1)
    else:
        timestamp = datetime(2026, 4, 3, 12, 0, 0)
    return timestamp.date().isoformat(), timestamp.isoformat(timespec="milliseconds")


def _fixture_column_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "long"
    if isinstance(value, float):
        return "float"
    return "symbol"


def _wait_for_import_job(
    endpoint: str,
    payload: Any,
    assembly: str | None,
    action: str,
) -> str:
    text = json.dumps(payload or {})
    job_id = extract_job_id(payload, text)
    status = extract_import_status(payload, text)
    if not job_id:
        raise SpecSampleExecutionError(f"{action} did not return a job id")
    if status != "completed":
        status, final_payload = _poll_job(endpoint, job_id, assembly=assembly)
        if status != "completed":
            raise SpecSampleExecutionError(
                _job_failure_message(job_id, status, final_payload)
            )
    return job_id


def _prepare_delete_fixture(endpoint: str, scenario: DeleteScenario) -> None:
    """Create one matching row using the table and filter from the example."""
    table_path = f"/api/v0/tables/{urllib.parse.quote(scenario.table, safe='')}"
    if scenario.table in _tables_for_assembly(endpoint, scenario.assembly):
        _rest_json(
            endpoint,
            table_path,
            method="DELETE",
            assembly=scenario.assembly,
        )
        _wait_for_table_absent_in_assembly(
            endpoint,
            scenario.table,
            scenario.assembly,
        )

    columns: list[dict[str, str]] = [
        {"name": "trddate", "type": "date"},
        {"name": "ts", "type": "timestamp"},
    ]
    column_names = ["trddate", "ts"]
    row_values: list[Any] = list(_fixture_time(scenario.start_ts))
    for column, value in scenario.filter_values:
        if column in column_names:
            continue
        columns.append({"name": column, "type": _fixture_column_type(value)})
        column_names.append(column)
        row_values.append(value)

    create_payload = {
        "type": "partitioned",
        "prtnCol": "ts",
        "columns": columns,
    }
    create_result = _rest_json(
        endpoint,
        table_path,
        method="POST",
        payload=create_payload,
        assembly=scenario.assembly,
    )
    _wait_for_import_job(
        endpoint,
        create_result,
        scenario.assembly,
        f"create delete fixture table {scenario.table}",
    )

    import_result = _rest_json(
        endpoint,
        "/api/v0/imports/data",
        method="POST",
        payload={
            "table": scenario.table,
            "data": [row_values],
            "columnNames": column_names,
            "insert_as": "rows",
        },
        assembly=scenario.assembly,
    )
    _wait_for_import_job(
        endpoint,
        import_result,
        scenario.assembly,
        f"import delete fixture table {scenario.table}",
    )


def _assert_delete_job_cleared(
    endpoint: str,
    job_id: str,
    assembly: str | None,
) -> None:
    try:
        _rest_json(
            endpoint,
            f"/api/v0/deletes/{urllib.parse.quote(job_id, safe='')}",
            assembly=assembly,
        )
    except SpecSampleExecutionError as exc:
        if "HTTP 404" in str(exc):
            return
        raise
    raise SpecSampleExecutionError(
        f"delete job {job_id} is still available after clearing its status"
    )


def _cleanup_delete_fixture(endpoint: str, scenario: DeleteScenario) -> None:
    try:
        if scenario.table not in _tables_for_assembly(endpoint, scenario.assembly):
            return
        _rest_json(
            endpoint,
            f"/api/v0/tables/{urllib.parse.quote(scenario.table, safe='')}",
            method="DELETE",
            assembly=scenario.assembly,
        )
        _wait_for_table_absent_in_assembly(
            endpoint,
            scenario.table,
            scenario.assembly,
        )
    except Exception:
        pass


def _fixture_path_exists(path: str) -> bool:
    candidates = [
        Path(path),
        Path("data") / "imports" / path,
        Path("samples") / path,
    ]
    return any(candidate.exists() for candidate in candidates)


def _missing_import_paths(sample: SpecSample) -> list[str]:
    if sample.operation_id not in {"importFiles", "ingestStart"}:
        return []
    return [path for path in extract_import_paths_from_source(sample.source) if not _fixture_path_exists(path)]


def _sample_skip_reason(
    sample: SpecSample,
    endpoint: str,
    table_failures: dict[str, str],
) -> str | None:
    assembly = _extract_assembly(sample.source, sample.client)
    if assembly and not _assembly_available(endpoint, assembly):
        return f"required assembly {assembly!r} is not configured"

    missing_paths = _missing_import_paths(sample)
    if missing_paths:
        return f"required import file not found: {', '.join(missing_paths)}"

    required_tables = _sample_required_tables(sample)
    if not required_tables:
        return None

    tables = _safe_table_set(endpoint)
    if tables is None:
        return None

    missing_tables = [table for table in required_tables if table not in tables]
    if not missing_tables:
        return None

    reasons = []
    for table in missing_tables:
        failure = table_failures.get(table)
        if failure:
            reasons.append(f"required table {table} unavailable because {failure}")
        else:
            reasons.append(f"required table {table} does not exist")
    return "; ".join(reasons)


def _record_table_failure(
    table_failures: dict[str, str],
    endpoint: str,
    table: str | None,
    display_op: str,
    message: str,
) -> None:
    if not table:
        return
    tables = _safe_table_set(endpoint)
    if tables is None or table not in tables:
        table_failures[table] = f"{display_op} failed: {_clean_message(message, limit=240)}"


def _replace_endpoint(source: str, endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    bare_endpoint = re.sub(r"^https?://", "", endpoint)
    source = source.replace("http://localhost:8080", endpoint)
    source = source.replace("localhost:8080", bare_endpoint)
    return source


def _replace_job_placeholder(source: str, job_id: str | None) -> str:
    if not job_id:
        return source
    source = source.replace("/imports/NAME", f"/imports/{job_id}")
    source = source.replace("/deletes/NAME", f"/deletes/{job_id}")
    source = source.replace("3fa85f64-5717-4562-b3fc-2c963f66afa6", job_id)
    source = source.replace(DELETE_JOB_EXAMPLE_ID, job_id)
    source = source.replace('job["name"]', repr(job_id))
    source = source.replace("job['name']", repr(job_id))
    source = source.replace('job["jobId"]', repr(job_id))
    source = source.replace("job['jobId']", repr(job_id))
    source = source.replace("job`name", f'"{job_id}"')
    source = source.replace("job`jobId", f'"{job_id}"')
    return source


def _extract_job_from_text(text: str) -> tuple[str | None, str | None]:
    payload = parse_json_text(text)
    if payload is JSON_PARSE_ERROR:
        payload = {}
    job_id = extract_job_id(payload, text)
    if not job_id:
        match = re.search(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            text or "",
        )
        if match:
            job_id = match.group(0)
    return job_id, extract_import_status(payload, text)


def _raise_if_failure_payload(text: str) -> None:
    payload = parse_json_text(text)
    if payload is JSON_PARSE_ERROR:
        payload = {}
    if payload_indicates_failure(payload, text):
        message = None
        if isinstance(payload, dict):
            message = find_first_key(payload, ["error", "message", "text", "title"])
        raise SpecSampleExecutionError(f"DbServiceError: {_clean_message(message or text)}")


def _raise_if_qclient_failure_payload(text: str) -> None:
    payload = parse_json_text(text)
    if payload is JSON_PARSE_ERROR:
        payload = {}

    message = None
    if isinstance(payload, dict):
        message = find_first_key(payload, ["error", "message", "text", "title"])

    cleaned_output = _clean_message(text)
    cleaned_message = _clean_message(message or text)
    lower = f"{cleaned_message} {cleaned_output}".lower()
    failure_patterns = [
        "missing required",
        "batch ingest session not",
        "does not exist",
        "createtable flag",
        "unknown table",
        "no file found",
        "no such file",
        "`code`text`details",
        "traceback",
        "exception",
    ]
    if payload_indicates_failure(payload, text) or any(pattern in lower for pattern in failure_patterns):
        raise SpecSampleExecutionError(f"DbServiceError: {cleaned_message}")


def _job_failure_message(job_id: str, status: str | None, payload: Any) -> str:
    error = None
    if isinstance(payload, dict):
        error = payload.get("error") or payload.get("message")
    detail = f" error={_clean_message(error)}" if error else f" payload={_clean_message(str(payload))}"
    return f"job {job_id} did not complete: status={status}{detail}"


def _poll_sample_job_if_needed(
    sample: SpecSample,
    endpoint: str,
    output: str,
    last_job_id: str | None,
) -> str | None:
    if sample.operation_id == DELETE_START_OPERATION:
        job_id, status = _extract_job_from_text(output)
        if not job_id:
            raise SpecSampleExecutionError(
                f"{_sample_display_op(sample)} did not return a parseable job id"
            )
        assembly = _extract_assembly(sample.source, sample.client)
        if status != "completed":
            final_status, payload = _poll_delete_job(
                endpoint,
                job_id,
                assembly=assembly,
            )
            if final_status != "completed":
                raise SpecSampleExecutionError(
                    _job_failure_message(job_id, final_status, payload)
                )
        return job_id

    if sample.operation_id not in ASYNC_JOB_OPERATIONS:
        return last_job_id

    job_id, status = _extract_job_from_text(output)
    if not job_id:
        raise SpecSampleExecutionError(
            f"{_sample_display_op(sample)} did not return a parseable job id; "
            "refusing to reuse the previous job id"
        )
    if job_id and status != "completed":
        final_status, payload = _poll_job(
            endpoint,
            job_id,
            assembly=_extract_assembly(sample.source, sample.client),
        )
        if final_status != "completed":
            raise SpecSampleExecutionError(_job_failure_message(job_id, final_status, payload))
    return job_id


def _poll_job(
    endpoint: str,
    job_id: str | None,
    timeout_s=120,
    poll_s=1,
    assembly: str | None = None,
) -> tuple[str | None, Any]:
    if not job_id:
        return None, None
    deadline = time.time() + timeout_s
    last_payload = None
    while time.time() < deadline:
        try:
            payload = parse_json_text(
                subprocess.check_output(
                    [
                        "curl",
                        "-s",
                        "-H",
                        "Accept: application/json",
                        endpoint.rstrip("/")
                        + _with_assembly(f"/api/v0/imports/{job_id}", assembly),
                    ],
                    text=True,
                )
            )
        except Exception as exc:
            payload = {"error": str(exc)}
        if payload is JSON_PARSE_ERROR:
            payload = {}
        last_payload = payload
        status = extract_import_status(payload, json.dumps(payload))
        if status == "completed":
            return status, payload
        if status == "errored":
            return status, payload
        time.sleep(poll_s)
    return extract_import_status(last_payload or {}, json.dumps(last_payload or {})) or "unknown", last_payload


def _poll_delete_job(
    endpoint: str,
    job_id: str,
    *,
    assembly: str | None = None,
    timeout_s: int = 120,
    poll_s: int = 1,
) -> tuple[str | None, Any]:
    deadline = time.time() + timeout_s
    last_payload: Any = None
    while time.time() < deadline:
        try:
            payload = _rest_json(
                endpoint,
                f"/api/v0/deletes/{urllib.parse.quote(job_id, safe='')}",
                assembly=assembly,
            )
        except Exception as exc:
            payload = {"error": str(exc)}
        last_payload = payload
        status = extract_import_status(payload, json.dumps(payload or {}))
        if status in {"completed", "errored"}:
            return status, payload
        time.sleep(poll_s)
    final_status = extract_import_status(last_payload or {}, json.dumps(last_payload or {}))
    return final_status or "unknown", last_payload


class SpecSampleExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpecSampleResult:
    index: int
    total: int
    sample: SpecSample
    display_op: str
    status: str
    message: str = ""
    duration_s: float = 0.0


def _compact_lines(text: str, limit: int = 2000) -> str:
    return short_text(" ".join((text or "").split()), limit=limit)


def _clean_message(text: str, limit: int = 500) -> str:
    ascii_text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in str(text or ""))
    return _compact_lines(ascii_text, limit=limit)


def _format_rendered_source(source: str, limit: int = 4000) -> str:
    ascii_text = "".join(ch if ch == "\n" or 32 <= ord(ch) < 127 else " " for ch in str(source or ""))
    text = ascii_text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_sample_failure(
    sample: SpecSample,
    table: str | None,
    reason: str,
    output: str = "",
    rendered_source: str = "",
) -> str:
    if sample.method == "README":
        source_file = sample.path
        request_line = ""
    else:
        source_file = os.getenv("TEST_SOURCE", "").strip() or "openapi/dbservice.yaml"
        request_line = f"Request: {sample.method} {sample.path}"
    lines = [
        "SAMPLE VALIDATION FAILED",
        f"File: {source_file}",
        f"Client: {sample.client}",
        f"Operation: {_sample_display_op(sample)} ({sample.operation_key})",
    ]
    if request_line:
        lines.append(request_line)
    if table:
        lines.append(f"Table: {table}")
    if rendered_source:
        lines.extend(["Command/source:", textwrap.indent(_format_rendered_source(rendered_source), "  ")])
    lines.append(f"Error: {_clean_message(reason)}")
    if output:
        lines.append(f"Output: {_clean_message(output, limit=2000)}")
    return "\n".join(lines)


def _ensure_sample_failure(
    sample: SpecSample,
    endpoint: str,
    last_job_id: str | None,
    reason: Exception | str,
    output: str = "",
) -> str:
    message = str(reason)
    if message.startswith("SAMPLE VALIDATION FAILED"):
        return message
    source = _replace_endpoint(_replace_job_placeholder(sample.source, last_job_id), endpoint)
    return _format_sample_failure(
        sample,
        sample_table(sample),
        message,
        output=output,
        rendered_source=source,
    )


def _sample_display_op(sample: SpecSample) -> str:
    operation_names = {
        "configTableCreate": "create_table",
        "configTable": "describe_table",
        "configTableDelete": "drop_table",
        "listTables": "list_tables",
        "importFiles": "import_files",
        "importData": "import_data",
        "ingestStart": "import_database",
        "ingestStatus": "get_import",
        "ingestDelete": "cancel_import",
        "simple": "query_simple",
        "preview": "query_preview",
        "sql": "query_sql",
        "qsql": "query_q",
        DELETE_START_OPERATION: "delete_start",
        DELETE_STATUS_OPERATION: "delete_status",
        DELETE_CLEAR_OPERATION: "delete_clear",
    }
    name = operation_names.get(sample.operation_id, sample.operation_key)
    qualifier = ""
    match = re.search(r"\(([^)]+)\)", sample.label or "")
    if match:
        qualifier = f"[{match.group(1)}]"
    return f"{name}{qualifier}"


def _spec_prefix(client: str) -> str:
    return f"spec_samples:{client}"


def _extract_structured_error(output: str) -> str | None:
    for line in reversed((output or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = parse_json_text(line)
        if payload is JSON_PARSE_ERROR or not isinstance(payload, dict):
            continue
        if payload.get("__spec_sample_error__"):
            error_type = payload.get("type") or "Error"
            message = _clean_message(payload.get("message") or payload.get("error") or "")
            return f"{error_type}: {message}".strip()
    return None


def _parse_sample_output(output: str) -> Any:
    payload = parse_json_text(output)
    if payload is not JSON_PARSE_ERROR:
        return payload
    for line in reversed((output or "").splitlines()):
        payload = parse_json_text(line.strip())
        if payload is not JSON_PARSE_ERROR:
            return payload
    return JSON_PARSE_ERROR


def _query_operation_name(sample: SpecSample) -> str | None:
    return {
        "simple": "query_simple",
        "preview": "query_preview",
        "sql": "query_sql",
        "qsql": "query_q",
    }.get(sample.operation_id)


def _query_group_fields(source: str) -> list[str]:
    match = re.search(r"\bby\s+(.+?)\s+from\b", source or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    fields = []
    for raw in match.group(1).split(","):
        name = raw.strip()
        if not name:
            continue
        if ":" in name:
            name = name.split(":", 1)[0].strip()
        name = re.sub(r"[^A-Za-z0-9_].*$", "", name)
        if name and name not in fields:
            fields.append(name)
    return fields


def _dict_value_fields(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    for value in payload.values():
        if isinstance(value, dict):
            return list(value.keys())
    return []


def _sample_query_check_text(sample: SpecSample, payload: Any) -> str:
    op = _query_operation_name(sample)
    if not op:
        return ""

    rows = row_count(payload)
    fields = payload_fields(payload)
    if fields is None:
        fields = field_names(payload)

    if (
        sample.client == "python"
        and op == "query_q"
        and isinstance(payload, dict)
        and payload
        and all(isinstance(value, dict) for value in payload.values())
    ):
        rows = len(payload)
        fields = _query_group_fields(sample.source) + _dict_value_fields(payload)

    if rows is INCONSISTENT_ROWS:
        rows = "inconsistent"
    if fields is INCONSISTENT_FIELDS:
        fields = ["inconsistent"]

    response = type(payload).__name__ if payload is not JSON_PARSE_ERROR else "unknown"
    if sample.client == "python":
        if op in {"query_simple", "query_preview"}:
            response = "list"
        elif op in {"query_sql", "query_q"}:
            response = "dataframe"
    elif sample.client == "qclient":
        response = "q_table"

    rows_text = rows if rows is not None else "unknown"
    fields_text = f" fields={fields}" if fields else ""
    return f"response={response} rows={rows_text}{fields_text}"


def _sample_check_text(sample: SpecSample, endpoint: str, table: str | None, output: str, last_job_id: str | None) -> str:
    payload = _parse_sample_output(output)
    if sample.operation_id in {DELETE_START_OPERATION, DELETE_STATUS_OPERATION}:
        job_id = extract_job_id(payload, output) or last_job_id
        status = extract_import_status(payload, output) or "completed"
        parts = [f"status={status}"]
        if table:
            parts.append(f"table={table}")
        if job_id:
            parts.append(f"job={job_id}")
        return " ".join(parts)
    if sample.operation_id == DELETE_CLEAR_OPERATION:
        return f"status=cleared job={last_job_id or '-'}"

    operation_names = {
        "listTables": "list_tables",
        "configTable": "describe_table",
        "configTableCreate": "create_table",
        "configTableDelete": "drop_table",
        "importFiles": "import_file",
        "importData": "import_data",
        "ingestStart": "import_database",
        "ingestStatus": "import_status",
        "ingestDelete": "import_status",
        "simple": "query_simple",
        "preview": "query_preview",
        "sql": "query_sql",
        "qsql": "query_q",
        DELETE_START_OPERATION: "delete_start",
        DELETE_STATUS_OPERATION: "delete_status",
        DELETE_CLEAR_OPERATION: "delete_clear",
    }
    if sample.client == "python" and sample.operation_id in QUERY_OPERATIONS:
        return _sample_query_check_text(sample, payload)
    return validation_check_text(
        op=operation_names.get(sample.operation_id, sample.operation_id),
        client_name=sample.client,
        payload=payload,
        text=output,
        table=table,
        endpoint=endpoint,
        fallback_job_id=last_job_id,
        status_override="completed" if sample.operation_id in ASYNC_JOB_OPERATIONS else None,
    )


def _wait_for_table_absent(endpoint: str, table: str, timeout_s=60, poll_s=1) -> None:
    deadline = time.time() + timeout_s
    last_tables: list[str] = []
    last_error: str | None = None
    absent_checks = 0
    while time.time() < deadline:
        try:
            last_tables = list_tables_rest(endpoint)
            last_error = None
        except Exception as exc:
            last_error = str(exc)
            time.sleep(poll_s)
            continue

        if table not in last_tables:
            absent_checks += 1
            if absent_checks >= 2:
                return
        else:
            absent_checks = 0
        time.sleep(poll_s)

    raise SpecSampleExecutionError(
        f"table {table!r} still exists after delete wait; "
        f"tables={last_tables} last_error={last_error or '-'}"
    )


def _drop_table(endpoint: str, table: str | None) -> None:
    """Delete one known table and wait until DB Service no longer lists it."""
    if not table:
        return
    subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "DELETE",
            "-H",
            "Accept: application/json",
            f"{endpoint.rstrip('/')}/api/v0/tables/{table}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_table_absent(endpoint, table)


def _drop_table_if_exists(endpoint: str, table: str | None, client: str = "unknown") -> None:
    """Give repeated create-table examples an isolated starting state."""
    if not table:
        return
    if table in list_tables_rest(endpoint):
        if validation_verbose():
            print(f"[{_spec_prefix(client)}] setup drop_table check: table={table} exists=true", flush=True)
        _drop_table(endpoint, table)


def _is_table_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "409" in message or "already exists" in message or "conflict" in message


def _curl_source_with_http_failure(source: str) -> str:
    """Preserve response bodies while making curl HTTP errors fail pipelines."""
    return 'set -o pipefail\ncurl() { command curl --fail-with-body "$@"; }\n' + source


def _run_curl_sample(sample: SpecSample, endpoint: str, last_job_id: str | None) -> tuple[str | None, str]:
    source = _replace_endpoint(_replace_job_placeholder(sample.source, last_job_id), endpoint)
    rendered_source = source
    execution_source = _curl_source_with_http_failure(source)
    proc = subprocess.run(
        execution_source,
        shell=True,
        executable="/bin/bash",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise SpecSampleExecutionError(
            _format_sample_failure(
                sample,
                sample_table(sample),
                f"curl exited {proc.returncode}",
                output,
                rendered_source=rendered_source,
            )
        )
    try:
        _raise_if_failure_payload(output)
        next_job_id = _poll_sample_job_if_needed(sample, endpoint, output, last_job_id)
    except SpecSampleExecutionError as exc:
        raise SpecSampleExecutionError(
            _format_sample_failure(sample, sample_table(sample), str(exc), output, rendered_source=rendered_source)
        ) from exc
    return next_job_id, output


def _python_capture_source(source: str) -> str:
    try:
        module = ast.parse(source)
    except SyntaxError:
        return source + "\n__sample_result = None\n"
    if module.body and isinstance(module.body[-1], ast.Expr):
        module.body[-1] = ast.Assign(
            targets=[ast.Name(id="__sample_result", ctx=ast.Store())],
            value=module.body[-1].value,
        )
        ast.fix_missing_locations(module)
        return ast.unparse(module)
    return source + "\n__sample_result = locals().get('__sample_result')\n"


def _run_python_sample(sample: SpecSample, endpoint: str, last_job_id: str | None) -> tuple[str | None, str]:
    source = _replace_endpoint(_replace_job_placeholder(sample.source, last_job_id), endpoint)
    rendered_source = source
    source = _python_capture_source(source)
    runner = f"""
import json
import sys
import dbservice_client as dbs

session = dbs.Session(endpoint={re.sub(r'^https?://', '', endpoint.rstrip('/'))!r})

try:
{textwrap.indent(source, '    ')}
except Exception as exc:
    print(json.dumps({{
        "__spec_sample_error__": True,
        "type": type(exc).__name__,
        "message": str(exc),
    }}))
    sys.exit(1)

def _json_default(value):
    try:
        return str(value)
    except Exception:
        return repr(value)

def _json_ready(value):
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict(orient="records")
        except TypeError:
            try:
                return value.to_dict()
            except Exception:
                pass
        except Exception:
            pass
    if hasattr(value, "py"):
        try:
            return _json_ready(value.py())
        except Exception:
            pass
    if isinstance(value, dict):
        return {{str(k): _json_ready(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    return value

print(json.dumps(_json_ready(__sample_result), default=_json_default))
"""
    proc = subprocess.run(
        [os.getenv("PYTHON", "python3"), "-c", runner],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        structured_error = _extract_structured_error(output)
        reason = structured_error or f"python exited {proc.returncode}"
        raise SpecSampleExecutionError(
            _format_sample_failure(sample, sample_table(sample), reason, output, rendered_source=rendered_source)
        )
    try:
        _raise_if_failure_payload(output)
        next_job_id = _poll_sample_job_if_needed(sample, endpoint, output, last_job_id)
    except SpecSampleExecutionError as exc:
        raise SpecSampleExecutionError(
            _format_sample_failure(sample, sample_table(sample), str(exc), output, rendered_source=rendered_source)
        ) from exc
    return next_job_id, output


def _qclient_source_lines(source: str, capture_mode: str = "") -> list[str]:
    lines = [line for line in source.splitlines() if not line.strip().startswith("#")]
    if not capture_mode:
        return lines
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip()
        if not line:
            continue
        lines[index] = f"specSampleResult:{lines[index]}"
        if capture_mode == "query":
            lines.append("-1 .j.j `rows`fields!(count specSampleResult;string cols specSampleResult)")
        else:
            lines.append("-1 .j.j specSampleResult")
        return lines
    return lines


def _subprocess_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_qclient_sample(sample: SpecSample, endpoint: str, last_job_id: str | None) -> tuple[str | None, str]:
    if shutil.which("q") is None:
        raise SpecSampleExecutionError("q executable not found on PATH")

    source = _replace_endpoint(_replace_job_placeholder(sample.source, last_job_id), endpoint)
    capture_mode = ""
    if sample.operation_id in QUERY_OPERATIONS:
        capture_mode = "query"
    elif sample.operation_id in QCLIENT_CAPTURE_OPERATIONS:
        capture_mode = "json"
    source_lines = _qclient_source_lines(source, capture_mode=capture_mode)
    bare_endpoint = re.sub(r"^https?://", "", endpoint.rstrip("/"))
    script = "\n".join(
        [
            "dbs:use`kx.dbservice_client",
            f'session:dbs.createSession([endpoint:"{bare_endpoint}"])',
            *source_lines,
            "\\\\",
            "",
        ]
    )
    with tempfile.NamedTemporaryFile("w", suffix=".q", delete=False) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        try:
            proc = subprocess.run(
                ["q", script_path],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _subprocess_text(exc.stdout)
            stderr = _subprocess_text(exc.stderr)
            output = stdout + ("\n" + stderr if stderr else "")
            raise SpecSampleExecutionError(
                _format_sample_failure(
                    sample,
                    sample_table(sample),
                    "q sample timed out after 60s",
                    output,
                    rendered_source=source,
                )
            ) from exc
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise SpecSampleExecutionError(
            _format_sample_failure(
                sample,
                sample_table(sample),
                f"q exited {proc.returncode}",
                output,
                rendered_source=source,
            )
        )
    try:
        _raise_if_qclient_failure_payload(output)
        next_job_id = _poll_sample_job_if_needed(sample, endpoint, output, last_job_id)
    except SpecSampleExecutionError as exc:
        raise SpecSampleExecutionError(
            _format_sample_failure(sample, sample_table(sample), str(exc), output, rendered_source=source)
        ) from exc
    return next_job_id, output


def _spec_sample_summary(results: list[SpecSampleResult]) -> dict[str, int]:
    passed = sum(1 for result in results if result.status == "passed")
    failed = sum(1 for result in results if result.status == "failed")
    skipped = sum(1 for result in results if result.status == "skipped")
    return {"passed": passed, "failed": failed, "skipped": skipped}


def _write_spec_sample_report(client: str, results: list[SpecSampleResult]) -> str:
    cases = [
        {
            "classname": _spec_prefix(result.sample.client),
            "name": f"{result.index}/{result.total} ({result.display_op})",
            "status": result.status,
            "message": result.message,
            "time": result.duration_s,
        }
        for result in results
    ]
    return write_junit_report(f"spec_samples-{client}", cases, _spec_sample_summary(results))


def _print_spec_sample_summary(results: list[SpecSampleResult]) -> None:
    summary = _spec_sample_summary(results)
    passed = summary["passed"]
    failed = summary["failed"]
    skipped = summary["skipped"]
    total = len(results)
    print(f"[spec_samples] summary: passed={passed} failed={failed} skipped={skipped} total={total}", flush=True)


def _enforce_operation_coverage(results: list[SpecSampleResult]) -> list[SpecSampleResult]:
    """Fail one skipped case when an endpoint had no executed sample."""
    covered_results = list(results)
    operations: dict[tuple[str, str, str], list[int]] = {}
    for index, result in enumerate(covered_results):
        sample = result.sample
        key = (sample.method, sample.path, sample.operation_key)
        operations.setdefault(key, []).append(index)

    for indexes in operations.values():
        grouped = [covered_results[index] for index in indexes]
        sample = grouped[0].sample
        if sample.operation_id in COVERAGE_EXEMPT_OPERATIONS:
            continue
        if any(result.status != "skipped" for result in grouped):
            continue

        first_index = indexes[0]
        first = covered_results[first_index]
        message = (
            f"no {sample.client} code sample executed for {sample.method} {sample.path}; "
            f"all {len(grouped)} sample(s) were skipped"
        )
        print(
            f"[{_spec_prefix(sample.client)}] COVERAGE FAIL "
            f"{_sample_display_op(sample).split('[', 1)[0]} | {message}",
            flush=True,
        )
        covered_results[first_index] = SpecSampleResult(
            first.index,
            first.total,
            first.sample,
            first.display_op,
            "failed",
            message,
            first.duration_s,
        )
    return covered_results


def run_spec_samples(samples: list[SpecSample], endpoint: str, fail_fast: bool = True) -> int:
    clients = clients_in_samples(samples)
    if len(clients) != 1:
        raise ValueError(
            "Spec sample execution requires a single client filter. "
            "Use TEST_FILTER=python, TEST_FILTER=qClient, or TEST_FILTER=curl."
        )
    client = next(iter(clients))
    ordered = order_spec_samples(samples)
    if validation_verbose():
        print(f"Running {len(ordered)} {client} OpenAPI code sample(s) against {endpoint}")
    reset_dbservice_tables(endpoint=endpoint, show_progress=validation_verbose(), notebook_name=_spec_prefix(client))
    last_job_id = None
    delete_workflow = DeleteWorkflowState()
    runners = {
        "curl": _run_curl_sample,
        "python": _run_python_sample,
        "qclient": _run_qclient_sample,
    }
    runner = runners.get(client)
    if not runner:
        raise ValueError(f"Unsupported sample client: {client}")

    results: list[SpecSampleResult] = []
    table_failures: dict[str, str] = {}
    try:
        for index, sample in enumerate(ordered, start=1):
            sample_started_at = time.time()
            table = sample_table(sample)
            display_op = _sample_display_op(sample)
            prefix = _spec_prefix(client)
            assembly = _extract_assembly(sample.source, sample.client)
            if sample.operation_id == "configTableCreate":
                _drop_table_if_exists(endpoint, table, client=client)
            if sample.operation_id == "ingestDelete":
                message = "requires an active import job; covered by generated/negative scenarios"
                print(f"[{prefix}] [{index}/{len(ordered)}] SKIP {display_op} | {message}", flush=True)
                results.append(SpecSampleResult(index, len(ordered), sample, display_op, "skipped", message, 0.0))
                continue
            skip_reason = _sample_skip_reason(sample, endpoint, table_failures)
            if skip_reason:
                print(f"[{prefix}] [{index}/{len(ordered)}] SKIP {display_op} | {skip_reason}", flush=True)
                if table and _sample_creates_table(sample):
                    table_failures[table] = f"{display_op} skipped: {_clean_message(skip_reason, limit=240)}"
                results.append(
                    SpecSampleResult(
                        index,
                        len(ordered),
                        sample,
                        display_op,
                        "skipped",
                        skip_reason,
                        time.time() - sample_started_at,
                    )
                )
                continue

            delete_job_id = delete_workflow.job_for(assembly)
            if sample.operation_id in {DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION} and not delete_job_id:
                message = "requires a successful delete_start sample and its returned job id"
                print(f"[{prefix}] [{index}/{len(ordered)}] SKIP {display_op} | {message}", flush=True)
                results.append(
                    SpecSampleResult(
                        index,
                        len(ordered),
                        sample,
                        display_op,
                        "skipped",
                        message,
                        time.time() - sample_started_at,
                    )
                )
                continue

            execution_job_id = (
                delete_job_id
                if sample.operation_id in {DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION}
                else last_job_id
            )
            delete_scenario: DeleteScenario | None = None
            try:
                if validation_verbose():
                    print(
                        f"[{prefix}] {index}/{len(ordered)} ({display_op}) RUN",
                        flush=True,
                    )
                if sample.operation_id == DELETE_START_OPERATION:
                    delete_scenario = extract_delete_scenario(sample)
                    if delete_scenario is None:
                        raise SpecSampleExecutionError(
                            "could not derive the delete table and fixture from the sample"
                        )
                    delete_workflow.track_fixture(delete_scenario)
                    _prepare_delete_fixture(endpoint, delete_scenario)

                next_job_id, output = runner(sample, endpoint, execution_job_id)
                if sample.operation_id == DELETE_START_OPERATION:
                    if delete_scenario is None or not next_job_id:
                        raise SpecSampleExecutionError(
                            "delete_start did not return a job id"
                        )
                    delete_workflow.remember(delete_scenario, next_job_id)
                    execution_job_id = next_job_id
                elif sample.operation_id not in {DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION}:
                    last_job_id = next_job_id
                    execution_job_id = last_job_id

                if sample.operation_id == DELETE_CLEAR_OPERATION and delete_job_id:
                    _assert_delete_job_cleared(endpoint, delete_job_id, assembly)
            except Exception as exc:
                formatted_message = _ensure_sample_failure(sample, endpoint, execution_job_id, exc)
                if sample.operation_id != "configTableCreate" or not _is_table_exists_error(exc):
                    message = formatted_message
                    _record_table_failure(table_failures, endpoint, table, display_op, message)
                    if fail_fast:
                        raise SpecSampleExecutionError(message)
                    print(f"[{prefix}] [{index}/{len(ordered)}] FAIL {display_op}", flush=True)
                    print(message, flush=True)
                    results.append(
                        SpecSampleResult(
                            index, len(ordered), sample, display_op, "failed", message, time.time() - sample_started_at
                        )
                    )
                    continue

                if validation_verbose():
                    print(
                        f"[{prefix}] {index}/{len(ordered)} ({display_op}) "
                        f"check: table={table} exists=true retry=true",
                        flush=True,
                    )
                if table:
                    _drop_table(endpoint, table)
                else:
                    if validation_verbose():
                        print(
                            f"[{prefix}] {index}/{len(ordered)} ({display_op}) "
                            "cleanup: table name unavailable; resetting tables before retry",
                            flush=True,
                        )
                    reset_dbservice_tables(endpoint=endpoint, show_progress=validation_verbose(), notebook_name=prefix)
                time.sleep(2)
                try:
                    last_job_id, output = runner(sample, endpoint, last_job_id)
                    execution_job_id = last_job_id
                except Exception as retry_exc:
                    message = _ensure_sample_failure(sample, endpoint, last_job_id, retry_exc)
                    _record_table_failure(table_failures, endpoint, table, display_op, message)
                    if fail_fast:
                        raise SpecSampleExecutionError(message)
                    print(f"[{prefix}] [{index}/{len(ordered)}] FAIL {display_op}", flush=True)
                    print(message, flush=True)
                    results.append(
                        SpecSampleResult(
                            index, len(ordered), sample, display_op, "failed", message, time.time() - sample_started_at
                        )
                    )
                    continue
            try:
                check_text = _sample_check_text(sample, endpoint, table, output, execution_job_id)
            except Exception as exc:
                message = _ensure_sample_failure(sample, endpoint, execution_job_id, exc, output=output)
                _record_table_failure(table_failures, endpoint, table, display_op, message)
                if fail_fast:
                    raise SpecSampleExecutionError(message)
                print(f"[{prefix}] [{index}/{len(ordered)}] FAIL {display_op}", flush=True)
                print(message, flush=True)
                results.append(
                    SpecSampleResult(
                        index, len(ordered), sample, display_op, "failed", message, time.time() - sample_started_at
                    )
                )
                continue
            if validation_verbose():
                print(
                    f"[{prefix}] {index}/{len(ordered)} ({display_op}) "
                    f"check: {check_text}",
                    flush=True,
                )
                print(
                    f"[{prefix}] {index}/{len(ordered)} ({display_op}) PASS",
                    flush=True,
                )
            else:
                print(
                    f"[{prefix}] [{index}/{len(ordered)}] PASS {display_op} | {check_text}",
                    flush=True,
                )
            results.append(
                SpecSampleResult(index, len(ordered), sample, display_op, "passed", duration_s=time.time() - sample_started_at)
            )
            if table:
                table_failures.pop(table, None)
    finally:
        for scenario in delete_workflow.all_fixtures():
            _cleanup_delete_fixture(endpoint, scenario)
        reset_dbservice_tables(
            endpoint=endpoint,
            show_progress=validation_verbose(),
            notebook_name=_spec_prefix(client),
        )
    results = _enforce_operation_coverage(results)
    _write_spec_sample_report(client, results)
    _print_spec_sample_summary(results)
    if any(result.status == "failed" for result in results):
        return 1
    return 0
