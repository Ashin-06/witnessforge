# WitnessForge — forensic parser bug minimizer

WitnessForge reproduces a forensic parser's incorrect interpretation, reduces the action history while preserving the same error field and message identity, and checks a candidate repair against independent examples.

**Implemented scope:** a runnable local research prototype using a synthetic messaging application that produces real SQLite databases in three schema versions, plus a separate real-evidence workflow. The latter imports externally exported SQLite/WAL files, parses a documented AOSP-compatible SMS table, and acquires allowlisted databases from owned debuggable Android apps through ADB. A real Android emulator run recovered committed WAL records from the included owned fixture app. The browser workbench, semantic minimizer, custom parsers, repair suite, reports, and hash verification remain available. No AI API is needed.

**Limits:** real-evidence import/acquisition is a CLI workflow, with no claim of external semantic ground truth. Android action replay/minimization for arbitrary apps, privileged system-provider acquisition, encrypted databases, deleted-record carving, Puma/ALEAPP adapters, automatic AI patch generation, and signed chain of custody are not implemented. The built-in repair is explicit code. The Android validation used an owned test app with synthetic records, not personal messages or the system Telephony provider.

For real file import, ADB acquisition, and an independently checked Android fixture run, follow [INTEGRATION.md](INTEGRATION.md). A captured emulator example is in [examples/android-fixture-evidence](examples/android-fixture-evidence), with [validation results](examples/android-runtime-validation.json).

## Quick start

Requires Python 3.12+; tested here with Python 3.12 on Windows. Run from this folder. There are no third-party runtime dependencies and no package installation is needed.

```powershell
python -m witnessforge serve
```

Visit **http://127.0.0.1:8765**. The server listens only on this computer. Stop it with Ctrl+C. If the port is occupied, use `python -m witnessforge serve --port 8766`.

The supplied `Start-WitnessForge.ps1` starts the same server from the correct folder. The Python command also works if your PowerShell execution policy prevents running scripts.

### First experiment

1. Load the demo; choose schema **2**, parser **buggy**, two repeats, and budget **200**.
2. Run the experiment. The first full trace contains 20 actions.
3. Inspect the four retained actions: send the source message, forward it, edit the forward, delete the forward.
4. Compare expected sender **Maya** with incorrectly parsed sender **Noor** for `forwarded-42`.
5. Download the report or complete evidence bundle. The bundle contains both SQLite databases, traces, observations, and a hash manifest.
6. Select parser **fixed** and rerun. This should pass without claiming a minimized failure.
7. Run repair validation. The fixed parser should pass 27 independently constructed scenario/version combinations; the buggy baseline should fail eight.

This is an intentionally seeded defect in the reference parser, not a newly discovered vulnerability in a real app.

## Commands

```powershell
# Run and preserve the default experiment.
python -m witnessforge demo

# Export an editable JSON action trace.
python -m witnessforge export-demo my-trace.json

# Reduce your valid reference-app trace.
python -m witnessforge minimize --trace my-trace.json --version 3 --budget 200 --repeats 2

# Compare output without saving a full case.
python -m witnessforge inspect --trace my-trace.json --parser fixed

# Run the repair suite and save the result.
python -m witnessforge validate-repair --output repair-validation.json

# Test a custom trusted Python parser, separately executed with a timeout.
python -m witnessforge minimize --parser-file examples/custom_parser.py --timeout 10
python -m witnessforge validate-repair --parser-file examples/custom_parser.py

# Verify an existing case directory; substitute an actual case ID.
python -m witnessforge verify cases/CASE_ID

# Import an authorized export; keep mmssms.db-wal/-shm beside mmssms.db if present.
python -m witnessforge import-sqlite path/to/mmssms.db --output evidence/my-import
python -m witnessforge verify-evidence evidence/my-import
python -m witnessforge bundle-evidence evidence/my-import --output evidence/my-import.zip

# Inspect acquisition prerequisites without acquiring any app data.
python -m witnessforge adb-status

# Run the automated tests.
python -m unittest discover -s tests -v
```

`demo`/`minimize` return 0 when an experiment completes, including when it finds a bug. `inspect` returns 0 for PASS, 1 for FAIL, and 2 for UNRESOLVED. `validate-repair` and `verify` return 0 for PASS and 1 for failed validation. Invalid CLI arguments or processing failures return 2. `--json` prints the full case result for automation.

## Files and responsibilities

```text
01-forensic-parser-minimizer/
  README.md                    Start here
  ARCHITECTURE.md              Modules, data flow, design decisions
  WORKFLOW.md                  Worked experiment and reduction algorithm
  TRACE_FORMAT.md              Supported actions and canonical evidence
  INTEGRATION.md               Custom parsers and real-app extension boundary
  RESEARCH_NOTES.md            Prior art and limits of originality
  VALIDATION.md                Actual verification record and test instructions
  Start-WitnessForge.ps1       Windows launcher
  pyproject.toml               Optional packaging metadata
  witnessforge/
    core.py                   Action specification, SQLite simulator, parsers, reducer, repair suite
    cases.py                  Case persistence, SHA-256 manifests, bundles
    external.py               Original-byte capture, WAL validation, SQLite recovery, AOSP SMS extraction
    adb.py                    Explicit allowlist, debuggable app acquisition, device capability reporting
    reports.py                Standalone escaped HTML reports
    cli.py / __main__.py       Command-line interface
    plugins.py                Timeout and integrity wrapper for custom parser processes
    plugin_worker.py          Trusted parser module execution
    server.py                 HTTP API and static asset delivery (loopback by default)
    ops.py                    Settings, API keys and roles, rate limits, JSON logs, metrics
    static/                   HTML, CSS, browser JavaScript
  examples/
    custom_parser.py          Runnable parser extension example
    demo-trace.json            Default 20-action experiment
    adb-allowlist.json         Exact owned fixture package/database authorization
    android-fixture-evidence/ Real emulator acquisition containing only synthetic fixture records
  android-fixture/            Java app, no-Gradle build, independent expectations, device validation script
  tests/
    test_core.py              Semantic, reduction, dependency and stability checks
    test_integration.py       CLI, HTTP, evidence, report and plugin checks
  cases/                      Generated local evidence; excluded from source control
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [WORKFLOW.md](WORKFLOW.md), and [TRACE_FORMAT.md](TRACE_FORMAT.md) for the detailed structure and working. [VALIDATION.md](VALIDATION.md) records the completed checks. Open the [saved demonstration report](examples/sample-case/report.html) to review a complete result without starting the server.

## Interpreting results

| Result | Meaning |
| --- | --- |
| PASS | Parser output matches the independent specification on the tested trace. There is no failing trace to minimize. |
| FAIL | Canonical evidence differs. The initial mismatch's field and message ID define the reduction target. |
| UNRESOLVED | The experiment cannot establish a reliable result: invalid parser output, execution error, unstable repeat runs, insufficient baseline budget, or disagreeing preserved witnesses. |
| 1-minimal | No tested removal of one retained action plus its dependents preserves the target error. Not a globally shortest trace. |
| Budget exhausted | Reduction stopped before completing the search or minimality checks. The report states this explicitly. |

## Running as a service

The container image runs the same workbench in production mode, still with no third-party packages:

```bash
docker build -t witnessforge .
python -m witnessforge.ops keygen            # one API key per person: analyst, viewer or monitor
docker run -d --name witnessforge -p 8765:8765 -v witnessforge-data:/data \
  -e WITNESSFORGE_KEYS_FILE=/run/secrets/witnessforge-keys.json -e WITNESSFORGE_ALLOWED_HOSTS=lab.example.org \
  witnessforge
```

With API keys configured, every `/api` route needs a key (`X-API-Key` or `Authorization: Bearer`): `analyst` runs experiments, `viewer` opens saved cases and downloads, `monitor` reads `/metrics`. The page asks for the key once per browser session. `/healthz` and `/readyz` are open for probes; requests are rate-limited per client and logged as JSON lines with request IDs, without submitted content. See [docs/runbook.md](docs/runbook.md) and [docs/threat-model.md](docs/threat-model.md).

| Variable | Purpose |
|---|---|
| `WITNESSFORGE_ENV=production` | refuse to start without hashed API keys |
| `WITNESSFORGE_API_KEYS` / `WITNESSFORGE_KEYS_FILE` | hashed API keys with roles |
| `WITNESSFORGE_HOST`, `WITNESSFORGE_PORT` | default `127.0.0.1:8765`; any other address needs API keys |
| `WITNESSFORGE_ALLOWED_HOSTS` | host names clients use, besides loopback (DNS-rebinding protection) |
| `WITNESSFORGE_DATA_DIR` | where cases are kept (default `./cases`, `/data` in the image) |
| `WITNESSFORGE_RATE_LIMIT` | requests per minute per client (default 120) |

## Local operation and evidence boundaries

The browser interface accepts only built-in parsers. Custom Python parsers are CLI-only and **must be trusted**: a separate process and a timeout do not create a security sandbox. Candidate database mutation is detected, but arbitrary host access by a malicious plugin is not prevented.

Every experiment uses fresh synthetic evidence. Case IDs are generated locally. Case files may contain whatever text you enter, so use synthetic data for demonstrations. The local service checks Host and Origin, limits request size and concurrent experiments, and has no cloud calls or remote assets. On a network it needs API keys and `WITNESSFORGE_ALLOWED_HOSTS`, and it is still single-tenant: every key sees every case, and experiments run one at a time.

The manifest is unsigned. It detects edits relative to the recorded hashes; it does not establish provenance if someone can replace both evidence and manifest. A repaired parser passing 27 tests is useful regression evidence, not proof of universal correctness or legal admissibility.

## License

MIT. See [LICENSE](LICENSE).
