"""Allowlisted acquisition from an owned debuggable app using ordinary adb run-as.

No root, su, permission bypass, or system-provider acquisition is attempted.
Stopping the selected app is an explicit, recorded change to the device.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .external import MAX_FILE_BYTES, import_sqlite

PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')
DATABASE = re.compile(r'databases/[A-Za-z0-9][A-Za-z0-9_.-]{0,100}')
SERIAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


def find_adb(explicit=None):
    candidates = [explicit] if explicit else [shutil.which('adb')]
    if not explicit:
        for key in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / 'platform-tools' / ('adb.exe' if os.name == 'nt' else 'adb'))
        if os.environ.get('LOCALAPPDATA'):
            candidates.append(Path(os.environ['LOCALAPPDATA']) / 'Android' / 'Sdk' / 'platform-tools' / 'adb.exe')
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise ValueError('Android SDK platform-tools adb was not found. Pass --adb or configure ANDROID_HOME.')


def _run(command, timeout=30, max_bytes=MAX_FILE_BYTES):
    """Bound time and output without buffering arbitrary adb output in memory."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + timeout
        failure = None
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    failure = 'ADB command timed out.'
                    break
                if os.fstat(stdout.fileno()).st_size > max_bytes or os.fstat(stderr.fileno()).st_size > 262144:
                    failure = 'ADB command exceeded output limits.'
                    break
                time.sleep(0.02)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        if failure or os.fstat(stdout.fileno()).st_size > max_bytes or os.fstat(stderr.fileno()).st_size > 262144:
            raise ValueError(failure or 'ADB command exceeded output limits.')
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, process.returncode, stdout.read(), stderr.read())


def adb_status(adb=None):
    try:
        executable = find_adb(adb)
        version = _run([executable, 'version'], max_bytes=65536)
        devices = _run([executable, 'devices', '-l'], max_bytes=65536)
        if version.returncode or devices.returncode:
            raise ValueError((version.stderr + devices.stderr).decode('utf-8', 'replace').strip())
        rows = []
        for line in devices.stdout.decode('utf-8', 'replace').splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                rows.append({'serial': parts[0], 'state': parts[1], 'details': ' '.join(parts[2:])})
        return {'status': 'AVAILABLE' if any(item['state'] == 'device' for item in rows) else 'BLOCKED',
                'adb': executable, 'version': version.stdout.decode('utf-8', 'replace').strip(), 'devices': rows,
                'requirements': ['An explicitly selected authorized device or emulator.',
                                 'An owned installed debuggable app and exact package/database allowlist.',
                                 'Explicit --stop-app authorization; selected app remains stopped.'],
                'limits': ['No root or permission bypass.', 'Not a claim of live-device acquisition validation.']}
    except (OSError, ValueError) as exc:
        return {'status': 'BLOCKED', 'devices': [], 'reason': str(exc)}


def _authorization(allowlist, package, database, serial, stop_app):
    if not isinstance(package, str) or not PACKAGE.fullmatch(package):
        raise ValueError('Invalid Android package name.')
    if not isinstance(database, str) or not DATABASE.fullmatch(database) or database.split('/')[-1] in ('.', '..'):
        raise ValueError('Database must be one safe file immediately inside databases/.')
    if not isinstance(serial, str) or not SERIAL.fullmatch(serial):
        raise ValueError('An explicit safe device serial is required.')
    if not stop_app:
        raise ValueError('Acquisition requires --stop-app: it force-stops only the allowlisted app and leaves it stopped.')
    path = Path(allowlist)
    if path.stat().st_size > 65536:
        raise ValueError('Allowlist exceeds 64 KiB.')
    raw = path.read_bytes()
    document = json.loads(raw.decode('utf-8-sig'))
    packages = document.get('packages') if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get('format') != 'witnessforge.adb.allowlist.v1' or not isinstance(packages, dict):
        raise ValueError('Unsupported acquisition allowlist.')
    allowed = packages.get(package)
    if not isinstance(allowed, list) or database not in allowed:
        raise ValueError('Package/database pair is not explicitly allowlisted.')
    return hashlib.sha256(raw).hexdigest()


def acquire_sqlite(serial, package, database, allowlist, output, *, stop_app=False, adb=None, parser='android-sms'):
    allowlist_hash = _authorization(allowlist, package, database, serial, stop_app)
    if Path(output).exists():
        raise ValueError('Output must be a new directory.')
    executable = find_adb(adb)
    command_log = []

    def call(arguments, accept_missing=False, binary=False):
        command = [executable, '-s', serial] + arguments
        result = _run(command, max_bytes=MAX_FILE_BYTES if binary else 65536)
        diagnostic = result.stderr.decode('utf-8', 'replace').strip()[:2048]
        command_log.append({'arguments': arguments, 'exit_code': result.returncode, 'stderr': diagnostic,
                            'stdout_bytes': len(result.stdout)})
        if result.returncode:
            if accept_missing and result.returncode == 1 and not diagnostic and not result.stdout:
                return None
            raise ValueError('ADB command failed: ' + (diagnostic or result.stdout.decode('utf-8', 'replace')[:2048] or str(result.returncode)))
        return result.stdout

    if call(['get-state']).strip() != b'device':
        raise ValueError('Selected ADB device is not ready.')
    identity = call(['shell', '-T', 'run-as', package, 'id']).decode('utf-8', 'replace').strip()
    if not identity.startswith('uid='):
        raise ValueError('run-as did not establish the selected app identity.')
    fingerprint = call(['shell', 'getprop', 'ro.build.fingerprint']).decode('utf-8', 'replace').strip()
    sdk = call(['shell', 'getprop', 'ro.build.version.sdk']).decode('utf-8', 'replace').strip()
    call(['shell', 'am', 'force-stop', package])
    # Normal run-as starts in the app's data directory. Reject links out of it.
    allowed_real_paths = ('/data/user/0/' + package + '/', '/data/data/' + package + '/')
    with tempfile.TemporaryDirectory(prefix='witnessforge-adb-') as temp:
        captures = []
        for index in range(2):
            directory = Path(temp) / str(index)
            directory.mkdir()
            inventory = {}
            for suffix in ('', '-wal', '-shm', '-journal'):
                remote = database + suffix
                exists = call(['shell', '-T', 'run-as', package, 'test', '-f', remote], accept_missing=True)
                if exists is None:
                    if not suffix:
                        raise ValueError('Allowlisted database does not exist in the selected app.')
                    continue
                resolved = call(['shell', '-T', 'run-as', package, 'readlink', '-f', remote]).decode('utf-8', 'strict').strip()
                if resolved not in tuple(prefix + remote for prefix in allowed_real_paths):
                    raise ValueError('Remote path resolution leaves the exact allowlisted app/database location.')
                content = call(['exec-out', 'run-as', package, 'cat', remote], binary=True)
                (directory / ('evidence.sqlite' + suffix)).write_bytes(content)
                inventory[suffix] = hashlib.sha256(content).hexdigest()
            captures.append(inventory)
        if captures[0] != captures[1]:
            raise ValueError('ADB files changed across two complete captures; acquisition is unresolved.')
        provenance = {'method': 'adb-run-as-double-capture', 'serial': serial, 'package': package,
                      'database': database, 'android_build_fingerprint': fingerprint, 'android_sdk': sdk,
                      'run_as_identity': identity, 'adb_executable': executable, 'adb_sha256': hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
                      'allowlist_sha256': allowlist_hash, 'app_force_stopped': True,
                      'app_left_stopped': True, 'matching_capture_passes': 2, 'capture_hashes': captures[0],
                      'commands': command_log,
                      'limits': 'Matching reads reduce races but do not prove an atomic or complete acquisition. Captures are app data, not physical filesystem images.'}
        return import_sqlite(Path(temp) / '0' / 'evidence.sqlite', output, parser=parser, provenance=provenance)
