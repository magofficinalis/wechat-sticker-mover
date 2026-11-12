"""Retain pip and native runtime notices; refuse unaudited native libraries.

The checked-in runtime notices are version-specific upstream documents. A
Conda package must own the actual bundled DLL bytes before those notices are
used. A different Python installation therefore cannot silently inherit this
build's dependency versions. No local user paths are written to the manifest.
"""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys


NOTICE_PREFIXES = ('license', 'licence', 'copying', 'notice')
RUNTIME_NOTICES = Path(__file__).resolve().parent / 'runtime-licenses'


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_tree(source, destination):
    files = []
    for path in sorted(source.rglob('*')):
        if path.is_file():
            relative = path.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            files.append(relative.as_posix())
    return files


def _microsoft_runtime(name):
    name = name.lower()
    return name.startswith(('api-ms-win-', 'vcruntime', 'msvcp', 'concrt')) or name == 'ucrtbase.dll'


def _collect_runtime(destination):
    base = Path(sys.base_prefix)
    payload = destination.parent / '_internal'
    binaries = {p.name.lower(): p for p in payload.glob('*.dll')}
    if not binaries:
        raise RuntimeError('No packaged native DLLs found beside dependency-licenses; build the application first.')
    hashes = {name: _digest(path) for name, path in binaries.items()}
    manifest = []
    covered = set()
    sources_path = RUNTIME_NOTICES / 'sources.json'
    sources = json.loads(sources_path.read_text(encoding='utf-8'))

    # Check vendored texts before copying them, so provenance is verifiable.
    for entry in sources:
        source = RUNTIME_NOTICES / entry['file']
        if not source.is_file() or _digest(source) != entry['sha256']:
            raise RuntimeError(f"Runtime license checksum mismatch: {entry['file']}")

    for record_path in sorted((base / 'conda-meta').glob('*.json')):
        record = json.loads(record_path.read_text(encoding='utf-8'))
        owned = set()
        for relative in record.get('files', []):
            name = Path(relative).name.lower()
            if name not in binaries or _microsoft_runtime(name):
                continue
            original = base / relative
            if original.is_file() and _digest(original) == hashes[name]:
                owned.add(name)
        if not owned:
            continue
        component = f"{record['name']}-{record['version']}"
        notices = RUNTIME_NOTICES / component
        if not notices.is_dir():
            raise RuntimeError(f'No verified runtime notices for {component}; add its exact-version upstream notices before release.')
        files = _copy_tree(notices, destination / 'native-runtime' / component)
        manifest.append({'component': component, 'conda_build': record.get('build'),
                         'binaries': sorted(owned), 'license_files': files,
                         'evidence': 'DLL SHA-256 matches files owned by this installed Conda package'})
        covered.update(owned)

    # Retain the exact installed Python notice as well as the upstream terms.
    python_name = f'python-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
    for local_name in ('LICENSE.txt', 'LICENSE_PYTHON.txt'):
        local_license = base / local_name
        if local_license.is_file():
            target = destination / 'native-runtime' / python_name / f'INSTALLED-{local_name}'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_license, target)

    microsoft = sorted(name for name in binaries if _microsoft_runtime(name))
    if microsoft:
        notices = RUNTIME_NOTICES / 'microsoft-runtime'
        if not notices.is_dir() or not any(notices.iterdir()):
            raise RuntimeError('Microsoft runtime DLLs are bundled but their redistribution terms have not been supplied.')
        files = _copy_tree(notices, destination / 'native-runtime' / 'microsoft-runtime')
        manifest.append({'component': 'Microsoft Visual C++ / Universal CRT',
                         'binaries': microsoft, 'license_files': files,
                         'evidence': 'Microsoft runtime file names; see accompanying terms and provenance for applicable product family'})
        covered.update(microsoft)

    unknown = sorted(set(binaries) - covered)
    if unknown:
        raise RuntimeError('Unverified native DLL origin/license: ' + ', '.join(unknown)
                           + '. Check build PATH and the PyInstaller Analysis/COLLECT TOC; do not substitute another version\'s notices.')
    adb_directory = payload / 'adbutils' / 'binaries'
    if (adb_directory / 'adb.exe').is_file():
        adb_notice = None
        for candidate in sorted(RUNTIME_NOTICES.glob('android-platform-tools-*')):
            fingerprints = candidate / 'BINARIES.json'
            if not fingerprints.is_file():
                continue
            expected = json.loads(fingerprints.read_text(encoding='utf-8'))
            if expected and all((adb_directory / name).is_file()
                                and _digest(adb_directory / name) == digest
                                for name, digest in expected.items()):
                adb_notice = candidate
                break
        if adb_notice is None:
            raise RuntimeError('Bundled Android platform-tools do not match any audited official release; obtain its exact NOTICE.txt before release.')
        files = _copy_tree(adb_notice, destination / 'native-runtime' / adb_notice.name)
        manifest.append({'component': adb_notice.name, 'binaries': sorted(expected),
                         'license_files': files,
                         'evidence': 'SHA-256 of adb.exe and both support DLLs matches the official Google platform-tools archive'})
    native = destination / 'native-runtime'
    native.mkdir(parents=True, exist_ok=True)
    used_folders = {item['component'] for item in manifest}
    if microsoft:
        used_folders.add('microsoft-runtime')
    used_sources = [entry for entry in sources if entry['file'].split('/')[0] in used_folders]
    (native / 'SOURCES.json').write_text(json.dumps(used_sources, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (native / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return [f"{item['component']}: {len(item['license_files'])} notice files, {len(item['binaries'])} binary files" for item in manifest]


def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    index = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = dist.metadata['Name'], dist.version
        folder = destination / f'{name}-{version}'
        files = []
        for relative in dist.files or []:
            if not any(part.lower().startswith(NOTICE_PREFIXES) for part in relative.parts):
                continue
            source = Path(dist.locate_file(relative))
            if source.is_file() and '..' not in relative.parts:
                target = folder / str(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files.append(str(relative))
        index.append(f'{name}=={version}: {len(files)} license/notice files')
    index.extend(_collect_runtime(destination))
    (destination / 'INDEX.txt').write_text('\n'.join(index) + '\n', encoding='utf-8')
    print(f'License collection complete: {len(index)} dependency/runtime entries.')


if __name__ == '__main__':
    collect(sys.argv[1])
