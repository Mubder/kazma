"""Kazma cross-machine migration engine.

A portable-bundle system for moving a full Kazma installation across
machines/OSes without the silent breakage of a naive copy-paste
(undecryptable vault, dead absolute paths, missing data).

Public API:

  * :func:`export_bundle` — create a ``.zip`` bundle from the current install.
  * :func:`import_bundle` — restore a bundle into the current install (atomic).
  * :class:`KazmaBundle` / :meth:`KazmaBundle.verify` — read/verify a bundle.

See :mod:`kazma_core.migration.bundle` for the format and the three
load-bearing invariants (vault pairing, path translation, atomicity),
documented in AGENTS.md §18.

Typical use (via the ``kazma migrate`` CLI)::

    kazma migrate export --out my-bundle.zip
    kazma migrate verify my-bundle.zip
    kazma migrate import my-bundle.zip --workspace /path/to/kazma
"""

from kazma_core.migration.bundle import (
    BUNDLE_VERSION,
    KazmaBundle,
    Manifest,
    VerifyReport,
)
from kazma_core.migration.exporter import export_bundle
from kazma_core.migration.importer import ImportReport, import_bundle
from kazma_core.migration.path_rewrite import PathMap, build_path_map

__all__ = [
    "BUNDLE_VERSION",
    "KazmaBundle",
    "Manifest",
    "VerifyReport",
    "PathMap",
    "build_path_map",
    "export_bundle",
    "import_bundle",
    "ImportReport",
]
