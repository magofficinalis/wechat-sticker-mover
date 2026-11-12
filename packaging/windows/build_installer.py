"""Build an NSIS installer from a complete PyInstaller onedir payload."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from xhs_importer import __version__
from xhs_importer.help_view import guide_html


def escape(path):
    return str(path).replace('/', '\\').replace('$', '$$').replace('"', '$\\"')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--makensis', type=Path, required=True)
    parser.add_argument('--nsis-license', type=Path, required=True)
    parser.add_argument('--app-dir', type=Path, default=ROOT / 'dist/StickerImporter')
    parser.add_argument('--output', type=Path, default=ROOT / f'release/StickerImporter-{__version__}-Setup.exe')
    args = parser.parse_args()
    payload, output = args.app_dir.resolve(), args.output.resolve()
    for required in (payload / 'StickerImporter.exe', payload / 'LICENSE', payload / 'NOTICE', args.makensis, args.nsis_license):
        if not required.is_file():
            parser.error(f'Missing required build input: {required}')
    if output.exists():
        parser.error(f'Refusing to overwrite existing installer: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / 'build/installer'
    scratch.mkdir(parents=True, exist_ok=True)
    guide = scratch / 'quick-start.html'
    guide.write_text(guide_html((ROOT / 'docs/QUICKSTART.md').read_text(encoding='utf-8'), '表情搬运助手｜新手教程'), encoding='utf-8')
    files = sorted(p for p in payload.rglob('*') if p.is_file())
    directories = sorted((p for p in payload.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    lines = [f'  Delete "$INSTDIR\\{escape(p.relative_to(payload))}"' for p in files]
    lines += [f'  RMDir "$INSTDIR\\{escape(p.relative_to(payload))}"' for p in directories]
    manifest = scratch / 'uninstall-files.nsh'
    manifest.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    subprocess.run([str(args.makensis.resolve()), f'-DAPP_PAYLOAD={payload}', f'-DQUICK_GUIDE={guide}',
        f'-DNSIS_LICENSE={args.nsis_license.resolve()}', f'-DUNINSTALL_INCLUDE={manifest}',
        f'-DOUTPUT_FILE={output}', str(ROOT / 'packaging/windows/installer.nsi')], check=True)
    print(output)


if __name__ == '__main__':
    main()
