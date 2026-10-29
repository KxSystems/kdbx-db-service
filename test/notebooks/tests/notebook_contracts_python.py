import ast
import re

from notebook_test_utils import (
    colored,
    has_html_output,
    has_output,
    output_text,
    parse_python_literal,
    payload_indicates_failure,
    short_text,
)


ASYNC_JOB_OPS = {"create_table", "import_file", "import_data", "import_database"}


def _call_operation(node):
    if not isinstance(node, ast.Call):
        return None

    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return None

    normalized = name.lower().replace("_", "")
    if normalized == "createtable":
        return "create_table"
    if normalized in {"importfiles", "importfile"}:
        return "import_file"
    if normalized == "importdata":
        return "import_data"
    if normalized == "importdatabase":
        return "import_database"
    return None


def _target_to_source(node):
    try:
        return ast.unparse(node)
    except Exception:
        return None


def capture_async_job_cell_source(source: str, cell_index: int):
    """Capture the final async job expression so the test harness can poll it."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return source, None, None

    if not module.body:
        return source, None, None

    last = module.body[-1]
    capture_name = f"_test_async_job_{cell_index}"

    if isinstance(last, ast.Expr):
        op = _call_operation(last.value)
        if op not in ASYNC_JOB_OPS:
            return source, None, None
        module.body[-1] = ast.Assign(
            targets=[ast.Name(id=capture_name, ctx=ast.Store())],
            value=last.value,
        )
        module.body.append(ast.Expr(value=ast.Name(id=capture_name, ctx=ast.Load())))
        ast.fix_missing_locations(module)
        return ast.unparse(module), capture_name, op

    if isinstance(last, (ast.Assign, ast.AnnAssign)):
        value = last.value
        op = _call_operation(value)
        if op not in ASYNC_JOB_OPS:
            return source, None, None

        if isinstance(last, ast.Assign) and last.targets:
            assigned_source = _target_to_source(last.targets[0])
        elif isinstance(last, ast.AnnAssign):
            assigned_source = _target_to_source(last.target)
        else:
            assigned_source = None

        if not assigned_source:
            return source, None, None

        module.body.append(
            ast.Assign(
                targets=[ast.Name(id=capture_name, ctx=ast.Store())],
                value=ast.parse(assigned_source, mode="eval").body,
            )
        )
        ast.fix_missing_locations(module)
        return ast.unparse(module), capture_name, op

    return source, None, None


def validate_python_operations(cells_by_op):
    ops_needing_output = {
        "list_tables",
        "describe_table",
        "import_status",
        "query_simple",
        "query_sql",
        "query_q",
    }

    for op, items in cells_by_op.items():
        if op in ops_needing_output and not any(has_output(cell) for _, _, cell in items):
            raise AssertionError(colored(f" - FAIL: python operation {op} produced no output.", "red"))

    for idx, op, cell in cells_by_op.get("list_tables", []):
        parsed = parse_python_literal(output_text(cell))
        if parsed is None or not isinstance(parsed, list):
            raise AssertionError(
                colored(f" - FAIL: python cell {idx} ({op}) expected list output.", "red")
            )
        if not all(isinstance(item, str) for item in parsed):
            raise AssertionError(
                colored(f" - FAIL: python cell {idx} ({op}) expected list of table names.", "red")
            )

    for op in {"create_table", "describe_table", "drop_table"}:
        for idx, _op, cell in cells_by_op.get(op, []):
            if not re.search(r"table\s*=", cell.get("source", "")):
                raise AssertionError(
                    colored(f" - FAIL: python cell {idx} ({op}) missing required `table` parameter.", "red")
                )

    for op in {"create_table", "import_file", "import_data", "import_database", "import_status"}:
        for idx, _op, cell in cells_by_op.get(op, []):
            parsed = parse_python_literal(output_text(cell))
            if parsed is not None and not isinstance(parsed, dict):
                raise AssertionError(
                    colored(f" - FAIL: python cell {idx} ({op}) expected dict-like status output.", "red")
                )

    for idx, op, cell in cells_by_op.get("import_status", []):
        text_raw = output_text(cell)
        parsed = parse_python_literal(text_raw)
        text = text_raw.lower()
        if parsed is None and ("status" not in text and "state" not in text):
            raise AssertionError(
                colored(
                    f" - FAIL: python cell {idx} ({op}) expected status-like output. "
                    f"output={short_text(text_raw)}",
                    "red",
                )
            )
        if payload_indicates_failure(parsed if parsed is not None else {}, text_raw):
            raise AssertionError(
                colored(
                    f" - FAIL: python cell {idx} ({op}) indicates import failure. "
                    f"output={short_text(text_raw)}",
                    "red",
                )
            )

    for idx, op, cell in cells_by_op.get("query_simple", []):
        parsed = parse_python_literal(output_text(cell))
        if not isinstance(parsed, (list, dict)) and not has_html_output(cell):
            raise AssertionError(
                colored(
                    f" - FAIL: python cell {idx} ({op}) expected list/dict or dataframe output.",
                    "red",
                )
            )

    for idx, op, cell in cells_by_op.get("query_sql", []):
        text = output_text(cell).lower()
        if not has_html_output(cell) and not ("instrumentid" in text and "category" in text):
            raise AssertionError(
                colored(
                    f" - FAIL: python cell {idx} ({op}) expected dataframe-like output.",
                    "red",
                )
            )

    for idx, op, cell in cells_by_op.get("query_q", []):
        text = output_text(cell).lower()
        if not has_html_output(cell) and not all(k in text for k in ["trddate", "sym", "o", "h", "l", "c"]):
            raise AssertionError(
                colored(
                    f" - FAIL: python cell {idx} ({op}) expected dataframe-like output.",
                    "red",
                )
            )
