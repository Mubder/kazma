"""The task-grant TTL is fixed, and nothing documents a knob for it (audit
2026-10-26 AUD-062).

``/hitl approve_task`` approves every danger tool of a task until the next
message, with a 10-minute safety net. The module docstring and the slash
command reference said ``KAZMA_TASK_GRANT_TTL_SECONDS`` changes that net; no
code read it, so an operator who set it to shorten the window was left with
the default and a false belief. The name is gone from both, and the gate
below holds every ``KAZMA_*`` name they mention to one the module reads.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "kazma-core" / "kazma_core" / "safety" / "task_grants.py"
SLASH_DOC = REPO / "docs" / "docs" / "reference" / "slash-commands.md"


def _collect():
    name = "_kazma_env_reference_for_task_grants"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, REPO / "scripts" / "generate_env_reference.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name].collect


def _named_but_unread(module_text: str, doc_text: str) -> set[str]:
    """``KAZMA_*`` names the module or its doc row mention that the module
    does not read from the environment."""
    read = set(_collect()({"kazma-core/kazma_core/safety/task_grants.py": module_text}))
    row = "\n".join(line for line in doc_text.splitlines() if "approve_task" in line)
    named = set(re.findall(r"\bKAZMA_[A-Z0-9_]+\b", module_text + "\n" + row))
    return named - read


def test_task_grants_names_no_variable_it_does_not_read() -> None:
    assert _named_but_unread(
        MODULE.read_text(encoding="utf-8"), SLASH_DOC.read_text(encoding="utf-8")
    ) == set()


def test_the_gate_sees_the_old_documented_knob() -> None:
    """Negative control: the docstring and doc row as they were."""
    old_module = (
        '"""2. **TTL safety net** -- default 10 minutes '
        '(``KAZMA_TASK_GRANT_TTL_SECONDS``)."""\n_DEFAULT_GRANT_TTL = 10 * 60\n'
    )
    old_row = (
        "| `/hitl approve_task [thread_id]` | until your next message (at most 10 "
        "minutes, `KAZMA_TASK_GRANT_TTL_SECONDS`). |"
    )
    assert _named_but_unread(old_module, old_row) == {"KAZMA_TASK_GRANT_TTL_SECONDS"}


def test_the_variable_does_not_lengthen_a_grant(monkeypatch) -> None:
    from kazma_core.config_store import _InMemoryStore
    from kazma_core.safety import task_grants

    store = _InMemoryStore(writable=True)
    monkeypatch.setattr(task_grants, "_cs", lambda: store)
    monkeypatch.setenv("KAZMA_TASK_GRANT_TTL_SECONDS", "999999")
    status = task_grants.grant_task("t-aud-062", actor="test")
    assert status["active"] is True
    assert status["ttl_seconds"] == 10 * 60
