"""Audit evidence (docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md, sections 3.1
to 3.3): what the recorded replies say about the extraction benchmark's
deep-pass scores. No model call: every reply read here was recorded before.

It reads three sets of replies of the same model (deepseek-flash) to the same
30 turns:

* the locked recording, ``tests/fixtures/memory_bench/extraction_llm_2026-10-15.json``
  (one asking a turn, 2026-10-15);
* six turns asked again with the same message, and
* all 30 turns asked with two "who speaks" lines appended,

the last two from the Spaces R3 study's evidence
(``docs/audits/evidence/spaces_r3/deep_replies.json``, commit 2ceb89b7f).
Every reply goes through the product's own parser and sanitizer and the
benchmark's own matcher.

    python extraction_replies.py [--r3 FOLDER]

``--r3`` is the folder holding the R3 study's ``deep_replies.json``,
``speakers.json`` and ``study.py`` (default: its place in this repository).
Writes ``extraction_replies.json`` beside this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LOCKED = ROOT / "tests" / "fixtures" / "memory_bench" / "extraction_llm_2026-10-15.json"
LANGS = ("en", "ar", "mixed")


class _OneReply:
    """A model client that answers with one recorded reply."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def chat(self, messages: list[dict[str, Any]], **_kw: Any) -> str:
        return self._reply


def parse(reply: str, user_text: str) -> list[dict[str, Any]]:
    """A recorded reply as the product reads it."""
    from kazma_core.memory.belief_extractor import _llm_extract, _sanitize_belief

    registry = type("_Registry", (), {"get_client": lambda self: _OneReply(reply)})()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        beliefs, _down = asyncio.run(_llm_extract(user_text, ""))
    return [b for b in (_sanitize_belief(x) for x in beliefs or []) if b]


def outcome(facts: list[Any], got: list[dict[str, Any]]) -> dict[str, Any]:
    """One turn, one asking, as the benchmark counts it."""
    from kazma_core.memory.benchmark import _fact_matches

    found = [any(_fact_matches(alts, g) for g in got) for alts in facts]
    right = [any(_fact_matches(alts, g) for alts in facts) for g in got]
    return {"gold": len(facts), "found": sum(found), "kept": len(got), "right": sum(right), "right_flags": right}


def misses(facts: list[Any], got: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every gold fact the benchmark counts as not found: was its value kept
    anyway, under a predicate name (or a direction) the gold list lacks?"""
    from kazma_core.memory.benchmark import _fact_matches, _folded, _slug

    out = []
    for alts in facts:
        if any(_fact_matches(alts, g) for g in got):
            continue
        values = [_folded(a["object"]) for a in alts]
        where = None
        for g in got:
            value, subject = _folded(g.get("object")), _folded(g.get("subject"))
            if not any(v and (v in value or value in v or v in subject) for v in values):
                continue
            same_subject = any(_slug(a["subject"]) == _slug(g.get("subject")) for a in alts)
            where = {"kept_as": f"{g['subject']} {g['predicate']} {g['object']}",
                     "why": "predicate name not listed" if same_subject else "written from the other side"}
            if same_subject:
                break
        out.append({"gold": f"{alts[0]['subject']} {alts[0]['predicates'][0]} {alts[0]['object']}",
                    "value_kept": bool(where), **(where or {"why": "not kept at all"})})
    return out


def shown(got: list[dict[str, Any]]) -> list[str]:
    return [f"{g['subject']} | {g['predicate']} | {g['object']} | conf {g['confidence']}" for g in got]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--r3", type=Path, default=ROOT / "docs" / "audits" / "evidence" / "spaces_r3")
    args = parser.parse_args(argv)

    from kazma_core.memory import benchmark as bench
    from kazma_core.memory.belief_extractor import _CONSERVATIVE_CONF_THRESHOLD, is_filler_turn
    from kazma_core.memory.predicates import _predicate_key

    sys.path.insert(0, str(args.r3))
    import study  # the R3 study's own job list: the exact messages it sent

    turns = json.loads(bench.EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]
    locked = json.loads(LOCKED.read_text(encoding="utf-8"))["replies"]
    r3 = json.loads((args.r3 / "deep_replies.json").read_text(encoding="utf-8"))["replies"]
    cases = json.loads((args.r3 / "speakers.json").read_text(encoding="utf-8"))
    jobs = {(j["set"], j["turn"]["id"]): j for j in study.deep_jobs(cases)}

    rows: dict[str, dict[str, Any]] = {}
    for turn in turns:
        key = bench._text_key(bench._llm_blob(turn["user"]))
        first = parse(locked[key], turn["user"])
        confident = [g for g in first if g["confidence"] >= _CONSERVATIVE_CONF_THRESHOLD]
        row: dict[str, Any] = {
            "lang": turn["lang"], "user": turn["user"], "gold": len(turn["facts"]),
            "the_product_never_sends_it": bool(is_filler_turn(turn["user"])),
            "first": outcome(turn["facts"], first), "first_kept": shown(first),
            "first_misses": misses(turn["facts"], first),
            "first_after_the_confidence_filter": outcome(turn["facts"], confident),
        }
        if key in r3:  # the same message, asked again
            again = parse(r3[key], turn["user"])
            row["again"] = outcome(turn["facts"], again)
            row["again_kept"] = shown(again)
            row["again_misses"] = misses(turn["facts"], again)
        # The same turn with the R3 study's two speaker lines appended.
        job = jobs[("v1_lines", turn["id"])]
        filed = study._filed_among(parse(r3[bench._text_key(job["blob"])], turn["user"]),
                                   job["speaker"], job["others"])
        me = study.self_id("name", job["speaker"])
        facts = [[{**alt, "subject": me if alt["subject"] == "user" else alt["subject"]} for alt in alts]
                 for alts in turn["facts"]]
        row["lines"] = outcome(facts, filed)
        row["lines_kept"] = shown(filed)
        row["lines_misses"] = misses(facts, filed)
        rows[turn["id"]] = row

    def scores(pick: Any) -> dict[str, Any]:
        out = {}
        for lang in LANGS:
            gold = found = kept = right = quiet = quiet_ok = 0
            for row in rows.values():
                if row["lang"] != lang:
                    continue
                o = pick(row)
                gold += o["gold"]
                found += o["found"]
                kept += o["kept"]
                right += o["right"]
                if not row["gold"]:
                    quiet += 1
                    quiet_ok += int(o["kept"] == 0)
            out[lang] = {"gold": gold, "found": found, "kept": kept, "right": right, "quiet_turns": quiet,
                         "recall": round(found / gold, 4), "precision": round(right / kept, 4) if kept else 1.0,
                         "quiet": round(quiet_ok / quiet, 4) if quiet else 1.0}
        return out

    report: dict[str, Any] = {
        "scores": {
            "the_locked_recording": scores(lambda r: r["first"]),
            "six_turns_take_their_second_asking": scores(lambda r: r.get("again") or r["first"]),
            "all_30_with_speaker_lines": scores(lambda r: r["lines"]),
            "the_locked_recording_after_the_products_confidence_filter":
                scores(lambda r: r["first_after_the_confidence_filter"]),
        },
    }

    def differ(other: str) -> dict[str, Any]:
        have = {tid: r for tid, r in rows.items() if other in r}
        return {"turns": len(have),
                "found_differs": sorted(t for t, r in have.items() if r["first"]["found"] != r[other]["found"]),
                "kept_or_right_differs": sorted(
                    t for t, r in have.items()
                    if (r["first"]["kept"], r["first"]["right"]) != (r[other]["kept"], r[other]["right"]))}

    report["two_askings_disagree"] = {"asked_again": differ("again"), "with_speaker_lines": differ("lines")}

    # The spread of one asking, estimated from two askings a turn (the locked
    # one and the one with speaker lines): Var(found) ~ d^2 / 2 a turn.
    spread = {}
    for lang in LANGS:
        sel = [r for r in rows.values() if r["lang"] == lang]
        gold = sum(r["gold"] for r in sel)
        sd = math.sqrt(sum((r["first"]["found"] - r["lines"]["found"]) ** 2 / 2 for r in sel)) / gold
        spread[lang] = {"gold_facts": gold, "one_fact_is": round(1 / gold, 4), "sd_recall_one_asking": round(sd, 4),
                        "sd_of_the_mean_of_k_askings": {str(k): round(sd / math.sqrt(k), 4) for k in (1, 3, 5, 8, 10)}}
    report["spread_of_one_asking_estimate"] = spread

    def miss_table(which: str) -> dict[str, Any]:
        items = [{"turn": tid, **m} for tid, r in rows.items() for m in r.get(f"{which}_misses", [])]
        return {"gold_facts_asked": sum(r["gold"] for r in rows.values() if f"{which}_misses" in r),
                "counted_not_found": len(items),
                "of_those_value_kept": sum(1 for m in items if m["value_kept"]),
                "of_those_not_kept_at_all": sum(1 for m in items if not m["value_kept"]),
                "items": items}

    report["what_the_misses_are"] = {"the_locked_recording": miss_table("first"),
                                     "asked_again": miss_table("again"),
                                     "with_speaker_lines": miss_table("lines")}
    report["kept_and_counted_wrong_in_the_locked_recording"] = [
        {"turn": tid, "user": r["user"], "kept": line}
        for tid, r in rows.items() for line, ok in zip(r["first_kept"], r["first"]["right_flags"]) if not ok]

    def named(lines: list[str]) -> dict[str, str]:
        out = {}
        for line in lines:
            _subject, predicate, value, _conf = [x.strip() for x in line.split(" | ")]
            out[bench._folded(value)] = " ".join(sorted(_predicate_key(predicate)))
        return out

    def name_agreement(other: str) -> dict[str, Any]:
        both = same = 0
        changed = []
        for tid, r in rows.items():
            if other not in r:
                continue
            a, b = named(r["first_kept"]), named(r[other])
            for value in sorted(set(a) & set(b)):
                both += 1
                if a[value] == b[value]:
                    same += 1
                else:
                    changed.append({"turn": tid, "value": value, "first": a[value], "other": b[value]})
        return {"facts_kept_with_the_same_value_in_both": both, "under_the_same_predicate_name": same,
                "changed": changed}

    report["predicate_name_agreement"] = {"asked_again": name_agreement("again_kept"),
                                          "with_speaker_lines": name_agreement("lines_kept")}
    report["confidence_filter"] = {
        "threshold": _CONSERVATIVE_CONF_THRESHOLD,
        "facts_kept": sum(r["first"]["kept"] for r in rows.values()),
        "facts_left_after_the_filter": sum(r["first_after_the_confidence_filter"]["kept"] for r in rows.values())}
    report["turns_the_product_never_sends_to_the_model"] = sorted(
        t for t, r in rows.items() if r["the_product_never_sends_it"])
    for row in rows.values():
        for part in ("first", "again", "lines", "first_after_the_confidence_filter"):
            if part in row:
                row[part].pop("right_flags", None)
    report["turns"] = rows
    out = HERE / "extraction_replies.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    for name, table in report["scores"].items():
        print(name)
        for lang, row in table.items():
            print("   ", lang, {k: row[k] for k in ("gold", "found", "kept", "right", "recall", "precision", "quiet")})
    for name, table in report["what_the_misses_are"].items():
        print(name, {k: v for k, v in table.items() if k != "items"})
    for name, table in report["predicate_name_agreement"].items():
        print(name, {k: v for k, v in table.items() if k != "changed"})
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
