"""Regressions for concrete hostile-input, recovery-limit, and export-race defects."""

import ast
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from witnessforge import adb, external
from test_external import create_database


class EvidenceHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / 'source.db'
        self.bundle = self.root / 'bundle'

    def tearDown(self):
        self.temp.cleanup()

    def make_bundle(self):
        create_database(self.database).close()
        return external.import_sqlite(self.database, self.bundle)

    def test_first_wal_salt_mismatch_cannot_silently_drop_committed_rows(self):
        with closing(create_database(self.database, wal=True)):
            copied = self.root / 'copied.db'
            copied.write_bytes(self.database.read_bytes())
            wal = bytearray(Path(str(self.database) + '-wal').read_bytes())
            wal[40] ^= 1
            Path(str(copied) + '-wal').write_bytes(wal)
            result = external.import_sqlite(copied, self.bundle)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('first frame salt', result['errors'][0])
        self.assertNotIn('extraction', result)

    def test_database_and_wal_page_size_mismatch_rejected(self):
        with closing(create_database(self.database, wal=True)):
            copied = self.root / 'copied.db'
            raw = bytearray(self.database.read_bytes())
            raw[16:18] = (8192).to_bytes(2, 'big')
            copied.write_bytes(raw)
            Path(str(copied) + '-wal').write_bytes(Path(str(self.database) + '-wal').read_bytes())
            result = external.import_sqlite(copied, self.bundle)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('page sizes differ', result['errors'][0])

    def test_nonfinite_sqlite_values_never_emit_nonstandard_json(self):
        with closing(create_database(self.database)) as connection:
            connection.execute('UPDATE sms SET date=? WHERE _id=1', (float('inf'),))
            connection.commit()
        result = external.import_sqlite(self.database, self.bundle)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('Non-finite', result['errors'][0])

        def reject_constant(value):
            raise AssertionError('Nonstandard JSON token: ' + value)

        document = json.loads((self.bundle / 'evidence.json').read_text(), parse_constant=reject_constant)
        self.assertEqual('UNRESOLVED', document['status'])

    def test_recovered_size_counts_actual_page_bytes_including_wal(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('PRAGMA page_size=8192')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA wal_autocheckpoint=0')
            connection.execute('CREATE TABLE sms (_id INTEGER PRIMARY KEY,address TEXT,date INTEGER,type INTEGER,body TEXT)')
            connection.execute('CREATE TABLE unrelated (id INTEGER)')
            connection.commit()
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            connection.executemany('INSERT INTO sms VALUES (?,NULL,0,1,?)', [(1, 'x' * 6000), (2, 'y' * 6000)])
            connection.commit()
            self.assertLessEqual(self.database.stat().st_size, 34000)
            self.assertLessEqual(Path(str(self.database) + '-wal').stat().st_size, 34000)
            self.assertGreater(connection.execute('PRAGMA page_count').fetchone()[0] * 8192, 34000)
            with patch.object(external, 'MAX_FILE_BYTES', 34000):
                result = external.import_sqlite(self.database, self.bundle)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('Recovered database exceeds', result['errors'][0])

    def test_python310_without_setlimit_still_enforces_cell_size(self):
        create_database(self.database).close()
        original_connect = sqlite3.connect

        class OldConnection:
            def __init__(self, *args, **kwargs):
                self.connection = original_connect(*args, **kwargs)

            def __getattr__(self, name):
                if name == 'setlimit':
                    raise AttributeError(name)
                return getattr(self.connection, name)

        with patch.object(external.sqlite3, 'connect', OldConnection), patch.object(external, 'MAX_CELL_BYTES', 5):
            with self.assertRaisesRegex(ValueError, 'SMS cell exceeds'):
                external.parse_android_sms(self.database)

    def test_extraction_size_is_bounded_before_result_is_returned(self):
        create_database(self.database).close()
        with patch.object(external, 'MAX_EXTRACTION_BYTES', 100):
            result = external.import_sqlite(self.database, self.bundle)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('serialized record limit', result['errors'][0])

    def test_hashing_rejects_file_growth_beyond_bound(self):
        self.database.write_bytes(b'123456789')
        with self.assertRaisesRegex(ValueError, 'hash size limit'):
            external._digest(self.database, max_bytes=5)

    def test_json_writer_is_strict_even_outside_sms_parser(self):
        with self.assertRaises(ValueError):
            external._json(self.root / 'invalid.json', {'value': float('nan')})
        self.assertFalse((self.root / 'invalid.json').exists())

    def test_duplicate_manifest_keys_are_not_silently_replaced(self):
        self.make_bundle()
        path = self.bundle / 'manifest.json'
        document = path.read_text()
        path.write_text(document.replace('"algorithm": "sha256"', '"algorithm": "other", "algorithm": "sha256"'))
        result = external.verify_evidence(self.bundle)
        self.assertEqual('FAIL', result['status'])
        self.assertIn('Duplicate JSON key', result['errors'][0])

    def test_manifest_accepts_only_fixed_artifact_paths_and_hex_hashes(self):
        self.make_bundle()
        path = self.bundle / 'manifest.json'
        original = json.loads(path.read_text())
        for malformed in ('XYZ', 42, None):
            document = json.loads(json.dumps(original))
            document['files']['evidence.json'] = malformed
            path.write_text(json.dumps(document))
            self.assertEqual('FAIL', external.verify_evidence(self.bundle)['status'])
        original['files']['arbitrary.bin'] = '0' * 64
        path.write_text(json.dumps(original))
        self.assertEqual('FAIL', external.verify_evidence(self.bundle)['status'])

    def test_unknown_empty_directories_and_deep_trees_are_rejected(self):
        self.make_bundle()
        (self.bundle / 'unexpected').mkdir()
        result = external.verify_evidence(self.bundle)
        self.assertEqual('FAIL', result['status'])
        self.assertIn('unsafe bundle entry', result['errors'][0])

    def test_export_hashes_the_same_bytes_it_writes_despite_later_source_mutation(self):
        self.make_bundle()
        report_path = self.bundle / 'evidence.json'
        original = report_path.read_bytes()
        original_write = zipfile.ZipFile.writestr

        def mutate_after_bytes_pinned(archive, name, data, *args, **kwargs):
            if name == 'evidence.json':
                report_path.write_bytes(b'mutated concurrently')
            return original_write(archive, name, data, *args, **kwargs)

        output = self.root / 'evidence.zip'
        with patch.object(zipfile.ZipFile, 'writestr', mutate_after_bytes_pinned):
            result = external.bundle_evidence(self.bundle, output)
        self.assertEqual('BUNDLED', result['status'])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(original, archive.read('evidence.json'))
            manifest = json.loads(archive.read('manifest.json'))
            for name, expected in manifest['files'].items():
                self.assertEqual(expected, hashlib.sha256(archive.read(name)).hexdigest())

    def test_export_rejects_mutation_before_read_and_leaves_no_partial_zip(self):
        self.make_bundle()
        original_read = external._read_bytes

        def mutate_before_read(path, limit):
            if Path(path).name == 'evidence.json':
                Path(path).write_bytes(b'mutated concurrently')
            return original_read(path, limit)

        output = self.root / 'evidence.zip'
        with patch.object(external, '_read_bytes', side_effect=mutate_before_read), self.assertRaisesRegex(ValueError, 'failed integrity'):
            external.bundle_evidence(self.bundle, output)
        self.assertFalse(output.exists())

    def test_export_rejects_manifest_change_during_zip_creation(self):
        self.make_bundle()
        original_write = zipfile.ZipFile.writestr

        def change_manifest(archive, name, data, *args, **kwargs):
            if name == 'evidence.json':
                (self.bundle / 'manifest.json').write_text('{}')
            return original_write(archive, name, data, *args, **kwargs)

        output = self.root / 'evidence.zip'
        with patch.object(zipfile.ZipFile, 'writestr', change_manifest), self.assertRaisesRegex(ValueError, 'manifest changed'):
            external.bundle_evidence(self.bundle, output)
        self.assertFalse(output.exists())

    def test_invalid_acquisition_options_fail_before_any_adb_side_effect(self):
        allowlist = self.root / 'allowlist.json'
        allowlist.write_text(json.dumps({'format': 'witnessforge.adb.allowlist.v1', 'packages': {'org.example.fixture': ['databases/example.db']}}))
        for options in ({'parser': 'typo', 'stop_app': True}, {'stop_app': 'false'}):
            with self.subTest(options=options), patch.object(adb, '_run') as run, self.assertRaises(ValueError):
                adb.acquire_sqlite('emulator-5580', 'org.example.fixture', 'databases/example.db', allowlist, self.bundle, **options)
            run.assert_not_called()

    def test_python310_source_syntax(self):
        root = Path(__file__).resolve().parents[1] / 'witnessforge'
        for path in root.glob('*.py'):
            with self.subTest(file=path.name):
                ast.parse(path.read_text(encoding='utf-8'), filename=str(path), feature_version=(3, 10))

    def test_historical_android_capture_still_verifies_and_parses(self):
        sample = Path(__file__).resolve().parents[1] / 'examples/android-fixture-evidence'
        self.assertEqual('PASS', external.verify_evidence(sample)['status'])
        # Only copies are parsed; the hash-covered historical capture is immutable.
        for source in (sample / 'originals').iterdir():
            shutil.copyfile(source, self.root / source.name)
        result = external.import_sqlite(self.root / 'evidence.sqlite', self.bundle)
        self.assertEqual('IMPORTED', result['status'])
        self.assertEqual(3, result['extraction']['record_count'])
        self.assertGreater(result['wal']['commits'], 0)


if __name__ == '__main__':
    unittest.main()
