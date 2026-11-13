"""A background task that is not a never-ending loop ends at a stop.

``drain_background`` cancels the tasks spawned ``never_completes=True`` and
waits up to its timeout for the rest. A task that sat in a plain
``asyncio.sleep`` held every stop for that timeout: live 2026-11-13 a reload
17 s after a boot logged "kb-crawl-auto-resume did not finish in 10s" -- the
task was in its 20 s "let the server answer first" wait. A delay now goes
through ``spawn_background(start_after=...)``, which a stop ends at once, and
the gate below reads every ``spawn_background`` call in product code: a
coroutine it starts that is not declared never-ending must not sleep with
``asyncio.sleep`` (use ``kazma_core.shutdown.sleep_or_shutdown``).
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path

import pytest

from kazma_core import background, shutdown

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_stop_left_behind():
    shutdown.reset_shutdown()
    yield
    shutdown.reset_shutdown()


async def test_a_stop_during_the_delay_ends_the_task_at_once():
    ran = []

    async def work():
        ran.append(True)

    task = background.spawn_background(work(), name="test-start-after", start_after=30)
    await asyncio.sleep(0)  # the task reaches its wait
    shutdown.signal_shutdown()
    # What drain_background waits for: the task itself, ended by the stop.
    done, _ = await asyncio.wait({task}, timeout=1.0)
    assert task in done and not task.cancelled(), "a stop did not end the delay"
    assert ran == [], "the work started although the server was stopping"


async def test_the_work_runs_after_the_delay():
    ran = []

    async def work():
        ran.append(True)
        return 7

    task = background.spawn_background(work(), name="test-start-after-runs", start_after=0.05)
    assert await task == 7 and ran == [True]


async def test_negative_control_a_plain_sleep_held_the_drain():
    async def work():
        await asyncio.sleep(30)

    task = background.spawn_background(work(), name="test-plain-sleep")
    await asyncio.sleep(0)  # the task reaches its wait
    shutdown.signal_shutdown()
    done, _ = await asyncio.wait({task}, timeout=0.5)
    task.cancel()
    assert not done, "a plain sleep is not ended by the stop: the drain waits it out"


# ── The gate ──────────────────────────────────────────────────────────


def _plain_sleep(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sleep"
            and isinstance(node.func.value, ast.Name) and node.func.value.id in ("asyncio", "_asyncio")
            and not (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in (0, 0.0)))


def _findings(source: str, path: str) -> list[str]:
    tree = ast.parse(source)
    defs: dict[str, list[ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            defs.setdefault(node.name, []).append(node)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if called != "spawn_background" or not node.args or not isinstance(node.args[0], ast.Call):
            continue
        never = any(k.arg == "never_completes" and getattr(k.value, "value", False) is True for k in node.keywords)
        if never:
            continue
        target = node.args[0].func
        name = getattr(target, "id", getattr(target, "attr", None))
        for body in defs.get(name or "", []):
            for inner in ast.walk(body):
                if _plain_sleep(inner):
                    found.append(f"{path}:{inner.lineno} {name} (started at line {node.lineno})")
    return found


def test_no_background_task_sleeps_through_a_stop():
    files = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/*.py"], capture_output=True, text=True,
                           cwd=ROOT, check=True).stdout.split()
    found = []
    for rel in files:
        if "_tests/" in rel.replace("\\", "/"):
            continue
        found += _findings((ROOT / rel).read_text(encoding="utf-8"), rel)
    assert not found, "background tasks that a stop waits for:\n" + "\n".join(found)


def test_negative_control_the_gate_finds_the_old_auto_resume():
    old = (
        "async def _resume_crawls_after_start():\n"
        "    await asyncio.sleep(20)\n"
        "    await resume_interrupted_crawls()\n"
        "spawn_background(_resume_crawls_after_start(), name='kb-crawl-auto-resume')\n"
    )
    assert _findings(old, "app.py") == ["app.py:2 _resume_crawls_after_start (started at line 4)"]
    loop = old.replace("name='kb-crawl-auto-resume')", "name='x', never_completes=True)")
    assert _findings(loop, "app.py") == []
