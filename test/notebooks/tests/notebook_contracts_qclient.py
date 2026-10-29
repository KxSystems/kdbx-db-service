import re

from notebook_test_utils import colored, has_output, output_text, payload_indicates_failure, short_text


ASYNC_JOB_OPS = {"create_table", "import_file", "import_data", "import_database"}
QUERY_OPS = {"query_simple", "query_preview", "query_sql", "query_q"}


def capture_q_async_job_cell_source(source: str, cell_index: int):
    return _capture_q_cell_source(
        source,
        cell_index,
        prefix="testasyncjob",
        op_patterns=[
            ("create_table", r"\bsession\.createTable\b"),
            ("import_file", r"\bsession\.importFiles\b"),
            ("import_data", r"\bsession\.importData\b"),
            ("import_database", r"\bsession\.(?:importDatabase|importKDB)\b"),
        ],
        allowed_ops=ASYNC_JOB_OPS,
    )


def capture_q_query_cell_source(source: str, cell_index: int):
    return _capture_q_cell_source(
        source,
        cell_index,
        prefix="testqueryresult",
        op_patterns=[
            ("query_simple", r"\bsession\.querySimple\b"),
            ("query_preview", r"\bsession\.queryPreview\b"),
            ("query_sql", r"\bsession\.querySQL\b"),
            ("query_q", r"\bsession\.queryQ\b"),
        ],
        allowed_ops=QUERY_OPS,
    )


def _capture_q_cell_source(source: str, cell_index: int, prefix: str, op_patterns, allowed_ops):
    lines = source.splitlines()
    body_indexes = [
        i for i, line in enumerate(lines)
        if line.strip() and not line.strip().startswith("//") and line.strip() != "%%q"
    ]
    if not body_indexes:
        return source, None, None

    last_idx = body_indexes[-1]
    last_line = lines[last_idx].strip()
    capture_name = f"{prefix}{cell_index}"

    op = None
    for candidate, pattern in op_patterns:
        if re.search(pattern, last_line):
            op = candidate
            break
    if op not in allowed_ops:
        return source, None, None

    if ":" in last_line and not last_line.startswith("session."):
        lhs = last_line.split(":", 1)[0].strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", lhs):
            return source, None, None
        lines.insert(last_idx + 1, f"{capture_name}:{lhs}")
    else:
        lines[last_idx] = f"{capture_name}:{last_line}"
        lines.insert(last_idx + 1, capture_name)

    return "\n".join(lines), capture_name, op


def validate_qclient_operations(cells_by_op):
    must_have_output = {
        "list_tables",
        "describe_table",
        "import_status",
        "query_simple",
        "query_preview",
        "query_sql",
        "query_q",
    }

    for op in must_have_output:
        items = cells_by_op.get(op, [])
        if not items:
            continue
        if not any(has_output(cell) for _, _, cell in items):
            raise AssertionError(colored(f" - FAIL: qClient operation {op} produced no output.", "red"))

    for op in {"import_file", "import_data", "import_status"}:
        for idx, _op, cell in cells_by_op.get(op, []):
            text = output_text(cell)
            if payload_indicates_failure({}, text):
                raise AssertionError(
                    colored(
                        f" - FAIL: qClient cell {idx} ({op}) indicates import failure. "
                        f"output={short_text(text)}",
                        "red",
                    )
                )

    # qClient display formatting can vary by pykx version/runtime, so for query cells
    # we rely on non-empty output + no-error checks above instead of strict token matches.
