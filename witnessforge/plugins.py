"""CLI-only execution of explicitly selected, trusted parser code."""

from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile


def external_parser(filename, timeout=10):
    filename = Path(filename).resolve()
    if not filename.is_file() or filename.suffix.lower() != '.py':
        raise ValueError('Parser file must be an existing Python .py file.')
    if not 0 < timeout <= 60:
        raise ValueError('Parser timeout must be greater than zero and at most 60 seconds.')
    source_digest = hashlib.sha256(filename.read_bytes()).hexdigest()

    def parse(database_path):
        if hashlib.sha256(filename.read_bytes()).hexdigest() != source_digest:
            raise ValueError('Custom parser source changed during this experiment; start a new run.')
        database_path = Path(database_path).resolve()
        before = hashlib.sha256(database_path.read_bytes()).hexdigest()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                outcome = subprocess.run(
                    [sys.executable, '-m', 'witnessforge.plugin_worker', str(filename), str(database_path)],
                    cwd=str(Path(__file__).resolve().parent.parent),
                    stdout=stdout, stderr=stderr, timeout=timeout,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f'Custom parser exceeded {timeout:g} seconds.') from exc
            if hashlib.sha256(database_path.read_bytes()).hexdigest() != before:
                raise ValueError('Custom parser modified its evidence database.')
            if stdout.tell() > 2 * 1024 * 1024:
                raise ValueError('Custom parser output exceeded 2 MiB.')
            stdout.seek(0)
            stderr.seek(0)
            if outcome.returncode:
                message = stderr.read(2000).decode('utf-8', errors='replace')
                raise ValueError('Custom parser failed: ' + message)
            try:
                return json.loads(stdout.read().decode('utf-8'))
            except (ValueError, UnicodeError) as exc:
                raise ValueError('Custom parser did not return valid JSON records.') from exc

    parse.source_sha256 = source_digest
    return parse
