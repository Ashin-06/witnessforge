# Extending the prototype

## Run your own reference-app parser

Create a trusted Python file with this entry point:

```python
from pathlib import Path

def parse(database_path: Path) -> list[dict]:
    # Open the provided SQLite file read-only, extract records,
    # and map them to the canonical schema in TRACE_FORMAT.md.
    ...
```

The runnable [examples/custom_parser.py](examples/custom_parser.py) shows the interface by delegating to the bundled correct parser. Replace that extraction call with your implementation. Do not return expected ground truth as parser output: that would invalidate the experiment.

```powershell
python -m witnessforge minimize --parser-file examples/custom_parser.py
python -m witnessforge validate-repair --parser-file examples/custom_parser.py --output candidate-validation.json
```

The module executes in a fresh Python process for each parser invocation. It receives only the generated database path. Diagnostics printed by the plugin go to stderr; the returned value is serialized as JSON. Output must be no larger than 2 MiB. The default timeout is 10 seconds, configurable up to 60 seconds. The parent checks that the database has not changed and pins the plugin source hash for the experiment. The saved case records this hash, but imported dependencies are not automatically captured. Exceptions, malformed output, mutation, timeout, and changed plugin source become UNRESOLVED, never an accepted semantic bug.

**Trust boundary:** this executes Python with your permissions. Process separation, output checks, and timeouts are reliability controls, not a sandbox. Run only trusted parser code. The HTTP service intentionally does not accept plugin paths.

## Programmatic API

```python
from pathlib import Path
from witnessforge.core import demo_trace, inspect_trace, minimize_trace, validate_repair
from witnessforge.cases import run_case, verify_case

trace = demo_trace()
observation = inspect_trace(trace, version="2", parser="buggy")
reduction = minimize_trace(trace, version="2", parser="buggy", repeats=2, max_evaluations=200)
case = run_case(Path("cases"), trace)
verification = verify_case(Path("cases") / case["case_id"])
repair = validate_repair(parser="fixed")
```

Python callers can supply a callable parser, but direct callables run inside the process and have no timeout. Use the CLI subprocess adapter if isolation from a hanging parser matters.

## Extending to a real Android app

This is a future integration, not a delivered or validated feature. A correct integration needs more than changing a database filename:

1. Pin an Android emulator/device image, app build, parser revision, and acquisition method. Record their versions and hashes.
2. Replace reference-app replay with actual UI action execution, for example through Puma/Appium, and verify that every intended action actually happened.
3. Reset to a known clean device/application state before every candidate. Control network/server state, clocks, synchronization, and account fixtures.
4. Acquire databases and required sidecar files consistently, without changing the original acquisition. Handle WAL and encryption through documented, authorized acquisition procedures.
5. Define an independently validated expected-artifact specification. A user action may not leave the expected artifact, so do not infer artifact existence merely because an action was attempted.
6. Map a real parser's outputs to a richer canonical schema that preserves uncertainty, timestamps, provenance, and unsupported fields.
7. Rebuild the dependency rules to match that application's semantics. The delivered five-action model is insufficient for arbitrary apps.
8. Extend the repeat oracle to handle acquisition failure and nondeterminism. Keep unresolved states explicit.
9. Evaluate on genuine historical regressions and app versions that were withheld during development, alongside seeded cases.

Puma already supplies mobile automation and reference-data workflows; ALEAPP supplies parsers for many Android artifacts. Reuse those capabilities instead of claiming them as this project's invention. The new contribution would need to be demonstrated in semantic-failure-preserving reduction and independently checked repair validation.

## Optional AI repair suggestions

AI patch generation is not implemented and is not required to run this project. A future component could accept a minimized witness, parser source, and schema documentation, then return a proposed code patch. Keep it separate from validation: review the patch, execute it only in a suitable isolated environment, and run withheld tests before accepting it. Model text alone is never evidence that a parser is repaired.
