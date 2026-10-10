# WitnessForge — forensic parser bug minimizer

WitnessForge reproduces a forensic parser's incorrect interpretation, reduces the action history while preserving the same error field and message identity, and checks a candidate repair against independent examples.

**Implemented scope:** a runnable local research prototype using a synthetic messaging application that produces real SQLite databases in three schema versions. It includes a deliberately defective parser, a corrected parser, a browser workbench, a CLI, custom parser support, evidence reports, hash verification, and automated tests. No AI API is needed.

**Not implemented:** real Android acquisition, Puma/ALEAPP adapters for production applications, automatic AI patch generation, signed chain of custody, or a general proof of parser correctness. The built-in repair is explicit code, not an AI-generated claim.

## Quick start

Requires Python 3.10+; tested here with Python 3.12 on Windows. Run from this folder. There are no third-party runtime dependencies and no package installation is needed.

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
    reports.py                Standalone escaped HTML reports
    cli.py / __main__.py       Command-line interface
    plugins.py                Timeout and integrity wrapper for custom parser processes
    plugin_worker.py          Trusted parser module execution
    server.py                 Loopback HTTP API and static asset delivery
    static/                   HTML, CSS, browser JavaScript
  examples/
    custom_parser.py          Runnable parser extension example
    demo-trace.json            Default 20-action experiment
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

## Local operation and evidence boundaries

The browser interface accepts only built-in parsers. Custom Python parsers are CLI-only and **must be trusted**: a separate process and a timeout do not create a security sandbox. Candidate database mutation is detected, but arbitrary host access by a malicious plugin is not prevented.

Every experiment uses fresh synthetic evidence. Case IDs are generated locally. Case files may contain whatever text you enter, so use synthetic data for demonstrations. The local service checks Host and Origin, limits request size and concurrent experiments, and has no cloud calls or remote assets. It is not designed for exposure on a network or for untrusted multi-user hosting.

The manifest is unsigned. It detects edits relative to the recorded hashes; it does not establish provenance if someone can replace both evidence and manifest. A repaired parser passing 27 tests is useful regression evidence, not proof of universal correctness or legal admissibility.
