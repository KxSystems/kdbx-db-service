"""Tests for shared validation harness helpers."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(__file__))

from core import (  # noqa: E402
    classify_operation,
    extract_job_id,
    extract_table_param_from_source,
    reset_dbservice_tables,
)


class ExtractTableParamTests(unittest.TestCase):
    def test_extracts_python_keyword_table(self):
        source = 'session.create_table(table="fxquote", schema=schema)'

        self.assertEqual(extract_table_param_from_source(source), "fxquote")

    def test_extracts_curl_json_table(self):
        source = '''curl -d '{"table": "fxquote", "path": "fxquote.parquet"}' /api/v0/imports/files'''

        self.assertEqual(extract_table_param_from_source(source), "fxquote")

    def test_extracts_curl_table_url(self):
        source = 'curl -X POST http://localhost:8080/api/v0/tables/instruments'

        self.assertEqual(extract_table_param_from_source(source), "instruments")

    def test_extracts_q_keyed_dictionary_symbol_table(self):
        source = "session.createTable([table:`fxquote; tableType:`partitioned; columns:fxquoteCols])"

        self.assertEqual(extract_table_param_from_source(source), "fxquote")

    def test_extracts_q_keyed_dictionary_string_table(self):
        source = 'session.createTable([table:"orders"; tableType:"partitioned"; columns:cols])'

        self.assertEqual(extract_table_param_from_source(source), "orders")

    def test_extracts_q_bang_dictionary_string_table_when_table_is_not_first(self):
        source = 'session.createTable `type`table`columns!("partitioned";"quotes";cols)'

        self.assertEqual(extract_table_param_from_source(source), "quotes")

    def test_extracts_q_bang_dictionary_string_table_when_table_is_first(self):
        source = 'session.createTable `table`type`columns!("fxquote";"partitioned";cols)'

        self.assertEqual(extract_table_param_from_source(source), "fxquote")

    def test_extracts_q_bang_dictionary_symbol_table_when_table_is_not_first(self):
        source = "session.createTable[`type`table`columns!(`partitioned;`trades;cols)]"

        self.assertEqual(extract_table_param_from_source(source), "trades")

    def test_extracts_q_bang_dictionary_symbol_table_when_table_is_first(self):
        source = "session.createTable[`table`type`columns!(`instruments;`partitioned;cols)]"

        self.assertEqual(extract_table_param_from_source(source), "instruments")

    def test_extracts_q_variadic_create_table_symbol(self):
        source = "session.createTable[`fxquote; ([tableType:`partitioned; columns:cols])]"

        self.assertEqual(extract_table_param_from_source(source), "fxquote")

    def test_extracts_q_variadic_import_table_string(self):
        source = 'session.importFiles["fxquote";"fxquote.parquet"]'

        self.assertEqual(extract_table_param_from_source(source), "fxquote")

    def test_extracts_q_variadic_drop_table_string(self):
        source = 'session.dropTable["instruments"]'

        self.assertEqual(extract_table_param_from_source(source), "instruments")

    def test_does_not_treat_q_query_text_as_a_table(self):
        source = 'session.querySQL"SELECT * FROM fxquote"'

        self.assertIsNone(extract_table_param_from_source(source))


class ExtractJobIdTests(unittest.TestCase):
    def test_extracts_helper_job_text(self):
        text = "check: status=completed table=fxquote job=bd0871b0-fe7f-6d6e-c675-3dfe07154fd5"

        self.assertEqual(extract_job_id({}, text), "bd0871b0-fe7f-6d6e-c675-3dfe07154fd5")

    def test_ignores_empty_helper_job_text(self):
        self.assertIsNone(extract_job_id({}, "check: status=completed job=None"))


class ClassifyQClientOperationTests(unittest.TestCase):
    def test_classifies_variadic_query_without_brackets(self):
        self.assertEqual(classify_operation('session.querySQL"SELECT * FROM fxquote"'), "query_sql")

    def test_classifies_qclient_delete_start(self):
        self.assertEqual(
            classify_operation('session.deleteRows["trade";();([startTS:2026.04.03D])]'),
            "delete_start",
        )

    def test_classifies_query_preview(self):
        self.assertEqual(
            classify_operation('session.queryPreview[`fxquote;([limit:5])]'),
            "query_preview",
        )


class ClassifyDeleteOperationTests(unittest.TestCase):
    def test_classifies_python_delete_status(self):
        self.assertEqual(
            classify_operation('session.get_delete(job_id="abc")'),
            "delete_status",
        )

    def test_classifies_curl_delete_clear(self):
        self.assertEqual(
            classify_operation('curl -X DELETE http://localhost:8080/api/v0/deletes/abc'),
            "delete_clear",
        )

    def test_classifies_import_kdb_name(self):
        self.assertEqual(classify_operation('session.importKDB"fxquote-hdb"'), "import_database")

    def test_classifies_unknown_dbservice_endpoint_as_generic_api_call(self):
        source = 'curl -s http://localhost:8080/api/v0/future-endpoint'

        self.assertEqual(classify_operation(source), "api_call")


class ResetDBServiceTablesTests(unittest.TestCase):
    @mock.patch("core.urllib.request.urlopen")
    @mock.patch("core.list_tables_rest", return_value=["orders", "fxquote"])
    def test_discovers_current_tables_instead_of_assuming_names(self, list_tables, urlopen):
        reset_dbservice_tables(endpoint="http://db-service")

        list_tables.assert_called_once_with("http://db-service")
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(
            [request.full_url for request in requests],
            [
                "http://db-service/api/v0/tables/orders",
                "http://db-service/api/v0/tables/fxquote",
            ],
        )
        self.assertTrue(all(request.method == "DELETE" for request in requests))


if __name__ == "__main__":
    unittest.main()
