"""The backup-model drill (resilience plan F2).

A failover chain that is configured but has never been taken is a guess. Once
a month the maintenance cadence runs this drill in a process of its own
(``python -m kazma_core.observability.failover_drill``; by hand,
``scripts/model_failover_drill.py``):

1. The backup must exist: ``agent.nonstop.failover.chain`` names a model of
   another vendor than the active one (another provider address: one
   vendor's outage takes every model it serves down at once).
2. The primary is forced down (every call a transient failure) and one test
   prompt goes through :func:`resilient_chat`: the backup must answer it.
3. The failover notice (AGENTS.md section 38) must be raised, naming both
   models. The drill holds it (:func:`model_fallback.drill_notices`): a
   monthly drill pages nobody.
4. The primary answers again and the notice closes: the switch back.

The result is kept (``system.drills.model_failover``), logged as
``[failover-drill] PASS`` / ``FAIL`` (the weekly report counts it), and a
failure is an ops alert naming the step. The model calls go through the
per-call ledger under ``drill:model-failover``: a drill costs cents, and they
are counted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

__all__ = ["DrillResult", "drill_sweep", "main", "run_drill"]

#: The test prompt: an answer anyone can check.
PROMPT = [{"role": "user", "content": "Reply with the word OK and nothing else."}]
#: Where the last result is kept (Kazma's own state: never in a backup's restore).
RESULT_KEY = "system.drills.model_failover"
#: The drill runs once in this long.
EVERY_S = 30 * 86400.0
#: Its process is stopped past this.
DRILL_TIMEOUT_S = 300.0
_THREAD = "drill:model-failover"


@dataclass
class DrillResult:
    ok: bool
    step: str  # not_configured | same_vendor | failover | notice | switch_back | passed | error
    detail: str
    primary: str = ""
    backup: str = ""
    seconds: float = 0.0
    at: float = field(default_factory=time.time)


class _ForcedDown:
    """The primary, forced down: every call is a transient failure."""

    def __init__(self, client: Any) -> None:
        self.config = getattr(client, "config", None)

    async def chat(self, *_args: Any, **_kwargs: Any) -> Any:
        from kazma_core.llm_provider import LLMError

        raise LLMError("failover drill: the primary is forced down", transient=True, kind="drill")


@dataclass
class _Chain:
    """``resilient_chat``'s frozen failover: the clients the drill chose."""

    clients: list[tuple[str, Any]]
    cooldown_seconds: float = 0.0


def _vendor(registry: Any, model: str) -> str:
    """The vendor serving *model*: its provider's host, else its name."""
    entry = registry.find_provider_for_model(model) or {}
    host = urlsplit(str(entry.get("base_url") or "")).hostname or ""
    return host.lower() or str(entry.get("name") or "").lower()


def _text(response: Any) -> str:
    return str(getattr(response, "content", "") or "")


async def run_drill(*, registry: Any = None, chain: list[str] | None = None) -> DrillResult:
    """One drill (see the module docstring). Never raises: a step that
    breaks is the result."""
    started = time.monotonic()

    def done(ok: bool, step: str, detail: str, **names: str) -> DrillResult:
        return DrillResult(ok, step, detail, seconds=round(time.monotonic() - started, 1), **names)

    if chain is None:
        from kazma_core.agent.nonstop import get_nonstop_config

        failover = (await asyncio.to_thread(get_nonstop_config)).failover  # settings
        chain = list(failover.chain) if failover.enabled else []
        if not failover.enabled and failover.chain:
            # Said for what it is (providers stage 1): a chain set with
            # failover off read "No backup model is configured".
            return done(False, "failover_off",
                        f"A backup model is set ({', '.join(failover.chain)}) but failover is off: turn "
                        "agent.nonstop.failover on (Settings -> System -> Non-stop).")
    if not chain:
        return done(False, "not_configured",
                    "No backup model is configured: set agent.nonstop.failover.chain to a model of another "
                    "vendor and turn agent.nonstop.failover on (Settings -> System -> Non-stop).")
    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = await asyncio.to_thread(get_model_registry)  # settings and the vault
    from kazma_core.failover_backup import suggest_backups, unusable_backups

    bad = await asyncio.to_thread(unusable_backups, registry, chain)
    if len(bad) == len(chain):
        offers = await asyncio.to_thread(suggest_backups, registry)
        offer = (" Usable instead: " + ", ".join(f"{o['model']} ({o['display_name']})" for o in offers) + ".")             if offers else ""
        return done(False, "backup_unusable",
                    "No provider that can be called serves the backup model "
                    + ", ".join(repr(row["model"]) for row in bad)
                    + " (switched on, with a key or on this network, listing the model)." + offer)
    primary = str((await asyncio.to_thread(registry.get_active_profile)).get("model") or "")
    vendors = await asyncio.to_thread(lambda: {m: _vendor(registry, m) for m in {primary, *chain}})
    own = vendors.get(primary, "")
    others = [m for m in chain if m and m != primary and vendors.get(m) not in ("", own)]
    if not others:
        return done(False, "same_vendor",
                    f"Every backup in agent.nonstop.failover.chain ({', '.join(chain)}) is served by {own or 'an unknown provider'}, "
                    f"like {primary}: one outage takes them all down. Add a model of another vendor.", primary=primary)
    backup = others[0]
    names = {"primary": primary, "backup": backup}
    from kazma_core.agent.resilient_chat import resilient_chat
    from kazma_core.llm_provider import LLMError
    from kazma_core.observability import model_fallback

    #: What a call to a model can raise; anything else is a fault of the drill
    #: itself and ends its process (the sweep reports it as the step "error").
    failed = (LLMError, OSError, RuntimeError, ValueError, KeyError, TimeoutError)

    asked = {"messages": PROMPT, "model": primary, "thread_id": _THREAD, "label": "failover drill",
             "max_tokens": 256, "reasoning": "off"}
    try:
        primary_client = await asyncio.to_thread(registry.get_client, primary)
        backup_client = await asyncio.to_thread(registry.get_client, backup)
    except failed as exc:
        return done(False, "failover", f"A client could not be built: {type(exc).__name__}: {exc}", **names)
    with model_fallback.drill_notices() as notices:
        try:
            answer = await resilient_chat(_ForcedDown(primary_client), max_attempts=1, backoff_base=0.0,
                                          frozen_failover=_Chain([(backup, backup_client)]), **asked)
        except failed as exc:
            return done(False, "failover", f"{backup} did not answer: {type(exc).__name__}: {exc}", **names)
        if "ok" not in _text(answer).lower():
            return done(False, "failover", f"{backup} answered {_text(answer)[:80]!r}, not OK.", **names)
        wanted = f"llm.fallback.failover:{primary.lower()}"
        if not any(n["key"] == wanted and backup in n["title"] for n in notices):
            return done(False, "notice", f"{backup} answered, but no failover notice named it (AGENTS.md section 38).",
                        **names)
        try:
            again = await resilient_chat(primary_client, max_attempts=2, backoff_base=1.0,
                                         frozen_failover=_Chain([]), **asked)
        except failed as exc:
            return done(False, "switch_back", f"{primary} did not answer again: {type(exc).__name__}: {exc}", **names)
        if "ok" not in _text(again).lower():
            return done(False, "switch_back", f"{primary} answered {_text(again)[:80]!r}, not OK.", **names)
        if model_fallback.failover_open(primary):
            return done(False, "switch_back", f"{primary} answered again, but its failover notice stayed open.", **names)
    return done(True, "passed",
                f"{primary} forced down: {backup} answered, the notice named it, and {primary} answers again.", **names)


def _report(result: DrillResult) -> None:
    if result.ok:
        logger.info("[failover-drill] PASS: %s (%.1f s)", result.detail, result.seconds)
        return
    logger.warning("[failover-drill] FAIL: %s (step %s)", result.detail, result.step)
    from kazma_core.observability.ops_alerts import alert  # never raises

    alert("llm.failover_drill_failed", "The backup-model drill failed",
          f"Step {result.step}: {result.detail}", severity="warn", cooldown_s=7 * 86400.0)


def _run_process(timeout: float = DRILL_TIMEOUT_S) -> DrillResult:
    """The drill in a process of its own: its own clients, loop and notices,
    so the server's failover state and its chat are never touched."""
    try:
        done = subprocess.run(  # noqa: S603 -- Kazma's own interpreter and module
            [sys.executable, "-m", "kazma_core.observability.failover_drill", "--json"],
            capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8", errors="replace",
            env=dict(os.environ),
        )
    except subprocess.TimeoutExpired:
        return DrillResult(False, "error", f"The drill did not finish within {int(timeout)} s.")
    lines = [line for line in (done.stdout or "").splitlines() if line.startswith("{")]
    try:
        return DrillResult(**json.loads(lines[-1]))
    except (IndexError, ValueError, TypeError):
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
        return DrillResult(False, "error", f"The drill process ended with code {done.returncode}: {tail[0][:300]}")


def drill_sweep(*, run: Any = None, now: float | None = None) -> str:
    """The maintenance sweep: the drill, once every :data:`EVERY_S`."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    last = store.get(RESULT_KEY) or {}
    when = time.time() if now is None else now
    if isinstance(last, dict) and last.get("at") and when - float(last["at"]) < EVERY_S:
        return "not due"
    result = (run or _run_process)()
    store.set(RESULT_KEY, asdict(result), category="system")
    _report(result)
    return result.step


def main(argv: list[str] | None = None) -> int:
    """Run one drill now; ``--json`` prints the result as one JSON line."""
    from kazma_core.env_files import load_env_files

    load_env_files()
    args = sys.argv[1:] if argv is None else argv
    result = asyncio.run(run_drill())
    if "--json" in args:
        print(json.dumps(asdict(result), ensure_ascii=True))
    else:
        print(("PASS" if result.ok else f"FAIL ({result.step})") + f": {result.detail}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
