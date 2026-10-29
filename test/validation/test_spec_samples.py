"""Tests for OpenAPI spec sample validation helpers."""

from __future__ import annotations

import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


sys.path.insert(0, os.path.dirname(__file__))

from spec_samples import (  # noqa: E402
    DELETE_CLEAR_OPERATION,
    DELETE_START_OPERATION,
    DELETE_STATUS_OPERATION,
    DeleteScenario,
    DeleteWorkflowState,
    SpecSample,
    SpecSampleResult,
    _curl_source_with_http_failure,
    _assembly_available,
    _enforce_operation_coverage,
    _extract_assembly,
    _format_sample_failure,
    _prepare_delete_fixture,
    _replace_job_placeholder,
    _source_has_create_table,
    collect_spec_samples,
    extract_delete_scenario,
    load_openapi_spec,
    order_spec_samples,
    run_spec_samples,
)


class SourceCreatesTableTests(unittest.TestCase):
    def test_detects_q_keyed_dictionary_create_table_flag(self):
        source = 'session.importFiles([table:"forex";path:"forex.csv";createTable:1b])'

        self.assertTrue(_source_has_create_table(source))

    def test_detects_q_bang_dictionary_create_table_flag(self):
        source = 'session.importFiles[`table`path`createTable!("forex";"forex.csv";1b)]'

        self.assertTrue(_source_has_create_table(source))

    def test_detects_python_create_table_flag(self):
        source = 'session.import_files(table="forex", path="forex.csv", createTable=True)'

        self.assertTrue(_source_has_create_table(source))

    def test_detects_json_create_table_flag(self):
        source = '{"table": "forex", "path": "forex.csv", "createTable": true}'

        self.assertTrue(_source_has_create_table(source))

    def test_ignores_missing_create_table_flag(self):
        source = 'session.importFiles([table:"forex";path:"forex.csv"])'

        self.assertFalse(_source_has_create_table(source))


class CurlExecutionTests(unittest.TestCase):
    def test_http_failures_are_not_hidden_by_output_pipeline(self):
        source = 'curl -s http://db-service/api/v0/future-endpoint | jq'

        hardened = _curl_source_with_http_failure(source)

        self.assertIn("set -o pipefail", hardened)
        self.assertIn("curl --fail-with-body", hardened)
        self.assertTrue(hardened.endswith(source))


class SampleFailureOutputTests(unittest.TestCase):
    def test_failure_identifies_spec_file_request_and_source(self):
        sample = SpecSample(
            method="POST",
            path="/api/v0/query/simple",
            operation_id="simple",
            tags=("Query",),
            client="python",
            label="Python",
            source='session.query_simple(table="fxquote")',
        )

        with mock.patch.dict(os.environ, {"TEST_SOURCE": "openapi/custom.yaml"}):
            message = _format_sample_failure(
                sample,
                "fxquote",
                "HTTP 400 Bad Request: type",
                rendered_source=sample.source,
            )

        self.assertIn("SAMPLE VALIDATION FAILED", message)
        self.assertIn("File: openapi/custom.yaml", message)
        self.assertIn("Request: POST /api/v0/query/simple", message)
        self.assertIn('session.query_simple(table="fxquote")', message)
        self.assertIn("Error: HTTP 400 Bad Request: type", message)


class OperationCoverageTests(unittest.TestCase):
    @staticmethod
    def result(sample: SpecSample, status: str) -> SpecSampleResult:
        return SpecSampleResult(1, 1, sample, sample.operation_key, status)

    @staticmethod
    def sample(operation_id: str = "simple") -> SpecSample:
        return SpecSample(
            method="POST",
            path="/api/v0/query/simple",
            operation_id=operation_id,
            tags=("Query",),
            client="curl",
            label="cURL",
            source="curl example",
        )

    def test_all_skipped_samples_fail_endpoint_coverage(self):
        sample = self.sample()

        with redirect_stdout(StringIO()):
            results = _enforce_operation_coverage([self.result(sample, "skipped")])

        self.assertEqual(results[0].status, "failed")
        self.assertIn("no curl code sample executed", results[0].message)

    def test_one_passed_sample_covers_endpoint(self):
        sample = self.sample()

        results = _enforce_operation_coverage(
            [self.result(sample, "skipped"), self.result(sample, "passed")]
        )

        self.assertEqual([result.status for result in results], ["skipped", "passed"])

    def test_cancel_import_remains_an_expected_skip(self):
        sample = self.sample(operation_id="ingestDelete")

        results = _enforce_operation_coverage([self.result(sample, "skipped")])

        self.assertEqual(results[0].status, "skipped")


class OpenAPISamplePairingTests(unittest.TestCase):
    @staticmethod
    def spec_samples() -> list[SpecSample]:
        repo_root = Path(__file__).resolve().parents[2]
        return collect_spec_samples(load_openapi_spec(repo_root / "openapi" / "dbservice.yaml"))

    def test_multi_assembly_samples_have_default_variants(self):
        samples = self.spec_samples()
        grouped: dict[tuple[str, str], list[SpecSample]] = {}
        for sample in samples:
            grouped.setdefault((sample.client, sample.operation_key), []).append(sample)

        missing_default = []
        for key, operation_samples in grouped.items():
            has_multi = any(
                _extract_assembly(sample.source, sample.client)
                for sample in operation_samples
            )
            has_default = any(
                _extract_assembly(sample.source, sample.client) is None
                for sample in operation_samples
            )
            if has_multi and not has_default:
                missing_default.append(key)

        self.assertEqual(missing_default, [])

    def test_q_samples_have_balanced_delimiters(self):
        failures = []
        pairs = {")": "(", "]": "[", "}": "{"}
        for sample in self.spec_samples():
            if sample.client != "qclient":
                continue
            source = re.sub(r'"(?:\\.|[^"\\])*"', '""', sample.source)
            stack = []
            for character in source:
                if character in "([{":
                    stack.append(character)
                elif character in pairs and (not stack or stack.pop() != pairs[character]):
                    failures.append(sample.label)
                    break
            else:
                if stack:
                    failures.append(sample.label)

        self.assertEqual(failures, [])


def delete_sample(client: str, source: str, operation_id: str = DELETE_START_OPERATION) -> SpecSample:
    return SpecSample(
        method="POST",
        path="/api/v0/deletes",
        operation_id=operation_id,
        tags=("Deletes",),
        client=client,
        label=client,
        source=source,
    )


class DeleteScenarioTests(unittest.TestCase):
    @mock.patch("spec_samples._tables_for_assembly")
    def test_missing_named_assembly_is_unavailable(self, tables):
        from spec_samples import SpecSampleExecutionError

        tables.side_effect = SpecSampleExecutionError("HTTP 404 No such database")

        self.assertFalse(_assembly_available("http://db", "marketdata-emea"))

    def test_extracts_dynamic_curl_table_and_filter(self):
        sample = delete_sample(
            "curl",
            """curl -X POST 'http://localhost:8080/api/v0/deletes' -d '{
              "table": "orders",
              "startTS": "2026.04.03D00:00:00",
              "endTS": "2026.04.04D00:00:00",
              "filter": [["=", "region", "EMEA"]]
            }'""",
        )

        scenario = extract_delete_scenario(sample)

        self.assertEqual(scenario.table, "orders")
        self.assertEqual(scenario.filter_values, (("region", "EMEA"),))
        self.assertEqual(scenario.start_ts, "2026.04.03D00:00:00")

    def test_extracts_dynamic_python_table_and_filter(self):
        sample = delete_sample(
            "python",
            """session.delete_rows(
                table="quotes",
                startTS="2026.04.03D00:00:00",
                endTS="2026.04.04D00:00:00",
                filter=[["=", "venue", "LSE"]],
            )""",
        )

        scenario = extract_delete_scenario(sample)

        self.assertEqual(scenario.table, "quotes")
        self.assertEqual(scenario.filter_values, (("venue", "LSE"),))

    def test_extracts_positional_python_table(self):
        sample = delete_sample(
            "python",
            'session.delete_rows("orders", filter=[["=", "region", "EMEA"]])',
        )

        scenario = extract_delete_scenario(sample)

        self.assertEqual(scenario.table, "orders")

    def test_extracts_dynamic_qclient_table_and_filter(self):
        sample = delete_sample(
            "qclient",
            'session.deleteRows["ticks";enlist("=";"ticker";"IBM");'
            '([startTS:2026.04.03D;endTS:2026.04.04D])]',
        )

        scenario = extract_delete_scenario(sample)

        self.assertEqual(scenario.table, "ticks")
        self.assertEqual(scenario.start_ts, "2026.04.03D")
        self.assertEqual(scenario.end_ts, "2026.04.04D")
        self.assertEqual(scenario.filter_values, (("ticker", "IBM"),))

    def test_extracts_current_qclient_multi_assembly_call(self):
        sample = delete_sample(
            "qclient",
            'session.deleteRows["orders";enlist("=";"region";"EMEA");'
            '([startTS:2027.05.01D;endTS:2027.05.02D;assembly:"ratesdb"])]',
        )

        scenario = extract_delete_scenario(sample)

        self.assertEqual(scenario.table, "orders")
        self.assertEqual(scenario.filter_values, (("region", "EMEA"),))
        self.assertEqual(scenario.assembly, "ratesdb")

    def test_extracts_qclient_bang_dictionary_form(self):
        sample = delete_sample(
            "qclient",
            'session.deleteRows[`table`startTS`endTS`filter`assembly!('
            '"orders";2027.05.01D;2027.05.02D;'
            'enlist("=";"region";"EMEA");"ratesdb")]',
        )

        scenario = extract_delete_scenario(sample)

        self.assertEqual(scenario.table, "orders")
        self.assertEqual(scenario.start_ts, "2027.05.01D")
        self.assertEqual(scenario.filter_values, (("region", "EMEA"),))
        self.assertEqual(scenario.assembly, "ratesdb")

    def test_tracks_jobs_separately_by_assembly(self):
        state = DeleteWorkflowState()
        default = DeleteScenario(table="trade")
        emea = DeleteScenario(table="trade", assembly="marketdata-emea")

        state.remember(default, "default-job")
        state.remember(emea, "emea-job")

        self.assertEqual(state.job_for(None), "default-job")
        self.assertEqual(state.job_for("marketdata-emea"), "emea-job")

    def test_tracks_every_dynamic_fixture(self):
        state = DeleteWorkflowState()
        state.track_fixture(DeleteScenario(table="orders"))
        state.track_fixture(DeleteScenario(table="quotes"))

        self.assertEqual(
            {scenario.table for scenario in state.all_fixtures()},
            {"orders", "quotes"},
        )

    def test_replaces_delete_placeholders_for_every_client(self):
        job_id = "new-delete-job"

        self.assertIn(job_id, _replace_job_placeholder("/api/v0/deletes/NAME", job_id))
        self.assertIn(
            job_id,
            _replace_job_placeholder(
                'session.get_delete(job_id="54f5cf87-dbc2-4336-d109-173ed56c0b86")',
                job_id,
            ),
        )
        self.assertIn(
            job_id,
            _replace_job_placeholder(
                'session.getDelete["54f5cf87-dbc2-4336-d109-173ed56c0b86"]',
                job_id,
            ),
        )

    def test_orders_delete_workflow(self):
        status = delete_sample("curl", "status", DELETE_STATUS_OPERATION)
        clear = delete_sample("curl", "clear", DELETE_CLEAR_OPERATION)
        start = delete_sample("curl", "start", DELETE_START_OPERATION)

        ordered = order_spec_samples([clear, status, start])

        self.assertEqual(
            [sample.operation_id for sample in ordered],
            [DELETE_START_OPERATION, DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION],
        )

    @mock.patch("spec_samples._wait_for_import_job")
    @mock.patch("spec_samples._rest_json")
    @mock.patch("spec_samples._tables_for_assembly", return_value=set())
    def test_fixture_uses_extracted_table_and_filter_columns(
        self,
        _tables,
        rest_json,
        _wait_for_job,
    ):
        rest_json.side_effect = [{"jobId": "create"}, {"jobId": "import"}]
        scenario = DeleteScenario(
            table="orders",
            start_ts="2026.04.03D00:00:00",
            filter_values=(("region", "EMEA"),),
        )

        _prepare_delete_fixture("http://db", scenario)

        create_call, import_call = rest_json.call_args_list
        self.assertEqual(create_call.args[1], "/api/v0/tables/orders")
        self.assertIn({"name": "region", "type": "symbol"}, create_call.kwargs["payload"]["columns"])
        self.assertEqual(import_call.kwargs["payload"]["table"], "orders")
        self.assertEqual(
            import_call.kwargs["payload"]["columnNames"],
            ["trddate", "ts", "region"],
        )

    @mock.patch("spec_samples._write_spec_sample_report")
    @mock.patch("spec_samples.reset_dbservice_tables")
    @mock.patch("spec_samples._cleanup_delete_fixture")
    @mock.patch("spec_samples._assert_delete_job_cleared")
    @mock.patch("spec_samples._prepare_delete_fixture")
    @mock.patch("spec_samples._run_curl_sample")
    def test_shared_workflow_passes_delete_job_to_status_and_clear(
        self,
        run_curl,
        _prepare,
        assert_cleared,
        _cleanup,
        _reset,
        _write_report,
    ):
        start = delete_sample(
            "curl",
            """curl -X POST 'http://localhost:8080/api/v0/deletes' -d '{
              "table": "orders",
              "filter": []
            }'""",
            DELETE_START_OPERATION,
        )
        status = delete_sample(
            "curl",
            "curl -X GET http://localhost:8080/api/v0/deletes/NAME",
            DELETE_STATUS_OPERATION,
        )
        clear = delete_sample(
            "curl",
            "curl -X DELETE http://localhost:8080/api/v0/deletes/NAME",
            DELETE_CLEAR_OPERATION,
        )
        run_curl.side_effect = [
            ("delete-job", '{"jobId":"delete-job","status":"completed"}'),
            ("delete-job", '{"jobId":"delete-job","status":"completed"}'),
            ("delete-job", ""),
        ]

        with redirect_stdout(StringIO()):
            result = run_spec_samples([clear, status, start], "http://db", fail_fast=False)

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[2] for call in run_curl.call_args_list],
            [None, "delete-job", "delete-job"],
        )
        assert_cleared.assert_called_once_with("http://db", "delete-job", None)


if __name__ == "__main__":
    unittest.main()
