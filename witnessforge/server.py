"""Loopback-only, dependency-free workbench API and static file server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
from urllib.parse import urlsplit

from .cases import bundle_case, case_path, list_cases, load_case, run_case, verify_case
from .core import demo_trace, validate_repair, validate_trace

MAX_BODY = 1024 * 1024


def create_server(port=8765, cases_dir='cases'):
    root = Path(cases_dir).resolve()
    static = Path(__file__).with_name('static')
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'WitnessForge/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def _allowed_host(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return self.headers.get('Host', '') in allowed

        def _send(self, status, body, content_type='application/json; charset=utf-8', filename=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            elif isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._allowed_host():
                return self._send(403, {'error': 'Only localhost requests are accepted.'})
            path = urlsplit(self.path).path
            try:
                if path in ('/', '/index.html', '/styles.css', '/app.js'):
                    name = 'index.html' if path == '/' else path[1:]
                    mime = {'index.html': 'text/html; charset=utf-8', 'styles.css': 'text/css; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8'}[name]
                    return self._send(200, (static / name).read_bytes(), mime)
                if path == '/api/health':
                    return self._send(200, {'status': 'ok', 'scope': 'synthetic SQLite reference app'})
                if path == '/api/demo':
                    return self._send(200, {'trace': demo_trace(), 'versions': ['1', '2', '3'], 'parsers': ['buggy', 'fixed']})
                if path == '/api/cases':
                    return self._send(200, {'cases': list_cases(root)})
                pieces = path.strip('/').split('/')
                if len(pieces) == 4 and pieces[:2] == ['api', 'cases']:
                    case_id, kind = pieces[2:]
                    directory = case_path(root, case_id)
                    if kind == 'json':
                        return self._send(200, load_case(root, case_id), filename=case_id + '.json')
                    if kind == 'report':
                        return self._send(200, (directory / 'report.html').read_bytes(), 'text/html; charset=utf-8', case_id + '.html')
                    if kind == 'bundle':
                        return self._send(200, bundle_case(root, case_id), 'application/zip', case_id + '.zip')
                    if kind == 'verify':
                        return self._send(200, verify_case(directory))
                return self._send(404, {'error': 'Not found.'})
            except FileNotFoundError:
                return self._send(404, {'error': 'File or case not found.'})
            except ValueError as exc:
                return self._send(400, {'error': str(exc)})
            except OSError:
                return self._send(500, {'error': 'Could not read local case files.'})

        def do_POST(self):
            if not self._allowed_host():
                return self._send(403, {'error': 'Only localhost requests are accepted.'})
            origin = self.headers.get('Origin')
            expected_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
            if origin and origin not in expected_origins:
                return self._send(403, {'error': 'Cross-origin requests are not accepted.'})
            if self.headers.get_content_type() != 'application/json':
                return self._send(415, {'error': 'Use application/json.'})
            if self.headers.get('Transfer-Encoding'):
                return self._send(400, {'error': 'Chunked requests are not supported.'})
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if length < 0 or length > MAX_BODY:
                    return self._send(413, {'error': 'Request must be at most 1 MiB and include Content-Length.'})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('JSON body must be an object.')
                path = urlsplit(self.path).path
                if path not in ('/api/run', '/api/validate-repair'):
                    return self._send(404, {'error': 'Not found.'})
                if not lock.acquire(blocking=False):
                    return self._send(409, {'error': 'Another experiment is running. Wait for it to finish.'})
                try:
                    if path == '/api/validate-repair':
                        parser = data.get('parser', 'fixed')
                        if parser not in ('buggy', 'fixed'):
                            raise ValueError('Parser must be buggy or fixed.')
                        return self._send(200, validate_repair(parser=parser))
                    version = data.get('version', '2')
                    parser = data.get('parser', 'buggy')
                    repeats = data.get('repeats', 2)
                    budget = data.get('max_evaluations', 200)
                    if version not in ('1', '2', '3') or parser not in ('buggy', 'fixed'):
                        raise ValueError('Choose version 1, 2, or 3 and parser buggy or fixed.')
                    if type(repeats) is not int or not 1 <= repeats <= 5:
                        raise ValueError('Repeats must be an integer from 1 to 5.')
                    if type(budget) is not int or not 2 <= budget <= 1000:
                        raise ValueError('Evaluation budget must be an integer from 2 to 1000.')
                    trace = data.get('trace')
                    validate_trace(trace)
                    return self._send(200, run_case(root, trace, version, parser, repeats, budget))
                finally:
                    lock.release()
            except (ValueError, UnicodeError, TypeError) as exc:
                return self._send(400, {'error': str(exc)})
            except OSError:
                return self._send(500, {'error': 'The local experiment could not complete. Check storage permissions and server logs.'})

        def log_message(self, fmt, *args):
            # Do not log submitted evidence text or parser output.
            super().log_message(fmt, *args)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(port=8765, cases_dir='cases'):
    server = create_server(port, cases_dir)
    print(f'WitnessForge is ready at http://127.0.0.1:{server.server_port}', flush=True)
    print('Local synthetic-data lab. Press Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
