"""A separate process, NOT a security sandbox. Only run trusted plugins."""

import contextlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    filename, database = sys.argv[1:3]
    spec = importlib.util.spec_from_file_location('witnessforge_custom_parser', filename)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load parser module.')
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        # Always execute the current source. Timestamp-based bytecode caches can
        # otherwise reuse a same-size parser edit made within the same second.
        exec(compile(Path(filename).read_bytes(), filename, 'exec'), module.__dict__)  # nosec B102 - runs the examiner's own plugin by design; see the module docstring
        if not callable(getattr(module, 'parse', None)):
            raise ValueError('Parser module must define parse(database_path).')
        records = module.parse(Path(database))
    # ASCII JSON is portable even when Windows redirects stdout using a legacy
    # code page. JSON decoding restores Unicode text and emoji exactly.
    print(json.dumps(records, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2) from None
