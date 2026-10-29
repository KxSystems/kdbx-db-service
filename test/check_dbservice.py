import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    endpoint = os.getenv("NOTEBOOK_IMPORT_STATUS_ENDPOINT", "http://localhost:8080").rstrip("/")
    url = f"{endpoint}/api/v0/tables"
    try:
        with urllib.request.urlopen(url, timeout=5):
            pass
    except urllib.error.HTTPError as exc:
        print(f"DB Service preflight failed: GET {url} returned HTTP {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"DB Service preflight failed: could not connect to {url} ({exc}).\n"
            "Start DB Service before running notebook tests, or set "
            "NOTEBOOK_IMPORT_STATUS_ENDPOINT to the correct endpoint.",
            file=sys.stderr,
        )
        return 1

    print(f"DB Service reachable at {endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
