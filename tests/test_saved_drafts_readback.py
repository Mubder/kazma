"""Saved drafts: the model can read them back, and each draft keeps its own state.

2026-09-25, asked "list me all remaining posts": 67 tool calls and an
operator-approved ``python_exec`` byte-dumping ``agent_artifacts.db``. The
model could save drafts (``save_proposal``) but had no tool to read them,
and every direct route to its own database is refused. Underneath sat a
second bug: posting ONE draft stamped the whole set ``proposal_posted``, so
after 4 of 11 went out the other 7 vanished from X Studio's inbox and moved
onto the 14-day age-out meant for spent sets — due for deletion around
2026-10-08.

These drive the real store, the real registered tool and the real
tool-registry classification. The class gates (every writer names a reader,
every store is declared, every door refuses and points at the reader) live
in ``tests/test_store_registry.py``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

ELEVEN = [f"draft {n}" for n in range(1, 12)]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    import kazma_core.agent.artifacts as artifacts_mod

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "kazma-data"))
    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(tmp_path / "kazma-data" / "agent_artifacts.db"))
    artifacts_mod.reset_artifact_store()
    yield artifacts_mod.get_artifact_store()
    artifacts_mod.reset_artifact_store()


def _kind(store, pid: str) -> str:
    with store._connect() as conn:
        return conn.execute(
            "SELECT kind FROM agent_artifacts WHERE key = ?", (f"proposal:{pid}",)
        ).fetchone()[0]


def _stamp_whole_set_posted(store, pid: str, *, age_days: float = 0.0) -> None:
    """What the pre-2026-09-25 build wrote: the ROW flipped, no item marks."""
    with store._connect() as conn:
        conn.execute(
            "UPDATE agent_artifacts SET kind = 'proposal_posted', updated_at = ? "
            "WHERE key = ?",
            (time.time() - age_days * 86400, f"proposal:{pid}"),
        )


# ── per-item state ────────────────────────────────────────────────────────


def test_posting_one_draft_leaves_the_rest_listed(store):
    pid = store.save_proposal("default", "t1", "tweets", ELEVEN)["proposal_id"]
    for n in (1, 4, 7, 10):
        assert store.proposal_posted(f"{pid}:{n}", via="x_post", used_ref=f"t{n}") == 1

    left = [r["id"] for r in store.list_proposals()]
    assert left == [f"{pid}:{n}" for n in (2, 3, 5, 6, 8, 9, 11)]
    assert _kind(store, pid) == "proposal", "the set was stamped used with 7 drafts unposted"


def test_the_set_is_used_only_when_its_last_draft_is(store):
    pid = store.save_proposal("default", "t1", "tweets", ["a", "b"])["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post")
    assert _kind(store, pid) == "proposal"
    store.proposal_posted(f"{pid}#2", via="x_schedule_post")
    assert _kind(store, pid) == "proposal_posted"
    assert store.list_proposals() == []
    both = store.list_proposals(include_posted=True)
    assert {r["used_via"] for r in both} == {"x_post", "x_schedule_post"}


def test_marking_twice_does_not_move_the_first_mark(store):
    pid = store.save_proposal("default", "t1", "tweets", ["a", "b"])["proposal_id"]
    assert store.proposal_posted(f"{pid}:1", via="x_post", used_ref="first") == 1
    assert store.proposal_posted(f"{pid}:1", via="x_studio_post", used_ref="second") == 0
    item = store.resolve_proposal(f"{pid}:1")["items"][0]
    assert (item["used_via"], item["used_ref"]) == ("x_post", "first")


def test_gc_keeps_a_partly_posted_set(store):
    """The age-out for spent sets must never take unposted drafts with it."""
    pid = store.save_proposal("default", "t1", "tweets", ELEVEN)["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post")
    with store._connect() as conn:  # 20 days old: past the 14-day spent-set horizon
        conn.execute(
            "UPDATE agent_artifacts SET updated_at = ? WHERE key = ?",
            (time.time() - 20 * 86400, f"proposal:{pid}"),
        )
    store.gc_sweep()
    assert len(store.list_proposals()) == 10


def test_a_bare_set_id_is_not_one_draft(store):
    """One publish, one draft — the chat gate's rule, now X Studio's too."""
    multi = store.save_proposal("default", "t1", "tweets", ["a", "b"])["proposal_id"]
    single = store.save_proposal("default", "t1", "tweets", ["only"])["proposal_id"]
    assert store.stored_text_for(multi) is None
    assert store.stored_text_for(f"{multi}:2") == "b"
    assert store.stored_text_for(single) == "only"


# ── healing sets an earlier build stamped whole ───────────────────────────


def test_heal_gives_back_drafts_with_no_evidence_of_going_out(store):
    pid = store.save_proposal("default", "t1", "tweets", ELEVEN)["proposal_id"]
    _stamp_whole_set_posted(store, pid)
    assert store.list_proposals() == []
    assert store.legacy_posted_count() == 1

    went_out = {f"draft {n}": (f"tw{n}", 1000.0 + n) for n in (1, 4, 7, 10)}

    def evidence(tenant, text):
        hit = went_out.get(text)
        return ("x_post", hit[1], hit[0]) if hit else None

    report = store.heal_legacy_posted(evidence)
    assert report == {"rows_healed": 1, "items_restored": 7, "rows_unproven": 0}
    assert [r["id"].rsplit(":", 1)[1] for r in store.list_proposals()] == [
        "2", "3", "5", "6", "8", "9", "11"
    ]
    one = store.resolve_proposal(f"{pid}:4")["items"][0]
    assert (one["used_via"], one["used_ref"]) == ("x_post", "tw4")
    assert store.legacy_posted_count() == 0


def test_heal_never_guesses(store):
    """No evidence for any draft: the row stays exactly as it was."""
    pid = store.save_proposal("default", "t1", "tweets", ["a", "b"])["proposal_id"]
    _stamp_whole_set_posted(store, pid)
    report = store.heal_legacy_posted(lambda tenant, text: None)
    assert report["rows_unproven"] == 1 and report["rows_healed"] == 0
    assert _kind(store, pid) == "proposal_posted"


def test_heal_leaves_per_item_sets_alone(store):
    pid = store.save_proposal("default", "t1", "tweets", ["a"])["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post")
    calls = []
    store.heal_legacy_posted(lambda tenant, text: calls.append(text))
    assert calls == [], "a set that already carries per-item state is not legacy"


def test_heal_runs_on_startup_from_kazmas_own_x_records(tmp_path, monkeypatch):
    """The evidence is the X post ledger and the schedule, read from disk."""
    import kazma_core.agent.artifacts as artifacts_mod
    from kazma_core.x_api.ledger import XPostLedger
    from kazma_core.x_api.schedule import XScheduledStore

    data = tmp_path / "kazma-data"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(data / "agent_artifacts.db"))
    artifacts_mod.reset_artifact_store()
    first = artifacts_mod.get_artifact_store()
    pid = first.save_proposal("default", "t1", "tweets", ["posted one", "booked one", "still here"])["proposal_id"]
    _stamp_whole_set_posted(first, pid)

    XPostLedger(data / "x_posts.db").record(tweet_id="999", text="Posted  ONE")  # normalised match
    XScheduledStore(data / "x_scheduled.db").add(
        text="booked one", fire_at=time.time() + 3600, tz="", reply_to_id="",
        thread_id="t1", delivery_target="", tenant_id="default",
    )

    artifacts_mod.reset_artifact_store()  # a restart
    healed = artifacts_mod.get_artifact_store()
    try:
        assert [r["text"] for r in healed.list_proposals()] == ["still here"]
        vias = {i["text"]: i.get("used_via") for i in healed.resolve_proposal(pid)["items"]}
        assert vias == {"posted one": "x_post", "booked one": "x_schedule_post", "still here": None}
    finally:
        artifacts_mod.reset_artifact_store()


def test_no_x_records_means_no_x_files_are_created(store, tmp_path):
    """A heal must never create an X store on an install that never used X."""
    pid = store.save_proposal("default", "t1", "tweets", ["a"])["proposal_id"]
    _stamp_whole_set_posted(store, pid)
    import kazma_core.agent.artifacts as artifacts_mod

    artifacts_mod._heal_once(store)
    data = tmp_path / "kazma-data"
    assert not (data / "x_posts.db").exists()
    assert not (data / "x_scheduled.db").exists()


# ── the model's reader ────────────────────────────────────────────────────


def _registered_tool(name):
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry._tools[name].func


def test_list_proposals_is_a_registered_read_tier_tool():
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.safety.side_effects import EffectKind, get_effect_profile

    assert _registered_tool("list_proposals") is not None
    assert TOOL_TIERS["list_proposals"] == "read", "reading back what it saved must never cost an approval"
    assert get_effect_profile("list_proposals").effect is EffectKind.READ


def test_list_proposals_returns_what_save_proposal_saved(store):
    save = _registered_tool("save_proposal")
    read = _registered_tool("list_proposals")

    saved = asyncio.run(save("tweets", ["first draft\nwith two lines", "second draft"]))
    pid = saved.split("Proposal saved: ", 1)[1].split(" ", 1)[0]
    assert "list_proposals" in saved, "the save result should say how to read it back"

    out = asyncio.run(read())
    assert "2 unused" in out
    assert f"[{pid}:1] unused" in out and f"[{pid}:2] unused" in out
    assert "first draft\n" in out and "with two lines" in out, "text must come back verbatim"
    assert "untrusted" in out, "drafts can quote pages and other people's tweets: fence them"


def test_list_proposals_shows_what_happened_to_each_draft(store):
    read = _registered_tool("list_proposals")
    pid = store.save_proposal("default", "", "tweets", ["a", "b", "c"])["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post", used_ref="2103")
    store.proposal_posted(f"{pid}:2", via="x_schedule_post", used_ref="12")

    unused_only = asyncio.run(read())
    assert f"[{pid}:3] unused" in unused_only
    assert f"{pid}:1]" not in unused_only
    assert "1 of 3 unused" in unused_only

    everything = asyncio.run(read(include_used=True))
    assert f"[{pid}:1] posted" in everything and "(tweet 2103)" in everything
    assert f"[{pid}:2] scheduled" in everything and "(booking #12)" in everything

    one = asyncio.run(read(proposal_id=f"{pid}:2"))
    assert f"[{pid}:2] scheduled" in one and f"{pid}:1]" not in one

    missing = asyncio.run(read(proposal_id="prop_nope"))
    assert "does not match a saved draft" in missing


def test_the_reader_output_is_bounded_per_draft(store):
    """One set can hold 128 drafts of up to 8000 chars; the reader must not
    return megabytes. It stops at a budget and says how to read the rest."""
    from kazma_core.agent.artifacts import describe_proposals

    pid = store.save_proposal("default", "t", "tweets", ["x" * 7000] * 10)["proposal_id"]
    body = describe_proposals(store.list_proposal_sets(), include_used=False)
    assert len(body) < 30_000
    assert f"[{pid}:1] unused" in body
    assert "7 more item(s) not shown" in body
    assert "proposal_id=<set id>" in body


def test_list_proposals_is_tenant_scoped(store):
    from kazma_core.tenant_context import tenant_scope

    read = _registered_tool("list_proposals")
    store.save_proposal("tenant-a", "", "tweets", ["for a only"])
    with tenant_scope("tenant-b"):
        other = asyncio.run(read())
    with tenant_scope("tenant-a"):
        own = asyncio.run(read())
    assert "for a only" not in other
    assert "for a only" in own


# ── a refused post is a failed call ───────────────────────────────────────


def test_ok_false_json_is_classified_as_an_error():
    from kazma_core.agent.tool_registry import _json_reports_failure

    assert _json_reports_failure(json.dumps({"ok": False, "posted": False, "error": "dup"}))
    assert _json_reports_failure('  {"ok": false}')
    # Negative controls: only an explicit boolean false fails a call.
    assert not _json_reports_failure(json.dumps({"ok": True, "posted": True}))
    assert not _json_reports_failure(json.dumps({"posted": False}))
    assert not _json_reports_failure(json.dumps({"ok": "false"}))
    assert not _json_reports_failure("[1, 2]")
    assert not _json_reports_failure("ok: false")


def test_the_registry_reports_a_refused_post_as_failed(monkeypatch):
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety import side_effects
    from kazma_core.safety.hitl import TOOL_TIERS

    # Classified reads, so the approval and commitment layers let both run
    # and the ONLY difference between them is what they return. (Unclassified,
    # both are refused before running — "failed" for the wrong reason.)
    for name in ("refuses_tool", "succeeds_tool"):
        monkeypatch.setitem(TOOL_TIERS, name, "read")
        monkeypatch.setitem(
            side_effects._PROF, name,
            (side_effects.EffectKind.READ, side_effects.SemanticTier.NONE, None, ()),
        )
    registry = LocalToolRegistry()

    async def refuses() -> str:
        return json.dumps({"ok": False, "posted": False, "error": "duplicate"})

    async def succeeds() -> str:
        return json.dumps({"ok": True, "posted": True, "tweet_id": "1"})

    registry.register_function("refuses_tool", refuses, description="t", category="test")
    registry.register_function("succeeds_tool", succeeds, description="t", category="test")
    bad = asyncio.run(registry.execute("refuses_tool", {}))
    good = asyncio.run(registry.execute("succeeds_tool", {}))
    assert bad["is_error"] is True
    assert good["is_error"] is False


def test_x_status_lists_as_many_recent_posts_as_asked(tmp_path, monkeypatch):
    """x_status(recent=N) is the model's read path for the post ledger."""
    import kazma_core.x_api.ledger as ledger_mod
    from kazma_skills.native.x_publisher import tools as x_tools

    ledger = ledger_mod.XPostLedger(tmp_path / "x_posts.db")
    for n in range(8):
        ledger.record(tweet_id=str(n), text=f"post {n}")
    monkeypatch.setattr(x_tools, "get_ledger", lambda: ledger)
    out = json.loads(asyncio.run(x_tools.x_status(recent=7)))
    assert len(out["recent_posts"]) == 7
    default = json.loads(asyncio.run(x_tools.x_status()))
    assert len(default["recent_posts"]) == 5


def test_the_sqlite_file_is_left_consistent(store):
    """Marks are written in one transaction per call (two surfaces, one set)."""
    pid = store.save_proposal("default", "t1", "tweets", ["a", "b"])["proposal_id"]
    store.proposal_posted(f"{pid}:1", via="x_post")
    store.proposal_posted(f"{pid}:2", via="x_studio_post")
    conn = sqlite3.connect(store._db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    assert _kind(store, pid) == "proposal_posted"
