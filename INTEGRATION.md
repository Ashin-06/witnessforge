# Extending the prototype

## Import external SQLite evidence

This working CLI path is separate from the synthetic action-trace minimizer. Start with an authorized offline export. Keep the original database and any sidecars together with their exact names (`mmssms.db`, `mmssms.db-wal`, `mmssms.db-shm`). Do not open the original with a database viewer first.

```powershell
python -m witnessforge import-sqlite "C:/Evidence/export/mmssms.db" --output evidence/sms-import
python -m witnessforge verify-evidence evidence/sms-import
python -m witnessforge bundle-evidence evidence/sms-import --output evidence/sms-import.zip
```

`--artifact-parser sqlite-only` preserves and recovers another unencrypted SQLite schema without claiming to understand its artifacts. Every output directory and ZIP must be new. Imports return exit 0 for `IMPORTED`, exit 2 for `UNRESOLVED` or an input/acquisition error. Verification returns 0 for intact bytes and 1 for a mismatch. `IMPORTED` means readable supported artifacts; it does **not** mean semantic correctness, a complete acquisition, or valid chain of custody.

The importer reads source files as bytes, checks the file set, sizes, timestamps and SHA-256 before/after capture, and preserves exact copies in `originals/`. Source symlinks and changing captures are rejected. A disposable working directory receives the database and WAL; the SHM index is rebuilt there. SQLite's backup API produces `derived/snapshot.sqlite`, followed by an integrity check and supported extraction. The original copies are rehashed after processing. Failed recovery or an unsupported schema still leaves a verifiable `UNRESOLVED` bundle, including the raw evidence and errors.

WAL headers and frames receive structural/checksum validation. Truncated or damaged logs are unresolved instead of silently losing later transactions. A recycled tail outside the current salt sequence is reported. A WAL-mode database without a supplied WAL carries an explicit completeness warning: a clean checkpoint and a missing log cannot generally be distinguished from that file alone. Nonempty rollback journals are preserved but not recovered; obtain a clean offline export. Limits are 64 MiB per input file, 100,000 SMS records, and a 15-second SQLite recovery/query budget. The importer does not carve deleted or uncommitted messages. SQLite documents why the WAL is part of database state and why live file copies require care: [WAL behavior](https://www.sqlite.org/wal.html), [WAL format and checksums](https://www.sqlite.org/walformat.html), [backup API](https://www.sqlite.org/backup.html).

### Supported Android artifact contract

The `android-sms` parser targets an ordinary `sms` table with `_id`, `address`, `date`, `type`, and `body`, based on the [AOSP Telephony database schema](https://android.googlesource.com/platform/packages/providers/TelephonyProvider/+/0ec326a/src/com/android/providers/telephony/MmsSmsDatabaseHelper.java). It preserves supported optional fields such as `thread_id`, `date_sent`, `read`, `status`, `subject`, `service_center`, `sub_id` and `creator`; missing optional fields are listed. An OEM database with incompatible columns is unsupported, not automatically mapped.

Each extracted record contains `artifact`, `source_table`, `source_id`, `counterparty`, `body`, `direction`, `message_type`, `date_utc`, `date_sent_utc`, and `raw`. The raw fields remain available for review. Inbox type 1 is incoming; sent/draft/outbox/failed/queued types 2–6 are outgoing; unknown types retain their original value and unknown direction. A draft or queued message is not evidence of successful delivery. Null addresses remain null; the parser does not invent the device owner's phone number. The `creator` field identifies an app, not a human sender. [Android column/type definitions](https://developer.android.com/reference/android/provider/Telephony.TextBasedSmsColumns)

`date` and nonzero `date_sent` are interpreted as Unix milliseconds, with raw values retained and uninterpretable dates reported. AOSP inserts SMS dates using `System.currentTimeMillis()`: [SmsProvider source](https://android.googlesource.com/platform/packages/providers/TelephonyProvider/+/0aeed2d/src/com/android/providers/telephony/SmsProvider.java). MMS, contacts, RCS, WhatsApp and encrypted databases are outside this parser's contract.

## Acquire from an owned debuggable Android app

ADB must be installed and the selected device/emulator authorized. `adb-status` discovers PATH, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, or the usual Windows SDK location; `--adb` selects another executable. It reports unavailable/unauthorized targets without claiming acquisition succeeded.

Review a JSON allowlist containing exact package/database pairs:

```json
{"format":"witnessforge.adb.allowlist.v1","packages":{"org.witnessforge.fixture":["databases/mmssms.db"]}}
```

Then run:

```powershell
python -m witnessforge adb-status
python -m witnessforge adb-acquire --serial emulator-5580 --package org.witnessforge.fixture --database databases/mmssms.db --allowlist examples/adb-allowlist.json --stop-app --output evidence/android-capture
python -m witnessforge verify-evidence evidence/android-capture
python -m witnessforge bundle-evidence evidence/android-capture --output evidence/android-capture.zip
```

`--stop-app` authorizes a force-stop of exactly the selected allowlisted app; it remains stopped. The tool establishes ordinary `run-as` access first, checks resolved remote paths, captures database/WAL/SHM/journal twice, and requires identical file membership and hashes across passes. Missing sidecars and permission failures are distinguished. It records device serial, build fingerprint, SDK level, app UID, allowlist hash, adb executable hash and command outcomes. Each command has time/output limits. Matching reads reduce races; they are not a proof of an atomic filesystem image. There is no root, `su`, permission change, or bypass operation. Android's [run-as implementation](https://android.googlesource.com/platform/system/core/+/cddcc0d9cf7cbba53e54d67455737a3900ae5d9f/run-as/run-as.cpp) restricts access to installed debuggable applications; binary acquisition uses the documented [ADB raw-output mechanism](https://developer.android.com/tools/adb).

The normal system Telephony provider is not an owned debuggable fixture. This acquisition path does not grant access to it. Import its database only when you already have an authorized export from an appropriate acquisition process.

## Reproduce the actual Android fixture check

The included app `org.witnessforge.fixture` has no SMS or network permission. Its Java Activity creates three synthetic SMS-shaped records using Android SQLite, checkpoints the first, then commits two records into WAL and keeps the connection open. Independent expectations live in [android-fixture/expected.json](android-fixture/expected.json). It is a real Android runtime integration test, not a validation of the system Telephony provider or a production app.

Prerequisites: Python 3.10+, a JDK (tested with Android Studio JBR 21), SDK platform `android-34`, installed build-tools with aapt2/d8/apksigner/zipalign, and an authorized running Android target with API 26+. The APK build uses only installed tools and does not invoke Gradle or download dependencies. The generated debug key and APK stay under ignored `build/`.

```powershell
python android-fixture/build.py --sdk "$env:LOCALAPPDATA/Android/Sdk" --jdk "C:/Program Files/Android/Android Studio/jbr"
python android-fixture/validate.py --serial emulator-5580 --output .test-output/my-android-run
```

The validation script installs/replaces only the fixture package, launches it, waits for its ready marker, runs the allowlisted acquisition, checks the extracted records against independent expectations, requires committed WAL frames, verifies the bundle and writes `validation.json` plus `evidence.zip`. It leaves the fixture stopped. Use a new output directory for every run. The equivalent manual setup is:

```powershell
$wfAdb = "$env:LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe"
& $wfAdb -s emulator-5580 install -r build/android-fixture/witnessforge-fixture.apk
& $wfAdb -s emulator-5580 shell am start -W -n org.witnessforge.fixture/.MainActivity
# Wait until the following prints fixture-v1-ready, then run adb-acquire above.
& $wfAdb -s emulator-5580 shell -T run-as org.witnessforge.fixture cat files/ready.txt
```

### Provision a disposable emulator

You may use Android Studio Device Manager to create an isolated API 35 emulator. For a fresh command-line setup, install Android SDK Command-line Tools through Android Studio first. The following PowerShell example uses an x86_64 host, an unused emulator port 5580, and a new AVD name. Choose a disk with several gigabytes free; change `$wfLab` and the image package for your machine. `sdkmanager` may download missing SDK components; skip that command when these are already installed. Accept SDK licenses through the SDK tools if prompted. Hardware virtualization must be available.

```powershell
$wfSdk = "$env:LOCALAPPDATA/Android/Sdk"
$wfLab = "D:/WitnessForgeLab"
$env:JAVA_HOME = "C:/Program Files/Android/Android Studio/jbr"
$env:ANDROID_AVD_HOME = "$wfLab/avd"
New-Item -ItemType Directory -Force -Path $env:ANDROID_AVD_HOME | Out-Null
& "$wfSdk/cmdline-tools/latest/bin/sdkmanager.bat" "platform-tools" "emulator" "platforms;android-34" "build-tools;36.1.0" "system-images;android-35;google_apis_ps16k;x86_64"
"no" | & "$wfSdk/cmdline-tools/latest/bin/avdmanager.bat" create avd --name WitnessForge_Lab --package "system-images;android-35;google_apis_ps16k;x86_64" --path "$wfLab/avd/WitnessForge_Lab.avd"
Start-Process -WindowStyle Hidden -FilePath "$wfSdk/emulator/emulator.exe" -ArgumentList @('-avd','WitnessForge_Lab','-port','5580','-memory','2048','-no-window','-no-audio','-no-snapshot')
& "$wfSdk/platform-tools/adb.exe" -s emulator-5580 wait-for-device
& "$wfSdk/platform-tools/adb.exe" -s emulator-5580 shell getprop sys.boot_completed
```

Wait for `sys.boot_completed` to print `1` before installing. On Linux/macOS, use the corresponding installed SDK/JDK paths, executable names without `.exe`/`.bat`, a compatible system-image architecture, and the same emulator flags. [Official AVD creation](https://developer.android.com/tools/avdmanager) and [emulator command options](https://developer.android.com/studio/run/emulator-commandline) describe platform prerequisites. To stop only this disposable emulator after validation: `adb -s emulator-5580 emu kill`. Do not remove its files while the emulator is running.

The recorded run used API 35, an x86_64 16 KiB system image, SDK build-tools 36.1.0, and normal run-as acquisition. See [VALIDATION.md](VALIDATION.md) and the checked-in [fixture capture](examples/android-fixture-evidence). Physical-device and production-app behavior have not been validated.

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

## Extending semantic minimization to a production Android app

The acquisition/import path above is implemented. Automated real-app action replay and semantic failure reduction remain future extensions; imported files alone do not supply an independent action ground truth. A correct integration needs more than changing a database filename:

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
