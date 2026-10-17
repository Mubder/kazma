"""Settings -> Packages says what is installed and how to install safely.

Read on the live page 2026-10-17, with every extra installed:
- "For full vector recall install the rag extra" was shown under a memory
  stack whose `[rag]` row said Installed;
- the memory rows were labelled with the V1 layers ("L1 Chroma", "L2 Graph",
  "L3 FTS5", "L4 sqlite-vec") over what memory.health reports in their place
  since V2: facts, conversation memories, procedures and the task queue,
  entities (the Dashboard used the same labels);
- "Install Everything" said `uv sync --all-extras`, which removes every
  package outside the set it is asked for -- what `kazma update` avoids
  (`kazma_cli.update._reinstall_local`: an additive `uv pip install -e`).
"""

from __future__ import annotations

import re
from pathlib import Path

from kazma_ui.i18n.catalog import merged

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
UI = ROOT / "kazma-ui" / "kazma_ui"

#: Names of the V1 memory layers, gone since the V2 engine.
V1_NAMES = re.compile(r"chroma|kroma|كروما|\bgraph\b|الرسم|fts5|sqlite-vec", re.I)
#: A `uv sync` that is not `--inexact`: exact, so it prunes.
PRUNING_SYNC = re.compile(r"uv sync(?![^\n'\"<]*--inexact)")


def _hint_condition(template: str) -> str:
    i = template.index("packages.memory_hint")
    tag = template.rfind("<p", 0, i)
    return template[tag:i]


def test_the_rag_hint_is_shown_only_without_the_rag_extra():
    cond = _hint_condition(SETTINGS.read_text(encoding="utf-8"))
    assert "x-show=" in cond and "'rag'" in cond and "installed" in cond, cond
    assert "pkgLoaded" in cond, "say nothing before the list has loaded"


def test_the_old_hint_was_unconditional():
    """Negative control: the hint before the fix."""
    old = '<p class="pkg-muted pkg-hint">\n                {{ t(\'packages.memory_hint\') }}'
    assert "x-show" not in _hint_condition(old)


def test_no_page_recommends_a_pruning_uv_sync():
    offenders = []
    for path in [*UI.joinpath("templates").rglob("*.html"), *UI.joinpath("static", "js").rglob("*.js")]:
        if "vendor" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PRUNING_SYNC.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert not offenders, offenders
    assert 'uv pip install -e ".[all]"' in SETTINGS.read_text(encoding="utf-8")


def test_the_gate_catches_the_old_command():
    assert PRUNING_SYNC.search('<code class="pkg-install-cmd">uv sync --all-extras</code>')
    assert not PRUNING_SYNC.search("uv sync --inexact --extra rag")


def test_memory_rows_are_named_for_what_health_reports():
    catalog = merged()
    for lang in ("en", "ar"):
        for row in ("layer_l1", "layer_l2", "layer_l3", "layer_l4", "vector_memory"):
            packages = catalog[f"packages.layer.{row}"][lang]
            dashboard = catalog[f"dashboard.comp.{row}"][lang]
            assert packages == dashboard, (row, lang, packages, dashboard)
            assert not V1_NAMES.search(packages), (row, lang, packages)
    assert catalog["packages.layer.layer_l1"]["en"] == "Facts"
    assert catalog["packages.layer.layer_l4"]["en"] == "Entities"


def test_the_label_check_catches_the_v1_names():
    """Negative control: the labels before the fix."""
    for old in ("L1 Chroma", "L2 Graph", "L3 FTS5", "L4 sqlite-vec", "L1 كروما", "L2 الرسم"):
        assert V1_NAMES.search(old), old
