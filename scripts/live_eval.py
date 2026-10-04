"""Run real-model supervisor evaluations with isolated state and fixture tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kazma_core.agent_evaluation import evaluate_case, fingerprint, summarize, validate_dataset


def write_report(path: Path, value: Any) -> None:
    """Write privately and atomically; transcripts may contain private cases."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def environment(root: Path, *, source: dict[str, str] | None = None) -> dict[str, str]:
    """Strip live store overrides and pin every child-owned location."""
    result = {key: value for key, value in (source if source is not None else os.environ).items()
              if not key.startswith("KAZMA_") and key != "DATABASE_URL"}
    result.update(
        KAZMA_DATA_DIR=str(root), KAZMA_PROJECT_ROOT=str(root), KAZMA_USER_HOME=str(root / "home"),
        KAZMA_SKILLS_HOME=str(root / "skills"), KAZMA_WORKSPACE=str(root / "workspace"),
        KAZMA_DB_BACKEND="sqlite", KAZMA_LIVE_EVAL="1", KAZMA_LLM_STREAM="0",
        KAZMA_SELF_IMPROVEMENT="0", KAZMA_SEMANTIC_COMPACT="0", KAZMA_COMMITMENT_ENABLED="0",
        PYTHONIOENCODING="utf-8",
    )
    return result


def worker(payload: dict[str, Any]) -> dict[str, Any]:
    """One disposable process per case, with no source database copied."""
    if os.environ.get("KAZMA_LIVE_EVAL") != "1":
        raise ValueError("Use the isolated parent runner.")
    from kazma_core.agent_runner import KazmaAgent
    from kazma_core.config_store import get_config_store
    from kazma_core.model_registry import initialize_model_registry
    from kazma_core.paths import data_dir

    root = data_dir().resolve()
    if root != Path(payload["isolated_root"]).resolve() or root == Path(payload["source_root"]).resolve():
        raise ValueError("Evaluation storage must be isolated.")
    store = get_config_store()
    store.batch_set([
        ("providers.list", [payload["provider_entry"]], "providers"),
        ("memory.enabled", False, "memory"),
        ("memory.per_turn_retrieval", False, "memory"),
        ("agent.nonstop.ledger.enabled", False, "agent"),
        ("agent.nonstop.failover.enabled", False, "agent"),
    ])
    registry = initialize_model_registry(store)
    client = registry.get_client_by_provider(payload["provider"], payload["model"])
    if client is None:
        raise ValueError("Pinned provider is unavailable.")
    prompt = payload["system_prompt"] or KazmaAgent._default_system_prompt(None)

    async def run() -> dict[str, Any]:
        try:
            result = await evaluate_case(payload["case"], client, model=payload["model"], system_prompt=prompt)
            result["system_prompt_sha256"] = fingerprint(prompt)
            return result
        finally:
            await registry.close()

    return asyncio.run(run())


def run_cases(document: dict[str, Any], *, provider: str, model: str, output: Path, timeout: int) -> dict[str, Any]:
    from kazma_core.config_store import get_config_store
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.model_registry import get_model_registry
    from kazma_core.paths import data_dir

    cases = validate_dataset(document)
    with read_only_diagnostic("live evaluation snapshot"):
        registry = get_model_registry()
        entry = registry.get_provider(provider)
        if not entry or not entry.get("enabled", True):
            raise ValueError("Choose an enabled configured provider.")
        entry = dict(entry)
        entry["base_url"], entry["api_key"] = registry.resolve_provider_credentials(provider)
        system_prompt = get_config_store().get("agent.system_prompt", "")
        source_root = str(data_dir().resolve())
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=5).stdout.strip()
    report: dict[str, Any] = {
        "schema_version": 1, "provider": provider, "model": model, "revision": revision,
        "working_tree_dirty": bool(dirty),
        "dataset_sha256": fingerprint(document), "results": [],
        "tool_effects": "fixtures_only", "max_iterations": 5, "max_model_calls": 12, "planned_cases": len(cases),
    }
    script = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="kazma-live-eval-") as temporary:
        for index, case in enumerate(cases):
            root = Path(temporary) / str(index)
            root.mkdir()
            payload = {"isolated_root": str(root), "source_root": source_root, "provider_entry": entry,
                       "provider": provider, "model": model, "system_prompt": system_prompt, "case": case}
            try:
                process = subprocess.run([sys.executable, script, "--worker"], input=json.dumps(payload),
                                         text=True, encoding="utf-8", capture_output=True,
                                         cwd=root, env=environment(root), timeout=timeout)
                if process.returncode:
                    raise ValueError("Isolated worker failed; provider details were withheld.")
                result = json.loads(process.stdout.strip().splitlines()[-1])
            except (subprocess.TimeoutExpired, ValueError, IndexError):
                result = {"id": case["id"], "language": case["language"], "split": case["split"],
                          "group_id": case["group_id"], "case_sha256": fingerprint(case),
                          "human_labeled": case["human_labeled"], "rubric": case["rubric"],
                          "answer": "", "answer_sha256": fingerprint(""), "error": "worker_failed_or_timed_out",
                          "checks": {"turn_succeeded": False}, "review": None}
            report["results"].append(result)
            report["summary"] = summarize(report)
            write_report(output, report)
            print(f"{index + 1}/{len(cases)} {case['id']}: {'failed' if result.get('error') else 'recorded; needs human review'}", flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.load(sys.stdin)), ensure_ascii=False))
        return 0
    if args.summarize:
        print(json.dumps(summarize(json.loads(args.summarize.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
        return 0
    if not args.dataset:
        parser.error("Choose a dataset or --summarize report.json.")
    if args.dataset.stat().st_size > 20_000_000:
        parser.error("Dataset exceeds twenty megabytes.")
    document = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = validate_dataset(document)
    if args.validate:
        print(f"Valid: {len(cases)} cases; no model calls made.")
        return 0
    if not args.provider or not args.model or not args.output:
        parser.error("A live run requires --provider, --model and --output; model calls may incur provider charges.")
    if not 10 <= args.timeout <= 600:
        parser.error("--timeout must be between 10 and 600 seconds per case.")
    if args.output.resolve() == args.dataset.resolve() or args.output.exists():
        parser.error("Choose a new output file; preserve the frozen dataset and earlier reports.")
    from kazma_core.env_files import load_env_files

    load_env_files()
    report = run_cases(document, provider=args.provider, model=args.model, output=args.output, timeout=args.timeout)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if any(result.get("error") for result in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
