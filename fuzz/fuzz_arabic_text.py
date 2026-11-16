"""Fuzz the Arabic text rules: search folding is stable, nothing raises.

``documents.arabic.fold_for_search`` folds both the Knowledge index and the
query (a fold on one side only is worse than none): folding twice must give
what folding once gave, or a stored word and its query fold apart.
``to_logical`` and ``is_rtl_dominant`` read every parsed page and every
document's blocks; neither may raise on any text.

Run under Atheris: ``python fuzz/fuzz_arabic_text.py -max_total_time=60``.
"""

from __future__ import annotations

import sys

TOKENS = (
    "ال", "ب", "ت", "ة", "ى", "ي", "أ", "إ", "آ", "ء", "ؤ", "ئ", "ً", "ّ",
    "ـ", "ﺍ", "ﻻ", "‏", "‎", "⁦", "⁩", "٠", "١",
    "٫", "،", "a", "1", " ", "\n", ".", "́", "ﷺ",
)
SEEDS = ("الكِتَابُ".encode(), "ﻻ ﺍﻟﻌﺮﺑﻴﺔ".encode(), "Kazma كاظمه 2026".encode())


def check(data: bytes) -> None:
    from kazma_core.documents.arabic import fold_for_search, is_rtl_dominant, to_logical

    text = data.decode("utf-8", errors="replace")
    once = fold_for_search(text)
    assert fold_for_search(once) == once, (text, once)
    assert isinstance(to_logical(text), str)
    assert isinstance(is_rtl_dominant(text), bool)


def main() -> None:
    import atheris

    with atheris.instrument_imports():
        import kazma_core.documents.arabic  # noqa: F401
    atheris.Setup(sys.argv, check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
