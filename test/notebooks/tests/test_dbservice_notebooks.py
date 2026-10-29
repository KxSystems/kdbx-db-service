import unittest
import os

from notebook_test_utils import (
    execute_notebook,
    notebook_progress_prefix,
    notebook_path,
    reset_dbservice_tables,
    validation_verbose,
)


DEFAULT_NOTEBOOKS = {
    "curl_notebook.ipynb",
    "python_notebook.ipynb",
    "qClient_notebook.ipynb",
}


def _explicit_notebook_source():
    raw = os.getenv("NOTEBOOK_TEST_SOURCE", "").strip()
    if not raw:
        return None
    return os.path.abspath(raw)

def _selected_notebook_filter():
    raw = os.getenv("NOTEBOOK_UNDER_TEST", "").strip()
    if not raw:
        return None
    aliases = {
        "curl": "curl_notebook",
        "python": "python_notebook",
        "qclient": "qClient_notebook",
        "q_client": "qClient_notebook",
        "q-client": "qClient_notebook",
    }
    value = aliases.get(raw.lower(), raw).lower()
    if not value.endswith(".ipynb"):
        value = f"{value}.ipynb"
    return value


def _should_run_notebook(notebook_name: str) -> bool:
    explicit = _explicit_notebook_source()
    if explicit:
        return os.path.basename(explicit).lower() == notebook_name.lower()
    selected = _selected_notebook_filter()
    if not selected:
        return True
    return notebook_name.lower() == selected


def _run_notebook(notebook_name: str, source_path: str | None = None):
    detailed_progress = validation_verbose()
    notebook = source_path or notebook_path(notebook_name)
    progress_prefix = notebook_progress_prefix(notebook)
    if detailed_progress:
        print()
    reset_dbservice_tables(show_progress=detailed_progress, notebook_name=progress_prefix)
    try:
        result = execute_notebook(notebook)
        if detailed_progress:
            print(f"[{progress_prefix}] NOTEBOOK PASS", flush=True)
            print(f"[{progress_prefix}] {result['summary_line']}", flush=True)
        else:
            print(
                f"PASS: {notebook} ({result['summary_line'].removeprefix('summary: ')})",
                flush=True,
            )
    finally:
        reset_dbservice_tables(show_progress=detailed_progress, notebook_name=progress_prefix)


class TestDBServiceNotebookExecution(unittest.TestCase):
    def test_execute_curl_notebook(self):
        if not _should_run_notebook("curl_notebook.ipynb"):
            self.skipTest("Skipping due to NOTEBOOK_UNDER_TEST filter")
        _run_notebook("curl_notebook.ipynb", _explicit_notebook_source())

    def test_execute_python_notebook(self):
        if not _should_run_notebook("python_notebook.ipynb"):
            self.skipTest("Skipping due to NOTEBOOK_UNDER_TEST filter")
        _run_notebook("python_notebook.ipynb", _explicit_notebook_source())

    def test_execute_qclient_notebook(self):
        if not _should_run_notebook("qClient_notebook.ipynb"):
            self.skipTest("Skipping due to NOTEBOOK_UNDER_TEST filter")
        _run_notebook("qClient_notebook.ipynb", _explicit_notebook_source())

    def test_execute_custom_notebook(self):
        explicit = _explicit_notebook_source()
        if not explicit or os.path.basename(explicit).lower() in {
            name.lower() for name in DEFAULT_NOTEBOOKS
        }:
            self.skipTest("No custom NOTEBOOK_TEST_SOURCE selected")
        _run_notebook(os.path.basename(explicit), explicit)


if __name__ == "__main__":
    unittest.main()
