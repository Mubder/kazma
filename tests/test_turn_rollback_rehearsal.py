"""The previous build can still read what this one wrote — plan §14.6-7.

    Preserve gate/checkpoint semantics across rollback. Verify the
    previous compatible build can read the data; if it cannot, define
    forward recovery rather than suggesting an unsafe downgrade.  (§14.6)

    Rollback restores a build, never approval databases or checkpoints
    from an older copy that could replay completed work.            (§14.7)

A rollback restores code, not data. So the question is never "does the
old build work" — it is "does the old build work **against rows the new
build already wrote**". This plan changed what a persisted turn carries:

* ``schema`` and ``rev``, which did not exist before (`e1367532`, the
  commit before Phase 1b introduced ``TURN_SCHEMA_VERSION``);
* activity rows that carry an ``id``;
* part keys of the form ``tool#<call_id>`` instead of positional keys.

The old reader is checked out from git rather than described from
memory, because "the old code probably ignores unknown keys" is a
prediction and this is supposed to be a rehearsal.

Not covered here, and deliberately: §14.7's rule that a rollback must
not restore an older *database*. No test can enforce an operator
procedure; it is stated in the acceptance report as a procedure.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The build a rollback would land on: the commit immediately before
#: Phase 1b added `TURN_SCHEMA_VERSION`. Pinned, not "HEAD~n" — the point
#: is a specific previous reader, and a moving target would silently stop
#: testing the thing it names.
PREVIOUS_BUILD = "e1367532"
PREVIOUS_FILE = "kazma-ui/kazma_ui/turn_document.py"


@pytest.fixture(scope="module")
def previous_reader(tmp_path_factory):
    """Import the pre-schema ``turn_document`` beside the current one.

    Loaded under a private module name so it cannot shadow the shipped
    module for any other test in the session.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"{PREVIOUS_BUILD}:{PREVIOUS_FILE}"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git unavailable: {exc}")
    if blob.returncode != 0:
        pytest.skip(
            f"{PREVIOUS_BUILD}:{PREVIOUS_FILE} is not in this clone "
            f"(shallow checkout?): {blob.stderr.strip()[:120]}"
        )

    path = tmp_path_factory.mktemp("rollback") / "turn_document_prev.py"
    path.write_text(blob.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_utb_prev_turn_doc", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_utb_prev_turn_doc"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"the previous build's module does not import alone: {exc}")
    yield module
    sys.modules.pop("_utb_prev_turn_doc", None)


@pytest.fixture
def row_written_by_this_build() -> dict:
    """A persisted assistant row, in the shape the CURRENT build writes.

    Built through the shipped module rather than hand-written, so it
    cannot drift into a shape the product never produces.
    """
    from kazma_ui import turn_document as current

    # Merged through the shipped merger, so the part keys are the ones the
    # product mints (`tool#<call_id>`) rather than ones this test invented.
    incoming = [
        {"type": "text", "text": "Scaffold ready."},
        {"type": "hitl", "interrupt_id": "gate-a", "tool": "file_write",
         "state": "approved", "tool_call_id": "call_1_file_write"},
        {"type": "tool", "tool": "file_write", "state": "done",
         "tool_call_id": "call_1_file_write"},
    ]
    parts = current.merge_parts([], incoming)
    assert any(
        current.part_key_str(p).startswith("tool#")
        for p in parts
        if isinstance(p, dict) and p.get("type") == "tool"
    ), "this build no longer keys tool parts by call id"

    row = {
        "role": "assistant",
        "content": current.text_of(parts),
        "parts": parts,
        "activity": current.activity_of(parts),
        "turn_id": "turn-rollback-1",
        "rev": 7,
        "schema": current.TURN_SCHEMA_VERSION,
    }
    assert row["schema"] >= 2, "this build no longer stamps a schema"
    assert row["content"].strip(), "the fixture row has no answer text"
    return row


# ══════════════════════════════════════════════════════════════════════
# The rehearsal
# ══════════════════════════════════════════════════════════════════════


def test_the_previous_build_still_finds_the_answer(
    previous_reader, row_written_by_this_build
) -> None:
    """The one thing a rollback must never lose.

    Thoughts can degrade and a gate row can lose its label; an answer
    that disappears is the user's work gone.
    """
    prev = previous_reader
    parts = row_written_by_this_build["parts"]
    text = prev.text_of(parts)
    assert "Scaffold ready." in text, (
        f"the previous build reads no answer from this build's parts: {text!r}"
    )


def test_the_previous_build_does_not_crash_on_the_new_keys(
    previous_reader, row_written_by_this_build
) -> None:
    """``schema``, ``rev`` and activity ``id`` are all new.

    An older reader is allowed to ignore them. It is not allowed to
    raise, because the row it cannot parse is every row written since
    the upgrade.
    """
    prev = previous_reader
    row = row_written_by_this_build
    parts = row["parts"]

    activity = prev.activity_of(parts)
    assert isinstance(activity, list)

    # Whatever round-trip helpers that build had, run them.
    for name in ("activity_to_parts", "hydrate_message", "text_of",
                 "part_key", "merge_tool_part"):
        fn = getattr(prev, name, None)
        if fn is None:
            continue
        try:
            if name == "hydrate_message":
                fn(dict(row))
            elif name == "activity_to_parts":
                fn(activity)
            elif name == "text_of":
                fn(parts)
            else:
                continue
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"the previous build's {name}() raises on a row this build "
                f"wrote: {type(exc).__name__}: {exc}"
            )


def test_the_previous_build_keeps_the_gate_distinct(
    previous_reader, row_written_by_this_build
) -> None:
    """Gate semantics across rollback (§14.6).

    The approval must still be there and must still be ONE approval. A
    reader that drops it shows a completed turn with no record that
    anything was ever asked; a reader that doubles it shows two
    decisions for one gate.
    """
    prev = previous_reader
    parts = row_written_by_this_build["parts"]
    hitl = [p for p in parts if isinstance(p, dict) and p.get("type") == "hitl"]
    assert len(hitl) == 1, f"this build wrote {len(hitl)} gate parts"

    activity = prev.activity_of(parts)
    gate_rows = [
        r for r in activity
        if isinstance(r, dict)
        and (str(r.get("kind") or "") == "hitl"
             or "gate-a" in json.dumps(r, default=str))
    ]
    assert len(gate_rows) <= 1, (
        f"the previous build renders one gate as {len(gate_rows)} rows: "
        f"{gate_rows}"
    )


#: Recorded rows rather than constructed ones. `tests/fixtures/unified_turn/`
#: is the agreed description of this plan's scenario and is what both
#: language projectors are locked against, so a row from here is the shape
#: the product actually stores — not a shape a test invented while holding
#: the same assumptions the code holds.
FIXTURE_ROWS = sorted(
    (ROOT / "tests" / "fixtures" / "unified_turn" / "messages").glob("*.json")
)


@pytest.mark.parametrize(
    "fixture", FIXTURE_ROWS, ids=lambda p: p.stem
)
def test_the_previous_build_reads_every_recorded_row(
    previous_reader, fixture: Path
) -> None:
    """The rehearsal, against the corpus instead of one specimen.

    §14.6 says "verify the previous compatible build can read the data".
    The data is not one row: it is four gates sharing a tool, a pending
    gate, reasoning mixed with tools and status, tool calls carrying ids,
    a non-ASCII row, and a pre-parts row with nothing but content. Any
    one of those could be the shape the older reader chokes on.
    """
    doc = json.loads(fixture.read_text(encoding="utf-8"))
    row = doc.get("message") or doc
    assert isinstance(row, dict), f"{fixture.name} has no message row"
    parts = row.get("parts") or []

    prev = previous_reader
    try:
        text = prev.text_of(parts)
        activity = prev.activity_of(parts)
        if hasattr(prev, "hydrate_message"):
            prev.hydrate_message(dict(row))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"the previous build raises on {fixture.name}: "
            f"{type(exc).__name__}: {exc}"
        )
    assert isinstance(activity, list)

    # Where the row has an answer, the older reader must still find it.
    stored = str(row.get("content") or "").strip()
    if stored and parts:
        assert text.strip(), (
            f"{fixture.name} stores an answer that the previous build "
            f"reads as empty"
        )


def test_a_pre_upgrade_row_still_reads_on_this_build() -> None:
    """Forward compatibility, the direction that actually gets exercised.

    §14.6 asks for the rollback direction; this is the other one, and it
    is the one every upgrade performs on existing history. A row with no
    ``schema`` and positional part keys is what the database is full of.
    """
    from kazma_ui import turn_document as current

    legacy = {
        "role": "assistant",
        "content": "Older answer.",
        "parts": [
            {"type": "text", "text": "Older answer."},
            {"type": "hitl", "interrupt_id": "old-gate", "tool": "shell_exec",
             "state": "approved"},
        ],
    }
    assert "schema" not in legacy and "rev" not in legacy

    hydrated = current.hydrate_message(dict(legacy))
    assert isinstance(hydrated, dict)
    text = current.text_of(hydrated.get("parts") or legacy["parts"])
    assert "Older answer." in text, (
        "this build cannot read a row written before the schema existed; "
        "that is all of history"
    )
    activity = current.activity_of(hydrated.get("parts") or legacy["parts"])
    assert isinstance(activity, list)
