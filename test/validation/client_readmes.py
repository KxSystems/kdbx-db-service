"""Collect and run examples from DB Service client READMEs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ast
import os
import re
import subprocess
import time

from core import validation_verbose, write_junit_report

from spec_samples import (
    DELETE_CLEAR_OPERATION,
    DELETE_START_OPERATION,
    DELETE_STATUS_OPERATION,
    DeleteScenario,
    DeleteWorkflowState,
    SpecSample,
    SpecSampleExecutionError,
    _assembly_available,
    _assert_delete_job_cleared,
    _cleanup_delete_fixture,
    _extract_assembly,
    _run_python_sample,
    _run_qclient_sample,
    _ensure_sample_failure,
    _prepare_delete_fixture,
    _sample_check_text,
    _sample_display_op,
    extract_delete_scenario,
    sample_table,
)


CLIENT_READMES = {
    "python": {
        "env": "DBSERVICE_PYTHON_CLIENT_README",
        "dir_env": "DBSERVICE_PYTHON_CLIENT_DIR",
        "url_env": "DBSERVICE_PYTHON_CLIENT_URL",
        "ref_env": "DBSERVICE_PYTHON_CLIENT_REF",
        "tmp": Path("/tmp/kdbx-db-service-python-client"),
        "url": "https://gitlab.com/kxdev/kxinsights/data-services/dbservice-python-client.git",
        "langs": {"python", "py"},
    },
    "qclient": {
        "env": "DBSERVICE_Q_CLIENT_README",
        "local_dir_env": "DBSERVICE_Q_CLIENT_LOCAL_DIR",
        "dir_env": "DBSERVICE_Q_CLIENT_DIR",
        "url_env": "DBSERVICE_Q_CLIENT_URL",
        "ref_env": "DBSERVICE_Q_CLIENT_REF",
        "tmp": Path("/tmp/kdbx-db-service-q-client"),
        "url": "https://gitlab.com/kxdev/kxinsights/data-services/dbservice-qclient.git",
        "langs": {"q"},
    },
}

METHOD_OPERATIONS = {
    "create_table": "configTableCreate",
    "createtable": "configTableCreate",
    "drop_table": "configTableDelete",
    "droptable": "configTableDelete",
    "describe_table": "configTable",
    "describetable": "configTable",
    "list_tables": "listTables",
    "listtables": "listTables",
    "import_files": "importFiles",
    "importfiles": "importFiles",
    "import_data": "importData",
    "importdata": "importData",
    "import_database": "ingestStart",
    "importkdb": "ingestStart",
    "get_import": "ingestStatus",
    "getimport": "ingestStatus",
    "query_simple": "simple",
    "querysimple": "simple",
    "query_sql": "sql",
    "querysql": "sql",
    "query_q": "qsql",
    "queryq": "qsql",
    "delete_rows": DELETE_START_OPERATION,
    "deleterows": DELETE_START_OPERATION,
    "get_delete": DELETE_STATUS_OPERATION,
    "getdelete": DELETE_STATUS_OPERATION,
    "cancel_delete": DELETE_CLEAR_OPERATION,
    "canceldelete": DELETE_CLEAR_OPERATION,
}


@dataclass(frozen=True)
class ClientReadmeResult:
    index: int
    total: int
    sample: SpecSample
    display_op: str
    status: str
    message: str = ""
    duration_s: float = 0.0


def normalize_client(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"q", "q_client", "q-client", "qclient"}:
        return "qclient"
    if token in {"py", "python"}:
        return "python"
    return token


def readme_path_for_client(client: str) -> Path:
    config = CLIENT_READMES[client]
    env_path = os.getenv(str(config["env"]), "")
    if env_path:
        return Path(env_path)

    local_dir_env = str(config.get("local_dir_env") or "")
    local_dir = os.getenv(local_dir_env, "") if local_dir_env else ""
    if local_dir:
        readme = Path(local_dir) / "README.md"
        if not readme.exists():
            raise FileNotFoundError(f"Local {client} README not found: {readme}")
        return readme

    env_dir = os.getenv(str(config["dir_env"]), "")
    if env_dir:
        repo_dir = Path(env_dir)
        _clone_repo(
            repo_dir,
            os.getenv(str(config["url_env"]), str(config["url"])),
            os.getenv(str(config["ref_env"]), "main"),
        )
        return repo_dir / "README.md"

    tmp_readme = Path(config["tmp"]) / "README.md"
    _clone_repo(
        Path(config["tmp"]),
        os.getenv(str(config["url_env"]), str(config["url"])),
        os.getenv(str(config["ref_env"]), "main"),
    )
    return tmp_readme


def _clone_repo(target: Path, url: str, ref: str = "main") -> None:
    target.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(target)], check=True)
    remote = subprocess.run(
        ["git", "-C", str(target), "remote", "get-url", "origin"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if remote.returncode == 0:
        subprocess.run(["git", "-C", str(target), "remote", "set-url", "origin", url], check=True)
    else:
        subprocess.run(["git", "-C", str(target), "remote", "add", "origin", url], check=True)

    fetch_cmd = ["git", "-C", str(target)]
    if url.startswith("https://gitlab.com/") and os.getenv("CI_JOB_TOKEN"):
        fetch_cmd.extend(
            [
                "-c",
                f"url.https://gitlab-ci-token:{os.environ['CI_JOB_TOKEN']}@gitlab.com/.insteadOf=https://gitlab.com/",
            ]
        )
    fetch_cmd.extend(["fetch", "-q", "--depth", "1", "origin", ref])
    subprocess.run(fetch_cmd, check=True)
    subprocess.run(["git", "-C", str(target), "checkout", "-q", "--detach", "FETCH_HEAD"], check=True)


def _code_blocks(markdown: str) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    pattern = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(markdown):
        lang = match.group(1).strip().lower()
        source = match.group(2).strip()
        heading = _nearest_heading(markdown[: match.start()])
        blocks.append((lang, source, heading))
    return blocks


def _nearest_heading(markdown_prefix: str) -> str:
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", markdown_prefix, flags=re.MULTILINE)
    return headings[-1] if headings else "README example"


def _strip_comments(source: str, client: str) -> str:
    stripped: list[str] = []
    for line in source.splitlines():
        text = line.strip()
        if client == "python" and text.startswith("#"):
            continue
        if client == "qclient" and (text.startswith("/") or text.startswith("//")):
            continue
        stripped.append(line)
    return "\n".join(stripped).strip()


def _comment_text(line: str, client: str) -> str | None:
    text = line.strip()
    if client == "python" and text.startswith("#"):
        return text.lstrip("#").strip()
    if client == "qclient" and text.startswith("//"):
        return text.lstrip("/").strip()
    if client == "qclient" and text.startswith("/") and not text.startswith("\\"):
        return text.lstrip("/").strip()
    return None


def infer_operation_id(source: str) -> str | None:
    calls = re.findall(r"\bsession[.\[]([A-Za-z_][A-Za-z0-9_]*)", source)
    for method in reversed(calls):
        operation_id = METHOD_OPERATIONS.get(method.lower())
        if operation_id:
            return operation_id
    return None


def _sample_label(index: int, source: str, heading: str, client: str) -> str:
    for line in source.splitlines():
        comment = _comment_text(line, client)
        if comment:
            return comment
    return heading or f"README example {index}"


def _python_statement_label(lines: list[str], lineno: int, heading: str, index: int) -> str:
    for line_index in range(lineno - 2, -1, -1):
        text = lines[line_index].strip()
        if not text:
            continue
        if text.startswith("#"):
            return text.lstrip("#").strip()
        break
    return heading or f"README example {index}"


def _python_capture_name(node: ast.AST) -> str | None:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]

    for target in targets:
        if isinstance(target, ast.Name):
            return target.id
    return None


def _is_python_runner_setup(node: ast.AST) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if not isinstance(node, ast.Assign):
        return False
    if not any(isinstance(target, ast.Name) and target.id.endswith("session") for target in node.targets):
        return False
    value = node.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "Session"
    )


def _split_python_readme_block(source: str, heading: str, block_index: int) -> list[tuple[str, str]]:
    try:
        module = ast.parse(source)
    except SyntaxError:
        label = _sample_label(block_index, source, heading, "python")
        return [(_strip_comments(source, "python"), label)]

    lines = source.splitlines()
    samples: list[tuple[str, str]] = []
    pending_setup: list[str] = []

    for node in module.body:
        statement = ast.get_source_segment(source, node) or "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if _is_python_runner_setup(node):
            continue

        operation_id = infer_operation_id(statement)
        if operation_id:
            label = _python_statement_label(lines, node.lineno, heading, block_index)
            parts = [*pending_setup, statement]
            capture_name = _python_capture_name(node)
            if capture_name:
                parts.append(capture_name)
            samples.append(("\n".join(part for part in parts if part.strip()).strip(), label))
            pending_setup = []
        else:
            pending_setup.append(statement)

    return [(sample, label) for sample, label in samples if sample.strip()]


def _split_q_readme_block(source: str, heading: str, block_index: int) -> list[tuple[str, str]]:
    samples: list[tuple[str, str]] = []
    pending_setup: list[str] = []
    pending_label: str | None = None

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        comment = _comment_text(raw_line, "qclient")
        if comment:
            pending_label = comment
            continue

        if infer_operation_id(raw_line):
            label = pending_label or heading or f"README example {block_index}"
            parts = [*pending_setup, raw_line]
            samples.append(("\n".join(part for part in parts if part.strip()).strip(), label))
            pending_setup = []
            pending_label = None
        else:
            pending_setup.append(raw_line)

    return samples


def split_readme_block(source: str, client: str, heading: str, block_index: int) -> list[tuple[str, str]]:
    if client == "python":
        return _split_python_readme_block(source, heading, block_index)
    if client == "qclient":
        return _split_q_readme_block(source, heading, block_index)
    label = _sample_label(block_index, source, heading, client)
    return [(_strip_comments(source, client), label)]


def collect_client_readme_samples(client: str, readme_path: str | Path | None = None) -> list[SpecSample]:
    client = normalize_client(client)
    if client not in CLIENT_READMES:
        raise ValueError("Client README samples require TEST_FILTER=python or TEST_FILTER=qClient.")

    path = Path(readme_path) if readme_path else readme_path_for_client(client)
    markdown = path.read_text(encoding="utf-8")
    config = CLIENT_READMES[client]

    samples: list[SpecSample] = []
    for block_index, (lang, raw_source, heading) in enumerate(_code_blocks(markdown), start=1):
        if lang not in config["langs"]:
            continue
        for sample_source, label in split_readme_block(raw_source, client, heading, block_index):
            source = _strip_comments(sample_source, client)
            if "session." not in source:
                continue
            operation_id = infer_operation_id(source)
            if not operation_id:
                continue
            samples.append(
                SpecSample(
                    method="README",
                    path=str(path),
                    operation_id=operation_id,
                    tags=("Client README",),
                    client=client,
                    label=label,
                    source=source,
                )
            )
    return samples


def filter_client_readme_samples(samples: list[SpecSample], test_filter: str = "") -> list[SpecSample]:
    needle = str(test_filter or "").strip().lower()
    client = normalize_client(needle)
    if client in {"python", "qclient"}:
        return [sample for sample in samples if sample.client == client]
    if not needle:
        return samples
    return [
        sample
        for sample in samples
        if needle in sample.operation_id.lower()
        or needle in sample.label.lower()
        or needle in sample.client.lower()
    ]


def print_client_readme_summary(samples: list[SpecSample], test_filter: str = "") -> None:
    suffix = f" filter={test_filter}" if test_filter else ""
    print(f"validation: suite=client_readmes source=client README files{suffix}", flush=True)
    print(f"Collected {len(samples)} client README sample(s)", flush=True)
    for sample in samples:
        print(
            f"- {sample.client:7} op={sample.operation_key:18} "
            f"label={sample.label} source={sample.path}",
            flush=True,
        )


def _client_prefix(client: str) -> str:
    return f"client_readmes:{client}"


def _runner_for_client(client: str):
    if client == "python":
        return _run_python_sample
    if client == "qclient":
        return _run_qclient_sample
    raise ValueError(f"Unsupported README sample client: {client}")


def _summary(results: list[ClientReadmeResult]) -> dict[str, int]:
    passed = sum(1 for result in results if result.status == "passed")
    failed = sum(1 for result in results if result.status == "failed")
    skipped = sum(1 for result in results if result.status == "skipped")
    return {"passed": passed, "failed": failed, "skipped": skipped}


def _write_report(client: str, results: list[ClientReadmeResult]) -> str:
    cases = [
        {
            "classname": _client_prefix(result.sample.client),
            "name": f"{result.index}/{result.total} ({result.display_op})",
            "status": result.status,
            "message": result.message,
            "time": result.duration_s,
        }
        for result in results
    ]
    return write_junit_report(f"client_readmes-{client}", cases, _summary(results))


def _print_summary(results: list[ClientReadmeResult]) -> None:
    summary = _summary(results)
    passed = summary["passed"]
    failed = summary["failed"]
    total = len(results)
    print(f"[client_readmes] summary: passed={passed} failed={failed} total={total}", flush=True)


def run_client_readme_samples(samples: list[SpecSample], endpoint: str, fail_fast: bool = True) -> int:
    clients = {sample.client for sample in samples}
    if len(clients) != 1:
        raise ValueError("Client README execution requires TEST_FILTER=python or TEST_FILTER=qClient.")
    client = next(iter(clients))
    runner = _runner_for_client(client)

    if validation_verbose():
        print(f"Running {len(samples)} {client} client README sample(s) against {endpoint}", flush=True)
    last_job_id = None
    delete_workflow = DeleteWorkflowState()
    results: list[ClientReadmeResult] = []

    try:
        for index, sample in enumerate(samples, start=1):
            sample_started_at = time.time()
            display_op = _sample_display_op(sample)
            prefix = _client_prefix(client)
            table = sample_table(sample)
            assembly = _extract_assembly(sample.source, sample.client)
            if assembly and not _assembly_available(endpoint, assembly):
                message = f"required assembly {assembly!r} is not configured"
                print(f"[{prefix}] [{index}/{len(samples)}] SKIP {display_op} | {message}", flush=True)
                results.append(
                    ClientReadmeResult(
                        index, len(samples), sample, display_op, "skipped", message, time.time() - sample_started_at
                    )
                )
                continue

            delete_job_id = delete_workflow.job_for(assembly)
            if sample.operation_id in {DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION} and not delete_job_id:
                message = "requires a successful delete_start sample and its returned job id"
                print(f"[{prefix}] [{index}/{len(samples)}] SKIP {display_op} | {message}", flush=True)
                results.append(
                    ClientReadmeResult(
                        index, len(samples), sample, display_op, "skipped", message, time.time() - sample_started_at
                    )
                )
                continue

            execution_job_id = (
                delete_job_id
                if sample.operation_id in {DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION}
                else last_job_id
            )
            delete_scenario: DeleteScenario | None = None
            if validation_verbose():
                print(f"[{prefix}] {index}/{len(samples)} ({display_op}) RUN", flush=True)
            try:
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
                        raise SpecSampleExecutionError("delete_start did not return a job id")
                    delete_workflow.remember(delete_scenario, next_job_id)
                    execution_job_id = next_job_id
                elif sample.operation_id not in {DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION}:
                    last_job_id = next_job_id
                    execution_job_id = last_job_id
                if sample.operation_id == DELETE_CLEAR_OPERATION and delete_job_id:
                    _assert_delete_job_cleared(endpoint, delete_job_id, assembly)
            except Exception as exc:
                message = _ensure_sample_failure(sample, endpoint, execution_job_id, exc)
                if fail_fast:
                    raise SpecSampleExecutionError(message)
                print(f"[{prefix}] [{index}/{len(samples)}] FAIL {display_op}", flush=True)
                print(message, flush=True)
                results.append(
                    ClientReadmeResult(
                        index, len(samples), sample, display_op, "failed", message, time.time() - sample_started_at
                    )
                )
                continue
            try:
                check_text = _sample_check_text(sample, endpoint, table, output, execution_job_id)
            except Exception as exc:
                message = _ensure_sample_failure(sample, endpoint, execution_job_id, exc, output=output)
                if fail_fast:
                    raise SpecSampleExecutionError(message)
                print(f"[{prefix}] [{index}/{len(samples)}] FAIL {display_op}", flush=True)
                print(message, flush=True)
                results.append(
                    ClientReadmeResult(
                        index, len(samples), sample, display_op, "failed", message, time.time() - sample_started_at
                    )
                )
                continue
            if validation_verbose():
                print(f"[{prefix}] {index}/{len(samples)} ({display_op}) check: {check_text}", flush=True)
                print(f"[{prefix}] {index}/{len(samples)} ({display_op}) PASS", flush=True)
            else:
                print(f"[{prefix}] [{index}/{len(samples)}] PASS {display_op} | {check_text}", flush=True)
            results.append(
                ClientReadmeResult(index, len(samples), sample, display_op, "passed", duration_s=time.time() - sample_started_at)
            )
    finally:
        for scenario in delete_workflow.all_fixtures():
            _cleanup_delete_fixture(endpoint, scenario)

    _write_report(client, results)
    _print_summary(results)
    return 1 if any(result.status == "failed" for result in results) else 0
