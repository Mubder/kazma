"""Audit evidence (docs/audits/AUDIT_MEMORY_FRAMEWORK_2026-11.md, finding F9):
what recall does when many remembered turns are about one subject, and what
the boost for the chat's own turns does.

Three made-up subjects, written for this script (no install was read):

* ``renovation``: 40 different English turns about one flat renovation.
* ``wedding``: 30 different Arabic turns about planning one wedding.
* ``training``: up to 80 near-identical English log lines (one a week), the
  shape of a status log.

Around them sit the recall benchmark's own turns (each different turn once)
as unrelated chat. Every turn is written as the benchmark writes one
(``dual_write.episode_row``), into a private temporary database, and every
question goes through the real ``recall()``. The script watches recall's own
ranking function to read the numbers it used; it changes nothing in it.

Part 1, the subject's size: the same questions asked while the store holds
few, then more, then all of the subject's turns. For each question: was the
turn that answers it among the five shown, and if not, where it was lost.

Part 2, the chat's own turns: the renovation's last ten turns are "the chat
the model is reading". Each question is asked with no chat named and with
that chat named, and the shown turns are counted by where they come from.

    KAZMA_DATA_DIR=<empty folder> KAZMA_USER_HOME=<empty folder> python dense_topic.py

Loads bge-m3 from the local cache, network off, below-normal priority, four
threads. About four minutes. No provider is called. ``--fake`` swaps the
model for a toy embedder, to check the script runs; its numbers mean nothing
and go to the file named by ``--out``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
DAY = 86400.0
T0 = 1767916800.0  # 2026-01-10 00:00 UTC
TENANT = "probe"

# (what the user said, what Kazma answered). Each holds one thing to ask about.
RENOVATION: list[tuple[str, str]] = [
    ("We signed the renovation contract today with Al Noor Contracting, 9,400 dinars in total.", "Noted: Al Noor Contracting, 9,400 KD for the whole job."),
    ("The architect, Dalia, wants the kitchen wall removed to open up the living room.", "So the kitchen and living room become one space, on Dalia's plan."),
    ("Demolition starts on the 3rd of March and should take four days.", "Demolition: 3 March, four days."),
    ("The plumber found a leaking pipe behind the bathroom wall, fixing it adds 180 dinars.", "An extra 180 KD for the pipe behind the bathroom wall."),
    ("We picked oak laminate for the bedroom floors, the shop is Al Hamra Floors.", "Oak laminate from Al Hamra Floors for the bedrooms."),
    ("For the kitchen worktop we chose white quartz instead of granite.", "White quartz for the worktop, not granite."),
    ("The electrician says the fuse box is too old and must be replaced, about 250 dinars.", "A new fuse box, about 250 KD."),
    ("The living room will be painted a warm grey called Dove Tale.", "Living room: Dove Tale, a warm grey."),
    ("Kitchen cabinets are ordered from IKEA, delivery on the 20th of March.", "IKEA cabinets arrive on 20 March."),
    ("The bathroom tiles are large beige porcelain, 60 by 120.", "Beige porcelain, 60 by 120, for the bathroom."),
    ("We decided on underfloor heating only in the bathroom, not the bedrooms.", "Underfloor heating in the bathroom only."),
    ("The new front door is a steel security door from Al Jazeera Doors, 320 dinars.", "A steel door from Al Jazeera Doors, 320 KD."),
    ("The balcony will get artificial grass and a small bench.", "Artificial grass and a bench on the balcony."),
    ("The AC units are staying, but the technician will clean the ducts on the 12th.", "Ducts cleaned on the 12th; the units stay."),
    ("The carpenter, Abu Yousef, is building a wardrobe wall in the main bedroom.", "Abu Yousef builds the wardrobe wall."),
    ("Windows: we are replacing only the two in the living room, with double glazing.", "Two living room windows, double glazed."),
    ("The building manager needs a copy of the work permit before any noise, I sent it on Monday.", "The permit copy went to the building manager on Monday."),
    ("The contractor asked for the second payment, 3,000 dinars, after demolition.", "Second payment of 3,000 KD after demolition."),
    ("Ceiling spotlights in the hallway, six of them, warm white.", "Six warm white spotlights in the hallway."),
    ("The kids' room gets a bunk bed and a blue accent wall.", "A bunk bed and a blue wall for the kids."),
    ("We found mould under the old kitchen sink, they treated it with an anti-fungal coat.", "The mould under the sink was treated."),
    ("The washing machine moves from the kitchen to a cupboard in the hallway.", "Washing machine goes into the hallway cupboard."),
    ("Dalia sent the final 3D drawings, I approved version 4.", "Version 4 of the drawings is approved."),
    ("The quartz worktop was cut wrong, the supplier will redo it by the 28th.", "The supplier recuts the worktop by the 28th."),
    ("The water heater is being replaced with an 80 litre Ariston.", "An 80 litre Ariston replaces the old heater."),
    ("We chose matte black taps for both bathrooms.", "Matte black taps in both bathrooms."),
    ("The laminate delivery was short by two boxes, Al Hamra will send them on Thursday.", "Two missing boxes come on Thursday."),
    ("The neighbours downstairs complained about drilling at 8 am, we agreed to start at 9.", "Drilling starts at 9 from now on."),
    ("The guest bathroom gets a walk-in shower instead of the bathtub.", "A walk-in shower replaces the tub in the guest bathroom."),
    ("The kitchen backsplash will be green zellige tiles.", "Green zellige for the backsplash."),
    ("The living room TV wall will have a recessed shelf with an LED strip.", "A recessed shelf with an LED strip on the TV wall."),
    ("Painting is scheduled for the last week of March, two coats.", "Two coats in the last week of March."),
    ("The intercom is broken, the building will replace it, not us.", "The building replaces the intercom."),
    ("The oven is a Bosch built-in, the hob is induction with four zones.", "Bosch oven, four-zone induction hob."),
    ("The storage room becomes a small office with a fold-down desk.", "The storage room turns into an office."),
    ("Final cleaning is booked with Sparkle Co for the 5th of April.", "Sparkle Co cleans on 5 April."),
    ("We move back in on the 8th of April if the inspection passes.", "Move-in day is 8 April, after the inspection."),
    ("The inspection found one loose socket in the kitchen, fixed the same day.", "One loose socket, fixed."),
    ("Total spend so far is 10,150 dinars, about 750 over the contract.", "10,150 KD so far, 750 over."),
    ("The warranty from Al Noor covers plumbing and electrics for two years.", "Two years of warranty on plumbing and electrics."),
]
# (question, index of the turn that answers it)
RENOVATION_QUESTIONS: list[tuple[str, int]] = [
    ("Which company is doing the work on the flat, and for how much?", 0),
    ("What extra did the leaking pipe cost us?", 3),
    ("What material did we go with for the kitchen counter?", 5),
    ("Where is the new entrance door from?", 11),
    ("Who is making the built-in closet in our bedroom?", 14),
    ("What went wrong with the countertop, and when will it be put right?", 23),
    ("Which boiler are we getting?", 24),
    ("What time may the workers start making noise in the morning?", 27),
    ("Who does the cleaning at the end, and when?", 35),
    ("When do we go back to live in the flat?", 36),
]

WEDDING: list[tuple[str, str]] = [
    ("حجزنا قاعة الراية لعرس نورة يوم ١٢ ديسمبر.", "تم: قاعة الراية، ١٢ ديسمبر."),
    ("العربون للقاعة ألف دينار ودفعناه اليوم.", "عربون القاعة ألف دينار، مدفوع."),
    ("المصورة اسمها هيا العتيبي وسعرها ٦٠٠ دينار.", "المصورة هيا العتيبي، ٦٠٠ دينار."),
    ("الكوشة بتكون ورد أبيض وأخضر من محل زهور الياسمين.", "كوشة بيضاء وخضراء من زهور الياسمين."),
    ("فستان العروس من مصممة لبنانية والتسليم بعد شهر.", "الفستان يوصل بعد شهر."),
    ("البوفيه من مطعم السرايا، مشاوي وأكل كويتي.", "البوفيه من السرايا."),
    ("عدد المعازيم تقريباً ثلاثمية وخمسين.", "حوالي ٣٥٠ معزوم."),
    ("بطاقات الدعوة بنطبعها في مطبعة الفجر، لون ذهبي.", "البطاقات ذهبية من مطبعة الفجر."),
    ("الفرقة اللي بتغني فرقة الأصيل، ساعتين.", "فرقة الأصيل لمدة ساعتين."),
    ("الكوافيرة هدى بتيي البيت الساعة ثلاث العصر.", "هدى تيي الساعة ثلاث."),
    ("شهر العسل في المالديف، أسبوع كامل.", "أسبوع في المالديف."),
    ("حجزنا تذاكر المالديف على طيران الإمارات.", "التذاكر على طيران الإمارات."),
    ("الكيكة خمس طوابق من حلويات لورين.", "كيكة خمس طوابق من لورين."),
    ("أم العريس تبي توزيعات عطور صغيرة للمعازيم.", "توزيعات عطور للمعازيم."),
    ("السيارة اللي بتزف العروس رولز رويس بيضا مأجرة.", "رولز رويس بيضاء بالإيجار."),
    ("الحنة قبل العرس بيومين في بيت الجدة.", "الحنة في بيت الجدة قبل العرس بيومين."),
    ("دفعنا للمصورة نص المبلغ مقدم.", "نص مبلغ المصورة مدفوع."),
    ("خاتم الزواج من مجوهرات الدهام، ألماس.", "خاتم ألماس من الدهام."),
    ("القاعة تقفل الساعة وحدة بالليل.", "القاعة لين الساعة وحدة."),
    ("البروفة الأخيرة للفستان يوم خمسة ديسمبر.", "بروفة الفستان خمسة ديسمبر."),
    ("منسقة العرس اسمها دانة وتاخذ عشرة بالمية.", "دانة المنسقة، عشرة بالمية."),
    ("طلبنا مئة كرسي زيادة للحديقة برا القاعة.", "مئة كرسي زيادة للحديقة."),
    ("الإضاءة بتكون شموع وثريات كريستال.", "شموع وثريات كريستال."),
    ("بنت خالتي ريم بتمسك استقبال المعازيم.", "ريم على الاستقبال."),
    ("العشا يتقدم الساعة عشر ونص.", "العشا عشر ونص."),
    ("حجزنا جناح في فندق الفورسيزنز ليلة العرس.", "جناح في الفورسيزنز ليلة العرس."),
    ("العريس بيلبس بشت أسود من خياط البغلي.", "بشت أسود من البغلي."),
    ("الفيديو بيكون درون مع المصورة بزيادة ميتين دينار.", "درون بزيادة ميتين دينار."),
    ("مواقف السيارات محجوزة مع خدمة فاليه.", "المواقف مع فاليه."),
    ("الميزانية كلها عشرين ألف دينار وإلى الحين صرفنا اثنعش.", "صرفنا اثنعش ألف من عشرين."),
]
WEDDING_QUESTIONS: list[tuple[str, int]] = [
    ("منو اللي بتصور العرس وكم تاخذ؟", 2),
    ("من وين أكل العرس؟", 5),
    ("وين بنسافر بعد العرس؟", 10),
    ("كم طابق الكيكة ومن وين؟", 12),
    ("متى الحنة ووين؟", 15),
    ("شنو اسم اللي تنظم العرس وكم نسبتها؟", 20),
    ("وين بنبات ليلة العرس؟", 25),
    ("كم صرفنا لحد الحين؟", 29),
]

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_PLACES = ["the corniche", "Shaheed Park", "the track", "the treadmill", "Marina Crescent", "the canal path"]
_FEEL = ["strong", "tired", "fine", "slow", "sore", "light"]


def training(count: int) -> list[tuple[str, str]]:
    """Near-identical log lines, one a week."""
    rows = []
    for week in range(1, count + 1):
        km = 5 + (week * 7) % 23
        pace = f"{4 + (week * 3) % 3}:{(week * 17) % 60:02d}"
        rows.append((
            f"Week {week} training: ran {km} km on {_DAYS[week % 7]} at {_PLACES[week % 6]}, "
            f"average pace {pace} per km, felt {_FEEL[week % 6]}.",
            f"Logged week {week}: {km} km.",
        ))
    return rows


def training_questions(count: int) -> list[tuple[str, int]]:
    """Ten weeks spread evenly over the log, whatever its length."""
    weeks = sorted({max(1, round(count * k / 10)) for k in range(1, 11)})
    return [(f"How far did I run in week {w}?", w - 1) for w in weeks]


class _Cached:
    """The embedder, asked for each text once."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.vectors: dict[str, list[float]] = {}
        self.arrays: dict[str, Any] = {}

    def encode(self, text: str) -> list[float]:
        if text not in self.vectors:
            self.vectors[text] = [float(x) for x in self.inner.encode(text)]
            self.arrays[text] = np.asarray(self.vectors[text], dtype=np.float64)
        return self.vectors[text]

    def array(self, text: str) -> Any:
        self.encode(text)
        return self.arrays[text]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _Toy:
    """Not a model: a bag of words hashed into 64 numbers. For ``--fake`` only."""

    dim = 64

    def encode(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"\w+", text.casefold()):
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            vec[digest[0] % self.dim] += 1.0 if digest[1] % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def _cosine(a: Any, b: Any) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) or 1.0) * (np.linalg.norm(b) or 1.0)))


def _unrelated() -> list[tuple[str, str, str, float]]:
    """The recall benchmark's turns, each different turn once: (chat, user, assistant, day)."""
    from kazma_core.memory import benchmark as bench

    seen: set[str] = set()
    rows = []
    for session in bench.load_dataset()["sessions"]:
        for turn in session["turns"]:
            if turn["user"] in seen:
                continue
            seen.add(turn["user"])
            rows.append((f"other-{session['id']}", turn["user"], turn["assistant"], float(session["day"] % 200)))
    return rows


def _store(subject: list[tuple[str, str]], unrelated: list[tuple[str, str, str, float]]) -> tuple[sqlite3.Connection, str, list[str], dict[str, str]]:
    """A private database holding the unrelated chat and the subject's turns,
    ten to a chat, one chat a week. Returns the subject's row ids in order and
    each row's embedded text."""
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    fd, path = tempfile.mkstemp(prefix="kazma-audit-dense-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    embedded: dict[str, str] = {}

    def write(chat: str, number: int, user: str, assistant: str, at: float) -> str:
        row = episode_row(session_id=chat, turn_number=number, user_text=user, assistant_text=assistant,
                          tenant_id=TENANT, source="benchmark", created_at=at)
        blob = encode_text_to_blob(row["embed_text"]) if row["embed_text"] else None
        conn.execute(
            "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "summary_text, tier, structural_importance, created_at, metadata_json, embedding_model_version, "
            "embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["tenant_id"], row["session_id"], row["turn_number"], row["user_text"],
             row["assistant_text"], row["summary_text"], row["tier"], row["importance"], row["created_at"],
             json.dumps(row["meta"], ensure_ascii=False), row["embedding_model_version"], blob))
        embedded[row["id"]] = row["embed_text"] or ""
        return str(row["id"])

    counters: dict[str, int] = {}
    for chat, user, assistant, day in unrelated:
        counters[chat] = counters.get(chat, 0) + 1
        write(chat, counters[chat], user, assistant, T0 + day * DAY + counters[chat] * 60)
    ids = []
    for i, (user, assistant) in enumerate(subject):
        chat = f"subject-{i // 10}"
        ids.append(write(chat, i % 10 + 1, user, assistant, T0 + (210 + (i // 10) * 7) * DAY + (i % 10) * 600))
    conn.commit()
    return conn, path, ids, embedded


def _remove(conn: sqlite3.Connection, path: str) -> None:
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def _ask(conn: sqlite3.Connection, question: str, *, chat: str | None = None) -> tuple[Any, dict[str, Any]]:
    """One question through the real recall(); what its ranking function was
    given and how it scored each candidate turn."""
    from kazma_core.memory import recall as recall_mod
    from kazma_core.memory.query_terms import content_terms, coverage

    seen: dict[str, Any] = {}
    real = recall_mod._rank_by_evidence

    def watching(query: str, candidates: list[Any], background: float, *, kind: str = "episode") -> list[Any]:
        if kind == "episode":
            cov_weight, floor, _strong, gap, _rec, _stand = recall_mod._evidence_rules("episode")
            terms = content_terms(query)
            scored = {}
            for cand in candidates:
                lift = (cand.similarity - background) if cand.similarity is not None else 0.0
                cov = coverage(terms, cand.text)
                scored[cand.id] = {"similarity": cand.similarity, "lift": lift, "coverage": cov,
                                   "evidence": lift + cov_weight * cov, "by_meaning": cand.by_meaning}
            seen.update(background=background, floor=floor, gap=gap, scored=scored)
        return real(query, candidates, background, kind=kind)

    with mock.patch.object(recall_mod, "_rank_by_evidence", watching):
        result = recall_mod.recall(question, conn=conn, tenant_id=TENANT, limit=5, session_id=chat, local_only=True)
    return result, seen


def _mean(values: list[Any]) -> float | None:
    kept = [float(v) for v in values if v is not None]
    return round(statistics.fmean(kept), 4) if kept else None


def _where_lost(target: str, shown: list[str], seen: dict[str, Any]) -> str:
    scored = seen.get("scored") or {}
    if target in shown:
        return "shown"
    if target not in scored:
        return "never a candidate (outside the 40 nearest and no keyword match)"
    mine = scored[target]["evidence"]
    if mine < seen["floor"]:
        return "under the floor"
    best = max(row["evidence"] for row in scored.values())
    if mine < best - seen["gap"]:
        return "too far behind the best"
    ahead = sum(1 for row in scored.values() if row["evidence"] > mine)
    return "ranked below the five shown on evidence" if ahead >= 5 else "in the first five on evidence, passed over for newer turns"


def subject_size(name: str, subject: list[tuple[str, str]], questions: list[tuple[str, int]], sizes: list[int],
                 unrelated: list[tuple[str, str, str, float]], cached: _Cached) -> dict[str, Any]:
    """Part 1 for one subject."""
    out: dict[str, Any] = {}
    targets = sorted({index for _q, index in questions})
    for size in sizes:
        asked = [(q, index) for q, index in questions if index < len(subject)]
        if size >= len(subject):
            part = list(range(len(subject)))
        elif name == "training":
            part = list(range(size))
            asked = [(q, index) for q, index in asked if index < size]
        else:  # the turns that answer a question, then the others in order
            others = [i for i in range(len(subject)) if i not in targets]
            part = sorted(targets + others[: max(0, size - len(targets))])
        conn, path, ids, embedded = _store([subject[i] for i in part], unrelated)
        place = {index: ids[n] for n, index in enumerate(part)}
        on_subject = set(ids)
        try:
            rows = []
            for question, index in asked:
                result, seen = _ask(conn, question)
                shown = [h.id for h in result.episodes[:5]]
                target = place[index]
                scored = seen.get("scored") or {}
                qvec = cached.array(question)
                sims = sorted((_cosine(qvec, cached.array(text)) for text in embedded.values() if text), reverse=True)
                target_sim = _cosine(qvec, cached.array(embedded[target]))
                rows.append({
                    "question": question, "shown": target in shown, "where": _where_lost(target, shown, seen),
                    "turns_shown": len(shown), "shown_on_the_subject": sum(1 for i in shown if i in on_subject),
                    "background_recall_used": round(float(seen["background"]), 4) if "background" in seen else None,
                    "background_if_the_median_of_every_turn": round(statistics.median(sims), 4),
                    "target_similarity": round(target_sim, 4),
                    "target_rank_by_meaning": 1 + sum(1 for s in sims if s > target_sim),
                    "target_evidence": round(scored[target]["evidence"], 4) if target in scored else None,
                    "target_word_share": round(scored[target]["coverage"], 3) if target in scored else None,
                    "candidates_over_the_floor": sum(1 for r in scored.values() if r["evidence"] >= seen.get("floor", 0)),
                })
            subject_vectors = [cached.array(embedded[i]) for i in ids]
            pairs = [_cosine(a, b) for n, a in enumerate(subject_vectors) for b in subject_vectors[n + 1:]]
            out[str(len(part))] = {
                "turns_in_the_store": len(embedded), "turns_on_the_subject": len(part), "questions": len(rows),
                "answer_shown": sum(1 for r in rows if r["shown"]),
                "nothing_shown": sum(1 for r in rows if not r["turns_shown"]),
                "lost_where": {w: sum(1 for r in rows if r["where"] == w)
                               for w in sorted({r["where"] for r in rows if not r["shown"]})},
                "mean_background_recall_used": _mean([r["background_recall_used"] for r in rows]),
                "mean_background_if_median": round(statistics.fmean(r["background_if_the_median_of_every_turn"] for r in rows), 4),
                "mean_target_similarity": round(statistics.fmean(r["target_similarity"] for r in rows), 4),
                "mean_similarity_between_the_subjects_turns": round(statistics.fmean(pairs), 4) if pairs else None,
                "each_question": rows,
            }
        finally:
            _remove(conn, path)
    return out


def own_chat(unrelated: list[tuple[str, str, str, float]]) -> dict[str, Any]:
    """Part 2: the renovation whole; its last ten turns are the chat being read."""
    conn, path, ids, _embedded = _store(RENOVATION, unrelated)
    reading = "subject-3"
    in_reading = set(ids[30:])
    try:
        rows = []
        for question, index in RENOVATION_QUESTIONS:
            target = ids[index]
            row: dict[str, Any] = {"question": question, "answer_is_in_the_chat_being_read": target in in_reading}
            for label, chat in (("no_chat_named", None), ("chat_named", reading)):
                result, _seen = _ask(conn, question, chat=chat)
                shown = [h.id for h in result.episodes[:5]]
                row[label] = {"turns_shown": len(shown), "from_the_chat_being_read": sum(1 for i in shown if i in in_reading),
                              "answer_shown": target in shown,
                              "answer_position": shown.index(target) + 1 if target in shown else None}
            rows.append(row)
        elsewhere = [r for r in rows if not r["answer_is_in_the_chat_being_read"]]

        def total(rows_: list[dict[str, Any]], label: str, key: str) -> int:
            return sum(int(bool(r[label][key])) if key == "answer_shown" else int(r[label][key]) for r in rows_)

        return {
            "questions": len(rows), "questions_answered_in_an_older_chat": len(elsewhere),
            "older_chat_answers_shown": {"no_chat_named": total(elsewhere, "no_chat_named", "answer_shown"),
                                         "chat_named": total(elsewhere, "chat_named", "answer_shown")},
            "turns_shown_from_the_chat_being_read": {"no_chat_named": total(rows, "no_chat_named", "from_the_chat_being_read"),
                                                     "chat_named": total(rows, "chat_named", "from_the_chat_being_read")},
            "turns_shown_in_all": {"no_chat_named": total(rows, "no_chat_named", "turns_shown"),
                                   "chat_named": total(rows, "chat_named", "turns_shown")},
            "each_question": rows,
        }
    finally:
        _remove(conn, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fake", action="store_true", help="a toy embedder, to check the script runs")
    parser.add_argument("--out", default=str(HERE / "dense_topic.json"))
    args = parser.parse_args(argv)
    if not os.environ.get("KAZMA_DATA_DIR"):
        raise SystemExit("set KAZMA_DATA_DIR to a private, empty folder: this run must not read an install")
    if args.fake and Path(args.out) == HERE / "dense_topic.json":
        raise SystemExit("--fake needs --out: its numbers are not evidence")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    import psutil

    if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        os.nice(5)

    started = time.perf_counter()
    if args.fake:
        inner: Any = _Toy()
        model = "toy (not a model)"
    else:
        import torch

        torch.set_num_threads(4)
        from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

        inner = get_embedder()
        if inner is None:
            raise SystemExit("no embedder here: not measured")
        model = get_embedding_model_name()
    cached = _Cached(inner)
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: cached), \
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: model):
        unrelated = _unrelated()
        report: dict[str, Any] = {
            "measured_at": time.strftime("%Y-%m-%d %H:%M %z"), "model": model,
            "unrelated_turns": len(unrelated),
            "1_the_subjects_size": {
                "renovation (English, 40 different turns)": subject_size(
                    "renovation", RENOVATION, RENOVATION_QUESTIONS, [10, 20, 40], unrelated, cached),
                "wedding (Arabic, 30 different turns)": subject_size(
                    "wedding", WEDDING, WEDDING_QUESTIONS, [8, 16, 30], unrelated, cached),
                "training (English, near-identical log lines)": {
                    str(size): subject_size("training", training(size), training_questions(size), [size],
                                            unrelated, cached)[str(size)]
                    for size in (10, 20, 40, 80)},
            },
            "2_the_chats_own_turns": own_chat(unrelated),
        }
    report["texts_embedded"] = len(cached.vectors)
    report["seconds"] = round(time.perf_counter() - started, 1)
    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    brief = {k: {size: {key: v[key] for key in ("turns_on_the_subject", "questions", "answer_shown", "nothing_shown", "lost_where")}
                 for size, v in sizes.items()} for k, sizes in report["1_the_subjects_size"].items()}
    print(json.dumps(brief, ensure_ascii=True, indent=1))
    print(json.dumps({k: v for k, v in report["2_the_chats_own_turns"].items() if k != "each_question"}, indent=1))
    print(f"{report['texts_embedded']} texts embedded, {report['seconds']} s, wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
