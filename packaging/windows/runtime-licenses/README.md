# Audited Windows runtime notices

These are unmodified upstream notices for the component versions named in each
folder. `sources.json` records source URLs, version identifiers, archive members
where relevant, and the SHA-256 of each downloaded notice. Files were obtained
from official tagged repositories or upstream release archives.

The release collector matches actual bundled DLL bytes to installed Conda
package records under `sys.base_prefix`. It copies only notices for the matched
package/version. A different runtime must be audited separately; these files
are not a license inventory for arbitrary Python installations.

The Android Platform Tools folder additionally records binary hashes verified
against Google's official Windows archive. `NOTICE.txt` belongs to that exact
tool release, not merely the Python `adbutils` wrapper.

The Microsoft runtime folder includes its own provenance explanation. Those
terms cover the named Microsoft product family and are not represented as a
license file extracted from an old Conda package.

All licenses remain the property of their respective authors. The project's
GPL license does not replace any third-party terms.
