"""Production settings, API keys and roles, rate limits, host checks, request IDs, metrics, logs."""

import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from witnessforge import ops
from witnessforge.server import create_server

KEYS = {'k-an': {'user': 'asha', 'role': 'analyst'}, 'k-view': {'user': 'vic', 'role': 'viewer'},
        'k-mon': {'user': 'prom', 'role': 'monitor'}}
HASHED = {f'sha256:{ops.hash_key("k-an")}': {'user': 'asha', 'role': 'analyst'}}


class Live:
    """A real server on a free port, with the given settings and case directory."""

    def __init__(self, env, root):
        self.server = create_server(0, root, ops.load_settings(env))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def __call__(self, path, body=None, key=None, headers=None):
        h = {'Content-Type': 'application/json', **({'X-API-Key': key} if key else {}), **(headers or {})}
        req = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urlopen(req, timeout=60) as r:
                return r.status, r.read(), r.headers
        except HTTPError as r:
            return r.code, r.read(), r.headers

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class SettingsTests(unittest.TestCase):
    def test_production_needs_hashed_keys(self):
        base = {'WITNESSFORGE_ENV': 'production', 'WITNESSFORGE_API_KEYS': json.dumps(HASHED)}
        self.assertTrue(ops.load_settings(base).production)
        for change, message in [({'WITNESSFORGE_API_KEYS': ''}, 'API keys'),
                                ({'WITNESSFORGE_API_KEYS': json.dumps(KEYS)}, 'sha256'),
                                ({'WITNESSFORGE_API_KEYS': json.dumps({'k': {'user': 'x', 'role': 'root'}})}, 'role'),
                                ({'WITNESSFORGE_ALLOWED_HOSTS': '*'}, 'ALLOWED_HOSTS'),
                                ({'WITNESSFORGE_ENV': 'prod'}, 'ENV')]:
            with self.assertRaisesRegex(ops.ConfigError, message):
                ops.load_settings({**base, **change})

    def test_no_public_bind_without_keys_and_readable_exit(self):
        with self.assertRaisesRegex(ops.ConfigError, 'without API keys'):
            ops.load_settings({'WITNESSFORGE_HOST': '0.0.0.0'})
        env = {k: v for k, v in os.environ.items() if not k.startswith('WITNESSFORGE_')}
        r = subprocess.run([sys.executable, '-m', 'witnessforge', 'serve'], env={**env, 'WITNESSFORGE_HOST': '0.0.0.0'},
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertNotEqual(0, r.returncode)
        self.assertIn('configuration error', r.stderr)


class KeyedServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.live = Live({'WITNESSFORGE_API_KEYS': json.dumps(KEYS), 'WITNESSFORGE_RATE_LIMIT': '1000'}, self.temp.name)

    def tearDown(self):
        self.live.close()
        self.temp.cleanup()

    def test_roles_decide_what_a_key_may_do(self):
        c = self.live
        self.assertEqual(200, c('/')[0])                          # the page itself is public
        self.assertEqual(200, c('/auth.js')[0])
        self.assertEqual(200, c('/healthz')[0])
        self.assertEqual(401, c('/api/cases')[0])
        self.assertEqual(401, c('/api/cases', key='wrong')[0])
        self.assertEqual(200, c('/api/cases', key='k-view')[0])
        trace = json.loads(c('/api/demo', key='k-view')[1])['trace']
        self.assertEqual(403, c('/api/run', {'trace': trace}, key='k-view')[0])
        status, data, _ = c('/api/run', {'trace': trace, 'version': '2', 'parser': 'buggy'}, key='k-an')
        self.assertEqual(200, status)
        case = json.loads(data)
        self.assertEqual(4, case['result']['minimized_count'])
        self.assertEqual(401, c(case['artifacts']['bundle'])[0])
        self.assertEqual(200, c(case['artifacts']['bundle'], key='k-view')[0])
        self.assertEqual(403, c('/metrics', key='k-an')[0])
        status, data, _ = c('/metrics', key='k-mon')
        self.assertEqual(200, status)
        text = data.decode()
        self.assertIn('witnessforge_decisions_total{decision="experiment_run"} 1', text)
        self.assertIn('route="/api/cases/{id}/bundle"', text)           # case IDs don't become series
        _, data, _ = c('/api/cases', headers={'Authorization': 'Bearer k-view'})
        self.assertEqual(case['case_id'], json.loads(data)['cases'][0]['case_id'])

    def test_cases_survive_a_restart(self):
        trace = json.loads(self.live('/api/demo', key='k-an')[1])['trace']
        case = json.loads(self.live('/api/run', {'trace': trace}, key='k-an')[1])
        self.live.close()
        self.live = Live({'WITNESSFORGE_API_KEYS': json.dumps(KEYS)}, self.temp.name)  # a new process, same volume
        _, data, _ = self.live('/api/cases/' + case['case_id'] + '/verify', key='k-view')
        self.assertEqual('PASS', json.loads(data)['status'])


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_rate_limit_spares_probes(self):
        live = Live({'WITNESSFORGE_RATE_LIMIT': '3'}, self.temp.name)
        try:
            codes = [live('/api/cases')[0] for _ in range(5)]
            self.assertEqual([200, 200, 200, 429, 429], codes)
            self.assertGreaterEqual(int(live('/api/cases')[2]['Retry-After']), 1)
            self.assertEqual(200, live('/healthz')[0])
            self.assertEqual(200, live('/readyz')[0])
        finally:
            live.close()

    def test_host_names_and_request_ids(self):
        live = Live({'WITNESSFORGE_ALLOWED_HOSTS': 'lab.example.org'}, self.temp.name)
        try:
            port = live.server.server_port
            self.assertEqual(403, live('/api/cases', headers={'Host': f'evil.example:{port}'})[0])
            self.assertEqual(200, live('/api/cases', headers={'Host': f'lab.example.org:{port}'})[0])
            origin = {'Origin': 'https://lab.example.org', 'Host': 'lab.example.org'}
            self.assertNotEqual(403, live('/api/validate-repair', {}, headers=origin)[0])
            self.assertEqual(403, live('/api/validate-repair', {}, headers={'Origin': 'https://evil.example'})[0])
            _, _, headers = live('/api/cases', headers={'X-Request-ID': 'abc-123'})
            self.assertEqual('abc-123', headers['X-Request-ID'])
            _, _, headers = live('/api/cases', headers={'X-Request-ID': 'bad id <script>'})
            self.assertNotIn(' ', headers['X-Request-ID'])
        finally:
            live.close()

    def test_access_logs_are_json_without_evidence(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(ops.JsonFormatter())
        logger = logging.getLogger('witnessforge.access')
        logger.addHandler(handler)
        logger.setLevel('INFO')
        live = Live({}, self.temp.name)
        try:
            trace = json.loads(live('/api/demo')[1])['trace']
            live('/api/run', {'trace': trace}, headers={'X-Request-ID': 'rid-7'})
        finally:
            live.close()
            logger.removeHandler(handler)
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        run = next(x for x in lines if x['route'] == '/api/run')
        self.assertEqual(('POST', 200, 'rid-7'), (run['method'], run['status'], run['request_id']))
        self.assertNotIn('Maya', stream.getvalue())  # trace contents never reach the log

    def test_readiness_fails_when_storage_is_not_a_directory(self):
        blocker = Path(self.temp.name) / 'not-a-dir'
        blocker.write_text('x')
        live = Live({}, str(blocker))
        try:
            self.assertEqual(503, live('/readyz')[0])
        finally:
            live.close()


if __name__ == '__main__':
    unittest.main()
