"""Run using official Python 3.8.10 x86 on Windows; no third-party runtime deps."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main():
    if os.name != 'nt' or sys.version_info[:3] != (3, 8, 10) or struct.calcsize('P') != 4:
        raise SystemExit('Build requires Windows and official Python 3.8.10 (32-bit).')
    root = Path(__file__).resolve().parent.parent
    os.chdir(str(root))
    uv = shutil.which('uv')
    if not uv:
        raise SystemExit('uv is required on the build host (Windows 10 or newer).')
    env = root / '.build-venv'
    subprocess.check_call([uv, 'venv', '--allow-existing', '--no-managed-python',
                           '--no-python-downloads', '--python', sys.executable, str(env)])
    python = str(env / 'Scripts' / 'python.exe')
    # Seed pinned build tools before building legacy source distributions.
    subprocess.check_call([uv, 'pip', 'install', '--python', python,
                           'setuptools==59.8.0', 'wheel==0.37.1'])
    subprocess.check_call([uv, 'pip', 'sync', '--python', python, '--no-build-isolation',
                           'requirements-build.txt'])
    subprocess.check_call([python, '-m', 'unittest', 'discover', '-v'])
    subprocess.check_call([python, '-m', 'PyInstaller', '--noconfirm', '--clean',
                           '--onedir', '--windowed', '--name', 'CustomerLedger', 'main.py'])
    app = root / 'dist' / 'CustomerLedger'
    for name in ('README.md', 'WINDOWS-VALIDATION.md'):
        shutil.copyfile(str(root / name), str(app / name))
    # Test the frozen executable itself, not only the source interpreter.
    with tempfile.TemporaryDirectory() as folder:
        result = Path(folder) / 'smoke-result.txt'
        child_env = dict(os.environ)
        child_env['LOCALAPPDATA'] = str(Path(folder) / 'appdata')
        subprocess.run([str(app / 'CustomerLedger.exe'), '--smoke-test', str(result)],
                       env=child_env, check=True, timeout=30)
        if not result.exists() or result.read_text(encoding='ascii') != 'ok':
            raise SystemExit('Frozen EXE failed to render application widgets.')
    (app / 'BUILD-INFO.txt').write_text(
        'Python: ' + sys.version + '\nArchitecture: x86\nPyInstaller: 4.8\nCommit: ' +
        os.environ.get('GITHUB_SHA', 'local build') +
        '\nSource tests and frozen EXE startup smoke test passed.\n'
        'Windows 7 SP1 x86/x64 runtime acceptance is still required.\n', encoding='utf-8')
    output = root / 'dist' / 'CustomerLedger-win7-x86.zip'
    with zipfile.ZipFile(str(output), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                archive.write(str(path), str(path.relative_to(app.parent)))
    print('Created: ' + str(output))
    print('Run WINDOWS-VALIDATION.md checks before delivering this build.')


if __name__ == '__main__':
    main()
