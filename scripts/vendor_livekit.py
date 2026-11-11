#!/usr/bin/env python
"""Vendor the LiveKit browser client into ``kazma-ui/kazma_ui/static/vendor/livekit/``.

Live duplex voice (``static/js/voice.js``) loaded ``livekit-client@2`` from
cdn.jsdelivr.net at the moment it was used: an unpinned version from another
host, run with the page's full rights (security audit 2026-11-10), and the
one script that kept the pages from sending a ``script-src`` policy. It now
ships with Kazma, pinned, like CodeMirror (``scripts/vendor_codemirror.py``).

This script takes the pinned version from npm, checks the tarball against
the integrity npm published for it (:data:`LK_INTEGRITY`), and writes the UMD
build byte for byte as npm ships it, its LICENSE (Apache-2.0), and
``vendored.json``: the package, version, licence, source, integrity and the
SHA-256 of every file written. ``tests/test_vendor_livekit.py`` holds the
files to that record.

Run it only when bumping :data:`LK_VERSION` (and :data:`LK_INTEGRITY`, from
``npm view livekit-client@<version> dist.integrity``); the output is
committed. Pick a version published at least two weeks earlier.

    python scripts/vendor_livekit.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

LK_PACKAGE = "livekit-client"
LK_VERSION = "2.23.0"
#: npm's own record of the tarball (``npm view livekit-client@2.23.0 dist.integrity``).
LK_INTEGRITY = (
    "sha512-j9Kg86nJjBjNFUD+ukp2jwMCGa0k+YD5gU//RzAM+ZY3pbG631QCte7fQjwqWMmtBzhDJL9nKYuOLMv/9IbAxg=="
)
LK_LICENSE = "Apache-2.0"

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "kazma-ui" / "kazma_ui" / "static" / "vendor" / "livekit"

#: package path -> the name it is served under.
FILES = {
    "dist/livekit-client.umd.js": "livekit-client.umd.js",
    "LICENSE": "LICENSE",
}


def _integrity_of(path: Path) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode("ascii")


def _fetch(workdir: Path) -> Path:
    """npm pack the pinned version, check it, and return the extracted package."""
    npm = shutil.which("npm")  # the full path: no shell (bandit B602)
    if npm is None:
        raise SystemExit("npm not found on PATH -- install Node.js to vendor livekit-client")
    print(f"npm pack {LK_PACKAGE}@{LK_VERSION} ...")
    proc = subprocess.run(
        [npm, "pack", f"{LK_PACKAGE}@{LK_VERSION}", "--silent", "--prefer-offline"],
        cwd=str(workdir), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"npm pack failed:\n{proc.stdout}\n{proc.stderr}")
    tarballs = list(workdir.glob(f"{LK_PACKAGE}-*.tgz"))
    if len(tarballs) != 1:
        raise SystemExit(f"npm pack produced {len(tarballs)} tarballs")
    got = _integrity_of(tarballs[0])
    if got != LK_INTEGRITY:
        raise SystemExit(f"the tarball is not the one npm published:\n  expected {LK_INTEGRITY}\n  got      {got}")
    with tarfile.open(tarballs[0]) as tf:
        tf.extractall(workdir, filter="data")  # noqa: S202 - checked npm registry tarball
    pkg = workdir / "package"
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    if meta.get("version") != LK_VERSION or meta.get("license") != LK_LICENSE:
        raise SystemExit(f"unexpected package.json: {meta.get('version')!r} {meta.get('license')!r}")
    return pkg


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _fetch(Path(tmp))
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        for source, name in FILES.items():
            data = (pkg / source).read_bytes()
            (OUT_DIR / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
            print(f"  {name}: {len(data) // 1024} KB")
    record = {
        "package": LK_PACKAGE,
        "version": LK_VERSION,
        "license": LK_LICENSE,
        "source": f"https://registry.npmjs.org/{LK_PACKAGE}/-/{LK_PACKAGE}-{LK_VERSION}.tgz",
        "integrity": LK_INTEGRITY,
        "sha256": hashes,
        "rebuild": "python scripts/vendor_livekit.py",
    }
    (OUT_DIR / "vendored.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT_DIR.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
