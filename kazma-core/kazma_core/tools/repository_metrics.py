"""Fresh Kazma source measurements using the shipped generator, without pytest."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _source_root() -> Path:
    """Only the source installation containing this tool; never a supplied script."""
    return Path(__file__).resolve().parents[3]


def _git(root: Path, *args: str) -> str:
    from kazma_core.security.child_env import tool_child_env

    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", *args], cwd=root,
        env=tool_child_env(), text=True, errors="replace", timeout=20,
        stderr=subprocess.DEVNULL,
    ).strip()


def _stamp(root: Path) -> tuple[str, str]:
    return (_git(root, "rev-parse", "HEAD"),
            _git(root, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"))


def _measure() -> dict:
    root = _source_root()
    script = root / "scripts" / "generate_metrics.py"
    if not (root / ".git").exists() or not script.is_file() or script.is_symlink():
        return {"ok": False, "error": "Fresh Kazma metrics require a source installation with the shipped metrics generator. Do not substitute a cached METRICS.md or another repository's script."}
    before = _stamp(root)
    spec = importlib.util.spec_from_file_location("_kazma_metrics_measurement", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Same counting implementation as the CLI/website; bounded git without
    # server credentials or filesystem monitors. No model-supplied command.
    module.git = lambda *args: _git(root, *args)
    module._count_contributors_via_api = lambda: None
    measured = module.collect(runtime_tests=False)
    upstream = None
    try:
        upstream_sha = _git(root, "rev-parse", "--verify", "refs/remotes/origin/main")
        upstream = {"ref": "origin/main", "sha": upstream_sha,
                    "commits": int(_git(root, "rev-list", "--count", upstream_sha))}
    except subprocess.CalledProcessError:
        pass  # A checkout without a fetched upstream still has source counts.
    if before != _stamp(root) or before[0] != measured["commit"]["sha"]:
        return {"ok": False, "error": "The source changed during measurement. Retry repository_metrics; no numbers from this run are verified."}
    return {
        "ok": True, "source": "scripts/generate_metrics.py:collect(runtime_tests=False)",
        "repository": "Kazma source installation", "commit": measured["commit"],
        "measured_at": datetime.now(UTC).isoformat(), "tracked_changes": bool(before[1]),
        "python": measured["python"], "areas": measured["areas"],
        "package_count": len(measured["packages"]),
        "test_files": measured["tests"]["files"],
        "test_functions": measured["tests"]["test_functions_total"],
        "collected_tests": None, "upstream": upstream,
        "installation_history_commits": measured["git"]["commits"],
        "claim_rules": "Use these newly measured figures, not METRICS.md. Test functions are source definitions, NOT collected tests or passing tests. Runtime collection is deliberately not executed by this read-only tool; omit that figure when unavailable. Tracked changes mean working-tree counts, not pristine commit counts. Installation history includes local merges: do not claim it as the public repository commit count or as metrics drift. Upstream counts describe the last fetched origin/main at its reported SHA, not a fresh GitHub lookup. Publishing still requires the normal X approval.",
    }


async def repository_metrics() -> str:
    """Measure fresh installed Kazma source counts without writes or test imports."""
    try:
        result = await asyncio.to_thread(_measure)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.warning("Repository metrics failed: %s", type(exc).__name__)
        result = {"ok": False, "error": "Fresh repository measurement failed. Do not quote cached metrics as current; retry repository_metrics."}
    return json.dumps(result, ensure_ascii=False)
