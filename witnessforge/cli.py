"""Command-line entry points; works from source without installation."""

import argparse
import json
from pathlib import Path
import sys

from .cases import run_case, verify_case, write_json
from .core import demo_trace, inspect_trace, validate_repair
from .plugins import external_parser


def _read_trace(filename):
    if filename is None:
        return demo_trace()
    path = Path(filename)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Trace exceeds 1 MiB.')
    trace = json.loads(path.read_text(encoding='utf-8-sig'))
    return trace.get('trace') if isinstance(trace, dict) and 'trace' in trace else trace


def _parser(args):
    if getattr(args, 'parser_file', None):
        return external_parser(args.parser_file, args.timeout)
    return args.parser


def main(argv=None):
    parser = argparse.ArgumentParser(prog='witnessforge', description='Reproduce, minimize, and validate forensic parser failures in a controlled SQLite lab.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('demo', 'minimize', 'inspect'):
        command = commands.add_parser(name, help={'demo': 'Run the bundled failing experiment.', 'minimize': 'Minimize a JSON action trace and save a case.', 'inspect': 'Compare parser output with independent ground truth.'}[name])
        command.add_argument('--trace', help='JSON array of actions; defaults to bundled demonstration.')
        command.add_argument('--version', choices=('1', '2', '3'), default='2')
        command.add_argument('--parser', choices=('buggy', 'fixed'), default='buggy')
        command.add_argument('--parser-file', help='Trusted Python module defining parse(database_path). Executes local code.')
        command.add_argument('--timeout', type=float, default=10, help='Custom parser process timeout in seconds.')
        command.add_argument('--json', action='store_true', help='Print full machine-readable result.')
        if name != 'inspect':
            command.add_argument('--cases-dir', default='cases')
            command.add_argument('--repeats', type=int, default=2)
            command.add_argument('--budget', type=int, default=200)
    validate = commands.add_parser('validate-repair', help='Evaluate a candidate on independent scenarios across three schemas.')
    validate.add_argument('--parser', choices=('buggy', 'fixed'), default='fixed')
    validate.add_argument('--parser-file', help='Trusted parser module; executed in a separate process, not a sandbox.')
    validate.add_argument('--timeout', type=float, default=10)
    validate.add_argument('--output', help='Optional path for JSON results.')
    verify = commands.add_parser('verify', help='Check an extracted case against its unsigned hash manifest.')
    verify.add_argument('directory')
    web = commands.add_parser('serve', help='Open the local HTTP workbench server.')
    web.add_argument('--port', type=int, default=8765)
    web.add_argument('--cases-dir', default='cases')
    export = commands.add_parser('export-demo', help='Write the editable demonstration action trace.')
    export.add_argument('output')
    args = parser.parse_args(argv)
    try:
        if args.command == 'serve':
            from .server import serve
            serve(args.port, args.cases_dir)
            return 0
        if args.command == 'export-demo':
            write_json(args.output, demo_trace())
            print(f'Trace saved: {Path(args.output).resolve()}')
            return 0
        if args.command == 'verify':
            result = verify_case(args.directory)
            print(json.dumps(result, indent=2))
            return 0 if result['status'] == 'PASS' else 1
        selected_parser = _parser(args)
        if args.command == 'validate-repair':
            result = validate_repair(parser=selected_parser)
            if args.output:
                write_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'PASS' else 1
        trace = _read_trace(args.trace)
        if args.command == 'inspect':
            result = inspect_trace(trace, version=args.version, parser=selected_parser)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'PASS' else (2 if result['status'] == 'UNRESOLVED' else 1)
        result = run_case(args.cases_dir, trace, args.version, selected_parser, args.repeats, args.budget, parser_label=str(Path(args.parser_file).resolve()) if args.parser_file else args.parser)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            reduced = result['result']
            print(f"Case: {result['case_id']}")
            print(f"Result: {reduced.get('status', reduced['initial_inspection']['status'])}")
            print(f"Actions: {reduced['original_count']} -> {reduced['minimized_count']}")
            print(f"Dependency-aware 1-minimal: {reduced['one_minimal']}")
            print(f"Report: {(Path(args.cases_dir) / result['case_id'] / 'report.html').resolve()}")
        # A reproduced bug is a successful minimization experiment, unlike a parser validation.
        return 2 if result['result'].get('status') == 'UNRESOLVED' else 0
    except (ValueError, TypeError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
