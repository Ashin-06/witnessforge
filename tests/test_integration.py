"""Exercise the actual CLI, server, parser subprocess and evidence lifecycle."""

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
from unittest.mock import patch

from witnessforge.cases import bundle_case, list_cases, load_case, run_case, verify_case
from witnessforge.cli import main
from witnessforge.core import demo_trace, inspect_trace
from witnessforge.plugins import external_parser
from witnessforge.server import create_server


class CasesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_full_case_contains_replayable_sqlite_and_verifies(self):
        case = run_case(self.root, demo_trace())
        directory = self.root / case['case_id']
        self.assertEqual('FAIL', case['result']['status'])
        self.assertEqual(4, case['result']['minimized_count'])
        self.assertTrue(case['result']['one_minimal'])
        self.assertTrue(all(c['matches_repeated_oracle'] for c in case['result']['witness_checks']))
        self.assertEqual('PASS', verify_case(directory)['status'])
        self.assertEqual(case, load_case(self.root, case['case_id']))
        self.assertEqual(1, len(list_cases(self.root)))
        databases = list(directory.rglob('*.sqlite'))
        self.assertEqual(2, len(databases))
        for database in databases:
            with contextlib.closing(sqlite3.connect(database)) as connection:
                self.assertEqual('ok', connection.execute('PRAGMA integrity_check').fetchone()[0])
        with zipfile.ZipFile(io.BytesIO(bundle_case(self.root, case['case_id']))) as archive:
            self.assertIn(case['case_id'] + '/manifest.json', archive.namelist())
            self.assertEqual(None, archive.testzip())

    def test_tampering_missing_and_extra_files_are_detected(self):
        case = run_case(self.root, demo_trace())
        directory = self.root / case['case_id']
        report = directory / 'report.html'
        report.write_text('modified', encoding='utf-8')
        (directory / 'original-trace.json').unlink()
        (directory / 'extra.txt').write_text('extra', encoding='utf-8')
        result = verify_case(directory)
        self.assertEqual('FAIL', result['status'])
        self.assertEqual(3, len(result['errors']))
        with self.assertRaises(ValueError):
            bundle_case(self.root, case['case_id'])

    def test_manifest_path_traversal_is_rejected(self):
        case = run_case(self.root, [])
        directory = self.root / case['case_id']
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        manifest['files']['../outside'] = 'fake'
        (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.assertIn('Unsafe manifest path', ' '.join(verify_case(directory)['errors']))

    def test_fixed_parser_and_html_escaping(self):
        trace = demo_trace()
        trace[0]['text'] = '<script>alert("unsafe")</script>'
        case = run_case(self.root, trace, parser='fixed')
        self.assertEqual('PASS', case['result']['status'])
        self.assertEqual(20, case['result']['minimized_count'])
        self.assertFalse(case['result']['one_minimal'])
        report = (self.root / case['case_id'] / 'report.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', report)
        self.assertIn('&lt;script&gt;', report)

    def test_cli_exit_codes_distinguish_inspection_and_experiment(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, main(['inspect']))
            self.assertEqual(0, main(['inspect', '--parser', 'fixed']))
            self.assertEqual(0, main(['demo', '--cases-dir', str(self.root)]))
            self.assertEqual(2, main(['minimize', '--budget', '0', '--cases-dir', str(self.root)]))

    def test_external_parser_success_failure_timeout_and_mutation(self):
        candidate = self.root / 'parser.py'
        candidate.write_text('from witnessforge.core import parse_fixed\ndef parse(path):\n    return parse_fixed(path)\n', encoding='utf-8')
        self.assertEqual('PASS', inspect_trace(demo_trace(), parser=external_parser(candidate))['status'])
        candidate.write_text('def parse(path):\n    raise ValueError("deliberate failure")\n', encoding='utf-8')
        failure = inspect_trace(demo_trace(), parser=external_parser(candidate))
        self.assertEqual('UNRESOLVED', failure['status'])
        self.assertIn('deliberate failure', failure['error'])
        candidate.write_text('import time\ndef parse(path):\n    time.sleep(5)\n    return []\n', encoding='utf-8')
        self.assertIn('exceeded', inspect_trace([], parser=external_parser(candidate, timeout=0.1))['error'])
        candidate.write_text('def parse(path):\n    path.write_bytes(b"changed")\n    return []\n', encoding='utf-8')
        self.assertIn('modified', inspect_trace([], parser=external_parser(candidate))['error'])

    def test_external_parser_preserves_unicode_under_windows_codepage(self):
        candidate = self.root / 'unicode_parser.py'
        candidate.write_text('from witnessforge.core import parse_fixed\ndef parse(path):\n    return parse_fixed(path)\n', encoding='utf-8')
        trace = [{'id': 'u1', 'op': 'send', 'message_id': 'unicode', 'sender': 'Maya', 'recipient': 'Noor', 'text': '\u20b9 50 \u2713 \U0001f44d'}]
        with patch.dict('os.environ', {'PYTHONIOENCODING': 'cp1252'}):
            result = inspect_trace(trace, parser=external_parser(candidate))
        self.assertEqual('PASS', result['status'])
        self.assertEqual(trace[0]['text'], result['actual'][0]['text'])

    def test_external_parser_source_is_pinned_for_a_run(self):
        candidate = self.root / 'changing_parser.py'
        candidate.write_text('def parse(path):\n    return []\n', encoding='utf-8')
        selected = external_parser(candidate)
        candidate.write_text('def parse(path):\n    return None\n', encoding='utf-8')
        result = inspect_trace([], parser=selected)
        self.assertEqual('UNRESOLVED', result['status'])
        self.assertIn('source changed', result['error'])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = create_server(0, cls.temp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def request(self, path, body=None, headers=None):
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, response.read(), response.headers
        except HTTPError as response:
            return response.code, response.read(), response.headers

    def test_demo_run_download_and_repair(self):
        status, data, _ = self.request('/api/demo')
        self.assertEqual(200, status)
        trace = json.loads(data)['trace']
        status, data, headers = self.request('/api/run', {'trace': trace, 'version': '2', 'parser': 'buggy'})
        self.assertEqual(200, status)
        self.assertEqual('no-store', headers['Cache-Control'])
        case = json.loads(data)
        self.assertEqual(4, case['result']['minimized_count'])
        self.assertEqual(200, self.request(case['artifacts']['report'])[0])
        self.assertEqual(200, self.request(case['artifacts']['bundle'])[0])
        self.assertEqual('PASS', json.loads(self.request('/api/cases/' + case['case_id'] + '/verify')[1])['status'])
        status, data, _ = self.request('/api/validate-repair', {})
        self.assertEqual(200, status)
        self.assertEqual('PASS', json.loads(data)['status'])

    def test_bad_inputs_and_cross_origin_rejected(self):
        self.assertEqual(403, self.request('/api/run', {}, {'Origin': 'https://example.com'})[0])
        self.assertEqual(403, self.request('/api/demo', headers={'Host': 'attacker.invalid'})[0])
        self.assertEqual(415, self.request('/api/run', {}, {'Content-Type': 'text/plain'})[0])
        self.assertEqual(400, self.request('/api/run', {'trace': 'bad'})[0])
        self.assertEqual(400, self.request('/api/run', {'trace': [], 'repeats': True})[0])
        self.assertEqual(400, self.request('/api/run', {'trace': [], 'version': '99'})[0])
        self.assertEqual(400, self.request('/api/run', {'trace': [], 'max_evaluations': 999999})[0])
        self.assertEqual(404, self.request('/witnessforge/core.py')[0])
        self.assertEqual(404, self.request('/api/unknown')[0])


if __name__ == '__main__':
    unittest.main()
