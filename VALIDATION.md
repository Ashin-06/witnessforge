# Validation record

Latest Python and Android integration verification: 11 October 2026 using Python 3.12.3 on Windows. The original browser checks used installed Chrome through Playwright on 10 October. The Android check used an owned fixture app running in a real API 35 emulator; its records are synthetic and it is not the system Telephony provider or a commercial forensic tool.

## Completed checks

| Check | Observed result |
| --- | --- |
| Python unit/integration suite | **56 tests passed** in 21.265 seconds |
| Owned Android fixture build | Signed APK built using installed JBR 21 / SDK build-tools 36.1.0; no Gradle or downloads |
| Actual Android acquisition | Normal ADB run-as on emulator-5580 / API 35; exact package/path allowlist; stopped owned fixture; two captures matched |
| Actual Android extraction | **3/3 records matched** independent ID/address/body/direction/type/millisecond-date expectations, including Unicode and null address |
| Android WAL and bundle | Committed WAL preserved and recovered; **5 files verified**, ZIP created |
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

The 24 new external-evidence/acquisition tests include independent host SQLite fixtures with committed WAL-only rows, exact original-byte preservation, WAL checksum/truncation errors, absent-WAL uncertainty, rollback-journal refusal, incompatible schemas, view rejection, null/unknown values, finite row limits, changing source rejection, output overwrite refusal, manifest tampering/traversal, CLI Unicode serialization, and verified ZIPs. Acquisition boundary tests use a controlled fake command transport to check allowlists, argument injection, non-debuggable failures without bypass, mismatched captures, remote-path escape, missing SDK, timeout and output limits. These mocked transport tests are separate from the actual emulator run below.

Actual runtime evidence is available in [examples/android-fixture-evidence](examples/android-fixture-evidence), the [validation result](examples/android-runtime-validation.json), and [build provenance](examples/android-fixture-build.json). The build fingerprint was `google/sdk_gphone16k_x86_64/emu64xa16k:15/AE3A.240806.043/12960925:userdebug/dev-keys`. APK SHA-256 was `a8ec831c32f77f2049ad6a5622dc3bc20ea05344f4ee2c174eb82bb907577030`. Paths recorded in these historical reports identify the machine used; they need not exist when verifying the copied bundle. The debug key and APK are generated build outputs, not repository assets.

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
python -m witnessforge verify-evidence examples/android-fixture-evidence
# With an already running, authorized emulator and installed SDK/JDK:
python android-fixture/build.py
python android-fixture/validate.py --serial emulator-5580 --output .test-output/new-android-run
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
- Android rejects a row-returning WAL configuration PRAGMA through `execSQL`; the fixture now uses a cursor query. The corrected APK completed the actual acquisition/extraction validation.
- External CLI output now uses ASCII-escaped JSON, preserving Unicode artifact content on Windows legacy code pages.

## Remaining limits

The seed defect and reference-app schemas are intentionally constructed. The test suite is finite. A 1-minimal witness is relative to the permitted removal operation, target field/message signature, replay behavior, and budget; it is not a globally shortest sequence. Hash manifests are unsigned. Custom Python plugins execute trusted local code, not sandboxed code. External import and owned-app ADB acquisition are working, with actual emulator validation. Physical devices, OEM Telephony variants, encrypted stores, privileged acquisition, arbitrary real-app action replay/minimization, AI patch generation, and independently adjudicated real-world forensic cases remain unvalidated or unsupported as described in INTEGRATION.md.
