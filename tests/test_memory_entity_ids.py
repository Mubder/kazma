"""An entity's id keeps its script (Memory audit M02, MEM-R01).

The id rule kept ``a-z0-9`` and fell back to the constant ``"entity"``: every
all-Arabic, Persian or CJK subject became ``entity``, so "شركة الخليج
located_in الكويت" was superseded by "مطعم البحر located_in دبي", and once one
non-Latin object entity existed, "user works_at أرامكو" was stored as
``user works_at entity``. Four copies of the rule (entity resolution, the
belief writer, the mirror, the V1 backfill) and a fifth in the Memory API.

Here: the audit's two witnesses inverted (their English negative controls
kept), the same through Persian, CJK and emoji; one id helper and no constant
fallback, read from the source with the old rule as the negative control; the
sibling ASCII-only rules (service names, the same-words key); and the repair
of rows the old rule wrote, run on a copy.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]

OLD_RULE = '''
def _slug(text: str) -> str:
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "entity"
'''


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, ops=ops, dir=tmp_path)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _current(db, tenant: str) -> dict[str, str]:
    return {
        r["predicate"]: r["object"]
        for r in db.execute(
            "SELECT predicate, object FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL "
            "AND invalidated_at IS NULL", (tenant,))
    }


def _subjects(db, tenant: str) -> list[tuple[str, str]]:
    return [(r["subject"], r["object"]) for r in db.execute(
        "SELECT subject, object FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL "
        "AND invalidated_at IS NULL ORDER BY valid_from", (tenant,))]


# ── The id rule ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("label", "expected"), [
    ("John Smith", "john_smith"),
    ("ShipX — Deployment Modes", "shipx_deployment_modes"),
    ("v2.4.0", "v2_4_0"),
    ("شركة الخليج", "شركة_الخليج"),
    ("أحمد Smith", "أحمد_smith"),
    ("東京タワー", "東京タワー"),
    ("کتاب‌خانه", "کتاب_خانه"),
    ("किताब घर", "किताब_घर"),  # Devanagari vowel signs are marks: kept
])
def test_an_id_keeps_every_scripts_letters(label, expected):
    from kazma_core.memory.entity_resolution import slug

    assert slug(label) == expected


def test_labels_with_no_letter_get_their_own_id_and_an_empty_label_none():
    from kazma_core.memory.entity_resolution import slug

    party, confetti = slug("🎉"), slug("🎊")
    assert party != confetti and party.startswith("x_") and slug("🎉") == party
    with pytest.raises(ValueError):
        slug("   ")


def test_ascii_ids_are_unchanged():
    """Every id an install already holds for an ASCII label is what the old
    rule gave it (the change is invisible to them)."""
    from kazma_core.memory.entity_resolution import slug

    old = {}
    exec(OLD_RULE, old)  # noqa: S102 - the rule this file replaces, run on fixed labels
    for label in ("John Smith", "gpt-4", "user", "ShipX  Deployment", "a__b", "x" * 120, "Grok_Next-Reset"):
        assert slug(label) == old["_slug"](label), label


def test_vowel_marks_and_letter_forms_meet_in_the_alias_hash():
    from kazma_core.memory.entity_resolution import alias_hash

    assert alias_hash("أحمد") == alias_hash("أَحْمَد") == alias_hash("احمد")
    assert alias_hash("John  Smith") == alias_hash("john smith")


# ── The audit's witnesses, inverted ───────────────────────────────────────


def test_r01_arabic_subjects_keep_their_own_facts(mem):
    from kazma_core.memory.belief_mutation import mutate_belief

    kw = dict(ops_conn=mem.ops, predicate_type="functional", confidence=0.9, importance=3,
              tenant_id="t", private=True)
    a = mutate_belief(mem.db, "شركة الخليج", "located_in", "الكويت", now=1000.0, **kw)
    b = mutate_belief(mem.db, "مطعم البحر", "located_in", "دبي", now=2000.0, **kw)
    assert b["superseded_id"] is None and a["belief_id"] != b["belief_id"]
    assert _subjects(mem.db, "t") == [("شركة_الخليج", "الكويت"), ("مطعم_البحر", "دبي")]
    # The English negative control stays two current facts.
    mutate_belief(mem.db, "Gulf Company", "located_in", "Kuwait", now=1000.0, **{**kw, "tenant_id": "c"})
    mutate_belief(mem.db, "Sea Restaurant", "located_in", "Dubai", now=2000.0, **{**kw, "tenant_id": "c"})
    assert len(_subjects(mem.db, "c")) == 2


def _fact(pred, obj, subject="user"):
    return [{"subject": subject, "predicate": pred, "predicate_type": "functional",
             "object": obj, "confidence": 0.9, "importance": 4}]


def test_r01_an_arabic_object_keeps_its_value(mem):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    _apply_beliefs_to_v2(_fact("lives_in", "الكويت"), mem.db, mem.ops, tenant_id="t", now=1000.0)
    _apply_beliefs_to_v2(_fact("works_at", "أرامكو"), mem.db, mem.ops, tenant_id="t", now=2000.0)
    assert _current(mem.db, "t") == {"lives_in": "الكويت", "works_at": "أرامكو"}
    _apply_beliefs_to_v2(_fact("lives_in", "Kuwait"), mem.db, mem.ops, tenant_id="e", now=1000.0)
    _apply_beliefs_to_v2(_fact("works_at", "Aramco"), mem.db, mem.ops, tenant_id="e", now=2000.0)
    assert _current(mem.db, "e") == {"lives_in": "Kuwait", "works_at": "Aramco"}


@pytest.mark.parametrize(("first", "second"), [
    (("کتابخانه مرکزی", "تهران"), ("رستوران دریا", "شیراز")),  # Persian
    (("东京塔", "东京"), ("大阪城", "大阪")),  # CJK
    (("🎉", "party"), ("🎊", "parade")),  # emoji
])
def test_subjects_in_every_script_stay_apart_through_extraction(mem, first, second):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    _apply_beliefs_to_v2(_fact("located_in", first[1], first[0]), mem.db, mem.ops, tenant_id="t", now=1000.0)
    _apply_beliefs_to_v2(_fact("located_in", second[1], second[0]), mem.db, mem.ops, tenant_id="t", now=2000.0)
    current = [(r[0], r[1]) for r in mem.db.execute(
        "SELECT subject, object FROM beliefs WHERE tenant_id = 't' AND predicate = 'located_in' "
        "AND valid_until IS NULL AND invalidated_at IS NULL ORDER BY valid_from")]
    assert [o for _s, o in current] == [first[1], second[1]]  # neither superseded the other
    assert len({s for s, _o in current}) == 2 and "entity" not in {s for s, _o in current}


def test_an_empty_subject_is_refused_not_filed_under_a_shared_id(mem):
    from kazma_core.memory.belief_mutation import mutate_belief

    out = mutate_belief(mem.db, "   ", "located_in", "nowhere", ops_conn=mem.ops, tenant_id="t", private=True)
    # Refused by the subject hygiene check, or by the writer's own guard.
    assert out["action"] == "noop" and out["rejected"] in ("blocked_subject", "empty_subject")
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


# ── Siblings ──────────────────────────────────────────────────────────────


def test_a_service_named_in_arabic_keeps_its_name():
    from kazma_core.memory.current_facts import normalize_service_slug

    assert normalize_service_slug("نتفليكس") == "نتفليكس"
    assert normalize_service_slug("Netflix weekly") == "netflix"
    assert normalize_service_slug("SuperGrok") == normalize_service_slug("supergrok")


def test_arabic_entities_are_not_one_duplicate_group():
    from kazma_core.memory.graph_hygiene import _same_words_key

    assert _same_words_key("شركة_الخليج") != _same_words_key("مطعم_البحر")
    assert _same_words_key("grok_next_reset") == _same_words_key("next_grok_reset")


# ── The class gate: one id rule, no constant fallback ─────────────────────


def _rule_copies(sources: dict[str, str]) -> list[str]:
    """Functions holding an ``a-z0-9`` keep-list regex, and ``or "entity"``
    constant fallbacks, in the given sources."""
    found = []
    for rel, text in sources.items():
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and "[^a-z0-9" in node.value:
                    found.append(f"{rel}:{fn.name} keeps a-z0-9 only")
                if (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
                        and any(isinstance(v, ast.Constant) and v.value == "entity" for v in node.values)):
                    found.append(f"{rel}:{fn.name} falls back to 'entity'")
    return sorted(set(found))


def _memory_sources() -> dict[str, str]:
    paths = [*(REPO / "kazma-core" / "kazma_core" / "memory").glob("*.py"),
             REPO / "kazma-ui" / "kazma_ui" / "memory_api.py",
             REPO / "kazma-ui" / "kazma_ui" / "routes_direct" / "memory.py",
             REPO / "kazma-core" / "kazma_core" / "agent" / "tool_builtins" / "memory.py"]
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in paths}


def test_memory_code_has_one_id_rule():
    assert _rule_copies(_memory_sources()) == []


def test_the_old_rule_is_caught():
    """Negative control: the rule the four copies held."""
    assert _rule_copies({"old.py": OLD_RULE}) == [
        "old.py:_slug falls back to 'entity'", "old.py:_slug keeps a-z0-9 only"]


def test_every_id_rule_name_is_the_one_helper():
    """``_slug`` / ``_entity_slug`` survive as names of the one rule (callers
    and tests import them), never as their own copies."""
    from kazma_core.memory import backfill_v2, belief_mutation, dual_write, entity_resolution

    for module in (belief_mutation, backfill_v2):
        assert module._slug is entity_resolution.slug, module.__name__
    # The episode writer no longer makes ids from names (its belief writer is
    # gone, Memory audit M26): no copy of the rule either.
    assert getattr(dual_write, "_slug", entity_resolution.slug) is entity_resolution.slug


# ── The repair of what the old rule wrote, on a copy ──────────────────────


def _old_rows(mem) -> dict[str, str]:
    """What the old rule left: two Arabic subjects filed under ``entity`` (the
    second superseded the first), an object rewritten to ``entity``, a node
    called ``entity``, one fact whose turn is gone, and an English fact about
    a real "entity"."""
    db = mem.db
    rows = {}
    turns = [("s1", 1, "شركة الخليج في الكويت", "حسنًا."), ("s1", 2, "مطعم البحر في دبي", "تمام."),
             ("s2", 1, "أعمل في أرامكو", "رائع."), ("s3", 1, "the legal entity is Acme LLC", "Noted.")]
    for sess, turn, q, a in turns:
        db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                   "tier, created_at) VALUES (?, 'default', ?, ?, ?, ?, 'episodic', 1000)",
                   (f"ep-{sess}-{turn}", sess, turn, q, a))
    facts = [
        ("b1", "entity", "located_in", "الكويت", "s1", 1, 1000.0, 2000.0),
        ("b2", "entity", "located_in", "دبي", "s1", 2, 2000.0, None),
        ("b3", "user", "works_at", "entity", "s2", 1, 3000.0, None),
        ("b4", "entity", "founded_in", "1990", "gone", 9, 3000.0, None),
        ("b5", "entity", "is_named", "Acme LLC", "s3", 1, 4000.0, None),
    ]
    for bid, sub, pred, obj, sess, turn, vf, vu in facts:
        db.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
                   "valid_from, valid_until, ingested_at, source_session, source_turn, extraction_method, "
                   "metadata_json) VALUES (?, 'default', ?, ?, 'functional', ?, 0.9, ?, ?, ?, ?, ?, "
                   "'llm_inferred', '{}')", (bid, sub, pred, obj, vf, vu, vf, sess, turn))
        rows[bid] = obj
    db.execute("INSERT INTO entities (id, tenant_id, type, name, aliases_json) "
               "VALUES ('entity', 'default', 'concept', 'entity', '[]')")
    db.commit()
    return rows


def test_the_repair_sizes_first_then_quarantines_and_queues_the_turns(mem):
    from kazma_core.memory.entity_repair import repair_collapsed, size_collapsed

    _old_rows(mem)
    size = size_collapsed(mem.db)
    assert (size["facts"], size["rederivable"], size["unrecoverable"], size["ambiguous"]) == (5, 3, 1, 1)
    assert size["turns_to_extract_again"] == 3 and size["entity_node"] == ["default"]
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs WHERE metadata_json != '{}'").fetchone()[0] == 0  # read only

    queued = []
    out = repair_collapsed(mem.db, enqueue=lambda kind, payload: queued.append((kind, payload)) or "t1")
    assert out["quarantined"] == 4 and out["turns_queued"] == 3 and out["ambiguous_left"] == 1
    assert sorted(p["episode_id"] for _k, p in queued) == ["ep-s1-1", "ep-s1-2", "ep-s2-1"]
    assert {k for k, _p in queued} == {"micro_consolidation"}
    current = {r[0] for r in mem.db.execute(
        "SELECT id FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL")}
    assert current == {"b5"}  # only the English fact about a real entity
    quarantined = [json.loads(r[0]) for r in mem.db.execute(
        "SELECT metadata_json FROM beliefs WHERE id IN ('b1','b2','b3','b4')")]
    assert all(m["quarantined"]["reason"] == "collapsed_entity_id" for m in quarantined)
    # Nothing is lost: the rows stay, with the values they had.
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 5

    again = repair_collapsed(mem.db, enqueue=lambda *a: pytest.fail("queued twice"))
    assert (again["quarantined"], again["already_done"]) == (0, 4)


def test_the_requeued_turn_is_extracted_under_the_fixed_ids(mem):
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2
    from kazma_core.memory.entity_repair import repair_collapsed

    _old_rows(mem)
    repair_collapsed(mem.db, queue=False)
    # The deep pass's apply step on the requeued turn (the LLM's facts given).
    _apply_beliefs_to_v2(_fact("located_in", "الكويت", "شركة الخليج"), mem.db, mem.ops,
                         session_id="s1", turn=1, tenant_id="default", now=1000.0)
    _apply_beliefs_to_v2(_fact("located_in", "دبي", "مطعم البحر"), mem.db, mem.ops,
                         session_id="s1", turn=2, tenant_id="default", now=2000.0)
    current = {(r[0], r[1]) for r in mem.db.execute(
        "SELECT subject, object FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL "
        "AND predicate = 'located_in'")}
    assert current == {("شركة_الخليج", "الكويت"), ("مطعم_البحر", "دبي")}


def test_the_script_sizes_without_writing(mem, capsys, monkeypatch):
    import importlib.util

    # No test reads a real .env (AGENTS.md section 38).
    monkeypatch.setattr("kazma_core.env_files.load_env_files", lambda **_k: [])
    _old_rows(mem)
    mem.db.close()
    spec = importlib.util.spec_from_file_location("memory_entity_repair", REPO / "scripts" / "memory_entity_repair.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    path = str(mem.dir / "memory_state.db")
    assert script.main(["size", "--db", path]) == 0
    assert json.loads(capsys.readouterr().out)["facts"] == 5
    assert script.main(["apply", "--db", path]) == 2  # refused without --yes
    assert script.main(["apply", "--db", path, "--yes", "--no-queue"]) == 0
    check = sqlite3.connect(path)
    try:
        assert check.execute("SELECT COUNT(*) FROM beliefs WHERE metadata_json LIKE '%quarantined%'").fetchone()[0] == 4
    finally:
        check.close()
    mem.db = sqlite3.connect(path)  # the fixture closes it
