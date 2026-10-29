"""Tests for client README sample collection."""

from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(__file__))

from client_readmes import infer_operation_id, split_readme_block  # noqa: E402
from spec_samples import (  # noqa: E402
    DELETE_CLEAR_OPERATION,
    DELETE_START_OPERATION,
    DELETE_STATUS_OPERATION,
)


class DeleteReadmeSampleTests(unittest.TestCase):
    def test_identifies_python_delete_methods(self):
        self.assertEqual(
            infer_operation_id('session.delete_rows(table="orders", filter=[])'),
            DELETE_START_OPERATION,
        )
        self.assertEqual(
            infer_operation_id('session.get_delete("job-id")'),
            DELETE_STATUS_OPERATION,
        )
        self.assertEqual(
            infer_operation_id('session.cancel_delete("job-id")'),
            DELETE_CLEAR_OPERATION,
        )

    def test_splits_connected_python_delete_workflow(self):
        source = """
job = session.delete_rows(table="orders", filter=[])
session.get_delete(job["jobId"])
session.cancel_delete(job["jobId"])
"""

        samples = split_readme_block(source, "python", "Deleting Data", 1)

        self.assertEqual(len(samples), 3)
        self.assertEqual(
            [infer_operation_id(sample) for sample, _label in samples],
            [DELETE_START_OPERATION, DELETE_STATUS_OPERATION, DELETE_CLEAR_OPERATION],
        )


if __name__ == "__main__":
    unittest.main()
