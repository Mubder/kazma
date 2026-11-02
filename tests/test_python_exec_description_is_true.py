"""The ``python_exec`` description says what the runner does (2026-11, FA-120).

The model read "Max 30s timeout, 512MB memory, isolated mode (no
site-packages)". The timeout it passed was used as it came, the venv's
packages were importable, and the output was cut at 4,000 characters --
which the description never said, so a cut output read as complete. The
description is now built from the runner's constants, and the runner holds a
real ceiling on the timeout.

The old text is the negative control: its numbers and its "no
site-packages" are not what the runner does.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from kazma_core.tools import code_exec

OLD = ("Execute Python code in a sandboxed subprocess. Returns stdout + stderr. "
       "Max 30s timeout, 512MB memory, isolated mode (no site-packages). "
       "Use for calculations, data processing, prototyping.")


def _claims(text: str) -> dict[str, bool]:
    return {
        "max_timeout": f"at most {code_exec.MAX_TIMEOUT} s" in text,
        "default_timeout": f"default {code_exec.DEFAULT_TIMEOUT} s" in text,
        "memory": f"{code_exec.MEMORY_LIMIT_MB} MB" in text,
        "output_cap": f"{code_exec.MAX_OUTPUT_CHARS:,} characters" in text,
        "no_false_isolation": "no site-packages" not in text,
    }


def test_the_registered_description_states_the_runners_limits():
    from kazma_core.agent.tool_registry import get_tool_registry

    defs = {d["function"]["name"]: d for d in get_tool_registry().get_tool_definitions()}
    text = defs["python_exec"]["function"]["description"]
    assert all(_claims(text).values()), _claims(text)


def test_negative_control_the_old_text_states_none_of_them():
    claims = _claims(OLD)
    assert not claims["max_timeout"] and not claims["output_cap"] and not claims["no_false_isolation"]


@pytest.mark.parametrize("asked, used", [(999, 120), (0, 30), ("x", 30), (45, 45)])
def test_the_timeout_has_a_ceiling(asked, used, monkeypatch):
    seen: list[int] = []

    async def run(code_file, tmp_dir, timeout):
        seen.append(timeout)
        return "ok"

    monkeypatch.setattr(code_exec, "_run_local_subprocess", run)
    monkeypatch.setattr(code_exec, "use_docker_jail", lambda: False, raising=False)
    monkeypatch.setattr(code_exec, "sandbox_refusal", lambda code: None, raising=False)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    try:
        asyncio.run(code_exec.python_exec("print(1)", timeout=asked))
    except Exception:  # noqa: BLE001 - only the timeout handed to the runner matters here
        pass
    assert seen and seen[-1] == used, seen


def test_the_cut_is_marked():
    assert re.search(r"\[truncated", code_exec.PYTHON_EXEC_DESCRIPTION)
