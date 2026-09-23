"""A NUL character in tool output must not stop a chat from saving.

Postgres cannot store U+0000 in ``text`` and rejects its escape in ``jsonb``.
On 2026-09-23 a tool result carrying one (binary data read as text) landed in
a chat session, and from then on every save of that session failed with
``UntranslatableCharacter: \\u0000 cannot be converted to text`` -- the
operator saw "A reply was produced but NOT saved to the transcript". SQLite
stores NUL fine, so the bug only exists on the Postgres backend.

The unit tests run everywhere. The session and task-store tests use whichever
backend is configured: SQLite locally, real Postgres in CI's Postgres job
(this file is in its list).
"""

from __future__ import annotations

import json
import logging

from kazma_core.db.pg_helpers import json_dumps, strip_nul
from kazma_core.db.postgres_pool import _without_nul

NUL = chr(0)
REPLACEMENT = chr(0xFFFD)
ESCAPED_NUL = "\\" + "u0000"


def test_strip_nul_cleans_nested_strings_and_keys():
    dirty = {"k" + NUL: ["a" + NUL + "b", {"x": NUL}], "n": 1, "none": None}
    clean = strip_nul(dirty)
    assert clean == {"k" + REPLACEMENT: ["a" + REPLACEMENT + "b", {"x": REPLACEMENT}], "n": 1, "none": None}


def test_json_for_postgres_never_carries_the_nul_escape():
    out = json_dumps({"content": "tool said U" + chr(2) + "id" + NUL})
    assert ESCAPED_NUL not in out and NUL not in out
    assert json.loads(out)["content"].endswith(REPLACEMENT)


def test_pool_parameters_are_cleaned():
    assert _without_nul(("a" + NUL, 1, None)) == ("a" + REPLACEMENT, 1, None)
    assert _without_nul(["x"]) == ["x"]
    assert _without_nul({"p": NUL}) == {"p": REPLACEMENT}


def test_a_session_whose_messages_contain_nul_still_saves(tmp_path, caplog):
    from kazma_ui.session_manager import SessionManager

    path = str(tmp_path / "chat_sessions.db")
    sm = SessionManager(db_path=path)
    session = sm.get_or_create("nul-session")
    session.messages = [
        {"role": "user", "content": "check for duplicates"},
        {"role": "tool", "content": "row: U" + chr(2) + "b9492862" + NUL + "end"},
        {"role": "assistant", "content": "No duplicates found."},
    ]
    with caplog.at_level(logging.ERROR, logger="kazma_ui.session_manager"):
        sm.put(session)
    assert not [r for r in caplog.records if "upsert failed" in r.getMessage()]

    reloaded = SessionManager(db_path=path)._load_one_from_db(session.tenant_id, "nul-session")
    assert reloaded is not None, "the session was not saved"
    tool = reloaded.messages[1]["content"]
    assert tool.startswith("row: U") and tool.endswith("end")
    assert reloaded.messages[2]["content"] == "No duplicates found."


def test_a_swarm_task_with_nul_in_prompt_and_metadata_still_saves(tmp_path):
    from kazma_core.swarm.task import SwarmTask, TaskType
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(db_path=str(tmp_path / "swarm_tasks.db"))
    task = SwarmTask(
        prompt="summarise" + NUL,
        type=TaskType.DISPATCH,
        workers=["alpha"],
        metadata={"note": "blob" + NUL},
    )
    store.persist_task(task)
    loaded = store.get_task(task.id)
    assert loaded is not None, "the task was not saved"
    assert loaded.prompt.startswith("summarise")
    assert loaded.metadata["note"].startswith("blob")
    store.close()
