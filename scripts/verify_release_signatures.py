"""Check a release's Sigstore bundles with the rule ``kazma update`` applies.

Run by ``.github/workflows/release.yml`` after it signs and before it
publishes: every file the release will publish -- the wheel, the sdist, the
SBOM and ``SHA256SUMS`` -- must have its bundle beside it
(``<file>.sigstore.json``), and each bundle must pass
``kazma_cli.release_signature.check_bundle``: chained to Sigstore, over the
file's SHA-256, made by this repository's release workflow from ``main`` or a
``v*`` tag. A release whose bundles the updater would refuse is never
published: from 0.21.2 on, every wheel install would refuse to update to it.

The rule is loaded from ``kazma-cli/kazma_cli/release_signature.py`` by path
(standard library at import), so this needs only sigstore installed:

    uv run --no-project --with sigstore==4.5.1 python scripts/verify_release_signatures.py dist
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "kazma-cli" / "kazma_cli" / "release_signature.py"

#: What the release workflow publishes, each with a bundle.
PUBLISHED = ("kazma-*.whl", "kazma-*.tar.gz", "kazma-*.cdx.json", "SHA256SUMS")
BUNDLE_SUFFIX = ".sigstore.json"


def load_rule() -> Any:
    """``release_signature``, loaded by path (registered before it runs)."""
    spec = importlib.util.spec_from_file_location("_kazma_release_signature", RULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def published_files(folder: Path) -> list[Path]:
    """The files of *folder* a release publishes (never a bundle)."""
    found: set[Path] = set()
    for pattern in PUBLISHED:
        found.update(p for p in folder.glob(pattern) if p.is_file())
    return sorted(found)


def check_files(files: list[Path], *, verifier: Any | None = None) -> list[str]:
    """One line per file whose bundle is missing or refused; empty when every
    file is signed as the updater requires."""
    rule = load_rule()
    problems: list[str] = []
    for path in files:
        bundle = path.with_name(path.name + BUNDLE_SUFFIX)
        if not bundle.is_file():
            problems.append(f"{path.name}: no {bundle.name}")
            continue
        digest = hashlib.sha256(path.read_bytes()).digest()
        try:
            signer = rule.check_bundle(digest, bundle.read_bytes(), verifier=verifier)
        except rule.SignatureRefused as exc:
            problems.append(f"{path.name}: refused ({exc})")
            continue
        print(f"{path.name}: signed by {signer}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: verify_release_signatures.py <folder>", file=sys.stderr)
        return 2
    folder = Path(args[0])
    files = published_files(folder)
    problems: list[str] = []
    if not any(p.name.endswith(".whl") for p in files):
        problems.append(f"{folder}: no kazma wheel to check")
    if not any(p.name == "SHA256SUMS" for p in files):
        problems.append(f"{folder}: no SHA256SUMS to check")
    problems += check_files(files)
    for line in problems:
        print(f"::error::{line}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
