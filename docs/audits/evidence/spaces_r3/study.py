"""Spaces R3 study: who is "I" in a shared space (docs/plans/SPACES_R3_MEMORY_STUDY.md).

A measurement, not product code. It writes a multi-speaker case set
(``speakers.json``) into a PRIVATE database the way the memory benchmark
does (``dual_write.episode_row``, ``mutate_belief(private=True)``), asks every
question through the real ``recall()``, and scores four ways of filing a
member's facts about themself:

``control``   today: every "I" is the one ``user`` entry.
``name``      the member's own entry, its id the slug of their name ("sara").
``name_uv``   ``name``, with the entry's vectors made from "user <predicate>
              <object>", the text today's vectors are made from.
``id``        the member's own entry, an opaque id ("member_<account>").
``id_names``  ``id``, with recall reading the member's name for the id.
``id_uv``     ``id_names``, with the entry's vectors made from "user ...".

and three ways of showing them to the member who asks: ``label`` (other
members' facts are shown under their names), ``self_first`` (a first-person
question that names nobody else is shown the asker's own facts only) and
``named_first`` (``self_first``, and a question that names a member is shown
that member's facts, not the other members').

It also runs dataset v5 of the memory benchmark with its one persona filed
each way (a space with one member), and the extraction benchmark's passes
with every turn tagged with a speaker.

    python docs/audits/evidence/spaces_r3/study.py run      # replays vectors
    python docs/audits/evidence/spaces_r3/study.py record   # loads bge-m3

``record`` embeds the texts no recorded file holds with the real model, at
below-normal CPU priority and four threads at most (the live install shares
this machine), and saves them to ``vectors.npz`` beside this file.

The deep pass (the model's extraction) is replayed from ``deep_replies.json``:
the extraction prompt with the speaker lines of the study's section 2.1, for
the extraction benchmark's turns and for this study's speaker turns; today's
prompt for the speaker turns; and six turns asked once more with today's
prompt. :func:`record_deep` made it, given a model client (60 calls to
deepseek-flash on 2026-11-18); it costs one model call a reply and is not a
command here.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CASES = HERE / "speakers.json"
VECTORS = HERE / "vectors.npz"
RESULTS = HERE / "results.json"
DEEP = HERE / "deep_replies.json"
#: The most model calls one deep-pass recording may make.
MAX_DEEP_CALLS = 60
#: The extraction turns whose result with the speaker lines differed from the
#: existing recording. They were asked once more with today's message, to see
#: how much one and the same message varies from one asking to the next. They
#: were chosen after seeing the first replies.
ASKED_AGAIN = ("e07", "a04", "a06", "m05", "m07", "m08")
V5_VECTORS = ROOT / "tests" / "fixtures" / "memory_bench" / "vectors.npz"
THRESHOLDS = ROOT / "tests" / "fixtures" / "memory_bench" / "thresholds.json"
LLM_REPLIES = ROOT / "tests" / "fixtures" / "memory_bench" / "extraction_llm.json"
TENANT = "bench-speakers"
BASE_TIME = 1780000000.0
VARIANTS = ("control", "name", "name_uv", "id", "id_names", "id_uv")
#: Variants in which recall reads a member's name for the opaque id.
READS_NAMES = ("id_names", "id_uv")
#: The runs whose shown lines are kept in results.json: today, and the design chosen.
SHOWN_FOR = ("control/label", "id_uv/named_first")
#: The persona of dataset v5, as a member of a space of its own.
V5_MEMBER = {"account": "u9c0ffee12ab3", "name": "Sami"}


def _private_install() -> None:
    """Nothing here reads or writes an install: its own empty data folder,
    and the model only from the machine's cache."""
    scratch = tempfile.mkdtemp(prefix="kazma-r3-study-")
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    os.environ["KAZMA_DATA_DIR"] = scratch
    os.environ["KAZMA_USER_HOME"] = scratch
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _below_normal() -> None:
    """Below-normal CPU priority (never a low I/O priority) and few threads."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)
    else:
        os.nice(5)
    try:
        import torch

        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))
    except ImportError:
        pass


# ── Vectors: recorded ones replayed; new texts from the real model on `record` ──


class _Vectors:
    def __init__(self, record: bool) -> None:
        from kazma_core.memory.benchmark import ReplayEmbedder

        self._replays = [ReplayEmbedder(p) for p in (V5_VECTORS, VECTORS) if p.exists()]
        self.model = self._replays[0].model
        self.dim = self._replays[0].dim
        self._record = record
        self._real: Any = None
        self.new: dict[str, list[float]] = {}
        #: Subject prefixes whose facts are embedded as "user ..." (``name_uv``).
        self.as_user: tuple[str, ...] = ()

    def encode(self, text: str) -> list[float]:
        for prefix in self.as_user:
            if text.startswith(prefix):
                text = "user " + text[len(prefix):]
                break
        for replay in self._replays:
            try:
                return replay.encode(text)
            except KeyError:
                continue
        if text in self.new:
            return self.new[text]
        if not self._record:
            raise KeyError(f"no recorded vector for {text[:60]!r}: run `study.py record`")
        if self._real is None:
            _below_normal()
            self._real = _REAL_GET_EMBEDDER()
            if self._real is None:
                raise RuntimeError("no embedder: the rag extra (bge-m3) is not installed")
        vec = [float(x) for x in self._real.encode(text)]
        self.new[text] = vec
        return vec


def _save_merged(new: dict[str, list[float]], model: str) -> int:
    """Write ``vectors.npz``: the vectors already there plus *new*."""
    import numpy as np
    from kazma_core.memory.benchmark import _text_key

    keys: list[str] = []
    vecs: list[Any] = []
    scales: list[float] = []
    if VECTORS.exists():
        old = np.load(VECTORS)
        keys = [str(k) for k in old["keys"]]
        vecs = list(old["vecs"])
        scales = [float(s) for s in old["scale"]]
    have = set(keys)
    for text in sorted(new):
        key = _text_key(text)
        if key in have:
            continue
        vec = np.asarray(new[text], dtype=np.float32)
        scale = max(float(np.abs(vec).max()), 1e-12) / 127.0
        keys.append(key)
        vecs.append(np.clip(np.rint(vec / scale), -127, 127).astype(np.int8))
        scales.append(scale)
        have.add(key)
    np.savez_compressed(VECTORS, keys=np.asarray(keys), vecs=np.asarray(vecs, dtype=np.int8),
                        scale=np.asarray(scales, dtype=np.float32), model=np.asarray(model))
    return len(keys)


_REAL_GET_EMBEDDER: Any = None


def _patched(vectors: _Vectors):
    return (
        mock.patch("kazma_core.memory.embedder.get_embedder", lambda: vectors),
        mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: vectors.model),
    )


# ── Filing: which subject a fact about a member is stored under ────────────


def self_id(variant: str, member: dict[str, Any]) -> str:
    if variant == "control":
        return "user"
    if variant in ("name", "name_uv"):
        from kazma_core.memory.entity_resolution import slug

        return slug(member["name"])
    return f"member_{member['account']}"


def stored_subject(variant: str, fact: dict[str, Any], members: dict[str, Any]) -> str:
    """Where *fact* is filed. Today (``control``) a statement about another
    member is filed under the name it was written with; the member-entry
    variants resolve that name onto the member's own entry."""
    about = fact["about"]
    if not about.startswith("@"):
        return about
    member = members[about[1:]]
    if variant == "control" and fact["by"] != about[1:]:
        from kazma_core.memory.entity_resolution import slug

        return slug(fact.get("written_as") or member["name"])
    return self_id(variant, member)


# ── What the model is shown ────────────────────────────────────────────────


def shown_fact(variant: str, row: Any, asker: str, members: dict[str, Any]) -> str:
    """A fact's line in the recall block. Today: its stored subject. The
    member-entry variants: ``user`` for the asker's own entry (as today), the
    member's name for another member's."""
    subject = str(row["subject"])
    if variant != "control":
        for key, member in members.items():
            if subject == self_id(variant, member):
                subject = "user" if key == asker else member["name"]
    return f"{subject.replace('_', ' ')} {str(row['predicate']).replace('_', ' ')} {row['object']}".strip()


def shown_episode(variant: str, row: Any, asker: str, members: dict[str, Any]) -> str:
    from kazma_core.memory.episode_text import display_text

    text = display_text(row["user_text"], row["assistant_text"], row["summary_text"])
    by = json.loads(row["metadata_json"] or "{}").get("said_by")
    if variant != "control" and by and by != members[asker]["account"] and text.startswith("User: "):
        name = next(m["name"] for m in members.values() if m["account"] == by)
        text = f"{name}: " + text[len("User: "):]
    return text


# ── First person (the `self_first` mode) ───────────────────────────────────

_EN_FIRST = re.compile(r"\b(?:i|i'm|i've|i'd|my|me|mine|myself)\b", re.IGNORECASE)
_AR_FIRST = frozenset({"انا", "اني", "لي", "عندي", "معي", "مني", "مالي", "مالتي", "حقي", "حقتي"})
_AR_NOT_FIRST = frozenset({"في", "الي", "اللي", "علي", "هي", "شي", "يعني", "حتي", "متي", "اي", "ماهي"})


def _folded_words(text: str) -> list[str]:
    from kazma_core.documents.arabic import fold_for_search

    return re.findall(r"[^\W\d_]+", fold_for_search(text).lower())


def members_named(question: str, members: dict[str, Any]) -> set[str]:
    """The members a question names, by name or by a name they are called."""
    words = set(_folded_words(question))
    return {key for key, member in members.items()
            if any(set(_folded_words(name)) <= words for name in (member["name"], *member["aliases"]))}


def first_person(question: str, members: dict[str, Any]) -> bool:
    """A crude reading of "the asker means themself": an English first-person
    word, an Arabic one, an Arabic word ending in the possessive ya, or an
    Arabic verb with the first-person prefix."""
    if _EN_FIRST.search(question):
        return True
    member_words = {w for m in members.values() for n in (m["name"], *m["aliases"]) for w in _folded_words(n)}
    for word in _folded_words(question):
        if not ("ء" <= word[0] <= "ي") or word in member_words:
            continue
        if word in _AR_FIRST:
            return True
        if word in _AR_NOT_FIRST or word.startswith("ال"):
            continue
        if len(word) >= 4 and (word.endswith("ي") or word.startswith("ا")):
            return True
    return False


# ── The multi-speaker run ──────────────────────────────────────────────────


def _seed(conn: sqlite3.Connection, cases: dict[str, Any], variant: str) -> dict[str, Any]:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob

    members = cases["members"]
    refs: dict[str, str] = {}
    for session in cases["sessions"]:
        for i, turn in enumerate(session["turns"], 1):
            row = episode_row(
                session_id=f"sp-{session['id']}", turn_number=i, user_text=turn["user"],
                assistant_text=turn["assistant"], tenant_id=TENANT, source="benchmark",
                created_at=BASE_TIME + session["day"] * 86400 + i * 60,
                metadata={"said_by": members[turn["by"]]["account"]},
            )
            conn.execute(
                "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
                "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json, "
                "embedding_model_version, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["tenant_id"], row["session_id"], row["turn_number"], row["user_text"],
                 row["assistant_text"], row["summary_text"], row["tier"], row["importance"],
                 row["created_at"], json.dumps(row["meta"], ensure_ascii=False),
                 row["embedding_model_version"], encode_text_to_blob(row["embed_text"])),
            )
            refs[f"{session['id']}#{i}"] = row["id"]
    conn.commit()
    refused: list[str] = []
    for fact in cases["facts"]:
        result = mutate_belief(
            conn, stored_subject(variant, fact, members), fact["predicate"], fact["object"],
            predicate_type=fact["predicate_type"], confidence=0.9, importance=4,
            extraction_method=fact["method"], tenant_id=TENANT,
            source_session=f"sp-{fact['session']}", source_turn=fact["turn"],
            now=BASE_TIME + fact["day"] * 86400 + fact["turn"] * 60 + 30, private=True,
        )
        if result.get("belief_id") and result.get("action") not in ("noop", "blocked", "rejected"):
            refs[fact["id"]] = str(result["belief_id"])
        else:
            refused.append(f"{fact['id']}:{result.get('reason') or result.get('action')}")
    current = {
        str(r[0]) for r in conn.execute(
            "SELECT id FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL",
            (TENANT,),
        )
    }
    # A fact the member never took back that is no longer current was overwritten.
    lost = [f["id"] for f in cases["facts"]
            if f["id"] in refs and refs[f["id"]] not in current and not f.get("later") and not f.get("guess")]
    guesses_kept = [f["id"] for f in cases["facts"] if f.get("guess") and refs.get(f["id"]) in current]
    return {"refs": refs, "lost": lost, "refused": refused, "guesses_kept": guesses_kept, "current": current}


def run_speakers(cases: dict[str, Any], variant: str, mode: str) -> dict[str, Any]:
    from kazma_core.memory import recall as recall_module
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    members = cases["members"]
    facts = {f["id"]: f for f in cases["facts"]}
    fd, path = tempfile.mkstemp(prefix="kazma-r3-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    names = {self_id("id", m): m["name"].lower() for m in members.values()}

    def read_names(row: Any) -> str:
        sub = str(recall_module._field(row, "subject") or "")
        sub = names.get(sub, sub.replace("_", " "))
        pred = str(recall_module._field(row, "predicate") or "").replace("_", " ")
        return f"{sub} {pred} {recall_module._field(row, 'object') or ''}".strip()

    patch = (mock.patch.object(recall_module, "_format_belief_text", read_names)
             if variant in READS_NAMES else contextlib.nullcontext())
    try:
        ensure_primary_schema(conn)
        seeded = _seed(conn, cases, variant)
        refs = seeded["refs"]
        owner_of = {refs[f["id"]]: f["about"][1:] for f in cases["facts"]
                    if f["id"] in refs and f["about"].startswith("@")}
        author_of = {refs[f"{s['id']}#{i}"]: t["by"]
                     for s in cases["sessions"] for i, t in enumerate(s["turns"], 1)}
        results: list[dict[str, Any]] = []
        with patch:
            for q in cases["questions"]:
                asker = q["by"]
                res = recall_module.recall(q["text"], conn=conn, tenant_id=TENANT, limit=5, local_only=True)
                beliefs = list(res.beliefs[:5])
                is_first = first_person(q["text"], members)
                named = members_named(q["text"], members) - {asker}
                if mode != "label" and variant != "control":
                    if named and mode == "named_first":
                        beliefs = [h for h in beliefs if owner_of.get(h.id) in (None, *named)]
                    elif is_first and not named:
                        beliefs = [h for h in beliefs if owner_of.get(h.id, asker) == asker]
                injected = [h.id for h in beliefs] + [h.id for h in res.episodes[:5]]
                lines, wrong = [], 0
                for item in injected:
                    if item in author_of:
                        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (item,)).fetchone()
                        line = shown_episode(variant, row, asker, members)
                        wrong += int(author_of[item] != asker and line.startswith("User: "))
                    else:
                        row = conn.execute("SELECT * FROM beliefs WHERE id = ?", (item,)).fetchone()
                        line = shown_fact(variant, row, asker, members)
                        wrong += int(owner_of.get(item, asker) != asker and line.startswith("user "))
                    lines.append(line)
                pieces = [[refs[a] for a in piece.split("|") if a in refs] for piece in q["gold"]]
                answers = {a for piece in pieces for a in piece}
                gold_facts = {refs[a] for piece in q["gold"] for a in piece.split("|") if a in facts and a in refs}
                rank = {item: i for i, item in reversed(list(enumerate(injected, 1)))}
                first_fact = next((i for i in injected if i not in author_of), None)
                out: dict[str, Any] = {
                    "id": q["id"], "lang": q["lang"], "category": q["category"], "by": asker,
                    "injected": len(injected), "misattributed": wrong, "first_person": is_first,
                    "shown": lines,
                }
                if not q["gold"]:
                    out["hit"] = wrong == 0
                else:
                    ranks = [rank[a] for a in answers if a in rank]
                    out["hit"] = all(any(a in rank for a in piece) for piece in pieces)
                    out["rr"] = 1.0 / min(ranks) if ranks else 0.0
                    out["precision"] = (sum(1 for i in injected if i in answers) / len(injected)
                                        if injected else 0.0)
                    if any(a in facts for piece in q["gold"] for a in piece.split("|")):
                        out["fact_hit"] = bool(gold_facts & set(injected))
                        # The first fact shown is someone else's, shown as the asker's.
                        out["wrong_first"] = bool(
                            first_fact and owner_of.get(first_fact, asker) != asker
                            and shown_fact(variant, conn.execute(
                                "SELECT * FROM beliefs WHERE id = ?", (first_fact,)).fetchone(),
                                asker, members).startswith("user ")
                        )
                results.append(out)
        return {"variant": variant, "mode": mode, "lost": seeded["lost"], "refused": seeded["refused"],
                "guesses_kept": seeded["guesses_kept"], "results": results, **_summary(results)}
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    def part(rows: list[dict[str, Any]]) -> dict[str, Any]:
        answerable = [r for r in rows if "rr" in r]
        with_fact = [r for r in rows if "fact_hit" in r]
        return {
            "n": len(rows),
            "hit_rate": round(sum(bool(r["hit"]) for r in rows) / len(rows), 4),
            "fact_hit_rate": round(sum(r["fact_hit"] for r in with_fact) / len(with_fact), 4) if with_fact else None,
            "mrr": round(sum(r["rr"] for r in answerable) / len(answerable), 4) if answerable else None,
            "precision": round(sum(r["precision"] for r in answerable) / len(answerable), 4)
            if answerable else None,
            "misattributed_items": sum(r["misattributed"] for r in rows),
            "questions_with_misattribution": sum(1 for r in rows if r["misattributed"]),
            "wrong_first_fact": sum(1 for r in rows if r.get("wrong_first")),
        }

    out = {"overall": part(results)}
    for field in ("lang", "category"):
        out[f"by_{field}"] = {k: part([r for r in results if r[field] == k])
                              for k in sorted({r[field] for r in results})}
    return out


# ── Dataset v5 with its one persona filed each way ─────────────────────────


def run_v5(variant: str) -> dict[str, Any]:
    from kazma_core.memory import recall as recall_module
    from kazma_core.memory.benchmark import gated_scores, load_dataset, run_benchmark

    data = load_dataset()
    if variant != "control":
        subject = self_id(variant, V5_MEMBER)
        for fact in data["facts"]:
            if fact["subject"] == "user":
                fact["subject"] = subject
    name = V5_MEMBER["name"].lower()
    own = self_id("id", V5_MEMBER)

    def read_names(row: Any) -> str:
        sub = str(recall_module._field(row, "subject") or "")
        sub = name if sub == own else sub.replace("_", " ")
        pred = str(recall_module._field(row, "predicate") or "").replace("_", " ")
        return f"{sub} {pred} {recall_module._field(row, 'object') or ''}".strip()

    if variant in READS_NAMES:
        with mock.patch.object(recall_module, "_format_belief_text", read_names):
            report = run_benchmark(data)
    else:
        report = run_benchmark(data)
    return gated_scores(report)


# ── Extraction, every turn tagged with its speaker ─────────────────────────


def _tagged(turns: list[dict[str, Any]], members: dict[str, Any]) -> list[dict[str, Any]]:
    keys = list(members)
    return [{**t, "by": t.get("by") or keys[i % len(keys)]} for i, t in enumerate(turns)]


def _filed(beliefs: list[dict[str, Any]], speaker: dict[str, Any]) -> list[dict[str, Any]]:
    """The prototype's one write-side rule: ``user`` is the speaker's entry."""
    return [{**b, "subject": self_id("name", speaker) if b.get("subject") == "user" else b.get("subject")}
            for b in beliefs]


def run_extraction(cases: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    from kazma_core.memory.belief_extractor import _llm_extract, _sanitize_belief, extract_beliefs_heuristic
    from kazma_core.memory.benchmark import (
        EXTRACTION_PATH,
        _ReplayClient,
        _score_extraction,
        extraction_scores,
    )
    from kazma_core.memory.entity_resolution import slug

    members = cases["members"]
    v1 = json.loads(EXTRACTION_PATH.read_text(encoding="utf-8"))
    turns = _tagged(v1["turns"], members)

    def gold_for(turn: dict[str, Any]) -> dict[str, Any]:
        me = self_id("name", members[turn["by"]])
        return {**turn, "facts": [[{**alt, "subject": me if alt["subject"] == "user" else alt["subject"]}
                                   for alt in alts] for alts in turn["facts"]]}

    plain = {t["id"]: extract_beliefs_heuristic(t["user"]) for t in turns}
    tagged_turns = [gold_for(t) for t in turns]
    tagged = {t["id"]: _filed(plain[t["id"]], members[t["by"]]) for t in turns}
    out: dict[str, Any] = {
        "v1_turns": len(turns),
        "quick_untagged": extraction_scores({"heuristic": _score_extraction(v1["turns"], plain)}),
        "quick_tagged": extraction_scores({"heuristic": _score_extraction(tagged_turns, tagged)}),
        "quick_first_person_facts": sum(1 for got in plain.values() for b in got if b["subject"] == "user"),
        "quick_other_subject_facts": sum(1 for got in plain.values() for b in got if b["subject"] != "user"),
    }
    replies = json.loads(LLM_REPLIES.read_text(encoding="utf-8"))
    registry = type("_Registry", (), {"get_client": lambda self: _ReplayClient(replies["replies"])})()

    async def deep() -> dict[str, list[dict[str, Any]]]:
        got = {}
        for t in turns:
            beliefs, _down = await _llm_extract(t["user"], "")
            got[t["id"]] = [b for b in (_sanitize_belief(x) for x in beliefs or []) if b]
        return got

    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        deep_plain = asyncio.run(deep())
    deep_tagged = {t["id"]: _filed(deep_plain[t["id"]], members[t["by"]]) for t in turns}
    out.update({
        "deep_model": replies.get("model"),
        "deep_untagged": extraction_scores({"llm": _score_extraction(v1["turns"], deep_plain)}),
        "deep_tagged": extraction_scores({"llm": _score_extraction(tagged_turns, deep_tagged)}),
        "deep_first_person_facts": sum(1 for got in deep_plain.values() for b in got if b["subject"] == "user"),
        "deep_other_subject_facts": sum(1 for got in deep_plain.values() for b in got if b["subject"] != "user"),
    })
    # The speaker turns of this study: the quick pass only (the deep pass was
    # not recorded for them: it costs a model call a turn).
    own = cases["extraction"]

    def subject_of(about: str) -> str:
        return self_id("name", members[about[1:]]) if about.startswith("@") else slug(about)

    speaker_turns = [{**t, "facts": [[{"subject": subject_of(alt["about"]), "predicates": alt["predicates"],
                                       "object": alt["object"]} for alt in alts] for alts in t["facts"]]}
                     for t in own]
    got = {t["id"]: _filed(extract_beliefs_heuristic(t["user"]), members[t["by"]]) for t in own}
    leaks = [t["id"] for t in own
             if not t["own"] and any(b["subject"] == self_id("name", members[t["by"]]) for b in got[t["id"]])]
    out["speaker_turns"] = {
        "turns": len(own),
        "quick": _score_extraction(speaker_turns, got),
        "filed_under_the_wrong_member": leaks,
        "kept": {t["id"]: [f"{b['subject']} {b['predicate']} {b['object']}" for b in got[t["id"]]] for t in own},
    }
    return out


# ── The deep pass, told who speaks ─────────────────────────────────────────


def speaker_lines(speaker: dict[str, Any], others: list[dict[str, Any]]) -> str:
    """What the extraction message gains in a shared space (study 2.1): who
    wrote it, and who else is in the space. The system prompt is unchanged."""
    def called(member: dict[str, Any]) -> str:
        also = ", ".join(member.get("aliases") or [])
        return f"{member['name']} ({also})" if also else member["name"]

    return (
        f"\n\nSpeaker: {speaker['name']} (the \"user\" of this message).\n"
        f"Other members of this space: {', '.join(called(m) for m in others)}. "
        "A fact about another member takes that member's name as its subject."
    )


def _people_for(turn: dict[str, Any], index: int, members: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(speaker, other members)`` of an extraction turn. A turn of this
    study names its speaker. A turn of the extraction benchmark that states
    its writer's name is spoken by a member of that name (a speaker line
    naming someone else would contradict the message); the others rotate."""
    if turn.get("by"):
        key = turn["by"]
        return members[key], [m for k, m in members.items() if k != key]
    stated = next(
        (alt["object"] for alts in turn["facts"] for alt in alts
         if alt["subject"] == "user" and {"name", "name_is"} & set(alt["predicates"])), None)
    if stated:
        speaker = {"account": f"uheld{index:02d}", "name": stated, "aliases": []}
        return speaker, [m for m in members.values() if _folded_words(m["name"]) != _folded_words(stated)]
    keys = list(members)
    key = keys[index % len(keys)]
    return members[key], [m for k, m in members.items() if k != key]


def deep_jobs(cases: dict[str, Any]) -> list[dict[str, Any]]:
    """Every deep-pass message of the study: the 30 turns of the extraction
    benchmark with the speaker lines, and the speaker turns with and without."""
    from kazma_core.memory.benchmark import EXTRACTION_PATH, _llm_blob

    members = cases["members"]
    jobs: list[dict[str, Any]] = []
    v1 = json.loads(EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]
    for i, turn in enumerate(v1):
        speaker, others = _people_for(turn, i, members)
        jobs.append({"set": "v1_lines", "turn": turn, "speaker": speaker, "others": others,
                     "blob": _llm_blob(turn["user"]) + speaker_lines(speaker, others)})
    for i, turn in enumerate(v1):
        if turn["id"] in ASKED_AGAIN:
            speaker, others = _people_for(turn, i, members)
            jobs.append({"set": "v1_again", "turn": turn, "speaker": speaker, "others": others,
                         "blob": _llm_blob(turn["user"])})
    for i, turn in enumerate(cases["extraction"]):
        speaker, others = _people_for(turn, i, members)
        jobs.append({"set": "speaker_plain", "turn": turn, "speaker": speaker, "others": others,
                     "blob": _llm_blob(turn["user"])})
        jobs.append({"set": "speaker_lines", "turn": turn, "speaker": speaker, "others": others,
                     "blob": _llm_blob(turn["user"]) + speaker_lines(speaker, others)})
    return jobs


async def record_deep(client: Any, cases: dict[str, Any], *, limit: int = MAX_DEEP_CALLS) -> dict[str, Any]:
    """Ask *client* the extraction prompt for every deep-pass message not yet
    recorded, at most *limit* calls, and keep the raw replies in
    ``deep_replies.json`` (saved after every reply: a reply paid for is never
    lost). A call that fails stops the recording; nothing is retried."""
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM
    from kazma_core.memory.benchmark import _text_key

    kept = json.loads(DEEP.read_text(encoding="utf-8")) if DEEP.exists() else {
        "model": "", "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "cost_usd": 0.0, "cost_known": True, "replies": {}}
    made = 0
    for job in deep_jobs(cases):
        key = _text_key(job["blob"])
        if key in kept["replies"]:
            continue
        if made >= limit:
            raise RuntimeError(f"the deep-pass recording stopped at its limit of {limit} calls")
        made += 1
        raw = await client.chat([{"role": "system", "content": _EXTRACT_SYSTEM},
                                 {"role": "user", "content": job["blob"]}])
        usage = getattr(raw, "usage", None) or {}
        kept["replies"][key] = raw.content if hasattr(raw, "content") else str(raw or "")
        kept["model"] = str(getattr(raw, "model", "") or kept["model"])
        kept["calls"] += 1
        kept["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        kept["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        kept["cost_usd"] = round(kept["cost_usd"] + float(getattr(raw, "cost_usd", 0.0) or 0.0), 6)
        kept["cost_known"] = bool(kept["cost_known"] and getattr(raw, "cost_known", False))
        DEEP.write_text(json.dumps(kept, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return {"made": made, **{k: v for k, v in kept.items() if k != "replies"}}


class _OneReply:
    """A model client that answers with one recorded reply."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def chat(self, messages: list[dict[str, Any]], **_kw: Any) -> str:
        return self._reply


def _filed_among(beliefs: list[dict[str, Any]], speaker: dict[str, Any],
                 others: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where the deep pass's facts are filed in a shared space: ``user`` is
    the speaker's entry, and a subject that is a member's name, or a name
    they are called by, is that member's entry (the alias match)."""
    out = []
    for belief in beliefs:
        subject = str(belief.get("subject") or "")
        named = _member_named(subject, (speaker, *others))
        if subject == "user":
            subject = self_id("name", speaker)
        elif named is not None:
            subject = self_id("name", named)
        out.append({**belief, "subject": subject})
    return out


def _member_named(subject: str, people: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    """The member whose name, or a name they are called by, *subject* is."""
    words = _folded_words(subject.replace("_", " "))
    for member in people:
        if any(words == _folded_words(name) for name in (member["name"], *member.get("aliases", []))):
            return member
    return None


def run_deep(cases: dict[str, Any]) -> dict[str, Any]:
    """Replay ``deep_replies.json`` through the product's own reply parser
    and sanitizer, file the facts, and score them."""
    import asyncio

    from kazma_core.memory.belief_extractor import _llm_extract, _sanitize_belief
    from kazma_core.memory.benchmark import _folded, _score_extraction, _text_key, extraction_scores
    from kazma_core.memory.entity_resolution import slug

    if not DEEP.exists():
        return {"recorded": False}
    kept = json.loads(DEEP.read_text(encoding="utf-8"))
    members = cases["members"]

    async def parsed(reply: str, user_text: str) -> list[dict[str, Any]]:
        registry = type("_Registry", (), {"get_client": lambda self: _OneReply(reply)})()
        with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
            beliefs, _down = await _llm_extract(user_text, "")
        return [b for b in (_sanitize_belief(x) for x in beliefs or []) if b]

    got: dict[str, dict[str, list[dict[str, Any]]]] = {}
    gold: dict[str, list[dict[str, Any]]] = {}
    raw_subjects: dict[str, dict[str, int]] = {}
    for job in deep_jobs(cases):
        reply = kept["replies"].get(_text_key(job["blob"]))
        if reply is None:
            raise KeyError(f"no recorded deep-pass reply for {job['set']} {job['turn']['id']}")
        beliefs = asyncio.run(parsed(reply, job["turn"]["user"]))
        counts = raw_subjects.setdefault(job["set"], {"user": 0, "a_members_name": 0, "other": 0})
        for belief in beliefs:
            named = _member_named(str(belief["subject"]), (job["speaker"], *job["others"]))
            counts["user" if belief["subject"] == "user" else "a_members_name" if named else "other"] += 1
        got.setdefault(job["set"], {})[job["turn"]["id"]] = _filed_among(beliefs, job["speaker"], job["others"])
        me = self_id("name", job["speaker"])
        if not job["set"].startswith("v1_"):  # this study's turns name whom a fact is about
            facts = [[{"subject": self_id("name", members[alt["about"][1:]]) if alt["about"].startswith("@")
                       else slug(alt["about"]), "predicates": alt["predicates"], "object": alt["object"]}
                      for alt in alts] for alts in job["turn"]["facts"]]
        else:
            facts = [[{**alt, "subject": me if alt["subject"] == "user" else alt["subject"]} for alt in alts]
                     for alts in job["turn"]["facts"]]
        gold.setdefault(job["set"], []).append({**job["turn"], "facts": facts, "_speaker": me})

    def misfiled(name: str) -> list[str]:
        """Turns about someone else where a gold fact's value was filed under the speaker."""
        out = []
        for turn in gold[name]:
            if turn.get("own", True):
                continue
            values = [_folded(alt["object"]) for alts in turn["facts"] for alt in alts]
            for belief in got[name][turn["id"]]:
                value = _folded(belief["object"])
                if belief["subject"] == turn["_speaker"] and any(v and (v in value or value in v) for v in values):
                    out.append(turn["id"])
                    break
        return out

    report: dict[str, Any] = {
        "recorded": True, "model": kept["model"], "calls": kept["calls"],
        "prompt_tokens": kept["prompt_tokens"], "completion_tokens": kept["completion_tokens"],
        "cost_usd": kept["cost_usd"] if kept["cost_known"] else None,
        "v1_lines": extraction_scores({"llm": _score_extraction(gold["v1_lines"], got["v1_lines"])}),
        "subjects_as_written": raw_subjects,
    }
    # The same message asked twice: the existing recording, and once more.
    from kazma_core.memory.benchmark import _fact_matches, _llm_blob

    first = json.loads(LLM_REPLIES.read_text(encoding="utf-8"))["replies"]
    again: dict[str, Any] = {}
    for turn in gold.get("v1_again", []):
        speaker = next(j["speaker"] for j in deep_jobs(cases) if j["set"] == "v1_again" and j["turn"]["id"] == turn["id"])
        others = next(j["others"] for j in deep_jobs(cases) if j["set"] == "v1_again" and j["turn"]["id"] == turn["id"])
        before = _filed_among(asyncio.run(parsed(first[_text_key(_llm_blob(turn["user"]))], turn["user"])),
                              speaker, others)

        def found(beliefs: list[dict[str, Any]], facts: list[Any] = turn["facts"]) -> int:
            return sum(1 for alts in facts if any(_fact_matches(alts, b) for b in beliefs))

        def shown(beliefs: list[dict[str, Any]]) -> list[str]:
            return [f"{b['subject']} {b['predicate']} {b['object']}" for b in beliefs]

        again[turn["id"]] = {
            "gold": len(turn["facts"]),
            "found_first_asking": found(before), "found_asked_again": found(got["v1_again"][turn["id"]]),
            "found_with_speaker_lines": found(got["v1_lines"][turn["id"]]),
            "first_asking": shown(before), "asked_again": shown(got["v1_again"][turn["id"]]),
            "with_speaker_lines": shown(got["v1_lines"][turn["id"]]),
        }
    report["same_message_asked_again"] = again
    for name in ("speaker_plain", "speaker_lines"):
        report[name] = {
            "scores": _score_extraction(gold[name], got[name]),
            "misfiled_under_the_speaker": misfiled(name),
            "kept": {tid: [f"{b['subject']} {b['predicate']} {b['object']}" for b in facts]
                     for tid, facts in got[name].items()},
        }
    return report


# ── The correction rule, through the real trust gate ───────────────────────


def run_correction_rule(cases: dict[str, Any]) -> dict[str, Any]:
    """A member's own statement about themself, then another member's
    statement about them: the real ``mutate_belief`` under each filing."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    members = cases["members"]
    out: dict[str, Any] = {}
    for variant in ("control", "name"):
        fd, path = tempfile.mkstemp(prefix="kazma-r3-rule-", suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            ensure_primary_schema(conn)
            omar = self_id(variant, members["omar"])
            sara = self_id(variant, members["sara"])

            def write(subject: str, obj: str, method: str, at: float) -> str:
                r = mutate_belief(conn, subject, "lives_in", obj, predicate_type="functional",
                                  confidence=0.9, importance=4, extraction_method=method,
                                  tenant_id=TENANT, now=BASE_TIME + at, private=True)
                return str(r.get("reason") or r.get("action"))

            steps = {
                "omar_says_i_live_in_lusail": write(omar, "Lusail", "user_stated", 10),
                "sara_says_i_live_in_kuwait_city": write(sara, "Kuwait City", "user_stated", 20),
                # Sara, explicitly, about Omar: today's label for a remember request.
                "sara_says_remember_omar_lives_in_dubai_as_user_explicit": write(
                    omar if variant != "control" else "omar", "Dubai", "user_explicit", 30),
            }
            current = [f"{r['subject']} lives_in {r['object']} ({r['extraction_method']})" for r in conn.execute(
                "SELECT subject, object, extraction_method FROM beliefs WHERE tenant_id = ? AND "
                "valid_until IS NULL AND invalidated_at IS NULL ORDER BY valid_from", (TENANT,))]
            out[variant] = {"steps": steps, "current": current}
        finally:
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass
    return out


def main(argv: list[str]) -> int:
    global _REAL_GET_EMBEDDER
    command = argv[1] if len(argv) > 1 else "run"
    if command not in ("run", "record"):
        print(__doc__)
        return 2
    _private_install()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    from kazma_core.memory.embedder import get_embedder

    _REAL_GET_EMBEDDER = get_embedder
    vectors = _Vectors(record=command == "record")
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    a, b = _patched(vectors)
    report: dict[str, Any] = {"cases_version": cases["version"], "embedding_model": vectors.model}
    with a, b:
        speakers = {}
        everyone = (*cases["members"].values(), V5_MEMBER)
        as_user = {"name_uv": tuple(f"{self_id('name', m)} " for m in everyone),
                   "id_uv": tuple(f"{self_id('id', m)} " for m in everyone)}
        for variant in VARIANTS:
            vectors.as_user = as_user.get(variant, ())
            for mode in (("label",) if variant in ("control", "id")
                         else ("label", "self_first", "named_first")):
                speakers[f"{variant}/{mode}"] = run_speakers(cases, variant, mode)
        report["speakers"] = speakers
        locked = thresholds["scores"]
        v5 = {}
        for variant in VARIANTS:
            vectors.as_user = as_user.get(variant, ())
            scores = run_v5(variant)
            diff = {k: [locked[k], v] for k, v in scores.items() if abs(v - locked.get(k, v)) > 1e-9}
            v5[variant] = {"scores": scores, "equal_to_locked": not diff and set(scores) == set(locked),
                           "different_from_locked": diff}
        report["v5_one_member"] = v5
        vectors.as_user = ()
        report["extraction"] = run_extraction(cases)
        report["deep_pass"] = run_deep(cases)
        report["correction_rule"] = run_correction_rule(cases)
    if command == "record":
        print(f"{VECTORS.name}: {_save_merged(vectors.new, vectors.model)} vectors "
              f"({len(vectors.new)} new) from {vectors.model}")
    for key, run in report["speakers"].items():
        if key not in SHOWN_FOR:  # the lines the model is shown, for two runs only
            for row in run["results"]:
                row.pop("shown", None)
    RESULTS.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    _print(report)
    return 0


def _print(report: dict[str, Any]) -> None:
    print("multi-speaker set (overall)")
    for key, run in report["speakers"].items():
        o = run["overall"]
        print(f"  {key:<20} hit {o['hit_rate']:.3f}  fact_hit {o['fact_hit_rate']:.3f}  mrr {o['mrr']:.3f}  "
              f"precision {o['precision']:.3f}  misattributed {o['misattributed_items']:>3} items in "
              f"{o['questions_with_misattribution']:>2} questions  wrong_first {o['wrong_first_fact']}  "
              f"lost {len(run['lost'])}  guesses_kept {len(run['guesses_kept'])}")
    for key, run in report["speakers"].items():
        for field in ("by_lang", "by_category"):
            cells = "  ".join(f"{k}: hit {v['hit_rate']:.2f} fact {v['fact_hit_rate'] if v['fact_hit_rate'] is not None else '-'} "
                              f"p {v['precision'] if v['precision'] is not None else '-'} mis {v['misattributed_items']}"
                              for k, v in run[field].items())
            print(f"  {key:<20} {field}: {cells}")
    print("dataset v5, one member")
    for variant, row in report["v5_one_member"].items():
        s = row["scores"]
        print(f"  {variant:<9} equal_to_locked {row['equal_to_locked']}  overall hit {s['overall.hit_rate']} "
              f"mrr {s['overall.mrr']} precision {s['overall.precision']}  heldout hit "
              f"{s['heldout.overall.hit_rate']} mrr {s['heldout.overall.mrr']} precision "
              f"{s['heldout.overall.precision']}  changed {len(row['different_from_locked'])}")
    e = report["extraction"]
    print("extraction")
    print("  quick tagged == untagged:", list(e["quick_tagged"].values()) == list(e["quick_untagged"].values()))
    print("  deep  tagged == untagged:", list(e["deep_tagged"].values()) == list(e["deep_untagged"].values()))
    print("  speaker turns (quick):", json.dumps(e["speaker_turns"]["quick"], ensure_ascii=False))
    print("  filed under the wrong member:", e["speaker_turns"]["filed_under_the_wrong_member"])
    print("correction rule:", json.dumps(report["correction_rule"], ensure_ascii=False))
    deep = report["deep_pass"]
    if deep.get("recorded"):
        locked = e["deep_untagged"]
        print(f"deep pass told who speaks ({deep['model']}, {deep['calls']} calls)")
        print("  extraction set, lines vs locked:",
              {k: [locked[k], v] for k, v in deep["v1_lines"].items() if abs(v - locked[k]) > 1e-9} or "equal")
        for name in ("speaker_plain", "speaker_lines"):
            rows = {lang: (row["recall"], row["precision"], row["quiet"]) for lang, row in deep[name]["scores"].items()}
            print(f"  {name}: {rows}  misfiled {deep[name]['misfiled_under_the_speaker']}")
        for tid, row in deep["same_message_asked_again"].items():
            print(f"  {tid}: gold {row['gold']}  first asking {row['found_first_asking']}  asked again "
                  f"{row['found_asked_again']}  with lines {row['found_with_speaker_lines']}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
