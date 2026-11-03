"""Verify a built wheel in a fresh environment, without importing the checkout."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

MODULE = 'witnessforge'

PROBE = r'''
import importlib
import json
from pathlib import Path
import sys
import threading
from urllib.request import build_opener, ProxyHandler

name = sys.argv[1]
package = importlib.import_module(name)
package_path = Path(package.__file__).resolve()
assert package_path.is_relative_to(Path(sys.prefix).resolve()), package_path
factory = importlib.import_module(name + '.server').create_server
server = factory(0, 'probe-output')
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
opener = build_opener(ProxyHandler({}))
try:
    base = f'http://127.0.0.1:{server.server_port}'
    with opener.open(base + '/api/health', timeout=5) as response:
        assert json.load(response)['status'] == 'ok'
    assets = ['/', '/styles.css', '/app.js', '/auth.js']
    if name == 'consentdiff':
        assets += ['/driver.html', '/driver.js']
        assert package_path.with_name('browser_runner.cjs').stat().st_size > 0
    for asset in assets:
        with opener.open(base + asset, timeout=5) as response:
            assert response.status == 200 and len(response.read()) > 0, asset
    print(json.dumps({'status': 'PASS', 'module': name, 'installed_assets_checked': len(assets), 'health': 'ok'}))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
'''


def checked(command, cwd, timeout=120):
    result = subprocess.run([str(value) for value in command], cwd=cwd,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Install check failed ({result.returncode}): {result.stdout}\n{result.stderr}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', default='dist')
    args = parser.parse_args()
    distribution = Path(args.dist).resolve()
    wheels, sources = list(distribution.glob('*.whl')), list(distribution.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error('Use a clean build directory containing exactly one wheel and one source archive.')
    required = {MODULE + '/__init__.py', MODULE + '/cli.py', MODULE + '/static/index.html',
                MODULE + '/static/styles.css', MODULE + '/static/app.js'}
    if MODULE == 'consentdiff':
        required |= {MODULE + '/browser_runner.cjs', MODULE + '/static/driver.html', MODULE + '/static/driver.js'}
    with zipfile.ZipFile(wheels[0]) as archive:
        if archive.testzip() or not required.issubset(archive.namelist()):
            raise ValueError('Wheel is damaged or omits required runtime assets.')
    with tarfile.open(sources[0], 'r:gz') as archive:
        names = {name.split('/', 1)[1] for name in archive.getnames() if '/' in name}
        if not required.issubset(names):
            raise ValueError('Source distribution omits required runtime assets.')
    with tempfile.TemporaryDirectory(prefix=MODULE + '-install-') as temporary:
        root = Path(temporary)
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        binary = environment / ('Scripts' if sys.platform == 'win32' else 'bin')
        python = binary / ('python.exe' if sys.platform == 'win32' else 'python')
        checked([python, '-I', '-m', 'pip', 'install', '--no-index', '--no-deps', wheels[0]], root)
        checked([python, '-I', '-m', MODULE, '--help'], root)
        checked([binary / (MODULE + ('.exe' if sys.platform == 'win32' else '')), '--help'], root)
        commands = {'witnessforge': ['validate-repair'], 'restoreguard': ['scenarios'],
                    'consentdiff': ['suite', '--mode', 'repaired']}
        outcome = json.loads(checked([python, '-I', '-m', MODULE, *commands[MODULE]], root))
        if outcome['status'] != 'PASS':
            raise ValueError('Installed application did not match its expected reference behavior.')
        probe = root / 'probe.py'
        probe.write_text(PROBE, encoding='utf-8')
        result = json.loads(checked([python, '-I', probe, MODULE], root))
        result.update(wheel=wheels[0].name, source_archive=sources[0].name,
                      isolated_install=True, module_cli=True, console_entrypoint=True, reference_scenario='PASS')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
