"""The copies Kazma's repairs keep of the memory database stay on this machine (FA-254).

The 2026-11 functional audit, a live ``kazma migrate export``: the bundle
carried ``memory_state.db.pre_entity_key``, ``.pre_relabel`` and
``.pre_reopen`` as "the operator's own files", and a scratch import restored
all three. Each holds the memory database as it was before a repair --
everything the owner has forgotten since included -- so a migration brought
forgotten memories to the new machine (the reason Time Travel's
``snapshots.db`` is not carried, §12 TT-05). ``store_registry.carried_in_bundle``
refused only names ending in a database suffix.

Every suffix a product module or repair script gives such a copy
(``path + ".pre_<name>"``, read from the source) is declared ``machine`` in
``DATA_DIR_ENTRIES`` and named in the erasure horizon. A planted suffix is
caught; the rule without the declarations carries the copies (the negative
control).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from kazma_core import store_registry
from kazma_core.memory.memory_stores import ERASURE_HORIZON

REPO = Path(__file__).resolve().parents[1]

#: ``x + ".pre_name"`` / ``f"{x}.pre_name"`` / ``x.with_name(... ".pre_name")``
_SUFFIX = re.compile(r"""["'](?:\{[^}]*\})?(\.pre_[a-z_]+)["']""")


def _sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py",
                             "scripts/*.py", "scripts/**/*.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8", errors="ignore")
            for rel in listed if "/tests/" not in rel and (REPO / rel).exists()}


def written_suffixes(sources: dict[str, str]) -> set[str]:
    """The ``.pre_*`` suffixes product code and scripts write beside a file."""
    found: set[str] = set()
    for text in sources.values():
        found |= set(_SUFFIX.findall(text))
    return found


def undeclared(suffixes: set[str]) -> set[str]:
    return {s for s in suffixes if store_registry.carried_in_bundle("memory_state.db" + s)
            or s not in store_registry.REPAIR_COPY_SUFFIXES}


def test_every_repair_copy_suffix_is_declared_and_stays_home():
    suffixes = written_suffixes(_sources())
    assert {".pre_entity_key", ".pre_relabel", ".pre_reopen", ".pre_reembed"} <= suffixes, suffixes
    assert not undeclared(suffixes), (
        "A repair writes a copy of a database that a migration would carry. Declare its suffix "
        f"in store_registry.DATA_DIR_ENTRIES (machine) and REPAIR_COPY_SUFFIXES: {sorted(undeclared(suffixes))}")
    for suffix in suffixes:
        for lean in (False, True):
            assert not store_registry.carried_in_bundle("memory_state.db" + suffix, lean=lean)


def test_the_erasure_horizon_names_every_memory_repair_copy():
    horizon = " ".join(what for what, _until in ERASURE_HORIZON)
    for suffix in (".pre_entity_key", ".pre_relabel", ".pre_reopen", ".pre_reembed"):
        assert "memory_state.db" + suffix in horizon, suffix


def test_a_planted_suffix_is_caught():
    planted = {"scripts/x.py": 'backup = path + ".pre_zombie"\nother = f"{db}.pre_ghost"'}
    assert written_suffixes(planted) == {".pre_zombie", ".pre_ghost"}
    assert undeclared({".pre_zombie"}) == {".pre_zombie"}


def test_without_the_declarations_a_bundle_carried_the_copies(monkeypatch):
    """Negative control: the registry before FA-254 had no entry for the
    copies, so each was "the operator's own" and travelled."""
    entries = {k: v for k, v in store_registry.DATA_DIR_ENTRIES.items() if not k.startswith("*.pre_")}
    monkeypatch.setattr(store_registry, "DATA_DIR_ENTRIES", entries)
    for suffix in (".pre_entity_key", ".pre_relabel", ".pre_reopen"):
        assert store_registry.carried_in_bundle("memory_state.db" + suffix)
