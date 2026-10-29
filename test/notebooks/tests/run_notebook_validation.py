import io
import sys
import unittest
from pathlib import Path


class ConciseTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.concise_failures = []

    def addError(self, test, err):
        self.concise_failures.append((test.id(), err[1]))
        super().addError(test, err)

    def addFailure(self, test, err):
        self.concise_failures.append((test.id(), err[1]))
        super().addFailure(test, err)


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    tests_dir = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(tests_dir), pattern="test_*.py")

    stream = sys.stderr if verbose else io.StringIO()
    runner = unittest.TextTestRunner(
        stream=stream,
        verbosity=2 if verbose else 0,
        resultclass=ConciseTestResult,
    )
    result = runner.run(suite)

    if result.wasSuccessful():
        return 0

    if not verbose:
        for test_id, exc in result.concise_failures:
            message = str(exc).strip()
            if message.startswith("NOTEBOOK VALIDATION FAILED"):
                print(message, file=sys.stderr)
            else:
                print("NOTEBOOK VALIDATION FAILED", file=sys.stderr)
                print(f"Test: {test_id}", file=sys.stderr)
                print(f"Error: {message or type(exc).__name__}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
