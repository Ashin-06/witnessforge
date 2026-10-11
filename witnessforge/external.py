"""Read-only source capture, SQLite/WAL recovery on copies, and Android SMS extraction.

External artifacts have no action ground truth. IMPORTED means structurally readable
and parsed, never a semantic PASS or a claim that an acquisition is complete.
"""

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import time
import zipfile

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000
SMS_REQUIRED = ('_id', 'address', 'date', 'type', 'body')
SMS_OPTIONAL = ('thread_id', 'date_sent', 'read', 'status', 'subject', 'service_center',
                'sub_id', 'creator', 'seen', 'locked', 'error_code', 'protocol')
SMS_TYPES = {0: 'all', 1: 'inbox', 2: 'sent', 3: 'draft', 4: 'outbox', 5: 'failed', 6: 'queued'}
SCOPE = 'External SQLite evidence; AOSP-compatible sms table. No deleted-record carving, MMS, encrypted database support, or semantic ground truth.'


def _json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _stamp(path):
    stat = path.stat()
    if not path.is_file() or path.is_symlink():
        raise ValueError('Evidence sources must be regular files, not symbolic links.')
    if stat.st_size > MAX_FILE_BYTES:
        raise ValueError('Each evidence file must be at most 64 MiB.')
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': _digest(path)}


def _sources(database):
    result = {'': database}
    for suffix in ('-wal', '-shm', '-journal'):
        path = Path(str(database) + suffix)
        if path.exists():
            result[suffix] = path
    return result


def capture_sources(database, directory):
    """Never open originals through SQLite; reject changing content/set during copy."""
    if Path(database).is_symlink():
        raise ValueError('Source database must not be a symbolic link.')
    database, directory = Path(database).resolve(), Path(directory)
    if not database.is_file():
        raise ValueError('Source database does not exist.')
    files = _sources(database)
    before = {suffix: _stamp(path) for suffix, path in files.items()}
    directory.mkdir()
    for suffix, path in files.items():
        with path.open('rb') as source, (directory / ('evidence.sqlite' + suffix)).open('xb') as target:
            copied = 0
            for block in iter(lambda: source.read(1024 * 1024), b''):
                copied += len(block)
                if copied > MAX_FILE_BYTES:
                    raise ValueError('Evidence grew beyond the 64 MiB file limit during capture.')
                target.write(block)
    after_files = _sources(database)
    after = {suffix: _stamp(path) for suffix, path in after_files.items()}
    if before != after or set(files) != set(after_files):
        raise ValueError('Source files changed during capture; stop writers and obtain a consistent export.')
    for suffix in files:
        if _digest(directory / ('evidence.sqlite' + suffix)) != before[suffix]['sha256']:
            raise ValueError('Captured bytes do not match the stable source hashes.')
    return [{'source_path': str(path), 'bundle_path': 'originals/evidence.sqlite' + suffix,
             **before[suffix]} for suffix, path in files.items()]


def _wal_info(path):
    """Validate WAL checksums before SQLite can silently discard damaged frames.

    A stale salt marks a recycled tail and is reported. Other truncation/checksum
    errors are unresolved, even if SQLite could recover a shorter prefix.
    """
    data = Path(path).read_bytes()
    if not data:
        return {'bytes': 0, 'valid_frames': 0, 'commits': 0, 'ignored_recycled_frames': 0}
    if len(data) < 32:
        raise ValueError('WAL header is truncated.')
    magic, version, page_size, _, salt1, salt2, c1, c2 = struct.unpack('>8I', data[:32])
    if magic not in (0x377F0682, 0x377F0683) or version != 3007000:
        raise ValueError('Unsupported WAL header or format version.')
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise ValueError('Invalid WAL page size.')
    endian = '<' if magic == 0x377F0682 else '>'

    def checksum(payload, first=0, second=0):
        words = struct.unpack(endian + str(len(payload) // 4) + 'I', payload)
        for index in range(0, len(words), 2):
            first = (first + words[index] + second) & 0xFFFFFFFF
            second = (second + words[index + 1] + first) & 0xFFFFFFFF
        return first, second

    if checksum(data[:24]) != (c1, c2):
        raise ValueError('WAL header checksum mismatch.')
    frame_size = page_size + 24
    if (len(data) - 32) % frame_size:
        raise ValueError('WAL ends with an incomplete frame; capture is unresolved.')
    frames = (len(data) - 32) // frame_size
    valid = commits = ignored = 0
    for index in range(frames):
        offset = 32 + index * frame_size
        frame = data[offset:offset + frame_size]
        page, db_size, fs1, fs2, fc1, fc2 = struct.unpack('>6I', frame[:24])
        if (fs1, fs2) != (salt1, salt2):
            ignored = frames - index
            break
        if page == 0 or checksum(frame[:8] + frame[24:], c1, c2) != (fc1, fc2):
            raise ValueError('WAL frame checksum/page mismatch; capture is unresolved.')
        c1, c2 = fc1, fc2
        valid += 1
        commits += bool(db_size)
    return {'bytes': len(data), 'page_size': page_size, 'valid_frames': valid,
            'commits': commits, 'ignored_recycled_frames': ignored}


def _utc_milliseconds(value):
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec='milliseconds')
    except (ValueError, OverflowError, OSError):
        return None


def parse_android_sms(database, max_rows=MAX_ROWS):
    """Extract an ordinary AOSP-compatible sms table; preserve unknown/raw values.

    Canonical records identify an address as a counterparty, not an inferred owner.
    date/date_sent stay raw milliseconds plus optional UTC interpretations.
    """
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_ROWS:
        raise ValueError('max_rows must be an integer from 1 through 100000.')
    deadline = time.monotonic() + 15
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024) if hasattr(connection, 'setlimit') else None
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        schema = connection.execute("SELECT type, sql FROM sqlite_master WHERE name='sms'").fetchone()
        if not schema or schema[0] != 'table' or not schema[1] or 'VIRTUAL TABLE' in schema[1].upper():
            raise ValueError('Supported ordinary sms table was not found; encrypted/other app schemas are unsupported.')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(sms)')}
        missing = sorted(set(SMS_REQUIRED) - columns)
        if missing:
            raise ValueError('Unsupported SMS schema: missing columns ' + ', '.join(missing))
        fields = list(SMS_REQUIRED) + [name for name in SMS_OPTIONAL if name in columns]
        rows = connection.execute('SELECT ' + ','.join('"' + field + '"' for field in fields) + ' FROM sms ORDER BY _id LIMIT ?', (max_rows + 1,)).fetchall()
        if len(rows) > max_rows:
            raise ValueError('SMS row limit exceeded; no partial extraction is labeled imported.')
        records = []
        warnings = []
        ids = set()
        for values in rows:
            raw = dict(zip(fields, values))
            if any(isinstance(value, bytes) for value in values):
                raise ValueError('Unexpected BLOB in SMS fields; schema interpretation is unresolved.')
            if not isinstance(raw['_id'], int) or raw['_id'] in ids:
                raise ValueError('SMS _id must be a unique integer.')
            ids.add(raw['_id'])
            message_type = raw['type']
            direction = 'incoming' if message_type == 1 else ('outgoing' if message_type in (2, 3, 4, 5, 6) else 'unknown')
            if message_type not in SMS_TYPES:
                warnings.append(f"SMS {raw['_id']} has unknown type {message_type!r}; no direction inferred.")
            timestamp = _utc_milliseconds(raw['date'])
            if timestamp is None:
                warnings.append(f"SMS {raw['_id']} has an uninterpretable date; raw value retained.")
            records.append({'artifact': 'android.sms', 'source_table': 'sms', 'source_id': raw['_id'],
                            'counterparty': raw['address'], 'body': raw['body'], 'direction': direction,
                            'message_type': SMS_TYPES.get(message_type, 'unknown'), 'date_utc': timestamp,
                            'date_sent_utc': _utc_milliseconds(raw.get('date_sent')) if raw.get('date_sent') else None,
                            'raw': raw})
        return {'parser': 'aosp-sms-v1', 'schema_columns': sorted(columns),
                'unavailable_optional_columns': sorted(set(SMS_OPTIONAL) - columns),
                'record_count': len(records), 'records': records, 'warnings': warnings,
                'type_counts': dict(Counter(record['message_type'] for record in records))}


def import_sqlite(database, output, parser='android-sms', provenance=None):
    """Create a new evidence directory. Failed parsing still preserves raw evidence."""
    if parser not in ('android-sms', 'sqlite-only'):
        raise ValueError('Parser must be android-sms or sqlite-only.')
    if Path(database).is_symlink():
        raise ValueError('Source database must not be a symbolic link.')
    database, output = Path(database).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Output must be a new directory; existing evidence is never overwritten.')
    if output == database.parent or database.is_relative_to(output):
        raise ValueError('Output cannot contain the source database.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.evidence-', dir=output.parent) as pending:
        root = Path(pending)
        sources = capture_sources(database, root / 'originals')
        report = {'format': 'witnessforge.external.v1', 'status': 'UNRESOLVED',
                  'captured_at': datetime.now(timezone.utc).isoformat(), 'scope': SCOPE,
                  'source_files': sources, 'provenance': provenance or {'method': 'external-file-export'},
                  'source_bytes_unchanged_during_capture': True,
                  'ground_truth_available': False, 'acquisition_completeness': 'not established',
                  'warnings': [], 'errors': [], 'sqlite_version': sqlite3.sqlite_version, 'parser': parser}
        try:
            original = root / 'originals' / 'evidence.sqlite'
            with original.open('rb') as stream:
                header = stream.read(100)
            if not header.startswith(b'SQLite format 3\x00') or len(header) < 100:
                raise ValueError('Not an unencrypted SQLite 3 database.')
            journal = Path(str(original) + '-journal')
            if journal.exists() and journal.stat().st_size:
                raise ValueError('Rollback journal present; obtain a clean offline export. Recovery is not attempted.')
            wal = Path(str(original) + '-wal')
            if wal.exists():
                report['wal'] = _wal_info(wal)
                if report['wal'].get('ignored_recycled_frames'):
                    report['warnings'].append('WAL has recycled trailing frames outside its current valid salt sequence.')
            elif header[18] == 2 or header[19] == 2:
                report['warnings'].append('Database is in WAL mode but no WAL was supplied. It may be cleanly checkpointed or incomplete; completeness is not established.')
            (root / 'derived').mkdir()
            with tempfile.TemporaryDirectory(prefix='sqlite-work-') as work:
                work_db = Path(work) / 'evidence.sqlite'
                shutil.copyfile(original, work_db)
                if wal.exists():
                    shutil.copyfile(wal, str(work_db) + '-wal')
                # SHM is a transient index: reconstruct it only beside the disposable copy.
                deadline = time.monotonic() + 15
                with closing(sqlite3.connect(work_db, timeout=2)) as source, closing(sqlite3.connect(root / 'derived' / 'snapshot.sqlite')) as target:
                    source.execute('PRAGMA trusted_schema=OFF')
                    source.execute('PRAGMA query_only=ON')
                    source.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

                    def progress(status, remaining, total):
                        if time.monotonic() > deadline or total > MAX_FILE_BYTES // 512:
                            raise ValueError('SQLite recovery exceeded time/page limits.')

                    source.backup(target, pages=256, progress=progress, sleep=0.01)
                    target.execute('PRAGMA trusted_schema=OFF')
                    target.execute('PRAGMA journal_mode=DELETE')
                    target.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                    integrity = [row[0] for row in target.execute('PRAGMA integrity_check(100)')]
                    report['integrity_check'] = integrity
                    if integrity != ['ok']:
                        raise ValueError('Recovered SQLite integrity check did not pass.')
                    report['tables'] = [row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            if parser == 'android-sms':
                report['extraction'] = parse_android_sms(root / 'derived' / 'snapshot.sqlite')
            report['status'] = 'IMPORTED'
        except (ValueError, sqlite3.Error, OSError) as exc:
            report['errors'].append(str(exc))
        # Reverify preserved originals after recovery/parsing, not just the source copy.
        if any(_digest(root / item['bundle_path']) != item['sha256'] for item in sources):
            raise ValueError('Preserved original evidence changed during processing.')
        _json(root / 'evidence.json', report)
        files = {path.relative_to(root).as_posix(): _digest(path) for path in root.rglob('*') if path.is_file()}
        _json(root / 'manifest.json', {'format': 'witnessforge.external.manifest.v1', 'algorithm': 'sha256', 'files': files,
                                     'notice': 'Unsigned integrity inventory; does not prove origin, completeness, or custody.'})
        # Rename to publish only a complete bundle; no overwrite.
        root.rename(output)
    return {**report, 'directory': str(output)}


def verify_evidence(directory):
    root = Path(directory).resolve()
    errors = []
    checked = 0
    try:
        manifest_path = root / 'manifest.json'
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 1024 * 1024:
            raise ValueError('Invalid manifest file.')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        files = manifest.get('files')
        if manifest.get('format') != 'witnessforge.external.manifest.v1' or manifest.get('algorithm') != 'sha256' or not isinstance(files, dict):
            raise ValueError('Unsupported evidence manifest.')
        if not {'evidence.json', 'originals/evidence.sqlite'} <= set(files):
            raise ValueError('Manifest omits mandatory evidence files.')
        for name, expected in files.items():
            relative = Path(name)
            if not isinstance(name, str) or relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe manifest path.')
            path = root / relative
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                errors.append('Missing or unsafe file: ' + name)
            elif _digest(path) != expected:
                errors.append('Hash mismatch: ' + name)
            checked += 1
        actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file() and path != manifest_path}
        if actual != set(files):
            errors.append('Bundle contains unlisted or missing files.')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append(str(exc))
    return {'status': 'PASS' if not errors else 'FAIL', 'checked_files': checked, 'errors': errors,
            'meaning': 'Byte integrity against an unsigned manifest only; not parser correctness or authentic provenance.'}


def bundle_evidence(directory, output):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    result = verify_evidence(directory)
    if result['status'] != 'PASS':
        raise ValueError('Cannot bundle evidence with failed integrity verification.')
    if output.exists() or output.is_relative_to(directory):
        raise ValueError('ZIP output must be new and outside the evidence directory.')
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return {'status': 'BUNDLED', 'path': str(output), 'sha256': _digest(output)}
