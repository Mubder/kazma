"""AUD-052: the chat stream changes a stored session only under its transaction.

The stream route appended the user's message (and cleared the transcript on
``/reset``, replaced it on ``/compact``, appended a steer, healed history from
the checkpoint) on the ``ChatSession`` object it had read at the start of the
request, then ``put`` that object back. A reply persisted meanwhile through
``SessionManager.transact`` -- the detached pump, the approval route, another
tab -- lives on whatever object the store holds by then, and the stale copy's
``put`` overwrote it. ``_mutate_session`` reads the session again under its
per-session lock, applies the change and writes it before releasing it.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SSE = _ROOT / "kazma-ui" / "kazma_ui" / "sse_chat"


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from kazma_ui.session_manager import get_session_manager, reset_session_manager

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    reset_session_manager()
    yield get_session_manager()
    reset_session_manager()


def _stale_copy_and_concurrent_reply(manager):
    """A session the request read, and a reply persisted after the read.

    The request's object goes stale the way it does live: the store replaced
    its cached entry (``_refresh_from_db``, an eviction) and the reply was
    written through the transaction onto the store's new object.
    """
    sid = "sess-" + uuid.uuid4().hex[:8]
    session = manager.get_or_create(sid)
    session.messages = [{"role": "user", "content": "first question"}]
    manager.put(session)
    stale = manager.get(sid)
    manager._sessions.clear()  # the cache drops the entry; the row stays
    with manager.transact(sid) as fresh:
        fresh.messages.append({"role": "assistant", "content": "the earlier reply"})
    return sid, stale


def test_a_reply_persisted_meanwhile_survives_the_users_message(manager):
    from kazma_ui.sse_chat._persistence import _mutate_session

    sid, stale = _stale_copy_and_concurrent_reply(manager)
    row = {"role": "user", "content": "second question"}
    _mutate_session(sid, lambda s: s.messages.append(row), stale)

    contents = [m["content"] for m in manager.get(sid).messages]
    assert contents == ["first question", "the earlier reply", "second question"]


def test_negative_control_the_old_put_of_the_stale_copy_loses_the_reply(manager):
    sid, stale = _stale_copy_and_concurrent_reply(manager)
    # The route's old write: append to the copy read earlier, put it back.
    stale.messages.append({"role": "user", "content": "second question"})
    manager.put(stale)

    contents = [m["content"] for m in manager.get(sid).messages]
    assert "the earlier reply" not in contents


def test_no_stored_session_and_no_fallback_writes_nothing(manager):
    from kazma_ui.sse_chat._persistence import _mutate_session

    assert _mutate_session("sess-missing", lambda s: s.messages.append({})) is None
    assert manager.get("sess-missing") is None


def test_a_session_only_the_request_holds_is_stored_first(manager):
    from kazma_ui.session_manager import ChatSession
    from kazma_ui.sse_chat._persistence import _mutate_session

    own = ChatSession(session_id="sess-own")
    out = _mutate_session(
        "sess-own", lambda s: s.messages.append({"role": "user", "content": "hi"}), own
    )
    assert out is not None
    assert [m["content"] for m in manager.get("sess-own").messages] == ["hi"]


# ---------------------------------------------------------------------------
# Class gate: nothing in the stream package writes a session by ``put``.
# ---------------------------------------------------------------------------


def _session_puts(source: str) -> list[int]:
    """Lines that ``put`` a session object instead of using the transaction."""
    found: list[int] = []
    tree = ast.parse(source)
    # The helper itself stores a session only the request holds (nothing
    # stored to re-read), then changes it under the transaction.
    exempt: set[int] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_mutate_session":
            exempt.update(id(n) for n in ast.walk(fn))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in exempt:
            continue
        func = node.func
        # store.put(session) / _get_store().put(session)
        if isinstance(func, ast.Attribute) and func.attr == "put" and node.args:
            recv = func.value
            if isinstance(recv, ast.Call) and getattr(recv.func, "id", "") == "_get_store":
                found.append(node.lineno)
                continue
            if isinstance(recv, ast.Name) and recv.id in {"store", "_store", "mgr", "sm"}:
                found.append(node.lineno)
                continue
        # asyncio.to_thread(<store>.put, session)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "to_thread"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "put"
        ):
            found.append(node.lineno)
    return found


def test_the_stream_package_never_puts_a_session_it_read_earlier():
    offenders = {
        path.name: _session_puts(path.read_text(encoding="utf-8"))
        for path in sorted(_SSE.glob("*.py"))
    }
    offenders = {name: lines for name, lines in offenders.items() if lines}
    assert not offenders, (
        "a session read earlier is written back by put (AUD-052); change it "
        f"through _mutate_session / SessionManager.transact: {offenders}"
    )


def test_negative_control_the_gate_sees_the_old_writes():
    old = (
        "async def route(session):\n"
        "    session.messages.append(row)\n"
        "    await asyncio.to_thread(_get_store().put, session)\n"
        "def heal(store, session):\n"
        "    store.put(session)\n"
    )
    assert sorted(_session_puts(old)) == [3, 5]
