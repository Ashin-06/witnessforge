"""Install the owned fixture on an explicit emulator/device, acquire and check its artifacts.

Only this fixture package is installed/restarted; acquisition leaves it stopped.
This script requires an already running authorized target and an existing fixture APK.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from witnessforge.adb import acquire_sqlite, find_adb
from witnessforge.external import verify_evidence, bundle_evidence


def validate(serial, apk, output, adb=None):
    executable = find_adb(adb)
    apk, output = Path(apk).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Validation output must be a new directory.')
    if not apk.is_file():
        raise ValueError('Build the fixture APK first.')
    output.mkdir(parents=True)
    commands = []

    def call(args):
        result = subprocess.run([executable, '-s', serial] + args, capture_output=True, timeout=60)
        commands.append({'arguments': args, 'exit_code': result.returncode,
                         'stdout': result.stdout.decode('utf-8', 'replace'), 'stderr': result.stderr.decode('utf-8', 'replace')})
        if result.returncode:
            raise ValueError('Fixture setup failed: ' + commands[-1]['stdout'] + commands[-1]['stderr'])
        return result.stdout

    report = {'status': 'FAIL', 'scope': 'Real Android runtime and ADB, owned synthetic fixture app, no real personal SMS or system Telephony provider validation.',
              'serial': serial, 'apk_sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'commands': commands}
    try:
        call(['install', '-r', str(apk)])
        call(['shell', 'am', 'force-stop', 'org.witnessforge.fixture'])
        call(['shell', 'am', 'start', '-W', '-n', 'org.witnessforge.fixture/.MainActivity'])
        ready = False
        for _ in range(30):
            result = subprocess.run([executable, '-s', serial, 'shell', '-T', 'run-as', 'org.witnessforge.fixture', 'cat', 'files/ready.txt'], capture_output=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip() == b'fixture-v1-ready':
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            raise ValueError('Owned fixture did not report ready.')
        evidence = acquire_sqlite(serial, 'org.witnessforge.fixture', 'databases/mmssms.db', ROOT.parent / 'examples/adb-allowlist.json', output / 'evidence', stop_app=True, adb=executable)
        expected_document = json.loads((ROOT / 'expected.json').read_text(encoding='utf-8'))
        expected = expected_document['records']
        observed = evidence.get('extraction', {}).get('records', [])
        fields = tuple(expected[0])
        actual = [{key: record.get(key) for key in fields} for record in observed]
        integrity = verify_evidence(output / 'evidence')
        wal_preserved = evidence.get('wal', {}).get('commits', 0) >= 1
        report.update({'import_status': evidence['status'], 'expected': expected, 'actual': actual,
                       'semantic_match': expected == actual, 'committed_wal_preserved': wal_preserved,
                       'integrity': integrity, 'build_fingerprint': evidence['provenance']['android_build_fingerprint'],
                       'expected_sha256': hashlib.sha256((ROOT / 'expected.json').read_bytes()).hexdigest()})
        if evidence['status'] == 'IMPORTED' and expected == actual and wal_preserved and integrity['status'] == 'PASS':
            report['bundle'] = bundle_evidence(output / 'evidence', output / 'evidence.zip')
            report['status'] = 'PASS'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--apk', default=str(ROOT.parent / 'build/android-fixture/witnessforge-fixture.apk'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--adb')
    args = parser.parse_args()
    result = validate(args.serial, args.apk, args.output, args.adb)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    sys.exit(0 if result['status'] == 'PASS' else 1)
