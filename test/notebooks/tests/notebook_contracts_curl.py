from notebook_test_utils import (
    JSON_PARSE_ERROR,
    colored,
    has_output,
    output_text,
    parse_json_text,
    payload_indicates_failure,
    short_text,
    validation_verbose,
)


def validate_curl_operations(cells_by_op):
    for op, items in cells_by_op.items():
        for idx, _op, cell in items:
            source_text = cell.get("source", "")
            is_placeholder_status_cell = op == "import_status" and "/imports/NAME" in source_text
            allow_http_error = "validation-allow-http-error" in cell.get("metadata", {}).get("tags", [])

            if not has_output(cell):
                raise AssertionError(colored(f" - FAIL: curl cell {idx} ({op}) has no output.", "red"))

            if allow_http_error:
                continue

            text = output_text(cell)
            payload = parse_json_text(text)
            if payload is JSON_PARSE_ERROR and not op.startswith("query_"):
                raise AssertionError(
                    colored(f" - FAIL: curl cell {idx} ({op}) did not return valid JSON output.", "red")
                )
            if payload is JSON_PARSE_ERROR:
                continue
            if isinstance(payload, str):
                nested = parse_json_text(payload)
                if nested is not JSON_PARSE_ERROR:
                    payload = nested

            if op == "api_call" and payload_indicates_failure(payload, text):
                raise AssertionError(
                    colored(
                        f" - FAIL: curl cell {idx} ({op}) returned an HTTP/API error. "
                        f"output={short_text(text)}",
                        "red",
                    )
                )

            if op.startswith("query_") and isinstance(payload, dict):
                header = payload.get("header")
                rc = header.get("rc") if isinstance(header, dict) else None
                if rc not in (None, 0, "0"):
                    ac = header.get("ac")
                    corr = header.get("corr")
                    error = header.get("ai") or "DB Service query failed"
                    raise AssertionError(
                        colored(
                            f" - FAIL: curl cell {idx} ({op}) DB Service query failed: "
                            f"rc={rc} ac={ac} corr={corr or 'unknown'} error={error}",
                            "red",
                        )
                    )

            if (
                op in {"import_file", "import_data", "import_status"}
                and payload_indicates_failure(payload, text)
                and not is_placeholder_status_cell
            ):
                raise AssertionError(
                    colored(
                        f" - FAIL: curl cell {idx} ({op}) indicates import failure. "
                        f"output={short_text(text)}",
                        "red",
                    )
                )

            if op == "list_tables" and not isinstance(payload, list):
                raise AssertionError(
                    colored(f" - FAIL: curl cell {idx} ({op}) expected list JSON output.", "red")
                )
            if op == "list_tables" and not all(isinstance(item, str) for item in payload):
                raise AssertionError(
                    colored(f" - FAIL: curl cell {idx} ({op}) expected list of table names.", "red")
                )

            if op in {"query_simple", "query_sql"} and not isinstance(payload, (list, dict)):
                raise AssertionError(
                    colored(f" - FAIL: curl cell {idx} ({op}) expected list/dict JSON output.", "red")
                )
            if op == "query_q" and not isinstance(payload, (list, dict)) and validation_verbose():
                print(
                    colored(
                        f" - WARN: curl cell {idx} ({op}) returned scalar/empty JSON rather than list/dict JSON.",
                        "yellow",
                    )
                )

            if op == "import_status":
                if is_placeholder_status_cell:
                    # The notebook uses /imports/NAME placeholder.
                    # Runtime polling validates the real job id separately.
                    continue
                if isinstance(payload, dict):
                    keys = {str(k).lower() for k in payload.keys()}
                else:
                    keys = set()
                has_statusish_key = bool(
                    {"status", "state", "code", "error", "message", "name", "id"} & keys
                )
                if not has_statusish_key:
                    text = output_text(cell).lower()
                    has_statusish_key = any(
                        token in text for token in ["status", "state", "code", "error", "message", "job"]
                    )
                if not has_statusish_key:
                    raise AssertionError(
                        colored(
                            f" - FAIL: curl cell {idx} ({op}) missing status/error metadata in output.",
                            "red",
                        )
                    )
