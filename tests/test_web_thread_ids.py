"""A new web chat's thread id follows one rule on every mouth.

The chat's stream and live voice both name a new chat's thread through
``spaces.threads.new_web_thread_id``: its session id in the owner's space
(one id everywhere, as before Spaces), a fresh id in any other -- two
spaces' browsers may hold the same session id, and a thread id names one
chat for good.

Control: live voice's rule of before took the session id in any space.

Gate: no product code in the web package gives a chat's thread its session
id itself, but the two declared places.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

ROOT = Path(__file__).resolve().parents[1]


def test_the_owners_space_keeps_the_session_id_another_gets_a_fresh_one() -> None:
    from kazma_core.spaces.threads import new_web_thread_id

    sid = "web-" + uuid.uuid4().hex
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert new_web_thread_id(sid) == sid
    with scope.bind_scope("sotherspace01", "pmemberacct01"):
        fresh = new_web_thread_id(sid)
    assert fresh != sid and uuid.UUID(fresh)


def test_live_voice_names_a_new_chat_by_the_rule() -> None:
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.voice_turn import resolve_voice_session

    sid = "voice-" + uuid.uuid4().hex
    with scope.bind_scope("sotherspace01", "pmemberacct01"):
        get_session_manager().get_or_create(sid, durable=False, thread_id="")
        found = resolve_voice_session(sid)
    assert found is not None
    _session, thread = found
    assert thread != sid and uuid.UUID(thread)


def test_control_voices_rule_of_before_took_the_session_id_in_any_space(monkeypatch) -> None:
    from kazma_core.spaces import threads
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.voice_turn import resolve_voice_session

    monkeypatch.setattr(threads, "new_web_thread_id", lambda sid: sid)
    sid = "voice-" + uuid.uuid4().hex
    with scope.bind_scope("sotherspace01", "pmemberacct01"):
        get_session_manager().get_or_create(sid, durable=False, thread_id="")
        _session, thread = resolve_voice_session(sid)
    assert thread == sid


DECLARED = {
    ("kazma-ui/kazma_ui/sse_chat/__init__.py", "_hydrate"):
        "a chat-app mirror (gw-*) keeps its own id: the chat app's thread",
    ("kazma-ui/kazma_ui/thread_rewind.py", "*"):
        "a fork's chat row is named by the fork's own new thread id",
}


def _sites(source: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    stack: list[str] = []
    names = {"sid", "session_id"}

    class _V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr == "thread_id"
                        and isinstance(node.value, ast.Name) and node.value.id in names):
                    found.append((stack[-1] if stack else "<module>", node.lineno))
            self.generic_visit(node)

        def visit_keyword(self, node):
            if node.arg == "thread_id" and isinstance(node.value, ast.Name) and node.value.id in names:
                found.append((stack[-1] if stack else "<module>", node.value.lineno))
            self.generic_visit(node)

    _V().visit(ast.parse(source))
    return found


def _problems(files: dict[str, str]) -> list[str]:
    out = []
    for rel, source in files.items():
        for fn, line in _sites(source):
            if (rel, fn) not in DECLARED and (rel, "*") not in DECLARED:
                out.append(f"{rel}:{line} {fn} gives a chat's thread its session id")
    return out


def test_no_web_code_gives_a_thread_its_session_id_itself() -> None:
    files = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
             for p in (ROOT / "kazma-ui" / "kazma_ui").rglob("*.py")}
    assert _problems(files) == []


def test_gate_control_voices_line_of_before_fails() -> None:
    planted = {"kazma-ui/kazma_ui/voice_turn.py": (
        "def _bind(session, sid):\n"
        "    session.thread_id = sid\n"
    )}
    assert len(_problems(planted)) == 1
