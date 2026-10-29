#!/usr/bin/env python3
"""DB Service validation harness entrypoint.

This is intentionally thin: source-specific collectors/runners live in nearby
modules, while this command selects the validation suite to run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


IMPLEMENTED_SUITES = {"notebooks", "spec_samples", "client_readmes"}
FUTURE_SUITES = {"core", "generated", "parity", "performance", "resilience"}
VALID_SUITES = IMPLEMENTED_SUITES | FUTURE_SUITES

NOTEBOOK_ALIASES = {
    "curl": "curl_notebook",
    "curl_notebook": "curl_notebook",
    "curl_notebook.ipynb": "curl_notebook",
    "python": "python_notebook",
    "python_notebook": "python_notebook",
    "python_notebook.ipynb": "python_notebook",
    "qclient": "qClient_notebook",
    "q_client": "qClient_notebook",
    "q-client": "qClient_notebook",
    "qClient_notebook": "qClient_notebook",
    "qclient_notebook": "qClient_notebook",
    "qClient_notebook.ipynb": "qClient_notebook",
    "qclient_notebook.ipynb": "qClient_notebook",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DB Service validation suites.")
    parser.add_argument(
        "--suite",
        default=os.getenv("TEST_SUITE", "notebooks"),
        choices=sorted(VALID_SUITES),
        help="Validation test suite to run.",
    )
    parser.add_argument(
        "--filter",
        default=os.getenv("TEST_FILTER", ""),
        help="Optional client or operation filter, for example qClient or query.",
    )
    parser.add_argument(
        "--python",
        default=os.getenv("PYTHON", sys.executable),
        help="Python executable to use for child test runs.",
    )
    parser.add_argument(
        "--test-source",
        default=os.getenv("TEST_SOURCE", ""),
        help="Notebook, OpenAPI specification, README, or scenario file used by the selected suite.",
    )
    parser.add_argument(
        "--run-samples",
        action="store_true",
        default=os.getenv("SPEC_SAMPLES_RUN", "0") == "1",
        help="Execute collected samples instead of only listing them.",
    )
    parser.add_argument(
        "--fail-fast",
        default=os.getenv("FAIL_FAST", "0"),
        choices=("0", "1", "false", "true", "False", "True"),
        help="Stop on the first validation failure. Use 0 to keep running and print a summary.",
    )
    return parser.parse_args()


def truthy(value: str) -> bool:
    return str(value).strip().lower() not in {"0", "false"}


def configure_python_client_source() -> int:
    client_dir = os.getenv("DBSERVICE_PYTHON_CLIENT_DIR", "").strip()
    if not client_dir:
        return 0

    src_dir = Path(client_dir) / "src"
    package_init = src_dir / "dbservice_client" / "__init__.py"
    if not package_init.exists():
        print(f"Local Python client source is not valid: {client_dir}", file=sys.stderr)
        print(f"Expected to find: {package_init}", file=sys.stderr)
        return 2

    src_text = str(src_dir)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)

    current_pythonpath = os.getenv("PYTHONPATH", "")
    pythonpath_parts = [part for part in current_pythonpath.split(os.pathsep) if part]
    if src_text not in pythonpath_parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([src_text] + pythonpath_parts)
    return 0


def normalize_notebook_filter(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    normalized = NOTEBOOK_ALIASES.get(value) or NOTEBOOK_ALIASES.get(value.lower())
    if normalized is None:
        raise ValueError(
            f"Unsupported TEST_FILTER: {value}. Use curl, python, or qClient."
        )
    return normalized


def run_notebooks(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    try:
        test_filter = normalize_notebook_filter(args.filter)
    except ValueError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2
    if test_filter:
        env["NOTEBOOK_UNDER_TEST"] = test_filter
    if args.test_source:
        env["NOTEBOOK_TEST_SOURCE"] = args.test_source

    cmd = [
        args.python,
        "-m",
        "unittest",
        "discover",
        "-s",
        "test/notebooks/tests",
        "-p",
        "test_*.py",
        "-v",
    ]
    print(
        f"validation: suite={args.suite} source={args.test_source or 'notebooks'}"
        + (f" filter={test_filter}" if test_filter else ""),
        flush=True,
    )
    return subprocess.call(cmd, env=env)


def run_spec_samples(args: argparse.Namespace) -> int:
    from spec_samples import (
        SpecSampleExecutionError,
        collect_spec_samples,
        default_spec_file,
        filter_spec_samples,
        load_openapi_spec,
        print_spec_sample_summary,
        run_spec_samples as execute_spec_samples,
    )

    spec_file = args.test_source or default_spec_file()
    spec = load_openapi_spec(spec_file)
    samples = collect_spec_samples(spec)
    samples = filter_spec_samples(samples, args.filter)
    if args.run_samples:
        endpoint = os.getenv("NOTEBOOK_IMPORT_STATUS_ENDPOINT", "http://localhost:8080")
        try:
            return execute_spec_samples(samples, endpoint=endpoint, fail_fast=truthy(args.fail_fast))
        except SpecSampleExecutionError as exc:
            print(str(exc), file=sys.stderr, flush=True)
            return 1
    print_spec_sample_summary(samples, spec_file, test_filter=args.filter)
    return 0


def run_client_readmes(args: argparse.Namespace) -> int:
    from client_readmes import (
        SpecSampleExecutionError,
        collect_client_readme_samples,
        filter_client_readme_samples,
        print_client_readme_summary,
        run_client_readme_samples as execute_client_readme_samples,
    )

    samples = []
    test_filter = args.filter.strip()
    normalized_filter = test_filter.lower()
    if args.test_source and not test_filter:
        print(
            "TEST_SOURCE for client_readmes requires TEST_FILTER=python or TEST_FILTER=qClient.",
            file=sys.stderr,
        )
        return 2
    if normalized_filter not in {"", "python", "q", "qclient", "q_client", "q-client"}:
        print(
            "client_readmes supports TEST_FILTER=python or TEST_FILTER=qClient.",
            file=sys.stderr,
        )
        return 2
    if test_filter:
        samples = collect_client_readme_samples(test_filter, readme_path=args.test_source or None)
    else:
        for client in ("python", "qClient"):
            samples.extend(collect_client_readme_samples(client))
    samples = filter_client_readme_samples(samples, test_filter)
    if args.run_samples:
        endpoint = os.getenv("NOTEBOOK_IMPORT_STATUS_ENDPOINT", "http://localhost:8080")
        readme_files = sorted({sample.path for sample in samples})
        if readme_files:
            print(f"Resolved README file(s): {', '.join(readme_files)}", flush=True)
        if not test_filter:
            failed = False
            for index, client in enumerate(("python", "qClient")):
                if index:
                    print(flush=True)
                client_samples = filter_client_readme_samples(samples, client)
                try:
                    rc = execute_client_readme_samples(
                        client_samples,
                        endpoint=endpoint,
                        fail_fast=truthy(args.fail_fast),
                    )
                except SpecSampleExecutionError as exc:
                    print(str(exc), file=sys.stderr, flush=True)
                    rc = 1
                if rc:
                    failed = True
                    if truthy(args.fail_fast):
                        return rc
            return 1 if failed else 0
        try:
            return execute_client_readme_samples(
                samples,
                endpoint=endpoint,
                fail_fast=truthy(args.fail_fast),
            )
        except SpecSampleExecutionError as exc:
            print(str(exc), file=sys.stderr, flush=True)
            return 1
    print_client_readme_summary(samples, test_filter=test_filter)
    return 0


def main() -> int:
    args = parse_args()
    configured = configure_python_client_source()
    if configured:
        return configured
    if args.suite in FUTURE_SUITES:
        print(
            f"test suite '{args.suite}' is reserved but not implemented yet.",
            file=sys.stderr,
        )
        return 2
    if args.suite == "notebooks":
        return run_notebooks(args)
    if args.suite == "spec_samples":
        return run_spec_samples(args)
    if args.suite == "client_readmes":
        return run_client_readmes(args)
    print(f"unknown test suite: {args.suite}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
