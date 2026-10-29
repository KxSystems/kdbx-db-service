"""Tests for the validation entrypoint."""

from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(__file__))

from validate import normalize_notebook_filter  # noqa: E402


class NotebookFilterTests(unittest.TestCase):
    def test_normalizes_supported_filter(self):
        self.assertEqual(normalize_notebook_filter("qClient"), "qClient_notebook")

    def test_rejects_unsupported_filter(self):
        with self.assertRaisesRegex(ValueError, "Use curl, python, or qClient"):
            normalize_notebook_filter("python-client")


if __name__ == "__main__":
    unittest.main()
