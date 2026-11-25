"""The pages' search fold is the Python one, character by character (W6).

The Memory page's map filtered its names by lower-cased text: an Arabic name
was found only in the spelling it was stored in, as the lists were. The map's
search is in the browser, so the fold is there too
(``static/js/search_fold.js``); this holds it to
``documents.arabic.fold_for_search``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from kazma_core.documents.arabic import fold_for_search

REPO = Path(__file__).resolve().parents[1]
FOLD_JS = REPO / "kazma-ui/kazma_ui/static/js/search_fold.js"
CONSOLE_JS = REPO / "kazma-ui/kazma_ui/static/js/memory_console.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _folded(texts: list[str], source: str | None = None) -> list[str]:
    script = ("global.window = {};\n" + (source if source is not None else FOLD_JS.read_text(encoding="utf-8"))
              + "\nlet d = ''; process.stdin.on('data', c => d += c); process.stdin.on('end', () =>"
              " process.stdout.write(JSON.stringify(JSON.parse(d).map(window.kazmaSearchFold))));")
    ran = subprocess.run(["node", "-e", script], input=json.dumps(texts), capture_output=True, text=True,
                         encoding="utf-8", timeout=120, check=True)
    return json.loads(ran.stdout)


def _texts() -> list[str]:
    spans = ((0x20, 0x7F), (0x600, 0x700), (0x750, 0x780), (0x8A0, 0x8E0), (0x200C, 0x2070), (0xFB50, 0xFE00),
             (0xFE70, 0xFF00), (0xFF10, 0xFF1A))
    out = [f"ق{chr(c)}ه" for first, last in spans for c in range(first, last)]
    return out + ["القَهْوَةُ", "قهـــوة", "احمد  أحمد", "إسطنبول", "مستشفى", "شاطئ", "١٢ مارس", "Kazma كاظمة", "", "   "]


def test_every_arabic_character_folds_as_in_python():
    texts = _texts()
    differ = [(t, p, j) for t, p, j in zip(texts, map(fold_for_search, texts), _folded(texts), strict=True) if p != j]
    assert differ == []


def test_a_letter_taken_out_of_the_table_is_seen():
    """Negative control: without the taa marbuta's row, the comparison fails."""
    source = FOLD_JS.read_text(encoding="utf-8")
    cut = source.replace("\\u0629\\u06be", "\\u06be")
    assert cut != source
    assert _folded(["قهوة"], cut) != [fold_for_search("قهوة")] == _folded(["قهوة"])


@pytest.mark.parametrize("function", ["_v2gApplyFilters", "_v2gApplyPathFromQuery"])
def test_the_map_compares_folded_names(function):
    source = CONSOLE_JS.read_text(encoding="utf-8")
    body = re.search(rf"function {function}\(\) \{{(.*?)\n  \}}\n", source, re.S).group(1)
    assert "window.kazmaSearchFold(" in body
    assert "toLowerCase" not in body, "a name compared as written: an Arabic one is found in one spelling only"


def test_the_fold_loads_before_the_map():
    page = (REPO / "kazma-ui/kazma_ui/templates/memory.html").read_text(encoding="utf-8")
    assert page.index("js/search_fold.js") < page.index("js/memory_console.js")
