"""Write the public benchmarks page from the committed results (REMAINING 4.8).

``docs/docs/reference/benchmarks.md`` is made from the three result files the
test suite holds every change to, never typed by hand:

- memory recall and fact extraction: ``tests/fixtures/memory_bench/thresholds.json``
  (``scripts/memory_bench.py``, ``tests/test_memory_benchmark.py``);
- Knowledge Library search: ``tests/fixtures/knowledge_bench/thresholds.json``
  (``scripts/knowledge_bench.py``, ``tests/test_knowledge_benchmark.py``);
- reading documents, Arabic and English, with and without OCR:
  ``tests/fixtures/documents/fidelity_thresholds.json``
  (``scripts/document_fidelity.py``, ``tests/test_document_fidelity.py``).

    .venv/Scripts/python.exe scripts/benchmarks_page.py          # write it
    .venv/Scripts/python.exe scripts/benchmarks_page.py --check  # 1 when stale

``tests/test_benchmarks_page.py`` runs ``--check``: a lock that changes a
result changes the page in the same commit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "docs" / "reference" / "benchmarks.md"
MEMORY = ROOT / "tests" / "fixtures" / "memory_bench" / "thresholds.json"
KNOWLEDGE = ROOT / "tests" / "fixtures" / "knowledge_bench" / "thresholds.json"
FIDELITY = ROOT / "tests" / "fixtures" / "documents" / "fidelity_thresholds.json"

_MEMORY_NAMES = {
    "fact": "A fact you told it",
    "single": "Something said in one turn",
    "multi": "Something spread over several turns",
    "update": "A fact that changed (the newest wins)",
    "paraphrase": "Asked in other words",
    "keyword": "Asked with the exact words",
    "assistant": "Something Kazma itself said",
    "overview": "Catch-me-up questions",
    "arabic": "Asked in Arabic",
    "mixed": "Arabic and English mixed",
    "noise": "A question with noise around it",
}
_KNOWLEDGE_NAMES = {
    "exact": "The exact words of the page",
    "paraphrase": "Asked in other words",
    "arabic": "Arabic",
    "arabic_paraphrase": "Arabic, in other words",
    "mixed": "Arabic and English mixed",
    "cross": "Asked in one language, written in the other",
    "multi": "An answer spread over several pages",
}
_FIDELITY_NAMES = {
    "token_recall": "words found", "segment_recall": "passages found", "order": "in order",
    "number_recall": "numbers exact", "table_cell_recall": "table cells", "punctuation": "punctuation",
    "cer": "character errors", "wer": "word errors",
}
_LOWER_IS_BETTER = {"cer", "wer"}


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f} %"


def _num(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def _memory(data: dict) -> list[str]:
    s = data["scores"]
    lines = [
        "## Memory: does it find what you told it? {#memory}",
        "",
        f"Dataset version {data['dataset_version']}: a private memory seeded the product's way (turns, facts, "
        "weekly summaries), then asked questions through the real `recall()`, with the meaning-search model's "
        "recorded vectors. **Hit rate**: the answer is among what Kazma shows the model. **MRR**: how high it "
        "ranks (1 = first). **Precision**: how much of what is shown is about the question.",
        "",
        "| Question | Hit rate | MRR | Precision |",
        "|---|---|---|---|",
    ]
    for key, name in _MEMORY_NAMES.items():
        if f"{key}.hit_rate" in s:
            lines.append(f"| {name} | {_pct(s.get(f'{key}.hit_rate'))} | {_num(s.get(f'{key}.mrr'))} | "
                         f"{_pct(s.get(f'{key}.precision'))} |")
    lines.append(f"| **All questions** | **{_pct(s.get('overall.hit_rate'))}** | **{_num(s.get('overall.mrr'))}** | "
                 f"**{_pct(s.get('overall.precision'))}** |")
    lines.append(f"| **Held out** (never used to choose a setting) | **{_pct(s.get('heldout.overall.hit_rate'))}** | "
                 f"**{_num(s.get('heldout.overall.mrr'))}** | **{_pct(s.get('heldout.overall.precision'))}** |")
    target = (data.get("targets") or {}).get("abstain.hit_rate")
    lines += [
        "",
        f"Questions it has no memory for are answered with nothing in {_pct(s.get('abstain.hit_rate'))} of cases "
        f"({_pct(s.get('heldout.abstain.hit_rate'))} held out); the goal is {_pct(target)}: the weakest "
        "number on this page.",
        "",
    ]
    ex = (data.get("extraction") or {}).get("scores") or {}
    if ex:
        lines += [
            "### Learning facts from a conversation {#memory-facts}",
            "",
            "Turns in English, Arabic and both, with the facts a person would expect remembered. **Quick pass**: "
            "Kazma's own rules, on every turn. **Model pass**: the model reading the turn afterwards (recorded "
            "replies). **Recall**: facts found. **Precision**: facts found that are right. **Quiet**: turns with "
            "nothing to learn that taught nothing.",
            "",
            "| Pass | Language | Recall | Precision | Quiet |",
            "|---|---|---|---|---|",
        ]
        for kind, label in (("heuristic", "Quick pass"), ("llm", "Model pass")):
            for lang, lang_label in (("en", "English"), ("ar", "Arabic"), ("mixed", "Mixed")):
                if f"{kind}.{lang}.recall" in ex:
                    lines.append(f"| {label} | {lang_label} | {_pct(ex[f'{kind}.{lang}.recall'])} | "
                                 f"{_pct(ex[f'{kind}.{lang}.precision'])} | {_pct(ex[f'{kind}.{lang}.quiet'])} |")
        lines.append("")
    pg = data.get("postgres_primary") or {}
    if pg:
        lines += [
            f"With Postgres as the memory store, the same questions score {_pct(pg.get('overall.hit_rate'))} hit "
            f"rate, {_num(pg.get('overall.mrr'))} MRR and {_pct(pg.get('overall.precision'))} precision.",
            "",
        ]
    lines += ["Reproduce: `python scripts/memory_bench.py run` (add `--real` to use the real model), and "
              "`python -m pytest tests/test_memory_benchmark.py`.", ""]
    return lines


def _knowledge(data: dict) -> list[str]:
    s = data["scores"]
    lines = [
        "## Knowledge Library: does search find the right page? {#knowledge}",
        "",
        f"Dataset version {data['dataset_version']}: English, Arabic and mixed pages published into a private "
        "library the product's way, then searched the way the agent searches (**tool**) and the way a chat turn "
        "is given library text (**turn**), with meaning search and with keywords alone.",
        "",
        "| Question | Tool: hit rate | Tool: MRR | Turn: hit rate | Turn: precision |",
        "|---|---|---|---|---|",
    ]
    for key, name in _KNOWLEDGE_NAMES.items():
        if f"hybrid.explicit.{key}.hit_rate" in s:
            lines.append(f"| {name} | {_pct(s.get(f'hybrid.explicit.{key}.hit_rate'))} | "
                         f"{_num(s.get(f'hybrid.explicit.{key}.mrr'))} | {_pct(s.get(f'hybrid.inject.{key}.hit_rate'))} | "
                         f"{_pct(s.get(f'hybrid.inject.{key}.precision'))} |")
    lines += [
        "",
        "| All questions | Hit rate | MRR | Precision | Nothing shown when nothing answers |",
        "|---|---|---|---|---|",
    ]
    for prefix, label in (("hybrid.explicit", "Tool, meaning search"), ("hybrid.inject", "Turn, meaning search"),
                          ("keyword.explicit", "Tool, keywords only"), ("keyword.inject", "Turn, keywords only"),
                          ("hybrid.heldout.explicit", "Tool, held out"), ("hybrid.heldout.inject", "Turn, held out")):
        if f"{prefix}.overall.hit_rate" in s:
            lines.append(f"| {label} | {_pct(s.get(f'{prefix}.overall.hit_rate'))} | {_num(s.get(f'{prefix}.overall.mrr'))} | "
                         f"{_pct(s.get(f'{prefix}.overall.precision'))} | {_pct(s.get(f'{prefix}.overall.abstention'))} |")
    lines += ["", "Reproduce: `python scripts/knowledge_bench.py run` (`--real` for the real model, `--chroma` for "
              "the real vector store), and `python -m pytest tests/test_knowledge_benchmark.py`.", ""]
    return lines


def _fidelity_row(case: str, floors: dict, qualified: bool) -> str:
    cells = []
    for metric in ("token_recall", "number_recall", "punctuation", "table_cell_recall", "cer", "wer"):
        value = floors.get(metric)
        if value is None:
            cells.append("–")
        elif metric in _LOWER_IS_BETTER:
            cells.append(f"≤ {_pct(value)}")
        else:
            cells.append(f"≥ {_pct(value)}")
    return f"| `{case}` | {'yes' if qualified else 'no'} | " + " | ".join(cells) + " |"


def _fidelity(data: dict) -> list[str]:
    header = ("| Document | Qualified | Words found | Numbers exact | Punctuation | Table cells | Character errors | "
              "Word errors |")
    rule = "|---|---|---|---|---|---|---|---|"
    lines = [
        "## Reading documents, in Arabic and English {#documents}",
        "",
        f"Corpus version {data['corpus_version']}: documents Kazma builds itself (Word, PDF, HTML; scanned pages "
        "for OCR) from a known text, read back by the real parser, measured word by word. The numbers are the "
        "**floors** every change must reach (the measurement, less a small margin for OCR); a case is "
        "**qualified** when it meets the release targets. Measured on: " + data.get("measured_on", "") + ".",
        "",
        "### Text documents {#documents-text}",
        "",
        header,
        rule,
    ]
    qualified = set(data.get("qualified") or [])
    for case, floors in sorted((data.get("thresholds") or {}).items()):
        lines.append(_fidelity_row(case, floors, case in qualified))
    engines = data.get("ocr_engines") or {}
    for engine, label in (("neural", "Scanned pages, Kazma's neural OCR (the default)"),
                          ("tesseract", "Scanned pages, Tesseract alone")):
        block = engines.get(engine) if isinstance(engines, dict) else None
        if not block:
            continue
        lines += ["", f"### {label} {{#documents-ocr-{engine}}}", "", header, rule]
        q = set(block.get("qualified") or [])
        for case, floors in sorted((block.get("thresholds") or {}).items()):
            lines.append(_fidelity_row(case, floors, case in q))
        reasons = block.get("notes") or {}
        for case, why in sorted(reasons.items()):
            lines.append(f"\n- `{case}` is not qualified: {why}")
    lines += ["", "Reproduce: `python scripts/document_fidelity.py`, and "
              "`python -m pytest tests/test_document_fidelity.py`.", ""]
    return lines


def render() -> str:
    memory = json.loads(MEMORY.read_text(encoding="utf-8"))
    knowledge = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    fidelity = json.loads(FIDELITY.read_text(encoding="utf-8"))
    lines = [
        "---",
        "title: Benchmarks",
        "---",
        "",
        "# Benchmarks",
        "",
        "> This page is published at [kazma.ai/docs/benchmarks/](https://kazma.ai/docs/benchmarks/) "
        "([Arabic](https://kazma.ai/ar/docs/benchmarks/)).",
        "",
        "How well Kazma remembers, finds and reads, measured on versioned datasets kept in its repository and "
        "held by its tests: a change that makes a number worse fails the build. This page is written from those "
        "results (`scripts/benchmarks_page.py`), so it says what the code does today. Every dataset, script and "
        "result file is in the repository; the commands under each table reproduce it.",
        "",
        "The datasets are Kazma's own, built for these questions in English, Arabic and both, with questions "
        "held out from tuning. They are not a public leaderboard's: compare runs of Kazma with each other, not "
        "with numbers measured elsewhere.",
        "",
    ]
    lines += _memory(memory) + _knowledge(knowledge) + _fidelity(fidelity)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write docs/docs/reference/benchmarks.md from the results.")
    parser.add_argument("--check", action="store_true", help="exit 1 when the page is not what the results say")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{PAGE.relative_to(ROOT)} is stale: run scripts/benchmarks_page.py")
            return 1
        print("the benchmarks page matches the results")
        return 0
    PAGE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
