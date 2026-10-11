"""Build the owned fixture APK with an installed Android SDK/JDK, without Gradle/network."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent


def build(sdk, jdk, output):
    sdk, jdk, output = Path(sdk).resolve(), Path(jdk).resolve(), Path(output).resolve()
    build_tools = sorted((sdk / 'build-tools').glob('*'), key=lambda p: tuple(int(x) for x in p.name.split('.') if x.isdigit()))
    tool = next((path for path in reversed(build_tools) if (path / 'lib' / 'd8.jar').is_file()), None)
    android = sdk / 'platforms' / 'android-34' / 'android.jar'
    if not tool or not android.is_file():
        raise ValueError('Installed SDK build-tools and platforms/android-34/android.jar are required.')
    suffix = '.exe' if os.name == 'nt' else ''
    java, javac, keytool = [jdk / 'bin' / (name + suffix) for name in ('java', 'javac', 'keytool')]
    if not all(path.is_file() for path in (java, javac, keytool)):
        raise ValueError('JDK with java, javac and keytool is required.')
    output.mkdir(parents=True, exist_ok=True)
    for name in ('classes', 'dex'):
        (output / name).mkdir(exist_ok=True)

    def run(arguments):
        result = subprocess.run([str(value) for value in arguments], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError((result.stdout + result.stderr).strip())

    run([tool / ('aapt2' + suffix), 'link', '-o', output / 'resources.apk', '--manifest', ROOT / 'AndroidManifest.xml', '-I', android])
    run([javac, '--release', '8', '-encoding', 'UTF-8', '-classpath', android, '-d', output / 'classes', ROOT / 'src/org/witnessforge/fixture/MainActivity.java'])
    run([java, '-cp', tool / 'lib/d8.jar', 'com.android.tools.r8.D8', '--lib', android, '--min-api', '26', '--output', output / 'dex', *sorted((output / 'classes').rglob('*.class'))])
    shutil.copyfile(output / 'resources.apk', output / 'unsigned.apk')
    with zipfile.ZipFile(output / 'unsigned.apk', 'a') as archive:
        archive.write(output / 'dex/classes.dex', 'classes.dex')
    run([tool / ('zipalign' + suffix), '-f', '4', output / 'unsigned.apk', output / 'aligned.apk'])
    if not (output / 'debug.p12').exists():
        run([keytool, '-genkeypair', '-keystore', output / 'debug.p12', '-storepass', 'android', '-keypass', 'android', '-alias', 'fixture', '-dname', 'CN=WitnessForge Owned Fixture', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '3650'])
    apk = output / 'witnessforge-fixture.apk'
    run([java, '-jar', tool / 'lib/apksigner.jar', 'sign', '--ks', output / 'debug.p12', '--ks-pass', 'pass:android', '--key-pass', 'pass:android', '--out', apk, output / 'aligned.apk'])
    run([java, '-jar', tool / 'lib/apksigner.jar', 'verify', apk])
    report = {'apk': str(apk), 'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'sdk_build_tools': tool.name,
              'source_sha256': {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / 'AndroidManifest.xml', ROOT / 'src/org/witnessforge/fixture/MainActivity.java', ROOT / 'expected.json')}}
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', default=os.environ.get('ANDROID_HOME') or str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Android/Sdk'))
    parser.add_argument('--jdk', default=os.environ.get('JAVA_HOME') or 'C:/Program Files/Android/Android Studio/jbr')
    parser.add_argument('--output', default=str(ROOT.parent / 'build/android-fixture'))
    args = parser.parse_args()
    print(json.dumps(build(args.sdk, args.jdk, args.output), indent=2))
