# DB Service Validation

This directory contains the validation harness used by `make validate`.

The harness starts DB Service with Docker Compose, runs the selected examples in a runner container, writes reports under `validation-reports/`, copies executed notebooks to `executed-notebook/`, and tears the stack down afterwards.

## Test Model

The tests are not saved-output comparisons. The saved outputs in the notebooks are useful for readers, but validation does not treat them as golden files.

Instead, the harness executes examples and applies DB-Service-aware checks. Depending on the example, it checks things such as:

- tables are created or dropped as expected
- import jobs return job ids and complete successfully
- query responses contain rows and fields
- expected error cases fail in the expected way
- required import files are available before import samples run

For notebook tests, helper code is injected into a temporary executed copy of the notebook. The source notebooks under `notebooks/` are not modified by the test run.

Curl calls in that temporary copy use `--fail-with-body` and shell `pipefail`, so HTTP 4xx/5xx responses fail even when the command is piped through `jq` or `tee`. Calls to an otherwise unknown `/api/vN/...` path are reported as generic `api_call` checks until operation-specific validation is added. To demonstrate an intentional HTTP error in a notebook, add the `validation-allow-http-error` tag to that cell.

### Test isolation and cleanup

The notebook and spec-sample suites query DB Service for its current tables and remove them before and after each run. Repeated create-table samples also remove their target table before running so that one sample does not fail merely because an earlier variant created the same table.

For targeted cleanup and state checks, the harness extracts literal table names from the standard Python, curl, and qClient call forms. This supports both qClient dictionary arguments and the newer positional/variadic syntax. If a future source form cannot be identified and a create call reports that the table already exists, the harness resets the tables and retries once instead of depending entirely on source-text parsing.

Delete samples are treated as one workflow. Before `delete_start`, the harness derives the table, filter columns, timestamps, and assembly from the curl, Python, or qClient example and creates a matching fixture. It then stores the returned delete job ID separately from import job IDs, uses it for `delete_status` and `delete_clear`, and verifies that clearing the job removes its tracked status. The fixture follows the example, so changing the literal table or filter column does not require a harness change.

## Test Suites

- `notebooks`: runs the notebooks in this repo. The unittest entry point is `test/notebooks/tests/test_dbservice_notebooks.py`, with most DB-Service-aware checks in `test/notebooks/tests/notebook_test_utils.py`.
- `spec_samples`: extracts `x-codeSamples` from `openapi/dbservice.yaml`, runs the selected client language, and validates the resulting DB Service state. The collector and runner live in `test/validation/spec_samples.py`.
- `client_readmes`: extracts runnable examples from the qClient or Python client README and validates them against DB Service. The collector and runner live in `test/validation/client_readmes.py`. This suite is available locally but is currently disabled in GitLab CI.

Shared parsing, polling, assertion, and JUnit helpers live in `test/validation/core.py`. The CLI entry point is `test/validation/validate.py`.

## Running Locally

The default command runs every notebook against main DB Service and client code:

```bash
make validate
```

Run one notebook:

```bash
make validate TEST_SUITE=notebooks TEST_FILTER=curl
make validate TEST_SUITE=notebooks TEST_FILTER=python
make validate TEST_SUITE=notebooks TEST_FILTER=qClient
```

Run OpenAPI spec sample tests:

```bash
make validate TEST_SUITE=spec_samples TEST_FILTER=curl
make validate TEST_SUITE=spec_samples TEST_FILTER=python
make validate TEST_SUITE=spec_samples TEST_FILTER=qClient
```

Run client repo README tests:

```bash
make validate TEST_SUITE=client_readmes TEST_FILTER=python
make validate TEST_SUITE=client_readmes TEST_FILTER=qClient
```

## Configuration

Test controls:

- `TEST_SUITE`: selects `notebooks`, `spec_samples`, or `client_readmes`. The default is `notebooks`.
- `TEST_SOURCE`: selects the exact notebook, OpenAPI specification, or README to test. The path must be inside this repository. If it is omitted, the suite uses its normal source.
- `TEST_FILTER`: selects a client from the chosen source. Use `curl`, `python`, or `qClient`; an explicit client README source requires `python` or `qClient`.
- `FAIL_FAST=1`: stops spec sample or client README validation on the first failure.
- `VERBOSE=1`: also streams Docker setup, `RUN` lines, and detailed intermediate checks.

Examples of exact test sources:

```bash
make validate TEST_SUITE=notebooks TEST_SOURCE=notebooks/python_notebook.ipynb
make validate TEST_SUITE=spec_samples TEST_SOURCE=openapi/dbservice.yaml TEST_FILTER=python
```

### Code profiles

`CODE_PROFILE` selects a consistent source for all three code components. It defaults to `main`.

| Profile | DB Service | qClient | Python client |
| --- | --- | --- | --- |
| `main` | Freshly pulled dev-registry images tagged `latest` | GitLab `main` | GitLab `main` |
| `release` | Portal version in `test/validation/release.env` | Published GitHub source | Released installed package |
| `local` | Local Docker images tagged `debug` | `../dbservice-qclient` working tree | `../dbservice-python-client` working tree |

Use a profile when all components should come from the same stage:

```bash
CODE_PROFILE=main make validate TEST_SUITE=notebooks TEST_FILTER=qClient
CODE_PROFILE=release make validate TEST_SUITE=spec_samples TEST_FILTER=python
CODE_PROFILE=local make validate TEST_SUITE=notebooks TEST_FILTER=python
```

### Component overrides

Override one component while the other components continue to use `CODE_PROFILE`:

- `CODE_DBS=main`: pulls the current dev-registry DB Service images tagged `latest` before each run.
- `CODE_DBS=release`: portal DB Service version in `test/validation/release.env`.
- `CODE_DBS=0.1.1-beta.1`: that exact DB Service version from the portal.
- `CODE_DBS=local`: uses locally built DB Service images tagged `debug` without pulling from a registry.
- `CODE_QCLIENT=main`, `release`, or `local`: GitLab `main`, published GitHub source, or the sibling working tree.
- `CODE_PYCLIENT=main`, `release`, or `local`: GitLab `main`, the released installed package, or the sibling working tree.
- `CODE_QCLIENT=my-feature-branch` or `CODE_PYCLIENT=my-feature-branch`: that GitLab ref.
- `CODE_QCLIENT=/path/to/dbservice-qclient` or `CODE_PYCLIENT=/path/to/dbservice-python-client`: that exact local working tree, including uncommitted changes.

Mixed-source examples:

```bash
# Main DB Service and Python client, with local qClient changes.
CODE_QCLIENT=/path/to/dbservice-qclient make validate TEST_SUITE=notebooks TEST_FILTER=qClient

# Released components, with local Python client changes.
CODE_PROFILE=release CODE_PYCLIENT=/path/to/dbservice-python-client \
  make validate TEST_SUITE=spec_samples TEST_FILTER=python

# Exact portal DB Service version, with the remaining components from main.
CODE_DBS=0.1.1-beta.1 make validate TEST_SUITE=notebooks TEST_FILTER=curl
```

For local `main` or named-ref tests, the harness fetches through the sibling host checkouts using your normal Git credentials, then mounts the fetched source into Docker. This avoids requiring GitLab authentication inside the runner container and does not alter the clients' working trees. `local` and explicit path values mount the selected working tree directly instead.

## Output and Logs

Normal runs print a short configuration block before the tests. It identifies:

- the selected suite and exact notebook, OpenAPI spec, or README location
- the DB Service source and resolved component image location/tag
- the separately versioned RT image
- the qClient and Python client source, including the resolved GitLab ref and commit, the host checkout used to fetch it, or the checkout path for explicitly selected local sources
- the host and runner endpoints and the directory containing the full logs

Each validation check then prints a concise `PASS`, `FAIL`, or `SKIP` line as soon as it completes. Docker setup remains hidden when successful. To also stream setup, `RUN` lines, and separate detailed `check` lines, use:

```bash
make validate TEST_SUITE=notebooks TEST_FILTER=curl VERBOSE=1
```

The complete quiet-mode output is retained under `validation-logs/`:

- `dbservice-setup.log`: DB Service container setup
- `validation-runner-build.log`: validation runner image build
- `validation-runner.log`: complete validation runner output
- `dbservice-notebook-runner.log`: validation runner container output
- `dbservice-notebook-test-containers.log`: final validation container state
- `dbservice-notebook-test.log`: full DB Service component output
- `dbservice-teardown.log`: container teardown

Notebook failures identify the source notebook and the cell's position in the original notebook, not its position in the temporary notebook after test helpers are added. They also print the exact rewritten command or source that ran, the DB Service error, the executed-notebook path, and the JUnit report path.

Spec and client README failures similarly identify the source file, operation, HTTP request where relevant, executed source, and returned error. Full DB Service component logs remain available in `validation-logs/dbservice-notebook-test.log`.

## Runner Setup Output

With `VERBOSE=1`, the runner prints a setup section before validation starts. For example:

```text
== Runner Setup ==
-> Installing KDB-X license
-> Python notebook dependencies already available
-> Using packaged DB Service Python client
-> Installing DB Service q client
```

This means the validation container is preparing its runtime. It installs the KDB-X license, checks Python notebook dependencies, selects packaged or source Python client code, installs the q client module when needed, and then runs the selected tests.

## Adding Coverage

Changes to the three standard notebooks under `notebooks/` are picked up automatically. Run a new notebook explicitly with `TEST_SOURCE=path/to/notebook.ipynb`; add it to `test/notebooks/tests/test_dbservice_notebooks.py` if it should become part of the default suite. If it performs a new kind of DB Service operation, update `test/notebooks/tests/notebook_test_utils.py` and shared helpers in `test/validation/core.py`.

To add OpenAPI sample coverage, add or update `x-codeSamples` in `openapi/dbservice.yaml`. The `spec_samples` suite collects those samples directly from the spec.

To add client README coverage, add runnable examples to the relevant client README. The `client_readmes` suite extracts examples from the qClient and Python client READMEs.

## Disabling Coverage

Use `TEST_FILTER` for local selection while developing:

```bash
make validate TEST_SUITE=notebooks TEST_FILTER=python
make validate TEST_SUITE=spec_samples TEST_FILTER=curl
```

To temporarily disable a whole CI job, update `.gitlab/notebook-tests.yml` with GitLab `rules`. The `client_readme_tests` job is currently disabled that way.

For spec samples, prefer fixing or removing the relevant `x-codeSamples` entry rather than adding hidden skip logic. The spec is treated as the source of truth for runnable API examples.

## Expected Skips

Some spec samples can be skipped intentionally:

- `cancel_import` is skipped because it requires a live import job.
- `import_database` is skipped if the `fxquote-hdb` import fixture has not been staged. Under `make validate`, the fixture is staged from `samples/fxquote` into `data/imports/fxquote-hdb/fxquote` and mounted into the validation runner, so this sample should normally run.
- A sample that explicitly names an assembly is skipped when that assembly is not configured in the validation stack. Single-assembly variants still run; configure the named assembly to exercise the specific-assembly variant.

Skips are included in the JUnit reports so they are visible in CI.

The suite also requires at least one code sample to execute for every API operation selected for a client. If every sample for an operation is skipped, validation fails with a coverage error instead of reporting a misleading successful run. `cancel_import` is the temporary exception described above.
