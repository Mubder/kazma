"""Build Kazma's Windows installer from a release's source archive (REMAINING 2.7).

    python scripts/windows_installer/build.py --source dist/kazma-0.21.2.tar.gz --out dist

The archive is the one the release signs (``kazma-<version>.tar.gz``). It is
read with the updater's own rules (``kazma_cli.release_tree.shipped_files``:
files under ``kazma-<version>/`` only, nothing where the owner's files live),
laid out with the release-install marker beside the files, and handed to Inno
Setup (``kazma.iss``). The installer is
``<out>/kazma-<version>-windows-setup.exe``; its SHA-256 is printed.

Inno Setup is found where its own installer puts it; ``--install-inno``
installs the pinned version first (:data:`INNO_URL`, checked against
:data:`INNO_SHA256` before it runs), for CI and a machine without it.
Windows only (Inno Setup is a Windows program).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "kazma-cli"))

from kazma_cli import release_tree  # noqa: E402

#: Inno Setup 6.7.4 (28 Oct 2026), from its project's GitHub release; the
#: SHA-256 is the release's own asset digest. Raise both by hand, after trying.
INNO_VERSION = "6.7.4"
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe"
INNO_SHA256 = "a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_version(archive: Path) -> str:
    """The version ``kazma-<version>.tar.gz`` is named for."""
    name = archive.name
    if not (name.startswith("kazma-") and name.endswith(".tar.gz")):
        raise SystemExit(f"not a Kazma source archive: {name}")
    return name[len("kazma-"):-len(".tar.gz")]


def lay_out(archive: Path, staging: Path) -> tuple[str, list[str]]:
    """The installer's files in *staging*: the archive's tree and the marker."""
    version = archive_version(archive)
    try:
        files = release_tree.shipped_files(archive, version)
    except release_tree.ArchiveRefused as exc:
        raise SystemExit(f"the archive is refused: {exc}") from None
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(staging, filter="data")
    tree = staging / f"kazma-{version}"
    release_tree.write_marker(tree, version, files)
    return version, files


def find_iscc(explicit: str = "") -> Path | None:
    """Inno Setup's compiler, where its installer puts it."""
    candidates = [explicit] if explicit else []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    found = shutil.which("ISCC")
    return Path(found) if found else None


def install_inno(target: Path) -> Path:
    """The pinned Inno Setup, installed for this user into *target*."""
    with tempfile.TemporaryDirectory(prefix="kazma-inno-") as tmp:
        exe = Path(tmp) / Path(INNO_URL).name
        with urllib.request.urlopen(INNO_URL, timeout=120) as resp, exe.open("wb") as fh:  # noqa: S310 - pinned https URL
            shutil.copyfileobj(resp, fh)
        actual = _sha256(exe)
        if actual != INNO_SHA256:
            raise SystemExit(f"Inno Setup's download does not match its SHA-256 (expected {INNO_SHA256}, got {actual})")
        subprocess.run([str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
                        f"/DIR={target}"], check=True, stdin=subprocess.DEVNULL, timeout=600)
    iscc = target / "ISCC.exe"
    if not iscc.is_file():
        raise SystemExit(f"Inno Setup {INNO_VERSION} installed no ISCC.exe in {target}")
    return iscc


def build(archive: Path, out: Path, iscc: Path) -> Path:
    """Compile the installer; returns its path."""
    out.mkdir(parents=True, exist_ok=True)
    staging = out / "_installer"
    version, files = lay_out(archive, staging)
    tree = staging / f"kazma-{version}"
    subprocess.run([str(iscc), f"/DAppVersion={version}", f"/DSourceDir={tree}", f"/DOutputDir={out.resolve()}",
                    "/Q", str(HERE / "kazma.iss")], check=True, cwd=str(HERE), stdin=subprocess.DEVNULL,
                   timeout=1800)
    shutil.rmtree(staging, ignore_errors=True)
    setup = out / f"kazma-{version}-windows-setup.exe"
    if not setup.is_file():
        raise SystemExit(f"Inno Setup made no {setup.name}")
    print(f"{setup}  {len(files)} files  sha256 {_sha256(setup)}")
    return setup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Kazma's Windows installer from a release source archive.")
    parser.add_argument("--source", required=True, help="kazma-<version>.tar.gz")
    parser.add_argument("--out", required=True, help="where the installer goes")
    parser.add_argument("--iscc", default="", help="Inno Setup's ISCC.exe (found by itself otherwise)")
    parser.add_argument("--install-inno", action="store_true",
                        help=f"install Inno Setup {INNO_VERSION} for this user first when none is found")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("Inno Setup runs on Windows only.", file=sys.stderr)
        return 2
    iscc = find_iscc(args.iscc)
    if iscc is None and args.install_inno:
        iscc = install_inno(Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Programs" / "Inno Setup 6")
    if iscc is None:
        print("Inno Setup's ISCC.exe was not found: pass --iscc or --install-inno.", file=sys.stderr)
        return 2
    build(Path(args.source), Path(args.out), iscc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
