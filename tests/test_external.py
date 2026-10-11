"""Independent SQLite/WAL fixtures and acquisition boundary regressions."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from witnessforge import adb, external


def create_database(path, wal=False):
    connection = sqlite3.connect(path)
    if wal:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.execute('CREATE TABLE sms (_id INTEGER PRIMARY KEY, address TEXT, date INTEGER, type INTEGER, body TEXT, date_sent INTEGER, thread_id INTEGER)')
    connection.execute('INSERT INTO sms VALUES (1, ?, ?, 1, ?, 0, 4)', ('+15550001001', 1700000000123, 'First'))
    connection.commit()
    if wal:
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    connection.execute('INSERT INTO sms VALUES (2, ?, ?, 2, ?, 1700000001000, 4)', ('+15550001001', 1700000001123, 'WAL café ☕'))
    connection.commit()
    return connection


class ExternalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'mmssms.db'
        self.output = self.root / 'bundle'

    def tearDown(self):
        self.temp.cleanup()

    def test_wal_rows_recovered_without_touching_sources(self):
        with closing(create_database(self.source, wal=True)):
            paths = [self.source, Path(str(self.source) + '-wal'), Path(str(self.source) + '-shm')]
            before = {path: path.read_bytes() for path in paths}
            result = external.import_sqlite(self.source, self.output)
            self.assertEqual('IMPORTED', result['status'], result)
            self.assertEqual(before, {path: path.read_bytes() for path in paths})
            self.assertEqual(2, result['extraction']['record_count'])
            self.assertEqual('WAL café ☕', result['extraction']['records'][1]['body'])
            self.assertGreaterEqual(result['wal']['commits'], 1)
            self.assertFalse(result['ground_truth_available'])
            self.assertEqual('not established', result['acquisition_completeness'])
            for item in result['source_files']:
                self.assertEqual(item['sha256'], hashlib.sha256((self.output / item['bundle_path']).read_bytes()).hexdigest())
            with closing(sqlite3.connect(self.output / 'derived/snapshot.sqlite')) as recovered:
                self.assertEqual(2, recovered.execute('SELECT count(*) FROM sms').fetchone()[0])
            self.assertEqual('PASS', external.verify_evidence(self.output)['status'])

    def test_wal_header_and_frame_corruption_are_unresolved(self):
        for offset in (8, 50):
            with self.subTest(offset=offset), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'messages.db'
                with closing(create_database(source, wal=True)):
                    copy = Path(directory) / 'copy.db'
                    copy.write_bytes(source.read_bytes())
                    wal = bytearray(Path(str(source) + '-wal').read_bytes())
                    wal[offset] ^= 1
                    Path(str(copy) + '-wal').write_bytes(wal)
                    report = external.import_sqlite(copy, Path(directory) / 'bundle')
                    self.assertEqual('UNRESOLVED', report['status'])
                    self.assertTrue(report['errors'])

    def test_truncated_wal_is_unresolved(self):
        with closing(create_database(self.source, wal=True)):
            copy = self.root / 'copy.db'
            copy.write_bytes(self.source.read_bytes())
            Path(str(copy) + '-wal').write_bytes(Path(str(self.source) + '-wal').read_bytes()[:-5])
            result = external.import_sqlite(copy, self.output)
            self.assertEqual('UNRESOLVED', result['status'])
            self.assertIn('incomplete frame', result['errors'][0])

    def test_missing_wal_is_explicit_uncertainty_not_complete_acquisition(self):
        with closing(create_database(self.source, wal=True)):
            copy = self.root / 'copy.db'
            copy.write_bytes(self.source.read_bytes())
            result = external.import_sqlite(copy, self.output)
            self.assertEqual('IMPORTED', result['status'])
            self.assertEqual(1, result['extraction']['record_count'])
            self.assertIn('no WAL', result['warnings'][0])
            self.assertEqual('not established', result['acquisition_completeness'])

    def test_nonempty_rollback_journal_is_not_silently_discarded(self):
        create_database(self.source).close()
        Path(str(self.source) + '-journal').write_bytes(b'potential hot journal')
        result = external.import_sqlite(self.source, self.output)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('Rollback journal', result['errors'][0])

    def test_sqlite_only_accepts_non_sms_schema_but_sms_parser_does_not(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute('CREATE TABLE harmless (id INTEGER)')
        result = external.import_sqlite(self.source, self.output)
        self.assertEqual('UNRESOLVED', result['status'])
        generic = external.import_sqlite(self.source, self.root / 'generic', 'sqlite-only')
        self.assertEqual('IMPORTED', generic['status'])
        self.assertNotIn('extraction', generic)

    def test_sms_view_cannot_impersonate_supported_table(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("CREATE VIEW sms AS SELECT 1 AS _id, '' AS address, 0 AS date, 1 AS type, '' AS body")
        with self.assertRaisesRegex(ValueError, 'ordinary sms table'):
            external.parse_android_sms(self.source)

    def test_unknown_values_and_null_counterparty_preserved(self):
        with closing(create_database(self.source)) as connection:
            connection.execute("INSERT INTO sms VALUES (3, NULL, 'unknown date', 99, NULL, NULL, NULL)")
            connection.commit()
        result = external.parse_android_sms(self.source)
        record = result['records'][-1]
        self.assertIsNone(record['counterparty'])
        self.assertEqual('unknown', record['direction'])
        self.assertEqual('unknown date', record['raw']['date'])
        self.assertIsNone(record['date_utc'])
        self.assertEqual(2, len(result['warnings']))
        self.assertEqual('2023-11-14T22:13:20.123+00:00', result['records'][0]['date_utc'])

    def test_row_limit_rejects_partial_extraction(self):
        create_database(self.source).close()
        with self.assertRaisesRegex(ValueError, 'row limit'):
            external.parse_android_sms(self.source, max_rows=1)

    def test_corrupt_database_preserved_with_unresolved_status(self):
        self.source.write_bytes(b'not a database')
        result = external.import_sqlite(self.source, self.output)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertEqual(b'not a database', (self.output / 'originals/evidence.sqlite').read_bytes())
        self.assertEqual('PASS', external.verify_evidence(self.output)['status'])

    def test_existing_output_never_overwritten(self):
        create_database(self.source).close()
        self.output.mkdir()
        (self.output / 'marker').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            external.import_sqlite(self.source, self.output)
        self.assertEqual('keep', (self.output / 'marker').read_text())

    def test_source_change_aborts_capture(self):
        create_database(self.source).close()
        real_stamp = external._stamp
        count = [0]

        def changed(path):
            result = real_stamp(path)
            count[0] += 1
            if count[0] > 1:
                result['sha256'] = 'changed'
            return result

        with patch.object(external, '_stamp', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed during capture'):
            external.import_sqlite(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_tampered_missing_and_extra_files_fail_manifest(self):
        create_database(self.source).close()
        external.import_sqlite(self.source, self.output)
        report_path = self.output / 'evidence.json'
        original = report_path.read_bytes()
        report_path.write_bytes(b'changed')
        self.assertEqual('FAIL', external.verify_evidence(self.output)['status'])
        report_path.write_bytes(original)
        (self.output / 'unlisted').write_bytes(b'x')
        self.assertEqual('FAIL', external.verify_evidence(self.output)['status'])
        (self.output / 'unlisted').unlink()
        report_path.unlink()
        self.assertEqual('FAIL', external.verify_evidence(self.output)['status'])

    def test_manifest_traversal_and_missing_mandatory_entry_rejected(self):
        create_database(self.source).close()
        external.import_sqlite(self.source, self.output)
        path = self.output / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files']['../outside'] = 'abc'
        path.write_text(json.dumps(manifest))
        self.assertEqual('FAIL', external.verify_evidence(self.output)['status'])
        manifest['files'] = {}
        path.write_text(json.dumps(manifest))
        self.assertEqual('FAIL', external.verify_evidence(self.output)['status'])

    def test_bundle_is_verified_and_cannot_be_created_inside_evidence(self):
        create_database(self.source).close()
        external.import_sqlite(self.source, self.output)
        result = external.bundle_evidence(self.output, self.root / 'evidence.zip')
        self.assertEqual('BUNDLED', result['status'])
        with zipfile.ZipFile(self.root / 'evidence.zip') as archive:
            self.assertIn('originals/evidence.sqlite', archive.namelist())
        with self.assertRaises(ValueError):
            external.bundle_evidence(self.output, self.output / 'self.zip')

    def test_cli_import_and_verify(self):
        create_database(self.source).close()
        result = subprocess.run([sys.executable, '-m', 'witnessforge', 'import-sqlite', str(self.source), '--output', str(self.output)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('IMPORTED', json.loads(result.stdout)['status'])
        verify = subprocess.run([sys.executable, '-m', 'witnessforge', 'verify-evidence', str(self.output)], capture_output=True, text=True)
        self.assertEqual(0, verify.returncode, verify.stderr)


class AdbBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.allowlist = self.root / 'allowlist.json'
        self.allowlist.write_text(json.dumps({'format': 'witnessforge.adb.allowlist.v1', 'packages': {'org.witnessforge.fixture': ['databases/mmssms.db']}}))
        self.executable = self.root / 'adb.exe'
        self.executable.write_bytes(b'fake adb for unit tests; never executed')
        self.database = self.root / 'source.db'
        create_database(self.database).close()
        self.calls = []
        self.reads = 0
        self.change = False
        self.denied = False

    def tearDown(self):
        self.temp.cleanup()

    def fake_run(self, command, **kwargs):
        args = command[3:]
        self.calls.append(args)
        output, error, code = b'', b'', 0
        if args == ['get-state']:
            output = b'device\n'
        elif args[-1] == 'id':
            output = b'uid=10099(u0_a99) gid=10099\n'
            if self.denied:
                output, error, code = b'', b'run-as: package not debuggable', 1
        elif 'getprop' in args:
            output = b'owned-test-build\n'
        elif 'test' in args:
            if args[-1].endswith(('-wal', '-shm', '-journal')):
                code = 1
        elif 'readlink' in args:
            output = ('/data/user/0/org.witnessforge.fixture/' + args[-1] + '\n').encode()
        elif 'cat' in args:
            self.reads += 1
            output = self.database.read_bytes()
            if self.change and self.reads == 2:
                output += b'changed'
        return subprocess.CompletedProcess(command, code, output, error)

    def acquire(self, **kwargs):
        options = {'serial': 'emulator-5580', 'package': 'org.witnessforge.fixture', 'database': 'databases/mmssms.db',
                   'allowlist': self.allowlist, 'output': self.root / 'acquired', 'stop_app': True, 'adb': self.executable}
        options.update(kwargs)
        return adb.acquire_sqlite(**options)

    def test_allowlisted_double_capture_and_force_stop(self):
        with patch.object(adb, '_run', side_effect=self.fake_run):
            result = self.acquire()
        self.assertEqual('IMPORTED', result['status'])
        self.assertEqual(2, self.reads)
        self.assertIn(['shell', 'am', 'force-stop', 'org.witnessforge.fixture'], self.calls)
        self.assertEqual(2, result['provenance']['matching_capture_passes'])
        self.assertFalse(any('root' in args or 'su' in args for args in self.calls))

    def test_allowlist_and_remote_shell_injection_rejected_before_adb(self):
        for changes in ({'package': 'org.other.app'}, {'package': 'org.app;id'}, {'database': 'databases/../secret'},
                        {'database': 'databases/x;id'}, {'serial': '-e'}, {'stop_app': False}):
            with self.subTest(changes=changes), patch.object(adb, '_run') as run, self.assertRaises(ValueError):
                self.acquire(**changes)
            run.assert_not_called()

    def test_nonobject_allowlist_rejected(self):
        self.allowlist.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'Unsupported acquisition allowlist'):
            self.acquire()

    def test_non_debuggable_failure_never_triggers_bypass_or_force_stop(self):
        self.denied = True
        with patch.object(adb, '_run', side_effect=self.fake_run), self.assertRaisesRegex(ValueError, 'not debuggable'):
            self.acquire()
        self.assertFalse(any('force-stop' in args or 'root' in args for args in self.calls))

    def test_changing_device_capture_is_unresolved_and_not_published(self):
        self.change = True
        with patch.object(adb, '_run', side_effect=self.fake_run), self.assertRaisesRegex(ValueError, 'changed across'):
            self.acquire()
        self.assertFalse((self.root / 'acquired').exists())

    def test_remote_symlink_escape_rejected(self):
        original = self.fake_run

        def wrong_path(command, **kwargs):
            result = original(command, **kwargs)
            if 'readlink' in command:
                result.stdout = b'/data/user/0/org.other.app/databases/mmssms.db\n'
            return result

        with patch.object(adb, '_run', side_effect=wrong_path), self.assertRaisesRegex(ValueError, 'path resolution'):
            self.acquire()

    def test_missing_adb_capability_is_blocked(self):
        result = adb.adb_status(self.root / 'missing-adb')
        self.assertEqual('BLOCKED', result['status'])

    def test_process_timeout_and_output_limit(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            adb._run([sys.executable, '-c', 'import time; time.sleep(3)'], timeout=0.05)
        with self.assertRaisesRegex(ValueError, 'output limits'):
            adb._run([sys.executable, '-c', 'print("x" * 100000)'], max_bytes=100)


if __name__ == '__main__':
    unittest.main()
