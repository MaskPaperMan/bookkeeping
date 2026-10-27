"""Create a source-only ZIP using an allowlist; never include customer data."""
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    output = root / 'dist' / 'CustomerLedger-source.zip'
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [root / name for name in ('main.py', 'README.md', 'WINDOWS-VALIDATION.md',
             'requirements-build.txt', 'build_windows.bat', '.gitignore', 'pyproject.toml',
             'uv.lock', '.python-version')]
    for folder in ('ledger', 'scripts', 'tests'):
        paths.extend((root / folder).glob('*.py'))
    with zipfile.ZipFile(str(output), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(str(path), 'CustomerLedger-source/' + path.relative_to(root).as_posix())
    print(output)


if __name__ == '__main__':
    main()
