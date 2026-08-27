"""Order-dependent-flake bisection helper (tests-only tooling).

Symptoms observed on CI/fast_test chunks (2026-08-27): some suites pass
standalone but fail or hang when they share a pytest PROCESS with an
earlier stateful suite. Chunk membership is deterministic round-robin
(``scripts/fast_test.py::chunk_files``), so this helper replays candidate
POLLUTER -> VICTIM PAIRS in isolated subprocesses to find the minimal
repro without running a whole chunk.

Usage::

    # Baseline sanity: does the victim pass alone?
    python tests/order_flake_bisect.py --baseline --victim tests/test_session_directory.py

    # Sweep curated suspects against one victim:
    python tests/order_flake_bisect.py --victim tests/test_session_directory.py

    # Explicit candidates:
    python tests/order_flake_bisect.py --victim <nodeid> --candidates a.py b.py

    # Everything:
    python tests/order_flake_bisect.py --all-victims

Not collected by pytest (name lacks the ``test_`` prefix on the module? it
HAS one -- so it defines no ``test_*`` functions and registers itself via
``collect_ignore``-safe absence of tests)."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Victims = the observed flaky symptoms.
DEFAULT_VICTIMS: list[str] = [
    "tests/test_session_directory.py",
    "tests/test_tui_session_load.py",
]

#: Polluter suspects. Curated from (a) the victim's real chunk-mates and
#: (b) suites that touch process-global singletons: config_store
#: set_config_store, ModelRegistry initialize, SwarmEngine/registry
#: singletons, AlertDispatcher class buffers, SafetyMiddleware set_safety,
#: delivery TurnBroker locks/journal, active_turns registry,
#: SessionManager singleton, and create_app() boots (production).
DEFAULT_CANDIDATES: list[str] = [
    # Real chunk-02 neighbours of test_session_directory.py
    "tests/test_pg_store_dual_backend.py",
    "tests/test_production.py",
    "tests/test_providers.py",
    # Session-manager / delivery / turn-registry stateful suites
    "tests/test_session_manager.py",
    "tests/test_sse_chat.py",
    "tests/test_sse_delivery_v2.py",
    "tests/test_ws_delivery_v2.py",
    "tests/test_ws_chat_telemetry.py",
    "tests/test_chat_sse_fix.py",
    # AlertDispatcher buffers + dedup window (class-level, 300s)
    "tests/test_alert_dispatcher_upgrade.py",
    # Swarm engine / registry singletons
    "tests/test_swarm_dispatch_integration.py",
    "kazma-core/kazma_core_tests/test_hitl_gates_wired.py",
    # Gateway approval callbacks (discord-approval hang suspect)
    "kazma-gateway/kazma_gateway_tests/test_swarm_approval_callbacks.py",
    # Package suites WITHOUT tests/conftest isolation fixtures
    "kazma-ui/kazma_ui_tests/test_active_turns.py",
    "kazma-tui/kazma_tui_tests/test_comprehensive.py",
]


def _run(args: list[str], timeout: float) -> tuple[int, str, float]:
    """Run a command, return (returncode, combined tail output, wall seconds)."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        rc = proc.returncode
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        rc = -999  # hang sentinel
        out = (exc.stdout or "")
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        out += "\nHARD TIMEOUT after %.0fs" % timeout
    return rc, out[-4000:], time.monotonic() - start


def run_pair(candidate: str, victim: str, timeout: float, verbose: bool) -> bool:
    """Return True when the pair REPRODUCES (victim fails/hang only after polluter)."""
    label = f"[{candidate}] -> [{victim}]"
    base_args = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        f"--timeout={int(timeout)}", "--timeout-method=thread",
    ]

    rc_base, out_base, secs_base = _run(base_args + [victim], timeout * 3)
    if rc_base != 0:
        print(f"SKIP   {label}: victim FAILS ALONE (rc={rc_base}, {secs_base:.0f}s) — not order-dependent")
        return False

    rc_pair, out_pair, secs_pair = _run(base_args + [candidate, victim], timeout * 4)
    ok_pair = rc_pair == 0
    verdict = "PASS" if ok_pair else ("REPRO (hang)" if rc_pair == -999 else "REPRO")
    print(f"{verdict:<12} {label}  (pair {secs_pair:.0f}s)")
    if not ok_pair:
        if verbose:
            print("---- pair tail ----")
            print(out_pair)
            print("-------------------")
        else:
            lines = [
                ln for ln in out_pair.splitlines()
                if ("FAILED" in ln or "ERROR" in ln or "Timeout" in ln or "hang" in ln.lower())
            ]
            for ln in lines[-12:]:
                print("   ", ln.strip())
    return not ok_pair


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--victim", action="append", default=[], help="victim nodeid/file (repeatable)")
    ap.add_argument("--candidates", nargs="*", default=None, help="polluter files (default: curated list)")
    ap.add_argument("--timeout", type=float, default=60.0, help="per-pytest-run pytest-timeout seconds")
    ap.add_argument("--baseline", action="store_true", help="only run the victim alone")
    ap.add_argument("--all-victims", action="store_true", help="sweep every DEFAULT_VICTIMS entry")
    ap.add_argument("-v", "--verbose", action="store_true")
    ns = ap.parse_args(argv)

    victims = DEFAULT_VICTIMS if ns.all_victims else ns.victim
    if not victims:
        ap.error("give --victim <path> or --all-victims")
    candidates = DEFAULT_CANDIDATES if ns.candidates is None else ns.candidates

    repros = []
    for victim in victims:
        if ns.baseline:
            rc, out, secs = _run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", victim],
                ns.timeout * 3,
            )
            print(f"BASELINE {victim}: rc={rc} ({secs:.0f}s)")
            continue
        for cand in candidates:
            if run_pair(cand, victim, ns.timeout, ns.verbose):
                repros.append((cand, victim))

    print("\n==== SUMMARY ====")
    if repros:
        for cand, victim in repros:
            print(f"REPRO: [{cand}] -> [{victim}]")
    else:
        print("no pair reproduced among the candidates swept")
    return 1 if repros else 0


if __name__ == "__main__":
    raise SystemExit(main())
