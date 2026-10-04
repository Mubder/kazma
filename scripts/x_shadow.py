"""Collect real X cases or evaluate the full reply path in isolated snapshots.

Uses the configured X model endpoints. X network reads/writes and alert delivery
are blocked. Each case starts from the same captured history/budget baseline.
Human labels and actual-candidate safety review are never generated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _write(path: Path, document: Any) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(document, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_database(source: Path, destination: Path) -> None:
    """Read-only online snapshot, including committed WAL contents."""
    reader = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer, pages=100, sleep=.01)
            writer.commit()
        finally:
            writer.close()
    finally:
        reader.close()


def _snapshot(destination: Path) -> dict[str, Any]:
    from kazma_core import paths
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.qualification import pipeline_fingerprint
    from kazma_core.x_api.stance import get_reply_config

    # Compute first: registry initialization must not make a child inherit a
    # different provider inventory from the identity captured here.
    fingerprint = pipeline_fingerprint(get_reply_config())
    cs = get_config_store()
    items = [(key, cs._resolve_vault_value(key, value, migrate=False), category)
             for category, values in cs.get_all().items() for key, value in values.items()]
    root = paths.data_dir().resolve()
    for name in ("settings.db", "x_replies.db", "x_posts.db", "x_publications.db", "x_scheduled.db"):
        source = Path(paths.settings_db()) if name == "settings.db" else root / name
        if source.is_file():
            _copy_database(source, destination / name)
    _snapshot_vectors(Path(paths.vector_memory_path()), destination / "vector_memory")
    if pipeline_fingerprint(get_reply_config()) != fingerprint:
        raise ValueError("The source policy/model/evidence changed during capture. Retry from a quiet snapshot.")
    return {"items": items, "source_root": str(root), "fingerprint": fingerprint}


def _snapshot_vectors(source: Path, destination: Path) -> None:
    """Keep semantic retrieval in the captured directory, never the live index."""
    if not source.is_dir():
        return
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Vector snapshot refuses links that could reopen a live store.")
    before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
    for path in files:
        if path.name.endswith(("-wal", "-shm")):
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in (".sqlite3", ".db"):
            _copy_database(path, target)
        else:
            shutil.copy2(path, target)
    after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in source.rglob("*") if path.is_file()}
    if before != after:
        raise ValueError("Vector index changed during capture. Retry after indexing has settled.")


def _worker(payload: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("KAZMA_X_SHADOW") != "1":
        raise ValueError("Worker requires isolated shadow transport.")
    from kazma_core.config_store import get_config_store
    from kazma_core.model_registry import get_model_registry
    from kazma_core.paths import data_dir
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.qualification import pipeline_fingerprint
    from kazma_core.x_api.shadow import evaluate_case
    from kazma_core.x_api.stance import get_reply_config

    root = data_dir().resolve()
    if root == Path(payload["snapshot"]["source_root"]).resolve():
        raise ValueError("Worker must never use source stores.")
    (root / ".x-shadow-isolated").write_text(str(os.getpid()), encoding="utf-8")
    with tenant_scope(payload["tenant"]):
        get_config_store().batch_set([tuple(item) for item in payload["snapshot"]["items"]])
        fingerprint = pipeline_fingerprint(get_reply_config())
        if fingerprint != payload["snapshot"]["fingerprint"]:
            raise ValueError("Snapshot model/policy/evidence identity changed; evaluate a fresh snapshot.")

        async def run() -> dict[str, Any]:
            try:
                return await evaluate_case(payload["case"], isolated_root=root)
            finally:
                await get_model_registry().close()

        return asyncio.run(run())


def _environment(directory: Path) -> dict[str, str]:
    """Pin child stores and Unicode pipes without inheriting live path overrides."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("KAZMA_")}
    environment.update({key: value for key, value in os.environ.items()
                        if key in ("KAZMA_X_POST", "KAZMA_X_REPLY", "KAZMA_X_SCHEDULE")})
    environment.update(KAZMA_DATA_DIR=str(directory), KAZMA_PROJECT_ROOT=str(directory),
                       KAZMA_USER_HOME=str(directory / "home"), KAZMA_DB_BACKEND="sqlite",
                       KAZMA_X_SHADOW="1", PYTHONIOENCODING="utf-8")
    return environment


def _collect(*, tenant: str, limit: int) -> list[dict[str, Any]]:
    """Upgrade a copied legacy reply store, never the source being exported."""
    from kazma_core import paths

    source = paths.data_dir() / "x_replies.db"
    if not source.is_file():
        return []
    with tempfile.TemporaryDirectory(prefix="kazma-x-collect-") as temporary:
        directory = Path(temporary).resolve()
        _copy_database(source, directory / "x_replies.db")
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--collect-worker"],
                                 input=json.dumps({"tenant": tenant, "limit": limit, "source_root": str(source.parent)}),
                                 text=True, encoding="utf-8", capture_output=True, cwd=directory,
                                 env=_environment(directory), timeout=60)
        if process.returncode or not process.stdout.strip():
            raise ValueError("Isolated case collection failed; source stores were not modified.")
        return json.loads(process.stdout.strip().splitlines()[-1])


def _collection_worker(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """An explicit CLI entry point keeps the collector isolated and inspectable."""
    from kazma_core.paths import data_dir
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.corpus import collect_cases

    if (os.environ.get("KAZMA_X_SHADOW") != "1"
            or data_dir().resolve() == Path(payload["source_root"]).resolve()):
        raise ValueError("Collection worker requires an isolated copied store.")
    with tenant_scope(payload["tenant"]):
        return collect_cases(limit=payload["limit"])


def _evaluate(cases: list[dict[str, Any]], *, tenant: str, output: Path) -> None:
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="kazma-x-shadow-") as temporary:
        root = Path(temporary).resolve()
        baseline = root / "baseline"
        baseline.mkdir()
        from kazma_core.diagnostic_scope import read_only_diagnostic

        with read_only_diagnostic("X shadow capture"):
            snapshot = _snapshot(baseline)
        report = {"fingerprint": snapshot["fingerprint"], "evaluated_at": time.time(), "cases": [],
                  "protocol": "independent cases; fixed history/budget baseline; activation simulated; X transport blocked"}
        for index, case in enumerate(cases):
            with tempfile.TemporaryDirectory(prefix=f"case-{index:05d}-", dir=root) as child:
                directory = Path(child)
                shutil.copytree(baseline, directory, dirs_exist_ok=True)
                request = {"snapshot": snapshot, "case": case, "tenant": tenant}
                process = subprocess.run([sys.executable, str(script), "--worker"],
                                         input=json.dumps(request, ensure_ascii=False), text=True, encoding="utf-8",
                                         capture_output=True, cwd=directory, env=_environment(directory), timeout=240)
            lines = process.stdout.strip().splitlines()
            if process.returncode or not lines:
                raise ValueError(f"Isolated case {index + 1} failed; earlier observations remain in the output file.")
            observed = json.loads(lines[-1])
            report["cases"].append(observed)
            _write(output, report)
            print(f"Recorded {index + 1}/{len(cases)}; auto eligibility: {observed['actual']['auto']}", flush=True)
        print("Human review is required: label applicability and inspect each actual candidate before qualification.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--collect", type=Path, metavar="OUTPUT")
    modes.add_argument("--evaluate", type=Path, metavar="CASES")
    modes.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--collect-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    try:
        if args.collect_worker:
            print(json.dumps(_collection_worker(json.load(sys.stdin)), ensure_ascii=False))
            return 0
        if args.worker:
            print(json.dumps(_worker(json.load(sys.stdin)), ensure_ascii=False))
            return 0
        from kazma_core.env_files import load_env_files
        from kazma_core.tenant_context import tenant_scope

        load_env_files()
        with tenant_scope(args.tenant):
            if args.collect:
                cases = _collect(tenant=args.tenant, limit=args.limit)
                _write(args.collect, {"cases": cases, "annotation": "All labels are intentionally empty; split conversations before tuning."})
                print(f"Collected {len(cases)} real stored cases for human annotation.")
            else:
                if not args.output or args.output.resolve() == args.evaluate.resolve():
                    raise ValueError("Evaluation needs a separate --output file.")
                if args.evaluate.stat().st_size > 20_000_000:
                    raise ValueError("Case input exceeds twenty megabytes.")
                cases = json.loads(args.evaluate.read_text(encoding="utf-8"))["cases"]
                if not isinstance(cases, list) or not 1 <= len(cases) <= 10000:
                    raise ValueError("Evaluation needs 1–10000 recorded cases.")
                _evaluate(cases, tenant=args.tenant, output=args.output)
    except (ValueError, OSError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        from kazma_core.errors import redact_secrets

        print(json.dumps({"ok": False, "error": redact_secrets(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
