# Third-party source

The modules in `xhs_importer/_vendor/wechat_meme_export/` are from
[Ant39140/wechat_meme_export](https://github.com/Ant39140/wechat_meme_export),
commit `27dd2eb4faa15cd9f054d53c0d4435d03f984864`, copyright (c) 2026, MIT.
The original license is included beside the modules. No upstream CLI or GUI is executed.

The integrated reader reuses authenticated decryption and the versioned, read-only
Weixin memory scanner. Formats.py contains only format/AES helper functions excerpted
from exporter.py; upstream network/file-writing code is excluded. Our download pipeline
implements trusted CDN checks, limits, cancellation and original-only handling.
Decrypt.py and memory.py bodies are unmodified. The enclosing package and wrappers are local.

WAL handling follows the [SQLite WAL file format](https://sqlite.org/fileformat.html#wal_format).

## Compatibility fixtures and trademarks

`xhs_importer/assets/` and `tests/fixtures/xhs-9.33.4/` contain small UI crops
and structural fixtures used to identify the add button and success message.
They are compatibility/test material from Xiaohongshu, not a collection of user
stickers or chat screenshots. Third-party UI, names and trademarks remain with
their respective owners; the project license does not grant trademark rights
or claim ownership of those UI elements. No affiliation or endorsement is implied.
First-run test stickers are generated locally by `trial_batch.py`.

## Runtime and build dependencies

The source pins Python packages in `requirements.txt` and `requirements-dev.txt`.
Dependencies are separately licensed; their licenses are not replaced by the
project's GPL license. Packaged builds must retain the license files supplied
by the installed packages (including transitive dependencies and native libraries).

- Python / Tkinter: https://www.python.org/psf/license/
- Pillow: https://github.com/python-pillow/Pillow
- uiautomator2: https://github.com/openatx/uiautomator2
- adbutils: https://github.com/openatx/adbutils
- cryptography: https://github.com/pyca/cryptography
- PyInstaller (including its bootloader exception): https://github.com/pyinstaller/pyinstaller
- NSIS, for the Windows installer: https://nsis.sourceforge.io/License

Bundled Android tooling and native components retain their own notices. Windows
builds include ADB through `adbutils`; users may also configure their emulator's
ADB. The bundled Android Platform Tools 36.0.0 binaries are matched against the
official Google archive, and its full `NOTICE.txt` is included. The application
does not distribute Weixin, Xiaohongshu or the emulator itself.

`packaging/windows/runtime-licenses/` records exact upstream license texts,
versions, source URLs and SHA-256 checksums for the audited Windows build. These
include CPython 3.11.4 and its incorporated-software notices, Tcl/Tk 8.6.12,
OpenSSL 1.1.1u, libffi 3.4.4, zlib 1.2.13, bzip2 1.0.8, SQLite 3.41.2, and
XZ/liblzma 5.4.2. The XZ copying file identifies liblzma as public domain; the
additional GPL/LGPL texts describe other portions of the upstream source
distribution and do not change the liblzma notice. Microsoft runtime terms and
their provenance are supplied separately under `microsoft-runtime/`.

During packaging, `collect_licenses.py` copies installed Python/package notices
and checks bundled native DLLs against the build interpreter's Conda package
metadata and file hashes. It refuses unknown native library versions/origins
instead of claiming that the notices for this build apply to another runtime.
See `dependency-licenses/native-runtime/MANIFEST.json` and `SOURCES.json` in the
packaged application. Builders using a different Python/native runtime must
add and verify that runtime's corresponding notices before distributing it.
