"""Read-only source capture, SQLite/WAL recovery on copies, and Android SMS extraction.

External artifacts have no action ground truth. IMPORTED means structurally readable
and parsed, never a semantic PASS or a claim that an acquisition is complete.
"""

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import time
import zipfile

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000
MAX_EXTRACTION_BYTES = 32 * 1024 * 1024
MAX_CELL_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 8 * MAX_FILE_BYTES
EVIDENCE_FILES = frozenset({'evidence.json', 'derived/snapshot.sqlite', 'originals/evidence.sqlite',
                            'originals/evidence.sqlite-wal', 'originals/evidence.sqlite-shm',
                            'originals/evidence.sqlite-journal'})
SMS_REQUIRED = ('_id', 'address', 'date', 'type', 'body')
SMS_OPTIONAL = ('thread_id', 'date_sent', 'read', 'status', 'subject', 'service_center',
                'sub_id', 'creator', 'seen', 'locked', 'error_code', 'protocol')
SMS_TYPES = {0: 'all', 1: 'inbox', 2: 'sent', 3: 'draft', 4: 'outbox', 5: 'failed', 6: 'queued'}
SCOPE = 'External SQLite evidence; AOSP-compatible sms table. No deleted-record carving, MMS, encrypted database support, or semantic ground truth.'


def _json(path, value):
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('Evidence JSON exceeds the 64 MiB output limit.')
    Path(path).write_bytes(data)


def _read_bytes(path, limit):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Evidence must be a regular file, not a link or special file.')
    with path.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError('Evidence file exceeds its size limit.')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Evidence file grew beyond its size limit.')
    return data


def _digest(path, max_bytes=None):
    limit = MAX_FILE_BYTES if max_bytes is None else max_bytes
    if not stat.S_ISREG(Path(path).lstat().st_mode):
        raise ValueError('Only regular evidence files may be hashed.')
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Only regular evidence files may be hashed.')
        consumed = 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            consumed += len(block)
            if consumed > limit:
                raise ValueError('Evidence file exceeds its hash size limit.')
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
        if path.exists() or path.is_symlink():
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
    data = _read_bytes(path, MAX_FILE_BYTES)
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
            if not valid:
                raise ValueError('WAL first frame salt does not match its header; capture is unresolved.')
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
    if Path(database).stat().st_size > MAX_FILE_BYTES:
        raise ValueError('Database exceeds the 64 MiB parsing limit.')
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL_BYTES) if hasattr(connection, 'setlimit') else None
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        schema = connection.execute("SELECT type, sql FROM sqlite_master WHERE name='sms'").fetchone()
        if not schema or schema[0] != 'table' or not schema[1] or 'VIRTUAL TABLE' in schema[1].upper():
            raise ValueError('Supported ordinary sms table was not found; encrypted/other app schemas are unsupported.')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(sms)')}
        if len(columns) > 256 or any(len(name) > 256 for name in columns):
            raise ValueError('SMS schema exceeds the supported column limits.')
        missing = sorted(set(SMS_REQUIRED) - columns)
        if missing:
            raise ValueError('Unsupported SMS schema: missing columns ' + ', '.join(missing))
        fields = list(SMS_REQUIRED) + [name for name in SMS_OPTIONAL if name in columns]
        # SQL length checks also work on Python 3.10, which has no setlimit API.
        excessive = ' OR '.join('length(CAST("' + field + '" AS BLOB)) > ?' for field in fields)
        if connection.execute('SELECT 1 FROM sms WHERE ' + excessive + ' LIMIT 1', (MAX_CELL_BYTES,) * len(fields)).fetchone():  # nosec B608 - quoted allow-listed columns
            raise ValueError('SMS cell exceeds the 8 MiB value limit.')
        rows = connection.execute('SELECT ' + ','.join('"' + field + '"' for field in fields) + ' FROM sms ORDER BY _id LIMIT ?', (max_rows + 1,))  # nosec B608 - quoted allow-listed columns
        records = []
        warnings = []
        warnings_suppressed = 0
        extracted_bytes = 0
        ids = set()
        for values in rows:
            if len(records) >= max_rows:
                raise ValueError('SMS row limit exceeded; no partial extraction is labeled imported.')
            if time.monotonic() > deadline:
                raise ValueError('SMS extraction exceeded the query/conversion time budget.')
            raw = dict(zip(fields, values))
            if any(isinstance(value, bytes) for value in values):
                raise ValueError('Unexpected BLOB in SMS fields; schema interpretation is unresolved.')
            if any(isinstance(value, float) and not math.isfinite(value) for value in values):
                raise ValueError('Non-finite numeric SMS value cannot be represented in strict JSON.')
            if not isinstance(raw['_id'], int) or raw['_id'] in ids:
                raise ValueError('SMS _id must be a unique integer.')
            ids.add(raw['_id'])
            message_type = raw['type']
            direction = 'incoming' if message_type == 1 else ('outgoing' if message_type in (2, 3, 4, 5, 6) else 'unknown')
            if message_type not in SMS_TYPES:
                warnings.append(f"SMS {raw['_id']} has unknown type {str(message_type)[:160]!r}; no direction inferred.")
            timestamp = _utc_milliseconds(raw['date'])
            if timestamp is None:
                warnings.append(f"SMS {raw['_id']} has an uninterpretable date; raw value retained.")
            record = {'artifact': 'android.sms', 'source_table': 'sms', 'source_id': raw['_id'],
                            'counterparty': raw['address'], 'body': raw['body'], 'direction': direction,
                            'message_type': SMS_TYPES.get(message_type, 'unknown'), 'date_utc': timestamp,
                            'date_sent_utc': _utc_milliseconds(raw.get('date_sent')) if raw.get('date_sent') else None,
                            'raw': raw}
            extracted_bytes += len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            if extracted_bytes > MAX_EXTRACTION_BYTES:
                raise ValueError('SMS extraction exceeds the 32 MiB serialized record limit.')
            records.append(record)
            if len(warnings) > 1000:
                warnings_suppressed += len(warnings) - 1000
                del warnings[1000:]
        return {'parser': 'aosp-sms-v1', 'schema_columns': sorted(columns),
                'unavailable_optional_columns': sorted(set(SMS_OPTIONAL) - columns),
                'record_count': len(records), 'records': records, 'warnings': warnings,
                'warnings_suppressed': warnings_suppressed,
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
                page_size = int.from_bytes(header[16:18], 'big')
                page_size = 65536 if page_size == 1 else page_size
                if report['wal'].get('page_size', page_size) != page_size:
                    raise ValueError('Database and WAL page sizes differ; capture is unresolved.')
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
                    page_size = source.execute('PRAGMA page_size').fetchone()[0]
                    page_count = source.execute('PRAGMA page_count').fetchone()[0]
                    if page_count * page_size > MAX_FILE_BYTES:
                        raise ValueError('Recovered database exceeds the 64 MiB output limit.')

                    def progress(status, remaining, total):
                        if time.monotonic() > deadline or total * page_size > MAX_FILE_BYTES:
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


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key in evidence manifest: ' + str(key))
        result[key] = value
    return result


def _manifest(directory):
    raw = _read_bytes(directory / 'manifest.json', 1024 * 1024)
    manifest = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict):
        raise ValueError('Unsupported evidence manifest.')
    files = manifest.get('files')
    if manifest.get('format') != 'witnessforge.external.manifest.v1' or manifest.get('algorithm') != 'sha256' or not isinstance(files, dict):
        raise ValueError('Unsupported evidence manifest.')
    if not {'evidence.json', 'originals/evidence.sqlite'} <= set(files):
        raise ValueError('Manifest omits mandatory evidence files.')
    if not set(files) <= EVIDENCE_FILES:
        raise ValueError('Unsafe or unsupported manifest path.')
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in files.values()):
        raise ValueError('Manifest hashes must be lowercase SHA-256 hex strings.')
    return raw, files


def _inventory(root):
    """Bound directory traversal to the fixed external-evidence layout."""
    actual = set()
    for entry in root.iterdir():
        mode = entry.lstat().st_mode
        if stat.S_ISDIR(mode) and entry.name in ('originals', 'derived'):
            for child in entry.iterdir():
                name = child.relative_to(root).as_posix()
                if name not in EVIDENCE_FILES or not stat.S_ISREG(child.lstat().st_mode):
                    raise ValueError('Unlisted or unsafe bundle entry: ' + name)
                actual.add(name)
        elif stat.S_ISREG(mode) and entry.name in ('manifest.json', 'evidence.json'):
            if entry.name != 'manifest.json':
                actual.add(entry.name)
        else:
            raise ValueError('Unlisted or unsafe bundle entry: ' + entry.name)
    return actual


def verify_evidence(directory):
    root = Path(directory).resolve()
    errors = []
    checked = 0
    try:
        _, files = _manifest(root)
        if _inventory(root) != set(files):
            raise ValueError('Bundle contains unlisted or missing files.')
        for name, expected in files.items():
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                errors.append('Missing or unsafe file: ' + name)
            elif _digest(path) != expected:
                errors.append('Hash mismatch: ' + name)
            checked += 1
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append(str(exc))
    return {'status': 'PASS' if not errors else 'FAIL', 'checked_files': checked, 'errors': errors,
            'meaning': 'Byte integrity against an unsigned manifest only; not parser correctness or authentic provenance.'}


def bundle_evidence(directory, output):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(directory):
        raise ValueError('ZIP output must be new and outside the evidence directory.')
    manifest_bytes, files = _manifest(directory)
    if _inventory(directory) != set(files):
        raise ValueError('Cannot bundle evidence with unlisted or missing files.')
    # Pin exactly the bytes that are hashed and added to ZIP. A source mutation
    # between a preliminary verify and a later read must never yield a bad bundle.
    # Publish by exclusive creation only after every member has passed its hash.
    with tempfile.TemporaryFile() as pending:
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, expected in sorted(files.items()):
                path = directory / name
                if not path.resolve().is_relative_to(directory):
                    raise ValueError('Unsafe evidence path during bundling.')
                content = _read_bytes(path, MAX_FILE_BYTES)
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError('Evidence changed or failed integrity verification: ' + name)
                archive.writestr(name, content)
            archive.writestr('manifest.json', manifest_bytes)
        if _inventory(directory) != set(files) or _read_bytes(directory / 'manifest.json', 1024 * 1024) != manifest_bytes:
            raise ValueError('Bundle inventory or manifest changed during export.')
        if pending.tell() > MAX_BUNDLE_BYTES:
            raise ValueError('ZIP exceeds the bounded evidence size.')
        pending.seek(0)
        try:
            with output.open('xb') as target:
                shutil.copyfileobj(pending, target)
        except FileExistsError:
            raise ValueError('ZIP output already exists; it was not overwritten.') from None
    return {'status': 'BUNDLED', 'path': str(output), 'sha256': _digest(output, MAX_BUNDLE_BYTES)}
