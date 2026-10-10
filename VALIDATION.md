# Validation record

Verified on 10 October 2026 using Python 3.12.3 on Windows and installed Chrome through Playwright for browser checks. These results cover the delivered synthetic reference application, not an external Android application or commercial forensic tool.

## Completed checks

| Check | Observed result |
| --- | --- |
| Python unit/integration suite | **32 tests passed** |
| Default schema-2 experiment | **20 actions reduced to 4**, target `FIELD_SENDER:forwarded-42` retained |
| Reduction work | **64 parser executions**, including repeat checks; dependency-aware 1-minimality established |
| Fixed parser repair suite | **27/27 passed**, zero unresolved |
| Buggy parser comparison | **8 semantic failures** across the 27 scenario/version combinations |
| Custom-parser example through a separate process | **27/27 passed**, including Unicode and emoji |
| Browser workflow checks | **9 passed**, no JavaScript page errors |
| Mobile layout | No horizontal document overflow at a 390-pixel viewport |
| JSON examples and saved validation results | Parsed successfully |
| Local Markdown document links | No missing targets found |

## Automated coverage

The Python suite checks independent expected evidence, three physical SQLite layouts, the required bug conditions, text-copy and reaction semantics, malformed parser output, read-only evidence handling, dependencies, stable failure identity, nondeterministic repeats, evaluation budgets, input immutability, empty traces, and repairs that accidentally damage unrelated fields.

Integration checks exercise the CLI exit codes, actual local HTTP endpoints, complete evidence directories and ZIP bundles, SQLite integrity, changed/missing/extra files, unsafe manifest paths, HTML escaping, custom parser errors/timeouts/mutation, Unicode under a Windows code page, and refusal when a custom parser source changes during a run.

The browser check loads the workbench, runs the reduction, checks the actual sender mismatch, renders all 27 repair cases, reopens an archived case, checks the passing-parser state, checks budget exhaustion, rejects malformed JSON, and inspects mobile overflow. Desktop and mobile screenshots were also visually reviewed.

## Reproduce the checks

From this project folder:

```powershell
python -m unittest discover -s tests -v
python -m witnessforge demo
python -m witnessforge validate-repair --output examples/repair-validation.json
python -m witnessforge validate-repair --parser-file examples/custom_parser.py --output examples/custom-parser-validation.json
python -m witnessforge verify examples/sample-case
```

Browser checks are optional development tooling; Playwright and Chrome are not needed to run the application. With Playwright available to Node and Chrome installed, start the server in another terminal, then run:

```powershell
node tests/browser_smoke.cjs
```

The browser check writes screenshots and its results to `.test-output/`. The saved sample case contains an HTML report, original and minimized SQLite evidence, traces, observations, and a manifest. Its hashes can be checked after copying the folder to another machine.

## Issues found and corrected during verification

- SQLite transaction contexts did not close file handles promptly on Windows. Connections now close explicitly, allowing temporary evidence cleanup.
- Custom-parser JSON output used the Windows legacy output encoding. The worker now emits ASCII-escaped JSON, preserving decoded Unicode and emoji.
- A Python bytecode cache could reuse a quickly edited, same-size plugin. The worker executes current source, and each experiment pins its plugin source hash.

## Remaining limits

The seed defect and reference-app schemas are intentionally constructed. The test suite is finite. A 1-minimal witness is relative to the permitted removal operation, target field/message signature, replay behavior, and budget; it is not a globally shortest sequence. Hash manifests are unsigned. Custom Python plugins execute trusted local code, not sandboxed code. Real-device acquisition, production app adapters, AI patch generation, and external forensic validation remain outside this implementation.
