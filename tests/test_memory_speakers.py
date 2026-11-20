"""Who is "I" in a shared space (Spaces S1-11; the R3 study, decisions N30 to N36).

Two members of one shared space state facts about themselves through the
real post-turn pipeline (``consolidator._run_turn_memory``: the episode
writer and the quick pass), and each is recalled for the right one by the
real ``recall``, in English, Arabic and both mixed. Then the gates of the
study's section 5.2, each with its negative control:

* writers: every function that writes a fact or an episode from a turn
  passes the speaker (found from the source, as the turn-time gate finds its
  keyword); a planted writer without it fails;
* readers: the case set's every question asked as every member shows no
  other member's fact or turn as the asker's (control: one ``user`` entry);
* one member: a personal space stores rows exactly as before (no ``said_by``,
  subject ``user``), and dataset v5's gated scores are the locked ones;
* the speakers ratchet (``thresholds.json`` ``speakers``);
* rules: ``member_stated`` never replaces a member's own statement (control:
  today's label replaces it); a first-person fact with no speaker is not
  filed; a model never writes a member's entry;
* forget by entry (N36): no fact under it is recalled afterwards, the
  member's messages and the space's shared facts stay, and a restore of an
  older copy forgets them again (control: the entry left in place);
* who may forget (N32, N33): another member's turn is refused to a member
  who is neither its author nor the space's owner; a fact about oneself that
  another member stated goes, the answer says their message stays, and the
  deep pass never files it from that message again;
* About me per member (N31) and "Copy from my personal space";
* the held-out questions (``tests/fixtures/memory_speakers/heldout_questions.json``),
  written by another session before the builder saw them: their scores are
  reported by language, kind and first-person form, and gated only on what
  never moves (nothing shown as the asker's that is another member's).
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

REPO = Path(__file__).resolve().parents[1]
TEAM = "team-r3"
SARA, OMAR = "sara", "omar"
THRESHOLDS = REPO / "tests" / "fixtures" / "memory_bench" / "thresholds.json"
V5_VECTORS = REPO / "tests" / "fixtures" / "memory_bench" / "vectors.npz"
SPEAKER_VECTORS = REPO / "tests" / "fixtures" / "memory_bench" / "speakers_vectors.npz"
HELDOUT = REPO / "tests" / "fixtures" / "memory_speakers" / "heldout_questions.json"


class _Bag:
    """A stand-in embedder: word stems counted, folded, over a shared base
    every text holds -- as a real model puts unrelated texts near 0.5 and
    related ones above. Meaning search then finds what shares a word's stem
    ("live" and "lives", "ساكن" and "ساكنة"), so recall works with no model."""

    model = "bag-of-words"
    dim = 96
    # Words of one meaning, in either language, as a real model reads them.
    _STEMS = {"lives": "live", "living": "live", "ساكن": "live", "ساكنه": "live", "اسكن": "live",
              "سكني": "live"}

    def encode(self, text: str) -> list[float]:
        from kazma_core.documents.arabic import fold_for_search

        vec = [0.0] * self.dim
        vec[0] = 2.0  # the shared base
        for word in re.findall(r"[^\W\d_]+", fold_for_search(str(text)).lower()):
            word = self._STEMS.get(word, word)
            vec[1 + int(hashlib.md5(word.encode()).hexdigest(), 16) % (self.dim - 1)] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


@pytest.fixture()
def team(tmp_path, monkeypatch):
    """A private memory store; a shared space of Sara and Omar (the owner
    made it and owns it); Sara's personal space; Spaces enforced."""
    from kazma_core.config_store import get_config_store
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    bag = _Bag()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: bag)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: bag.model)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    get_config_store().delete(identity.REGISTRY_KEY)

    def people(reg: dict[str, Any]) -> None:
        for account, name in ((SARA, "Sara"), (OMAR, "Omar")):
            reg["accounts"][account] = {"id": account, "name": name, "role": "member", "enabled": True}
        reg["spaces"]["sara-own"] = {"id": "sara-own", "name": "", "kind": "sandboxed",
                                     "personal_of": SARA, "created_by": OWNER_ACCOUNT_ID}
        reg["memberships"].append({"account": SARA, "space": "sara-own", "role": "owner"})

    identity.update_registry(people)
    identity.set_called_names(SARA, ["سارة"])
    identity.set_called_names(OMAR, ["عمر"])
    identity.create_space("Team", created_by=OWNER_ACCOUNT_ID, space_id=TEAM)
    identity.add_member(TEAM, SARA)
    identity.add_member(TEAM, OMAR)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.commit()
    dual_write._reset_mirror()
    monkeypatch.setattr(scope, "_enforced", True)
    yield SimpleNamespace(db=db, ops=ops, tmp=tmp_path, mp=monkeypatch)
    dual_write._reset_mirror()
    ops.close()
    db.close()
    get_config_store().delete(identity.REGISTRY_KEY)


def _say(account: str | None, session: str, text: str, *, n: int = 1, at: float = 1_780_000_000.0,
         space: str = TEAM) -> None:
    """One turn through the real post-turn pipeline: the episode writer and
    the quick pass, as a finished turn's thread runs them."""
    from kazma_core.memory.consolidator import _run_turn_memory

    assert _run_turn_memory([{"role": "user", "content": text}, {"role": "assistant", "content": "Noted."}],
                            session_id=session, turn=n, tenant_id=space, at=at, said_by=account)


def _current(db: sqlite3.Connection, predicate: str, *, space: str = TEAM) -> dict[str, str]:
    return {str(r["subject"]): str(r["object"]) for r in db.execute(
        "SELECT subject, object FROM beliefs WHERE tenant_id = ? AND predicate = ? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (space, predicate))}


def _shown(asker: str | None, question: str, *, space: str = TEAM) -> tuple[list[str], list[str]]:
    from kazma_core.memory.recall import recall

    res = recall(question, tenant_id=space, asker=asker, local_only=True, limit=5)
    assert not res.failed, res.degraded
    return [h.content for h in res.beliefs], [str(h.metadata.get("display") or "") for h in res.episodes]


SAYINGS = {
    # language: (Sara's statement, Omar's, each one's question, Sara's value, Omar's value).
    # The stand-in embedder finds by shared words: each question shares one
    # with its asker's own statement, and recall's choice does the rest.
    "en": ("I live in Kuwait City.", "I live in Doha.",
           {SARA: "Where do I live?", OMAR: "Where do I live?"}, "Kuwait City", "Doha"),
    "ar": ("أنا ساكنة في الكويت", "أنا ساكن في الدوحة",
           {SARA: "وين ساكنة أنا؟", OMAR: "وين ساكن أنا؟"}, "الكويت", "الدوحة"),
    "mixed": ("I live in Kuwait City, الحمد لله.", "I live in Doha, الحمد لله.",
              {SARA: "وين I live؟", OMAR: "وين I live؟"}, "Kuwait City", "Doha"),
}


@pytest.mark.parametrize("lang", sorted(SAYINGS))
def test_two_members_facts_about_themselves_are_each_their_own(team, lang) -> None:
    sara_says, omar_says, questions, sara_city, omar_city = SAYINGS[lang]
    _say(SARA, f"chat-sara-{lang}", sara_says, at=1_780_000_000.0)
    _say(OMAR, f"chat-omar-{lang}", omar_says, at=1_780_000_600.0)
    # Two facts, each under its member's own entry: neither replaced the other.
    lives = _current(team.db, "lives_in")
    assert lives == {f"member_{SARA}": sara_city, f"member_{OMAR}": omar_city}, lives
    for asker, mine, theirs, their_name in ((SARA, sara_city, omar_city, "Omar"),
                                            (OMAR, omar_city, sara_city, "Sara")):
        facts, turns = _shown(asker, questions[asker])
        own = [f for f in facts if f.startswith("user ")]
        assert own and all(mine in f for f in own), (asker, facts)
        assert not any(theirs in f for f in own), (asker, facts)
        # Another member's fact, when shown, is under their name.
        assert all(f.startswith(f"{their_name} ") for f in facts if theirs in f), facts
        # Another member's turn is under their name, never "User:".
        assert not any(t.startswith("User: ") and theirs in t for t in turns), turns


def test_control_choosing_after_the_ranking_loses_the_askers_own_fact(team) -> None:
    """Recall chooses whose facts a question is about before it ranks them
    (``recall._whose_facts``). Chosen after, Sara's "Kuwait City" ranks best
    for Omar's "Where do I live?", and his own "Doha" falls behind it by more
    than recall's gap: he is shown nothing of his own."""
    from kazma_core.memory import recall as recall_module

    team.mp.setattr(recall_module, "_whose_facts", lambda people, asker, query: None)
    _say(SARA, "chat-sara", "I live in Kuwait City.", at=1_780_000_000.0)
    _say(OMAR, "chat-omar", "I live in Doha.", at=1_780_000_600.0)
    facts, _turns = _shown(OMAR, "Where do I live?")
    assert not any(f.startswith("user ") and "Doha" in f for f in facts), facts


def test_control_with_the_speaker_left_out_no_first_person_fact_is_filed(team) -> None:
    _say(None, "chat-nobody", "I live in Kuwait City.")
    assert _current(team.db, "lives_in") == {}
    # The turn is still remembered.
    assert team.db.execute("SELECT COUNT(*) FROM episodes WHERE tenant_id = ?", (TEAM,)).fetchone()[0] == 1


def test_control_with_the_mapping_removed_one_user_entry_overwrites(team) -> None:
    from kazma_core.memory import speakers

    team.mp.setattr(speakers, "room", lambda space_id: speakers._personal_room(str(space_id or "")))
    _say(SARA, "chat-sara", "I live in Kuwait City.", at=1_780_000_000.0)
    _say(OMAR, "chat-omar", "I live in Doha.", at=1_780_000_600.0)
    # Today's single entry: Omar's statement replaced Sara's.
    assert _current(team.db, "lives_in") == {"user": "Doha"}


def test_the_turn_record_names_its_asker_and_reaches_the_pipeline(monkeypatch) -> None:
    from kazma_core.memory import consolidator

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(consolidator, "_schedule_post_turn_memory",
                        lambda messages, **kw: calls.append(kw))
    msgs = [{"role": "user", "content": "I live in Kuwait City."}, {"role": "assistant", "content": "Noted."}]
    assert consolidator.remember_turn({"messages": msgs, "_post_turn_memory": {
        "session_id": "t1", "turn": consolidator.user_turn_index(msgs), "tenant_id": TEAM, "said_by": SARA}})
    assert calls[0]["said_by"] == SARA
    # respond_node writes the record with the bound asker.
    source = (REPO / "kazma-core/kazma_core/agent/graph_respond.py").read_text(encoding="utf-8")
    assert '"said_by": bound_asker()' in source


# ── The writers' gate ────────────────────────────────────────────────────

_TURN_WRITERS = (
    "kazma-core/kazma_core/memory/consolidator.py",
    "kazma-core/kazma_core/memory/worker_bootstrap.py",
    "kazma-core/kazma_core/memory/turn_reconcile.py",
)
#: Each writer from a turn, and the keyword that names its speaker.
_SPEAKER_KEYWORD = {
    "_schedule_post_turn_memory": "said_by", "_run_turn_memory": "said_by", "_defer_turn_memory": "said_by",
    "_mirror_turn_to_v2": "said_by", "_v2_extract_sync": "said_by",
    "extract_and_apply_beliefs_sync": "said_by", "_apply_if_still_remembered": "said_by",
    "write_episode": "metadata", "mirror_episode": "metadata",
}


def _speakerless_writes(source: str) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            keyword = _SPEAKER_KEYWORD.get(name or "")
            if keyword and keyword not in {k.arg for k in node.keywords}:
                found.append((node.lineno, name))
    return found


def test_every_writer_from_a_turn_passes_its_speaker() -> None:
    offenders = [f"{rel}:{line} {name}()" for rel in _TURN_WRITERS
                 for line, name in _speakerless_writes((REPO / rel).read_text(encoding="utf-8"))]
    assert not offenders, (
        "A write from a turn must name who spoke, or a shared space files another member's "
        "\"I\" under nobody (or under the wrong person):\n  " + "\n  ".join(offenders))
    # Negative control: the deep pass as it was before S1-11.
    planted = ("async def handle(row):\n"
               "    _apply_if_still_remembered(raw, primary, ops, stats=stats, episode_id=e,\n"
               "                               tenant_id=row['tenant_id'], now=row['created_at'])\n")
    assert _speakerless_writes(planted) == [(2, "_apply_if_still_remembered")]


_ENCODERS = {"encode_vector", "encode_text_to_blob", "_reencode_row"}


#: Names a fact's parts go by in memory code.
_FACT_PARTS = {"sub", "subject", "pred", "predicate", "obj", "object", "a", "b", "c"}


def _part(value: ast.AST) -> str:
    """The name a formatted value reads (``sub``, ``row['subject']``)."""
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant):
        return str(value.slice.value)
    return ""


def _own_vector_texts(source: str) -> list[int]:
    """Lines where a fact's vector text is built by hand: an encoder called
    with an f-string of a subject, a predicate and an object."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in _ENCODERS:
            for arg in node.args:
                parts = [_part(v.value) for v in getattr(arg, "values", []) if isinstance(v, ast.FormattedValue)]
                if isinstance(arg, ast.JoinedStr) and len(parts) >= 3 and set(parts) <= _FACT_PARTS:
                    found.append(node.lineno)
    return found


def test_one_text_for_a_facts_vector() -> None:
    """Every fact vector is made by ``speakers.fact_vector_text``: a member's
    entry is embedded as ``user`` (the study's 4.3). A planted copy fails."""
    found = [f"{p.relative_to(REPO)}:{line}" for p in (REPO / "kazma-core/kazma_core/memory").glob("*.py")
             for line in _own_vector_texts(p.read_text(encoding="utf-8"))]
    assert found == [], found
    assert _own_vector_texts('vec = encode_vector(f"{sub} {pred} {obj}")') == [1]

    from kazma_core.memory.speakers import fact_vector_text

    assert fact_vector_text("member_sara", "lives_in", "Kuwait City") == "user lives_in Kuwait City"
    assert fact_vector_text("user", "lives_in", "Doha") == "user lives_in Doha"
    assert fact_vector_text("omar", "lives_in", "Doha") == "omar lives_in Doha"


# ── The readers' gate and the benchmark ──────────────────────────────────


def _replayed(monkeypatch) -> None:
    from kazma_core.memory.benchmark import SpeakerReplay

    replay = SpeakerReplay(V5_VECTORS, SPEAKER_VECTORS)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)


def test_every_question_as_every_member_shows_no_one_else_as_the_asker(monkeypatch) -> None:
    from kazma_core.memory.benchmark import load_speakers, run_speaker_benchmark

    _replayed(monkeypatch)
    cases = load_speakers()
    every = [{**q, "id": f"{q['id']}@{who}", "by": who} for q in cases["questions"] for who in cases["members"]]
    report = run_speaker_benchmark(cases, questions=every)
    assert report["overall"]["n"] == len(cases["questions"]) * len(cases["members"])
    assert report["overall"]["misattributed"] == 0, report["overall"]


def test_control_one_user_entry_shows_others_as_the_asker(monkeypatch) -> None:
    from kazma_core.memory import benchmark

    _replayed(monkeypatch)
    real = benchmark.speakers_room

    def one_entry(cases: dict[str, Any]) -> Any:
        from kazma_core.memory.speakers import Room

        shared = real(cases)
        return Room(space=shared.space, shared=False, members=shared.members)

    monkeypatch.setattr(benchmark, "speakers_room", one_entry)
    report = benchmark.run_speaker_benchmark()
    assert report["overall"]["misattributed"] > 0
    assert report["lost"], "with one entry, members' facts overwrite each other"


def test_the_speakers_ratchet(monkeypatch) -> None:
    from kazma_core.memory.benchmark import load_speakers, run_speaker_benchmark, speaker_scores

    _replayed(monkeypatch)
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["speakers"]
    assert locked["version"] == load_speakers()["version"]
    now = speaker_scores(run_speaker_benchmark())
    lower = {k: (v, now.get(k)) for k, v in locked["scores"].items()
             if k != "overall.misattributed" and now.get(k, -1.0) < v}
    assert not lower, f"below the speakers ratchet: {lower}"
    assert now["overall.misattributed"] <= locked["scores"]["overall.misattributed"] == 0.0


def test_a_personal_space_stores_rows_exactly_as_before(team) -> None:
    _say(OWNER_ACCOUNT_ID, "chat-own", "I live in Kuwait City.", space=OWNER_SPACE_ID)
    episode = team.db.execute("SELECT metadata_json FROM episodes WHERE tenant_id = ?",
                              (OWNER_SPACE_ID,)).fetchone()
    assert "said_by" not in json.loads(episode[0] or "{}")
    fact = team.db.execute("SELECT subject, metadata_json, extraction_method FROM beliefs WHERE tenant_id = ?",
                           (OWNER_SPACE_ID,)).fetchone()
    assert fact["subject"] == "user" and fact["extraction_method"] == "user_stated"
    assert "said_by" not in json.loads(fact["metadata_json"] or "{}")


def test_a_personal_space_gives_the_locked_v5_scores_exactly(monkeypatch) -> None:
    from kazma_core.memory.benchmark import ReplayEmbedder, gated_scores, run_benchmark

    replay = ReplayEmbedder(V5_VECTORS)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["scores"]
    assert gated_scores(run_benchmark()) == locked


# ── The rules ────────────────────────────────────────────────────────────


def _apply(team, raw: list[dict[str, Any]], *, said_by: str | None, method: str, at: float) -> dict[str, Any]:
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    return _apply_beliefs_to_v2(raw, team.db, team.ops, tenant_id=TEAM, extraction_method=method, now=at,
                                session_id="chat-r", turn=1, said_by=said_by)


def test_a_member_never_replaces_what_another_said_of_themself(team) -> None:
    _apply(team, [{"subject": "user", "predicate": "lives_in", "object": "Lusail", "predicate_type": "functional",
                   "confidence": 0.9}], said_by=OMAR, method="user_stated", at=1_780_000_000.0)
    # Sara: "remember that Omar lives in Dubai" -- the user's channel, her words.
    _apply(team, [{"subject": "Omar", "predicate": "lives_in", "object": "Dubai", "predicate_type": "functional",
                   "confidence": 0.9}], said_by=SARA, method="user_explicit", at=1_780_000_600.0)
    assert _current(team.db, "lives_in") == {f"member_{OMAR}": "Lusail"}
    row = team.db.execute("SELECT extraction_method, metadata_json FROM beliefs WHERE object = 'Dubai'").fetchone()
    assert row is None or row["extraction_method"] == "member_stated"


def test_control_with_todays_label_another_member_replaces_it(team) -> None:
    from kazma_core.memory.speakers import Room

    team.mp.setattr(Room, "label", lambda self, method, subject, speaker: method)
    _apply(team, [{"subject": "user", "predicate": "lives_in", "object": "Lusail", "predicate_type": "functional",
                   "confidence": 0.9}], said_by=OMAR, method="user_stated", at=1_780_000_000.0)
    _apply(team, [{"subject": "Omar", "predicate": "lives_in", "object": "Dubai", "predicate_type": "functional",
                   "confidence": 0.9}], said_by=SARA, method="user_explicit", at=1_780_000_600.0)
    assert _current(team.db, "lives_in") == {f"member_{OMAR}": "Dubai"}


def test_a_member_named_in_arabic_is_filed_under_their_entry(team) -> None:
    _apply(team, [{"subject": "عمر", "predicate": "lives_in", "object": "لوسيل", "predicate_type": "functional",
                   "confidence": 0.9}], said_by=SARA, method="llm_inferred", at=1_780_000_000.0)
    assert _current(team.db, "lives_in") == {f"member_{OMAR}": "لوسيل"}


def test_a_model_never_writes_a_members_entry(team) -> None:
    stats = _apply(team, [{"subject": f"member_{OMAR}", "predicate": "lives_in", "object": "Dubai",
                           "predicate_type": "functional", "confidence": 0.9}],
                   said_by=SARA, method="llm_inferred", at=1_780_000_000.0)
    assert stats["rejected"] == 1 and _current(team.db, "lives_in") == {}


def test_the_first_person_reading() -> None:
    """Grammar the reading is built on, written apart from the study's 35
    questions and the held-out file."""
    from kazma_core.memory.speakers import refers_to_asker

    names = ["Sara", "سارة", "Omar", "عمر", "أحمد"]
    for asks in ("What's my car?", "Am I allergic to anything?", "Remind me where I park",
                 "وين بيتي؟", "شنو أشتغل؟", "متى موعدي؟", "عندي حساسية من شي؟", "my car شنو؟"):
        assert refers_to_asker(asks, names), asks
    for asks in ("Where does Omar live?", "Where do we eat on Friday?", "شنو أفضل مطعم؟",
                 "وين ساكن أحمد؟", "متى عيد ميلاد عمر؟", "شنو صار في الاجتماع؟"):
        assert not refers_to_asker(asks, names), asks


# Three Arabic forms the first version of the reading missed (the held-out
# questions showed them; the main session asked for each to be written from
# the grammar). The sentences here are new: none is a held-out question.
FIRST_PERSON_FORMS = {
    # An object "me" on another person's verb ("-ني" after "ي"/"ت").
    "object_me": ["متى يذكرني التطبيق بالموعد؟", "شنو الفيلم اللي يعجبني؟", "كم يكلفني الاشتراك؟",
                  "مين يوصلني للمطار؟"],
    # A three-letter first-person imperfect (a doubled or weak root; alef madda).
    "short_verb": ["وين أحط المفاتيح عادة؟", "شنو أحب من الحلويات؟", "شنو آكل على الفطور؟",
                   "آخذ أي دوا للضغط؟", "متى أجي بكرة؟"],
    # MSA "I have".
    "msa_have": ["ما الموعد المسجل لديّ غدا؟", "هل لدي حساسية من شيء؟"],
}
#: Words that look like those forms and are not the asker.
NOT_FIRST_PERSON = [
    "مطعم يمني قريب؟",  # "-ني" with one letter before it: an adjective of origin
    "مطعم يوناني قريب؟",  # "-اني": an adjective of origin
    "الأكل هنا زين؟",  # the noun, with its article
    "أكل البيت أحسن من المطاعم؟",  # the noun "food", hamza, not madda
    "آخر مرة متى كانت؟",  # "آخر", a noun with madda
    "أهل عمر وين؟",  # a three-letter noun with alef
    "هل لديه سيارة؟",  # "he has"
]


def _old_refers_to_asker(question: str, names: list[str]) -> bool:
    """The reading as S1-11 first shipped it (commit 43691ea48): the
    control."""
    from kazma_core.memory import speakers as sp

    not_mine = {w for name in names for w in sp._words(name)}
    for word in sp._words(question):
        if word in sp._EN_SELF:
            return True
        if not sp._is_arabic(word) or word in not_mine:
            continue
        if word in sp._AR_SELF - {"لدي"}:
            return True
        bare = word[1:] if len(word) > 4 and word[0] in ("و", "ف") else word
        if bare in sp._AR_NOT_MINE or bare in not_mine or bare.startswith("ال"):
            continue
        if len(bare) >= 4 and bare.endswith("ي") and bare[0] != "ي":
            return True
        if len(bare) >= 4 and bare[0] == "ا" and bare not in sp._AR_ALEF_NOT_I:
            return True
    return False


@pytest.mark.parametrize("form", sorted(FIRST_PERSON_FORMS))
def test_each_arabic_first_person_form_is_read(form) -> None:
    from kazma_core.memory.speakers import refers_to_asker

    names = ["Sara", "سارة", "Omar", "عمر"]
    for asks in FIRST_PERSON_FORMS[form]:
        assert refers_to_asker(asks, names), asks
        assert not _old_refers_to_asker(asks, names), f"the old rule already read {asks}"
    for asks in NOT_FIRST_PERSON:
        assert not refers_to_asker(asks, names), asks


# ── Forget by entry (N36) and who may forget (N32, N33) ──────────────────


def test_forget_every_fact_under_an_entry(team) -> None:
    from kazma_core.memory import forget, forget_journal

    _say(SARA, "chat-sara", "I live in Kuwait City.", at=1_780_000_000.0)
    _apply(team, [{"subject": "Sara", "predicate": "favorite_food", "object": "machboos",
                   "predicate_type": "functional", "confidence": 0.9}],
           said_by=OMAR, method="llm_inferred", at=1_780_000_300.0)
    _apply(team, [{"subject": "team_trip", "predicate": "destination", "object": "Muscat",
                   "predicate_type": "functional", "confidence": 0.9}],
           said_by=SARA, method="user_stated", at=1_780_000_400.0)
    # A copy as a backup takes it: the database's own snapshot.
    team.db.commit()
    before = team.tmp / "memory_state.before.db"
    with sqlite3.connect(str(before)) as copy:
        team.db.backup(copy)
    copy.close()
    out = forget.forget_entry(f"member_{SARA}", tenant_id=TEAM)
    assert out["ok"] and len(out["facts"]) == 2
    for asker in (SARA, OMAR, OWNER_ACCOUNT_ID):
        facts, turns = _shown(asker, "Where does Sara live and what does she like to eat?")
        assert not any("Kuwait City" in f or "machboos" in f for f in facts), (asker, facts)
    # Her messages stay; the space's shared fact stays.
    assert team.db.execute("SELECT COUNT(*) FROM episodes WHERE tenant_id = ? AND tier != 'forgotten'",
                           (TEAM,)).fetchone()[0] == 1
    assert _current(team.db, "destination") == {"team_trip": "Muscat"}
    # A restore of the copy from before (through SQLite, as the Memory page's
    # restore copies): the journal forgets them again.
    team.db.close()
    with sqlite3.connect(str(before)) as source:
        target = sqlite3.connect(str(team.tmp / "memory_state.db"))
        source.backup(target)
        target.close()
    source.close()
    conn = sqlite3.connect(str(team.tmp / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) FROM beliefs WHERE subject = ? AND object != ''",
                            (f"member_{SARA}",)).fetchone()[0] == 2
        forget_journal.apply_pending(conn)
        assert conn.execute("SELECT COUNT(*) FROM beliefs WHERE subject = ? AND object != ''",
                            (f"member_{SARA}",)).fetchone()[0] == 0
    finally:
        conn.close()
    team.db = sqlite3.connect(str(team.tmp / "memory_state.db"), check_same_thread=False)


def test_control_the_entry_left_in_place_is_recalled(team) -> None:
    _say(SARA, "chat-sara", "I live in Kuwait City.", at=1_780_000_000.0)
    facts, _turns = _shown(OMAR, "Where does Sara live?")
    assert any("Kuwait City" in f for f in facts), facts


def _forget_route(asker: str, path: str, method: str = "POST", body: Any = None) -> dict[str, Any]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.memory_api import router

    app = FastAPI()
    app.include_router(router)
    with scope.bind_scope(TEAM, asker):
        return TestClient(app).request(method, path, json=body).json()


def test_another_members_turn_is_theirs_and_the_owners_to_forget(team) -> None:
    from kazma_core.memory.speakers import may_forget_turn

    _say(SARA, "chat-sara", "I live in Kuwait City.")
    eid = team.db.execute("SELECT id FROM episodes WHERE tenant_id = ?", (TEAM,)).fetchone()[0]
    assert not may_forget_turn(TEAM, OMAR, SARA)
    assert may_forget_turn(TEAM, SARA, SARA) and may_forget_turn(TEAM, OWNER_ACCOUNT_ID, SARA)
    assert _forget_route(OMAR, f"/api/memory/v2/episodes/{eid}/forget")["error"] == "not_yours"
    assert _forget_route(SARA, f"/api/memory/v2/episodes/{eid}/forget")["ok"]
    # A personal space: anyone who is there may.
    assert may_forget_turn(OWNER_SPACE_ID, OWNER_ACCOUNT_ID, None)


def test_a_fact_about_oneself_another_member_stated_goes_and_its_message_stays(team) -> None:
    from kazma_core.memory.forget import notes_for_forgotten_facts
    from kazma_core.memory.worker_bootstrap import _apply_if_still_remembered

    _say(OMAR, "chat-omar", "Sara's favourite food is machboos.", at=1_780_000_000.0)
    _apply(team, [{"subject": "Sara", "predicate": "favorite_food", "object": "machboos",
                   "predicate_type": "functional", "confidence": 0.9}],
           said_by=OMAR, method="llm_inferred", at=1_780_000_000.0)
    team.db.execute("UPDATE beliefs SET source_session = 'chat-omar' WHERE object = 'machboos'")
    team.db.commit()
    bid = team.db.execute("SELECT id FROM beliefs WHERE object = 'machboos'").fetchone()[0]
    notes = notes_for_forgotten_facts([bid], tenant_id=TEAM, asker=SARA)
    assert notes == [{"code": "others_message_stays", "author": "Omar", "belief_id": bid}]
    answer = _forget_route(SARA, "/api/memory/v2/beliefs/invalidate-batch", body={"ids": [bid]})
    assert answer["ok"] and answer["invalidated"] == 1
    # The message stays.
    assert team.db.execute("SELECT COUNT(*) FROM episodes WHERE session_id = 'chat-omar' AND tier != "
                           "'forgotten'").fetchone()[0] == 1
    # The deep pass of that message never files it again.
    eid = team.db.execute("SELECT id FROM episodes WHERE session_id = 'chat-omar'").fetchone()[0]
    stats = _apply_if_still_remembered(
        [{"subject": "Sara", "predicate": "favorite_food", "object": "Machboos", "predicate_type": "functional",
          "confidence": 0.9}], team.db, team.ops, stats={"applied": 0, "rejected": 0, "actions": []},
        episode_id=eid, now=1_780_000_900.0, tenant_id=TEAM, extraction_method="llm_inferred", said_by=OMAR)
    assert stats["applied"] == 0 and _current(team.db, "favorite_food") == {}


def test_a_shared_chat_is_kept_out_by_its_opener_or_the_owner(team) -> None:
    from kazma_core.memory.speakers import may_keep_chat_out
    from kazma_core.spaces import threads

    threads.record_thread("chat-shared", TEAM, SARA)
    assert may_keep_chat_out(TEAM, SARA, "chat-shared")
    assert may_keep_chat_out(TEAM, OWNER_ACCOUNT_ID, "chat-shared")
    assert not may_keep_chat_out(TEAM, OMAR, "chat-shared")
    assert _forget_route(OMAR, "/api/memory/v2/chats/chat-shared/memory", "PUT",
                         {"remember": False})["error"] == "not_yours"


# ── About me per member (N31) ────────────────────────────────────────────


def test_about_me_is_each_members_own_and_copies_once(team) -> None:
    from kazma_core.memory.profile import about_for_turn, set_about

    assert set_about("sara-own", "I am Sara; answer me in Arabic.")["ok"]
    copied = _forget_route(SARA, "/api/memory/v2/profile/copy-personal")
    assert copied == {"ok": True, "about": "I am Sara; answer me in Arabic."}
    assert _forget_route(OMAR, "/api/memory/v2/profile", "PUT", {"about": "I am Omar."})["ok"]
    assert about_for_turn(TEAM, SARA)["about"] == "I am Sara; answer me in Arabic."
    assert about_for_turn(TEAM, OMAR)["about"] == "I am Omar."
    seen = _forget_route(OMAR, "/api/memory/v2/profile", "GET")
    # The owner made the space and is in it; their account has no name yet.
    assert seen["shared"] is True and seen["members"] == ["owner", "Sara"] and seen["about"] == "I am Omar."
    # Independent after the copy.
    assert set_about("sara-own", "Changed later.")["ok"]
    assert about_for_turn(TEAM, SARA)["about"] == "I am Sara; answer me in Arabic."
    assert _forget_route(OMAR, "/api/memory/v2/profile/copy-personal")["code"] == "no_personal_space"


def test_a_shared_spaces_week_names_each_turns_author(team) -> None:
    from kazma_core.memory.topic_summaries import _load_turns, _messages

    _say(SARA, "chat-sara", "Please plan the team dinner for Friday.", at=1_780_000_000.0)
    _say(OMAR, "chat-omar", "Book the restaurant for the team dinner.", at=1_780_000_600.0)
    turns = _load_turns(team.db, TEAM, start=1_779_000_000.0, end=1_781_000_000.0)
    shown = _messages(turns, "2026-W22", 1_779_000_000.0)
    assert "] Sara: Please plan" in shown[1]["content"] and "] Omar: Book" in shown[1]["content"]
    assert "never merge two members" in shown[0]["content"]


# ── The held-out questions (the last step) ───────────────────────────────

HELDOUT_VECTORS = REPO / "tests" / "fixtures" / "memory_speakers" / "heldout_vectors.npz"


def test_the_heldout_questions_show_no_one_else_as_the_asker(monkeypatch) -> None:
    """The questions another session wrote over the case set before this
    item was built (Gulf Arabic above all, "I" as a prefix or a suffix,
    with traps). They decide, they never tune: their scores are reported
    (by language, kind, first-person form and dialect) and not gated, so no
    rule or threshold follows them. What never moves is gated: no line shows
    another member's fact or turn as the asker's. Their vectors were
    recorded with bge-m3 (``heldout_vectors.npz``); a question with no
    recorded vector would be searched by keywords alone, so each is held."""
    from kazma_core.memory.benchmark import SpeakerReplay, load_speakers, run_speaker_benchmark

    held = json.loads(HELDOUT.read_text(encoding="utf-8"))
    replay = SpeakerReplay(V5_VECTORS, SPEAKER_VECTORS, HELDOUT_VECTORS)
    for q in held["questions"]:
        replay.encode(q["text"])  # recorded, or KeyError
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    report = run_speaker_benchmark(load_speakers(), questions=held["questions"])
    assert report["overall"]["n"] == len(held["questions"])
    assert report["overall"]["misattributed"] == 0, report["overall"]
