"""Tests for concise notebook validation output."""

from __future__ import annotations

import json
import os
import sys
import unittest


NOTEBOOK_TEST_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "notebooks", "tests")
)
sys.path.insert(0, NOTEBOOK_TEST_DIR)

from notebook_test_utils import (  # noqa: E402
    QCLIENT_QUERY_ERROR_HELPER_SOURCE,
    format_notebook_failure,
    harden_curl_notebook_http_errors,
    output_text,
)
from notebook_contracts_curl import validate_curl_operations  # noqa: E402
from notebook_contracts_python import validate_python_operations  # noqa: E402
from notebook_contracts_qclient import capture_q_query_cell_source  # noqa: E402


class NotebookFailureOutputTests(unittest.TestCase):
    def test_unknown_curl_api_error_is_rejected(self):
        cell = {
            "source": 'curl -s "http://kx-db-gw:8080/api/v0/future-endpoint"',
            "outputs": [
                {
                    "output_type": "stream",
                    "text": json.dumps({"code": 404, "message": "Not Found"}),
                }
            ],
        }

        with self.assertRaisesRegex(AssertionError, "HTTP/API error.*Not Found"):
            validate_curl_operations({"api_call": [(4, "api_call", cell)]})

    def test_curl_api_cells_are_hardened_only_in_execution_copy(self):
        original = '%%bash\ncurl -s "http://localhost:8080/api/v0/future-endpoint" | jq'
        notebook = {"cells": [{"cell_type": "code", "metadata": {}, "source": original}]}

        harden_curl_notebook_http_errors(notebook)

        hardened = notebook["cells"][0]["source"]
        self.assertIn("set -o pipefail", hardened)
        self.assertIn("curl --fail-with-body", hardened)
        self.assertIn(original.splitlines()[1], hardened)

    def test_http_error_metadata_tag_leaves_curl_cell_unchanged(self):
        original = '%%bash\ncurl -s "http://localhost:8080/api/v0/expected-error" | jq'
        notebook = {
            "cells": [
                {
                    "cell_type": "code",
                    "metadata": {"tags": ["validation-allow-http-error"]},
                    "source": original,
                }
            ]
        }

        harden_curl_notebook_http_errors(notebook)

        self.assertEqual(notebook["cells"][0]["source"], original)

    def test_http_error_metadata_tag_allows_error_response(self):
        cell = {
            "source": 'curl -s "http://kx-db-gw:8080/api/v0/expected-error"',
            "metadata": {"tags": ["validation-allow-http-error"]},
            "outputs": [
                {
                    "output_type": "stream",
                    "text": json.dumps({"code": 404, "message": "Not Found"}),
                }
            ],
        }

        validate_curl_operations({"api_call": [(4, "api_call", cell)]})

    def test_failure_identifies_original_cell_and_command(self):
        notebook = os.path.join(os.getcwd(), "notebooks", "curl_notebook.ipynb")

        message = format_notebook_failure(
            notebook,
            21,
            "query_simple",
            'curl -X POST "http://kx-db-gw:8080/api/v0/query/simple"',
            "HTTP 400 Bad Request: type",
            executed_file="executed-notebook/curl_notebook_executed.ipynb",
            report_file="validation-reports/notebook-curl.xml",
        )

        self.assertIn("File: notebooks/curl_notebook.ipynb", message)
        self.assertIn("Cell: 21 (position in the original notebook)", message)
        self.assertIn("Operation: query_simple", message)
        self.assertIn('curl -X POST "http://kx-db-gw:8080/api/v0/query/simple"', message)
        self.assertIn("Error: HTTP 400 Bad Request: type", message)

    def test_curl_query_rejects_dbservice_error_in_notebook_output(self):
        response = {
            "header": {
                "corr": "37b3257c-66c1-4b70-934f-276b880d130f",
                "rc": 6,
                "ac": 11,
                "ai": "Unexpected error (type) encountered executing .query.simple",
            },
            "payload": [],
        }
        cell = {
            "source": 'curl -X POST "http://kx-db-gw:8080/api/v0/query/simple"',
            "outputs": [{"output_type": "stream", "text": json.dumps(response)}],
        }

        with self.assertRaisesRegex(
            AssertionError,
            "rc=6 ac=11 corr=37b3257c-66c1-4b70-934f-276b880d130f.*Unexpected error",
        ):
            validate_curl_operations({"query_simple": [(21, "query_simple", cell)]})

    def test_curl_query_reassembles_split_jupyter_output(self):
        response = {
            "header": {"corr": "e9a31a9f-3094-41ab-8fde-81906c590b15", "rc": 0, "ac": 0},
            "payload": [{"sym": "EURUSD"}],
        }
        rendered = json.dumps(response, indent=2)
        split_at = rendered.index("41ab") + 2
        cell = {
            "source": 'curl -X POST "http://kx-db-gw:8080/api/v0/query/simple"',
            "outputs": [
                {"output_type": "stream", "text": rendered[:split_at]},
                {"output_type": "stream", "text": rendered[split_at:]},
            ],
        }

        self.assertEqual(json.loads(output_text(cell)), response)
        validate_curl_operations({"query_simple": [(19, "query_simple", cell)]})

    def test_output_text_separates_distinct_outputs(self):
        cell = {
            "outputs": [
                {"output_type": "stream", "name": "stdout", "text": "first"},
                {"output_type": "display_data", "data": {"text/plain": "second"}},
            ]
        }

        self.assertEqual(output_text(cell), "first\nsecond")


class QClientQueryOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = {}
        exec(QCLIENT_QUERY_ERROR_HELPER_SOURCE, namespace)
        cls.assert_query_success = staticmethod(namespace["_test_assert_q_query_success"])

    def test_q_table_is_accepted(self):
        self.assert_query_success(
            98,
            ["ts", "sym", "status", "error"],
            lambda _key: self.fail("regular table fields must not be read as an error"),
        )

    def test_qclient_error_dictionary_is_rejected_with_details(self):
        response = {
            "status": "200",
            "error": "Unexpected error (type) encountered executing .query.simple",
            "details": "rc=6 ac=11",
        }

        with self.assertRaisesRegex(RuntimeError, "qClient query returned an error response") as raised:
            self.assert_query_success(99, list(response), response.get)

        message = str(raised.exception)
        self.assertIn("status=200", message)
        self.assertIn("Unexpected error (type)", message)
        self.assertIn("details=rc=6 ac=11", message)

    def test_variadic_query_without_brackets_is_captured(self):
        source, capture_name, operation = capture_q_query_cell_source(
            'session.querySQL"SELECT * FROM fxquote"',
            12,
        )

        self.assertEqual(capture_name, "testqueryresult12")
        self.assertEqual(operation, "query_sql")
        self.assertIn('testqueryresult12:session.querySQL"SELECT * FROM fxquote"', source)


class PythonQueryOutputTests(unittest.TestCase):
    def test_query_simple_accepts_pandas_dataframe_output(self):
        cell = {
            "source": 'session.query_simple(table="fxquote", return_as="pandas")',
            "outputs": [
                {
                    "output_type": "execute_result",
                    "data": {
                        "text/plain": "        ts     sym category\n0  10:00:00  EURUSD    Major",
                        "text/html": (
                            "<table><thead><tr><th></th><th>ts</th><th>sym</th>"
                            "<th>category</th></tr></thead><tbody><tr><th>0</th>"
                            "<td>10:00:00</td><td>EURUSD</td><td>Major</td>"
                            "</tr></tbody></table>"
                        ),
                    },
                }
            ],
        }

        validate_python_operations({"query_simple": [(23, "query_simple", cell)]})

    def test_query_simple_rejects_unstructured_output(self):
        cell = {
            "source": 'session.query_simple(table="fxquote")',
            "outputs": [{"output_type": "stream", "text": "unexpected"}],
        }

        with self.assertRaisesRegex(AssertionError, "list/dict or dataframe"):
            validate_python_operations({"query_simple": [(23, "query_simple", cell)]})
