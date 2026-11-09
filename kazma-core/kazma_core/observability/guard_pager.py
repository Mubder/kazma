"""The guard's pager, Kazma's side (the unified alerts plan, Stage 3).

The guard pages the owner through Kazma's Telegram bot when Kazma itself is
down (``Notifier`` in ``scripts/service/kazma_guard.py``). Settings -> Alerts
shows that pager and tests it:

* :func:`pager_status` -- from the guard's state file: is a guard running
  (its heartbeat), does it answer test pages (``guard_features``), the
  outcome of its last page (``pager``: when, taken or not, the chat, where
  the credentials came from) and of the last test (``pager_test``).
* :func:`request_test_page` -- writes ``guard.test_page`` beside the reload
  request (``KAZMA_GUARD_RELOAD_FILE``): an id and the time. The running
  guard sends one card through a notifier of its own -- the path a page takes
  when Kazma is down -- and records the answer in its state file
  (``PagerTest``).
* :func:`pager_test_result` -- that answer, for the page to poll.

The bot is Kazma's (``connectors.telegram.token``); the chat is
``guard.telegram.chat_id`` (Settings -> Alerts), else ``swarm.group_chat_id``.
``KAZMA_GUARD_TELEGRAM_TOKEN`` / ``KAZMA_GUARD_TELEGRAM_CHAT`` in the guard's
environment win over both: the state file says when they do (``source``).

Blocking (files): call these off the loop.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

__all__ = [
    "TEST_FEATURE",
    "pager_status",
    "request_test_page",
    "pager_test_result",
]

#: The guard feature that answers test pages (``GUARD_FEATURES``).
TEST_FEATURE = "pager_test"
#: A guard whose heartbeat is older than this is not asked (it beats every 10 s).
FRESH_S = 60.0
#: A request the guard has not answered in this long is said to be unanswered
#: (it sends within a second; its own credential lookup can take a minute).
ANSWER_WAIT_S = 150.0
_RELOAD_ENV = "KAZMA_GUARD_RELOAD_FILE"
_REQUEST_NAME = "guard.test_page"


def _state_path() -> Path:
    from kazma_core.observability.outside_watch import _guard_state_path

    return _guard_state_path()


def _read_state() -> dict[str, Any] | None:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _request_path(state_path: Path) -> Path:
    """``guard.test_page`` in the reload request's folder (the guard's
    ``_pager_test_path``)."""
    reload_file = (os.environ.get(_RELOAD_ENV) or "").strip()
    folder = Path(reload_file).parent if reload_file else state_path.parent
    return folder / _REQUEST_NAME


def _record(value: Any, fields: tuple[str, ...]) -> dict[str, Any] | None:
    """The named fields of a state record, scalars only (a web page reads it)."""
    if not isinstance(value, dict):
        return None
    return {k: value.get(k) for k in fields
            if isinstance(value.get(k), (str, int, float, bool)) or value.get(k) is None}


def pager_status(now: float | None = None) -> dict[str, Any]:
    """What the page shows of the guard's pager. Never the bot's token."""
    now = time.time() if now is None else now
    state = _read_state()
    if state is None:
        return {"guard": "none", "heartbeat_age_s": None, "tests": False, "pager": None, "test": None}
    beat = state.get("heartbeat")
    age = now - float(beat) if isinstance(beat, (int, float)) else None
    features = state.get("guard_features") or []
    return {
        "guard": "running" if age is not None and age < FRESH_S else "silent",
        "heartbeat_age_s": age,
        "tests": TEST_FEATURE in features,
        "pager": _record(state.get("pager"), ("at", "ok", "head", "source", "chat", "error", "held", "delayed")),
        "test": _record(state.get("pager_test"), ("id", "state", "requested_at", "at", "source", "chat", "error")),
    }


def request_test_page(now: float | None = None) -> dict[str, Any]:
    """Ask the running guard for a test page. ``{"ok": True, "id"}``, or
    ``{"ok": False, "code"}``: ``no_guard`` (no state file: Kazma was not
    started by its guard), ``guard_silent`` (no heartbeat for a minute),
    ``guard_too_old`` (it predates test pages: restart the guard),
    ``not_written`` (the request file could not be written)."""
    now = time.time() if now is None else now
    status = pager_status(now)
    if status["guard"] == "none":
        return {"ok": False, "code": "no_guard"}
    if status["guard"] != "running":
        return {"ok": False, "code": "guard_silent"}
    if not status["tests"]:
        return {"ok": False, "code": "guard_too_old"}
    req_id = secrets.token_hex(8)
    target = _request_path(_state_path())
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({"id": req_id, "ts": now, "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "code": "not_written"}
    return {"ok": True, "id": req_id, "requested_at": now}


def pager_test_result(req_id: str, requested_at: float | None = None,
                     now: float | None = None) -> dict[str, Any]:
    """The guard's answer to the request *req_id*: ``state`` is ``waiting``
    (not taken yet), ``sending``, ``sent``, ``failed``, ``expired`` or
    ``unanswered`` (nothing for ``ANSWER_WAIT_S``)."""
    now = time.time() if now is None else now
    test = pager_status(now).get("test") or {}
    if test.get("id") == req_id and test.get("state"):
        return {"id": req_id, **test}
    if requested_at is not None and now - requested_at > ANSWER_WAIT_S:
        return {"id": req_id, "state": "unanswered"}
    return {"id": req_id, "state": "waiting"}
