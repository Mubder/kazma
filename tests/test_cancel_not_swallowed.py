"""A cancel reaches the code that asked for it, or the swallow is declared.

SonarCloud review, 2026-11-16 (python:S7497). ``except CancelledError``
without a re-raise turns a cancel into a normal ending: the telemetry stream
and the swarm task stream ended "normally" under a cancelled request, and
``kazma ask --acp`` answered "cancelled" to Ctrl+C and kept serving while the
turn ran on. Every handler of ``asyncio.CancelledError`` in product code
re-raises, sits inside a handler that does, or is declared here with why its
caller wants the ending it gets (most: a loop task whose only canceller is
its owner's bounded stop).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: (file, function) -> why ending normally is what its canceller wants.
DECLARED: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/memory/task_queue.py", "_run_lease_renewal"):
        "the queue's lease-renewal task; its only canceller is stop(), whose bounded wait takes a normal end",
    ("kazma-core/kazma_core/swarm/checkpoint_manager.py", "_checkpoint_timeout_reject"):
        "a checkpoint's auto-reject timer, cancelled by its owner when the checkpoint is decided",
    ("kazma-core/kazma_core/swarm/durable.py", "run_via_durable"):
        "inside the engine's own run task: a stop becomes the run's recorded ending (section 9)",
    ("kazma-core/kazma_core/swarm/durable.py", "_stopped"):
        "a second stop during the bounded settle: the ending is recorded, then returned or re-raised",
    ("kazma-core/kazma_core/swarm/engine.py", "_stopped_at_admission"):
        "a second stop during the bounded wait: the run's ending is still recorded (SQ01)",
    ("kazma-core/kazma_core/swarm/engine.py", "_approval_stopped_at_admission"):
        "a second stop during the bounded wait: the checkpoint is offered again",
    ("kazma-core/kazma_core/swarm/engine.py", "_dispatch"):
        "a stop during the admission write becomes the run's interruption",
    ("kazma-core/kazma_core/swarm/engine.py", "_run_admitted"):
        "every ending of a run is recorded (SW-E02); the engine's stop awaits it",
    ("kazma-core/kazma_core/swarm/engine.py", "_resume_run"):
        "a durable resume: same as the run",
    ("kazma-core/kazma_core/swarm/engine.py", "_resume"):
        "an in-process resume: same as the run",
    ("kazma-gateway/kazma_gateway/adapters/discord.py", "_heartbeat"):
        "the adapter's heartbeat task, cancelled by the adapter on reconnect and stop",
    ("kazma-gateway/kazma_gateway/adapters/slack.py", "_listen_socket_mode"):
        "the adapter's own task: a halt closes the socket and its client, owners wait bounded",
    ("kazma-gateway/kazma_gateway/adapters/slack.py", "_listen_polling"):
        "the adapter's own task: the same owners",
    ("kazma-gateway/kazma_gateway/gateway.py", "_consume"):
        "the gateway-consumer task; its owner cancels it to stop it",
    ("kazma-gateway/kazma_gateway/typing_keepalive.py", "_loop"):
        "the keepalive task, ended by its owner's cancel_and_wait and its stop flag",
    ("kazma-ui/kazma_ui/delivery.py", "emit"):
        "the cancel is recorded and re-raised once the frame is journaled under the emit lock (frame order)",
    ("kazma-ui/kazma_ui/app.py", "lifespan"):
        "the shielded shutdown is bounded and the process exits through process_exit",
}


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [rel for rel in out.split("\0")
            if rel.endswith(".py") and "/tests/" not in rel and "_tests/" not in rel]


def _names_cancel(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    for node in (handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]):
        if isinstance(node, ast.Name) and node.id == "CancelledError":
            return True
        if (isinstance(node, ast.Attribute) and node.attr == "CancelledError"
                and isinstance(node.value, ast.Name) and node.value.id == "asyncio"):
            return True
    return False


def _reraises(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(ast.Module(body=handler.body, type_ignores=[])))


def swallowed(source: str) -> list[tuple[str, int]]:
    """(function, line) of each cancel handler that neither re-raises nor
    sits inside a cancel handler that does."""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str, covered: bool) -> None:
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name, inner_covered = child.name, False
            else:
                inner_covered = covered
            if isinstance(child, ast.ExceptHandler) and _names_cancel(child):
                if _reraises(child):
                    inner_covered = True
                elif not covered:
                    found.append((function, child.lineno))
            visit(child, name, inner_covered)

    visit(ast.parse(source), "<module>", False)
    return found


def test_no_product_code_swallows_a_cancel_undeclared():
    found, used = [], set()
    for rel in _product_files():
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "CancelledError" not in text:
            continue
        for function, line in swallowed(text):
            if (rel, function) in DECLARED:
                used.add((rel, function))
            else:
                found.append(f"{rel}:{line} in {function}()")
    assert found == [], "re-raise the cancel, or declare why ending normally is right:\n" + "\n".join(found)
    stale = set(DECLARED) - used
    assert not stale, f"declared handlers no longer there: {sorted(stale)}"


def test_the_gate_sees_the_three_swallows_it_was_built_for():
    """Negative control: the telemetry stream, the swarm stream and the ACP
    server as they were."""
    telemetry = (
        "async def stream(self):\n"
        "    while True:\n"
        "        yield 1\n"
        "        try:\n"
        "            await asyncio.sleep(1)\n"
        "        except asyncio.CancelledError:\n"
        "            break\n"
    )
    acp = (
        "async def run_acp_stdio():\n"
        "    try:\n"
        "        await run()\n"
        "    except asyncio.CancelledError:\n"
        "        reply()\n"
    )
    assert swallowed(telemetry) == [("stream", 6)]
    assert swallowed(acp) == [("run_acp_stdio", 4)]
    covered = (
        "async def f():\n"
        "    try:\n"
        "        await g()\n"
        "    except asyncio.CancelledError:\n"
        "        try:\n"
        "            await h()\n"
        "        except asyncio.CancelledError:\n"
        "            pass\n"
        "        raise\n"
    )
    assert swallowed(covered) == []
