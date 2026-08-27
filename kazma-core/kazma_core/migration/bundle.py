"""Kazma migration bundle — format, manifest, integrity.

A *migration bundle* is a versioned ``.zip`` archive that captures an entire
Kazma installation (config, secrets, memory, chat history, snapshots,
scheduled jobs, binary assets) in a portable, cross-OS form. It is produced
by :func:`kazma_core.migration.exporter.export_bundle` and consumed by
:func:`kazma_core.migration.importer.import_bundle`.

Bundle layout (all paths relative to the archive root)::

    manifest.json          # version, source OS/hostname, created_at, sha256 per file
    meta.env               # non-secret .env entries (vault key, public url, backend hint)
    config.yaml            # ConfigStore.export_yaml() (secrets are vault refs, not plaintext)
    vault.db               # encrypted secrets store — travels WITH KAZMA_VAULT_KEY
    data/
      settings.db, workspaces.db, chat_sessions.db, cron.db,
      checkpoints.db, snapshots.db, memory_state.db, memory_ops.db,
      sessions.db, swarm_tasks.db, sandbox_emails.db, research_sessions.db,
      pipeline_logs.db, knowledge_graph.db
    assets/                # binary artifacts with no embedded paths (copied verbatim)
      attachments/ documents/ exports/ images/ fonts/
    pathmap.json           # source workspace root + data dir (for path translation on import)

Three load-bearing invariants the bundle enforces (see AGENTS.md §18):

  A. ``vault.db`` and ``KAZMA_VAULT_KEY`` travel as an atomic pair. The
     key's fingerprint is in ``manifest.json``; import aborts if the
     target's key differs unless ``--reset-vault-key`` is passed.
  B. Every embedded absolute path (``/home/user/kazma``) is rewritten to
     the target path on import, across OS path-separator conventions.
  C. Import is atomic — it stages to a temp dir, verifies, backs up the
     live DBs, then swaps. A failure leaves live data untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import socket
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLE_VERSION",
    "Manifest",
    "KazmaBundle",
    "VerifyReport",
    "sha256_file",
    "vault_key_fingerprint",
]

# Bundle format version. Bump on any incompatible layout change; the importer
# checks compatibility and refuses bundles from a newer major version.
BUNDLE_VERSION = "1.0"

# Files that travel at the archive root (not under data/ or assets/).
# Note: vault.db is a DATA file — it lives at data/vault.db, not the root,
# because it's copied alongside the other SQLite DBs by the exporter.
_META_FILES = ("manifest.json", "meta.env", "config.yaml", "pathmap.json")


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Stream-hash a file (handles the multi-hundred-MB snapshots.db)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def vault_key_fingerprint(key: str) -> str:
    """A short, non-reversible fingerprint of the vault key for manifest comparison.

    Full sha256 would leak the key via brute-force on a 64-hex input; we use a
    salted double-hash so the manifest can detect a key *mismatch* without
    storing the key itself. The actual key travels in ``meta.env`` (the bundle
    is presumed private to the operator, but defense-in-depth).
    """
    if not key:
        return ""
    first = hashlib.sha256(key.encode()).hexdigest()
    return hashlib.sha256((first + "kazma.bundle.v1").encode()).hexdigest()[:16]


@dataclass
class Manifest:
    """The bundle manifest — written to ``manifest.json``."""

    bundle_version: str = BUNDLE_VERSION
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    created_epoch: float = field(default_factory=time.time)
    source_os: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    source_hostname: str = field(default_factory=socket.gethostname)
    source_python: str = field(default_factory=platform.python_version)
    # sha256 of each included file, keyed by archive-relative path.
    file_hashes: dict[str, str] = field(default_factory=dict)
    # Vault-key fingerprint (invariant A). Empty if source has no vault key.
    vault_key_fingerprint: str = ""
    # Detected source backend, for importer warnings (v1 imports into SQLite).
    source_backend: str = "sqlite"
    # Row counts per data db (for verify/dry-run reporting).
    table_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    # The source workspace root + data dir, captured for path translation.
    source_workspace_root: str = ""
    source_data_dir: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        data = json.loads(text)
        # Forward-compat: ignore unknown keys from newer bundle versions.
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def is_compatible(self) -> tuple[bool, str]:
        """Return (ok, reason). Refuse newer major versions."""
        try:
            major = int(self.bundle_version.split(".", 1)[0])
        except (ValueError, AttributeError):
            return False, f"unparseable bundle_version {self.bundle_version!r}"
        our_major = int(BUNDLE_VERSION.split(".", 1)[0])
        if major > our_major:
            return False, (
                f"bundle version {self.bundle_version} is newer than this Kazma "
                f"supports ({BUNDLE_VERSION}); upgrade Kazma before importing."
            )
        return True, ""


@dataclass
class VerifyReport:
    """Result of :meth:`KazmaBundle.verify` — human-readable + machine-checkable."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    table_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    vault_key_fingerprint: str = ""
    source_workspace_root: str = ""

    def add_error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class KazmaBundle:
    """Read-side handle over a written bundle (.zip on disk).

    Use :func:`export_bundle` to *create* a bundle; this class is the read/
    verify/extract side used by ``verify`` and ``import``.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"bundle not found: {self.path}")
        self._manifest: Manifest | None = None

    # ── Manifest ───────────────────────────────────────────────────────

    @property
    def manifest(self) -> Manifest:
        if self._manifest is None:
            with zipfile.ZipFile(self.path, "r") as zf:
                try:
                    text = zf.read("manifest.json").decode("utf-8")
                except KeyError:
                    raise ValueError(
                        f"{self.path.name} is not a Kazma bundle (no manifest.json)"
                    )
            self._manifest = Manifest.from_json(text)
        return self._manifest

    # ── Verification ───────────────────────────────────────────────────

    def verify(self, *, check_hashes: bool = True) -> VerifyReport:
        """Integrity-check the bundle: structure, hashes, manifest compatibility.

        Args:
            check_hashes: if False, skip the (slow) sha256 re-hash of every
                file — useful for a quick structural check.
        """
        report = VerifyReport(
            vault_key_fingerprint=self.manifest.vault_key_fingerprint,
            source_workspace_root=self.manifest.source_workspace_root,
        )
        compatible, reason = self.manifest.is_compatible()
        if not compatible:
            report.add_error(reason)

        try:
            with zipfile.ZipFile(self.path, "r") as zf:
                infos = zf.infolist()
                names = {i.filename for i in infos}
                # File entries only (directory members end with "/" and carry no data).
                arc_files = {i.filename for i in infos if not i.is_dir()}
                manifest_files = set(self.manifest.file_hashes)
                for required in _META_FILES:
                    if required not in names:
                        report.add_error(f"missing required file: {required}")
                if "data" not in names and not any(n.startswith("data/") for n in names):
                    report.add_warning("bundle has no data/ files (empty export?)")

                # Manifest ↔ archive cross-check, BOTH directions. Previously
                # only manifest→archive was checked, so a rogue member slipped
                # into the zip passed verification silently and the importer
                # then swapped it onto live databases (audit H13). A missing
                # manifest file is also failed unconditionally now — the old
                # check skipped it when check_hashes=False.
                for rel in sorted(manifest_files - arc_files):
                    report.add_error(f"manifest lists {rel} but it is not in the archive")
                # Extra archive members the manifest never heard of get
                # extracted and restored unchecked — treat them as tampering.
                # manifest.json itself is the one member legitimately absent
                # from file_hashes (it *carries* them; written after hashing).
                unlisted = sorted(arc_files - manifest_files - {"manifest.json"})
                if unlisted:
                    report.add_error(
                        f"{len(unlisted)} archive file(s) NOT listed in the manifest "
                        f"(tamper signal — they would be restored unchecked): "
                        + ", ".join(unlisted)
                    )

                for info in infos:
                    if info.is_dir():
                        continue
                    report.file_count += 1
                    report.total_bytes += info.file_size

                if check_hashes:
                    for rel, expected in self.manifest.file_hashes.items():
                        if rel not in arc_files:
                            continue  # already reported above as missing
                        actual = hashlib.sha256(zf.read(rel)).hexdigest()
                        if actual != expected:
                            report.add_error(
                                f"hash mismatch for {rel}: manifest={expected[:12]}… actual={actual[:12]}…"
                            )
        except zipfile.BadZipFile as exc:
            report.add_error(f"corrupt zip: {exc}")

        report.table_counts = dict(self.manifest.table_counts)
        return report

    # ── Extraction ─────────────────────────────────────────────────────

    def extract_all(self, dest: Path) -> Path:
        """Extract the whole bundle into ``dest``. Returns the dest path."""
        dest = Path(dest).resolve()
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.path, "r") as zf:
            # Zip-slip guard: reject any entry whose resolved target escapes
            # dest (e.g. data/../../../etc/cron.d/x). CPython's extractall is
            # not fully traversal-safe across versions; the bundle is operator-
            # supplied, but defense-in-depth is cheap (audit finding).
            for name in zf.namelist():
                target = (dest / name).resolve()
                try:
                    target.relative_to(dest)
                except ValueError:
                    raise ValueError(f"unsafe bundle entry escapes staging: {name}")
            zf.extractall(dest)
        return dest

    def read_text(self, arcname: str) -> str:
        """Read a text file from the bundle (e.g. ``config.yaml``, ``meta.env``)."""
        with zipfile.ZipFile(self.path, "r") as zf:
            return zf.read(arcname).decode("utf-8")

    def has_file(self, arcname: str) -> bool:
        with zipfile.ZipFile(self.path, "r") as zf:
            return arcname in zf.namelist()


# ── meta.env helpers (the vault-key + non-secret env that travels) ─────────


def parse_meta_env(text: str) -> dict[str, str]:
    """Parse a ``KEY=value`` .env-style blob (the bundle's ``meta.env``)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def render_meta_env(items: dict[str, str]) -> str:
    """Render a dict to a ``KEY=value`` .env-style blob."""
    lines: list[str] = []
    for k in sorted(items):
        v = str(items[k])
        # Quote if it contains whitespace or shell-special chars.
        if any(c in v for c in " \t#'\""):
            lines.append(f'{k}="{v}"')
        else:
            lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def current_vault_key() -> str:
    """The live ``KAZMA_VAULT_KEY`` from the environment (may be empty)."""
    return (os.environ.get("KAZMA_VAULT_KEY") or "").strip()
