"""Spaces S1-24: what reaches the admin -- alerts, the daily digest, the
weekly report and the log -- carries no member content, and the digest shows
counts per person.

* A member's turn, through the real server, route, graph and checkpoint store
  with Spaces enforced: a word planted in the member's message, in the
  model's tool call and in its answer is in no log line at INFO or above, in
  no alert (title or detail), in the daily digest and in the weekly report
  built from that log.
* The digest, once Spaces is enforced, names no chat of another person's
  space: what ran without asking there, and the turns, are counted per
  person. The owner's own chats are listed as before.

Control: today's digest names the member's chat (an install that does not
enforce Spaces keeps it, as before Spaces).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("uvicorn")

from kazma_core.observability import daily_digest  # noqa: E402
from kazma_core.safety.unasked import UNASKED_LINE  # noqa: E402
from kazma_core.spaces import identity, scope, threads  # noqa: E402
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID  # noqa: E402

PLANT = "zebrafinch-orchard-7731"
MEMBER = "dana-signals"
MEMBER_SPACE = "dana-signals-own"


class _Recorded(logging.Handler):
    """Every record at INFO or above, as the log would say it: the message
    and its traceback."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.lines.append(f"{record.levelname} {record.name} {text}")


def _member() -> tuple[str, str]:
    """A local user with a personal space: (account id, raw session id)."""
    from kazma_core.security import platform_rbac, web_sessions
    from kazma_ui import space_binding

    user = platform_rbac.create_local_user(MEMBER, "a-long-enough-password-1")
    account = space_binding.account_of({"source": "session", "actor": "login", "user_id": user.user_id,
                                        "username": MEMBER, "role": "operator"})
    assert account, "the member has no account"
    identity.create_space("Dana", created_by=account, personal_of=account, space_id=MEMBER_SPACE)
    session = web_sessions.create_session(role="operator", actor="login", username=MEMBER, user_id=user.user_id)
    return account, session


class _Model:
    """A tool call carrying the planted word, then an answer carrying it."""

    def respond(self, messages: list[Any]) -> Any:
        from kazma_core.llm_provider import LLMResponse, ToolCall

        if any(isinstance(m, dict) and str(m.get("role")) == "tool" for m in messages or []):
            return LLMResponse(content=f"Here is what I found about {PLANT}.", finish_reason="stop",
                               model="harness")
        return LLMResponse(content=f"Looking up {PLANT}.", finish_reason="tool_calls", model="harness",
                           tool_calls=[ToolCall(id="call_1", name="memory_search",
                                                arguments={"query": f"notes on {PLANT}"})])


def _digest_from(log_file: Path, monkeypatch: pytest.MonkeyPatch, guard_file: Path) -> str:
    monkeypatch.setattr(daily_digest, "_app_log_path", lambda: log_file)
    monkeypatch.setattr(daily_digest, "_guard_log_path", lambda: guard_file)
    return daily_digest.build_digest(24)


def _member_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str], Path, str]:
    """A member's turn through the real server with Spaces enforced: what it
    logged at INFO or above, the alerts it raised, the log file the digest
    reads, and the member's account."""
    import httpx
    from kazma_core.logging_config import StructuredJSONFormatter
    from kazma_core.observability import ops_alerts
    from kazma_ui import space_binding

    from tests._no_models import finish_turn_memory, keep_models_out
    from tests.e2e._unified_turn_harness import unified_turn_server

    keep_models_out(monkeypatch, llm=False)
    recorded = _Recorded()
    log_file = tmp_path / "kazma.log"
    written = logging.FileHandler(log_file, encoding="utf-8")
    written.setLevel(logging.INFO)
    written.setFormatter(StructuredJSONFormatter())
    alerts: list[str] = []
    real_alert = ops_alerts.alert

    def alert(key: str, title: str, detail: str = "", *args: Any, **kwargs: Any) -> Any:
        alerts.append(f"{key} | {title} | {detail}")
        return real_alert(key, title, detail, *args, **kwargs)

    monkeypatch.setattr(ops_alerts, "alert", alert)
    root = logging.getLogger()
    with unified_turn_server(_Model()) as h:
        os.environ["KAZMA_SECRET"] = "admin-signals-test-secret-" + "x" * 24
        account, session = _member()
        scope.set_enforced(True)
        level = root.level
        root.setLevel(logging.INFO)
        root.addHandler(recorded)
        root.addHandler(written)
        try:
            body = {"message": f"Please look into {PLANT} for me", "session_id": "admin-signals-chat"}
            with httpx.Client(cookies={"kazma-session": session}) as client, client.stream(
                    "POST", f"{h.base}/api/chat/stream", json=body, timeout=120,
                    headers={space_binding.SPACE_HEADER: MEMBER_SPACE}) as answer:
                assert answer.status_code == 200, answer.status_code
                frames = []
                for line in answer.iter_lines():
                    frames.append(line)
                    if line.startswith("event: done"):
                        break
            assert any(PLANT in line for line in frames), "the turn never carried the planted word"
            finish_turn_memory()
        finally:
            root.removeHandler(recorded)
            root.removeHandler(written)
            written.close()
            root.setLevel(level)
            scope.set_enforced(False)
    return recorded.lines, alerts, log_file, account


def test_a_member_s_turn_leaves_no_word_for_the_admin(tmp_path, monkeypatch) -> None:
    from kazma_core.observability import firing_ledger, weekly_targets

    lines, alerts, log_file, account = _member_turn(tmp_path, monkeypatch)
    # The turn ran and was logged: its finished line names the member, never
    # their words.
    finished = [line for line in lines if "[turn] Turn finished:" in line]
    assert finished and all(f"asker={account}" in line for line in finished), finished
    leaked = [line for line in lines if PLANT in line]
    assert leaked == [], leaked
    assert [a for a in alerts if PLANT in a] == []

    guard_file = tmp_path / "guard.log"
    guard_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(scope, "_enforced", True)
    digest = _digest_from(log_file, monkeypatch, guard_file)
    assert "Turns completed: 1" in digest, digest
    assert PLANT not in digest
    report = firing_ledger.scan_log(hours=24, path=log_file)
    title, text = firing_ledger.render(report)
    assert PLANT not in title and PLANT not in text
    written = log_file.read_text(encoding="utf-8").splitlines()
    targets = weekly_targets.render_targets(weekly_targets.measure_targets(written, [], now=time.time()))
    assert all(PLANT not in line for line in targets)


def test_control_a_log_line_that_quotes_as_before_leaks_the_member_s_words(tmp_path, monkeypatch) -> None:
    """Negative control: the log's words read as on an install that does not
    enforce Spaces -- the same turn's question and tool arguments are in the
    log, and the gate above sees them."""
    from kazma_core.observability import log_words

    monkeypatch.setattr(log_words, "spaces_enforced", lambda: False)
    lines, _alerts, _log_file, _account = _member_turn(tmp_path, monkeypatch)
    leaked = [line for line in lines if PLANT in line]
    assert any("recall_q=" in line for line in leaked), leaked
    assert any("[ToolWorker] exec name=memory_search" in line for line in leaked), leaked


# ── the digest: counts per person ────────────────────────────────────────


def _line(ts: float, message: str, logger: str = "kazma") -> str:
    from datetime import UTC, datetime

    stamp = datetime.fromtimestamp(ts, UTC).isoformat()
    return json.dumps({"timestamp": stamp, "level": "INFO", "logger": logger, "message": message})


def _finished(key: str, asker: str | None) -> str:
    """The line ``close_turn`` writes (``turn_runtime._note_finished_turn``;
    the member's turn above checks the writer itself)."""
    line = f"[turn] Turn finished: platform=web key={key} failed=no"
    return line if asker is None else f"{line} asker={asker}"


@pytest.fixture
def two_people(tmp_path):
    """The owner's chat and a member's, each with calls that ran without
    asking and a finished turn, in a log the digest reads."""
    from kazma_core.security import platform_rbac
    from kazma_ui import space_binding

    user = platform_rbac.create_local_user(MEMBER, "a-long-enough-password-1")
    account = space_binding.account_of({"source": "session", "actor": "login", "user_id": user.user_id,
                                        "username": MEMBER, "role": "operator"})
    identity.create_space("Dana", created_by=account, personal_of=account, space_id=MEMBER_SPACE)
    theirs, mine = "thread-dana-0123456789", "thread-mine-9876543210"
    threads.record_thread(theirs, MEMBER_SPACE, account)
    threads.record_thread(mine, OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    now = time.time()
    lines = [
        _line(now - 60, UNASKED_LINE % ("file_write", theirs, "yolo")),
        _line(now - 50, UNASKED_LINE % ("shell_exec", theirs, "grant")),
        _line(now - 40, UNASKED_LINE % ("file_write", mine, "yolo")),
        _line(now - 30, _finished("k1", account)),
        _line(now - 20, _finished("k2", OWNER_ACCOUNT_ID)),
        _line(now - 10, _finished("k3", OWNER_ACCOUNT_ID)),
    ]
    log_file, guard_file = tmp_path / "kazma.log", tmp_path / "guard.log"
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    guard_file.write_text("", encoding="utf-8")
    try:
        yield log_file, guard_file, theirs, mine, account
    finally:
        from kazma_core.config_store import get_config_store
        from kazma_core.security.vault import reset_posture_cache

        threads.forget_thread(theirs)
        threads.forget_thread(mine)
        get_config_store().delete(identity.REGISTRY_KEY)
        get_config_store().delete("platform.users")
        # A second account made the install multi-user, and the vault caches
        # that; the next test sees an install of one again.
        reset_posture_cache()


def test_the_digest_counts_another_person_s_chats_and_names_none(two_people, monkeypatch) -> None:
    log_file, guard_file, theirs, mine, account = two_people
    monkeypatch.setattr(scope, "_enforced", True)
    digest = _digest_from(log_file, monkeypatch, guard_file)
    names = {a["id"]: a.get("name") or a["id"] for a in identity.list_accounts()}
    dana, owner = names[account], "you"  # the digest is the owner's: "you"
    # Theirs: counted under the person, the chat never named, nor what ran.
    assert theirs[:8] not in digest, digest
    person = [line for line in digest.splitlines() if line.strip().startswith(f"{dana}:")]
    assert person == [f"    {dana}: 2 calls"], digest
    # Mine: listed as before.
    assert f"chat {mine[:8]}:" in digest and "file_write" in digest, digest
    # The turns, per person.
    assert "Turns completed: 3" in digest, digest
    assert f"  by person: {owner} 2, {dana} 1" in digest.splitlines(), digest


def _open_problems(two_people: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enforced: bool) -> str:
    """The digest's open-problems line with a swarm task of the member's
    space failed, its title holding the planted word."""
    from kazma_core.runs.contract import Run, RunState
    from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store

    log_file, guard_file, _theirs, _mine, account = two_people
    reset_incident_store(IncidentStore(tmp_path / "incidents.db"))
    try:
        now = time.time()
        run = Run(run_id="swarm_task:dana-1", kind="swarm_task", native_id="dana-1", title=f"research {PLANT}",
                  state=RunState.FAILED, native_state="failed", started_at=now, progress_at=now, ended_at=now,
                  version="1", space=MEMBER_SPACE, asker=account)
        get_incident_store().open_incident(run, "failed", origin={})
        if enforced:
            monkeypatch.setattr(scope, "_enforced", True)
        digest = _digest_from(log_file, monkeypatch, guard_file)
    finally:
        reset_incident_store(None)
    assert PLANT not in digest
    return next(line for line in digest.splitlines() if line.startswith("Open problems"))


def test_the_digest_counts_other_people_s_open_problems(two_people, tmp_path, monkeypatch) -> None:
    line = _open_problems(two_people, tmp_path, monkeypatch, enforced=True)
    dana = next(a.get("name") or a["id"] for a in identity.list_accounts() if a["id"] == two_people[4])
    # The owner's own list holds nothing; the member's problem is counted.
    assert line == f"Open problems: 0 -- in other people's spaces: {dana} 1", line


def test_control_without_spaces_the_open_problems_are_one_list(two_people, tmp_path, monkeypatch) -> None:
    line = _open_problems(two_people, tmp_path, monkeypatch, enforced=False)
    assert line.startswith("Open problems: 1 (") and "other people" not in line, line


def test_control_today_s_digest_names_the_member_s_chat(two_people, monkeypatch) -> None:
    """Not enforced -- the digest as it was: every chat by its id, the
    member's included, and no count per person."""
    log_file, guard_file, theirs, _mine, _account = two_people
    digest = _digest_from(log_file, monkeypatch, guard_file)
    assert f"chat {theirs[:8]}:" in digest, digest
    assert "by person" not in digest


# ── the one rule for words in a log line ─────────────────────────────────


def test_a_log_line_says_how_long_words_are_once_enforced(monkeypatch) -> None:
    from kazma_core.observability.log_words import args_for_log, words_for_log

    monkeypatch.setattr(scope, "_enforced", True)
    assert words_for_log(f"remind me about {PLANT}") == "<39 chars>"
    assert words_for_log(None) == "<0 chars>"
    shown = args_for_log({"query": PLANT, "limit": 5}, f"query={PLANT}, limit=5")
    assert shown == f"query=<{len(PLANT)} chars>, limit=<1 chars>" and PLANT not in shown


def test_control_without_spaces_a_log_line_quotes_as_before() -> None:
    from kazma_core.observability.log_words import args_for_log, words_for_log

    assert words_for_log("x" * 100) == "x" * 80
    assert words_for_log("a question", 5) == "a que"
    assert args_for_log({"query": PLANT}, f"query={PLANT}") == f"query={PLANT}"


# ── what leaves Kazma: Langfuse ──────────────────────────────────────────


class _FakeLangfuse:
    """Stands in for the Langfuse client: what would have left Kazma."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def generation(self, **fields: Any) -> Any:
        from types import SimpleNamespace

        self.sent.append(repr(fields))
        return SimpleNamespace(end=lambda **more: self.sent.append(repr(more)))

    def span(self, **fields: Any) -> Any:
        from types import SimpleNamespace

        self.sent.append(repr(fields))
        return SimpleNamespace(end=lambda: None)


def _traced(monkeypatch: pytest.MonkeyPatch, space: str, asker: str) -> list[str]:
    """One model call and one tool call traced in *space*: what Langfuse got."""
    import kazma_core.tracing as tracing
    from kazma_core.tracing import KazmaTracer, TraceStore, TracingBackend

    monkeypatch.setattr(tracing, "_trace_store", TraceStore())
    fake = _FakeLangfuse()
    tracer = KazmaTracer(backend="console")
    tracer.backend, tracer._client = TracingBackend.LANGFUSE, fake
    with scope.bind_scope(space, asker):
        tracer.trace_llm_call("m", f"prompt {PLANT}", f"answer {PLANT}", 10, 0.0)
        tracer.trace_tool_execution("memory_search", {"query": PLANT}, {"result": PLANT}, 1.0)
    return fake.sent


def test_langfuse_gets_only_the_owner_s_space_once_enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    assert _traced(monkeypatch, MEMBER_SPACE, "a0member") == []
    owners = _traced(monkeypatch, OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert owners and any(PLANT in sent for sent in owners)


def test_control_without_spaces_langfuse_gets_every_call(monkeypatch) -> None:
    sent = _traced(monkeypatch, MEMBER_SPACE, "a0member")
    assert any(PLANT in item for item in sent), sent
