"""Case persistence, hash manifests, and portable offline bundles."""

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import re
import shutil
import tempfile
import uuid
import zipfile

from . import __version__
from .core import inspect_trace, minimize_trace
from .reports import render_report

CASE_ID = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{10}$')


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def case_path(root, case_id):
    if not CASE_ID.fullmatch(case_id):
        raise ValueError('Invalid case identifier.')
    root = Path(root).resolve()
    candidate = root / case_id
    if candidate.is_symlink() or candidate.resolve().parent != root or not candidate.is_dir():
        raise FileNotFoundError('Case not found.')
    return candidate


def _relative_paths(value, case_root):
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == 'database_path' and item:
                try:
                    value[key] = Path(item).resolve().relative_to(case_root.resolve()).as_posix()
                except ValueError:
                    value[key] = None
            else:
                _relative_paths(item, case_root)
    elif isinstance(value, list):
        for item in value:
            _relative_paths(item, case_root)


def run_case(root, trace, version='2', parser='buggy', repeats=2, max_evaluations=200, parser_label=None):
    """Persist only finished cases; source inputs are never overwritten."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    case_id = now.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:10]
    work = Path(tempfile.mkdtemp(prefix='.pending-', dir=root))
    try:
        result = minimize_trace(trace, version=version, parser=parser, repeats=repeats, max_evaluations=max_evaluations)
        # Preserve actual SQLite witnesses separately from the reducer's disposable replays.
        evidence = work / 'evidence'
        witness_checks = []
        for name, selected in [('original', trace), ('minimized', result['minimized_trace'])]:
            directory = evidence / name
            directory.mkdir(parents=True)
            inspection = inspect_trace(selected, version=version, parser=parser, output_dir=directory)
            _relative_paths(inspection, work)
            write_json(directory / 'inspection.json', inspection)
            write_json(directory / 'trace.json', selected)
            reference = result['initial_inspection' if name == 'original' else 'final_inspection']
            matches = all(inspection.get(key) == reference.get(key) for key in ('status', 'signature', 'findings', 'actual'))
            witness_checks.append({'witness': name, 'matches_repeated_oracle': matches})
            # Do not replace repeated-oracle results with a fresh, potentially unstable run.
        result['witness_checks'] = witness_checks
        if not all(check['matches_repeated_oracle'] for check in witness_checks):
            result['status'] = 'UNRESOLVED'
            result['one_minimal'] = False
            result['error'] = 'Preserved witness replay disagreed with the reduction oracle; inspect evidence before drawing conclusions.'
        _relative_paths(result, work)
        label = parser_label or (parser if isinstance(parser, str) else 'custom parser')
        case = {
            'case_id': case_id,
            'created_at': now.isoformat(),
            'tool_version': __version__,
            'scope': 'Synthetic SQLite reference messaging app; not real Android acquisition.',
            'config': {'version': version, 'parser': label, 'parser_source_sha256': getattr(parser, 'source_sha256', None), 'repeats': repeats, 'max_evaluations': max_evaluations},
            'result': result,
            'artifacts': {'report': f'/api/cases/{case_id}/report', 'json': f'/api/cases/{case_id}/json', 'bundle': f'/api/cases/{case_id}/bundle'},
        }
        write_json(work / 'case.json', case)
        write_json(work / 'original-trace.json', trace)
        write_json(work / 'minimized-trace.json', result['minimized_trace'])
        (work / 'report.html').write_text(render_report(case), encoding='utf-8')
        manifest = {'algorithm': 'sha256', 'tool_version': __version__, 'case_id': case_id, 'files': {}}
        for file in sorted(work.rglob('*')):
            if file.is_file():
                manifest['files'][file.relative_to(work).as_posix()] = sha256(file)
        write_json(work / 'manifest.json', manifest)
        work.rename(root / case_id)
        return case
    except BaseException:
        # This exact temporary directory was created above inside our case root.
        if work.exists() and work.resolve().parent == root and work.name.startswith('.pending-'):
            shutil.rmtree(work)
        raise


def list_cases(root):
    root = Path(root)
    result = []
    if not root.exists():
        return result
    for path in sorted(root.iterdir(), reverse=True):
        if not CASE_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            continue
        try:
            case = json.loads((path / 'case.json').read_text(encoding='utf-8'))
            outcome = case['result']
            result.append({'case_id': case['case_id'], 'created_at': case['created_at'],
                           'status': outcome.get('status', outcome['initial_inspection']['status']),
                           **{key: outcome.get(key) for key in ('original_count', 'minimized_count', 'signature')}})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def load_case(root, case_id):
    return json.loads((case_path(root, case_id) / 'case.json').read_text(encoding='utf-8'))


def verify_case(directory):
    directory = Path(directory).resolve()
    errors = []
    try:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('algorithm') != 'sha256' or not isinstance(manifest.get('files'), dict) or not manifest['files']:
            raise ValueError('Invalid or empty manifest.')
        expected = manifest['files']
        if not {'case.json', 'report.html', 'original-trace.json', 'minimized-trace.json'}.issubset(expected):
            raise ValueError('Manifest omits required case files.')
        for name, digest in expected.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
                errors.append(f'Unsafe manifest path: {name}')
                continue
            file = directory.joinpath(*relative.parts)
            if file.is_symlink() or not file.resolve().is_relative_to(directory):
                errors.append(f'Unsafe evidence path: {name}')
            elif not file.is_file():
                errors.append(f'Missing: {name}')
            elif not isinstance(digest, str) or sha256(file) != digest:
                errors.append(f'Hash mismatch: {name}')
        actual = {file.relative_to(directory).as_posix() for file in directory.rglob('*') if file.is_file() and file.name != 'manifest.json'}
        errors.extend(f'Unexpected: {name}' for name in sorted(actual - set(expected)))
        return {'status': 'FAIL' if errors else 'PASS', 'checked_files': len(expected), 'errors': errors,
                'limitation': 'Unsigned hashes detect changes relative to this manifest, not authenticity.'}
    except (OSError, ValueError, TypeError) as exc:
        return {'status': 'FAIL', 'checked_files': 0, 'errors': [str(exc)]}


def bundle_case(root, case_id):
    directory = case_path(root, case_id)
    verification = verify_case(directory)
    if verification['status'] != 'PASS':
        raise ValueError('Case integrity check failed; refusing to export a modified case.')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob('*')):
            if path.is_file() and not path.is_symlink():
                archive.write(path, arcname=case_id + '/' + path.relative_to(directory).as_posix())
    return buffer.getvalue()
