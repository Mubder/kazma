#!/usr/bin/env python3
"""Mixed-load soak of the durable document pipeline, with restarts and crashes.

Q03 asks for a 48-72 hour run on a supported profile; this is the harness for
it, not the result. It drives a real ``DocumentIngestionService`` (the
configured isolation profile and metadata/job backends, in a PRIVATE data
directory unless ``--data-dir`` names one) with:

* a steady mix of uploads -- the EN/AR/bilingual fidelity corpus (DOCX, PDF,
  HTML) and small text documents -- at ``--rate`` per minute;
* a service restart every ``--restart-every`` seconds (workers stopped with
  their bounded shutdown, the service closed and reopened: recovery runs);
* an intake killed mid-publication every ``--crash-every`` seconds (a real
  child process ``os._exit`` after the document row): the next restart must
  roll it back.

The report (``--report``, JSON) holds throughput, intake-to-ready latency
p50/p95/p99, every job's final state, admission ``pending``/``stranded`` at the
end, process-tree RSS and handle samples, scratch left in the storage root,
and every error. The run FAILS (exit 1) when a job did not reach a terminal
state, a reservation is left pending, or scratch outlives the run.

Usage:
  python scripts/document_soak.py --duration 172800 --report soak.json
  python scripts/document_soak.py --duration 120 --rate 30 --restart-every 40 --crash-every 50
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import multiprocessing
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

_KILLED = 137


def _config(root: Path):
    from dataclasses import replace

    from kazma_core.documents.config import get_document_config

    return replace(get_document_config(), storage_root=root, security_local_salvage=False)


def _crash_child(root: str, data_dir: str) -> None:
    """An intake killed after its document row: what a power cut leaves."""
    os.environ["KAZMA_DATA_DIR"] = data_dir
    from kazma_core.documents.ingestion import DocumentIngestionService

    svc = DocumentIngestionService(config=_config(Path(root)))
    real = svc.repository.create_document

    def create_then_die(*args, **kwargs):
        real(*args, **kwargs)
        os._exit(_KILLED)

    svc.repository.create_document = create_then_die
    svc.ingest_stream(io.BytesIO(os.urandom(8).hex().encode()), filename="crash.txt",
                      tenant_id="soak", actor_id="soak", workspace_id="soak")
    os._exit(3)


def _corpus(work: Path) -> list[tuple[str, bytes]]:
    from kazma_core.documents import fidelity

    items = []
    for case in fidelity.corpus():
        if case.fmt == "ocr":
            continue
        try:
            path = fidelity.build_case(case, work / "corpus")
        except (OSError, RuntimeError, ValueError, ImportError):
            continue  # an engine missing here: the plain documents still run
        items.append((path.name, path.read_bytes()))
    return items


def _tree_sample() -> dict[str, float]:
    import psutil

    me = psutil.Process()
    procs = [me, *me.children(recursive=True)]
    rss = handles = 0
    for proc in procs:
        try:
            rss += proc.memory_info().rss
            handles += proc.num_handles() if os.name == "nt" else proc.num_fds()
        except psutil.Error:
            continue
    return {"rss_mib": round(rss / 2**20, 1), "handles": handles, "processes": len(procs)}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


async def _soak(args: argparse.Namespace, root: Path, data_dir: str) -> dict:
    from kazma_core.documents.ingestion import SYSTEM_ACTOR, DocumentIngestionService

    corpus = _corpus(root.parent)
    rng = random.Random(args.seed)
    started = time.monotonic()
    deadline = started + args.duration
    submitted: dict[str, float] = {}
    ready_at: dict[str, float] = {}
    errors: list[str] = []
    samples: list[dict] = []
    restarts = crashes = 0
    next_restart = started + args.restart_every
    next_crash = started + args.crash_every
    interval = 60.0 / max(1, args.rate)
    svc = DocumentIngestionService(config=_config(root))
    await svc.start_workers()
    try:
        while time.monotonic() < deadline:
            name, body = rng.choice(corpus) if corpus and rng.random() < 0.6 else (
                f"note-{rng.randrange(10**9)}.txt", f"Soak document {rng.random()}\n".encode())
            try:
                result = await asyncio.to_thread(
                    svc.ingest_stream, io.BytesIO(body), filename=name, tenant_id="soak",
                    actor_id="soak", workspace_id="soak",
                )
                submitted[str(result.job_id)] = time.monotonic()
            except Exception as exc:  # noqa: BLE001 - a soak records every failure
                errors.append(f"intake:{type(exc).__name__}:{str(exc)[:120]}")
            for job_id in [j for j in submitted if j not in ready_at]:
                status = await asyncio.to_thread(svc.job_status, tenant_id="soak", job_id=job_id,
                                                 actor_id=SYSTEM_ACTOR)
                if status and status["state"] in {"ready", "rejected", "dead_letter", "cancelled"}:
                    ready_at[job_id] = time.monotonic()
            now = time.monotonic()
            if now >= next_crash:
                ctx = multiprocessing.get_context("spawn")
                child = ctx.Process(target=_crash_child, args=(str(root), data_dir))
                child.start()
                child.join(120)
                crashes += child.exitcode == _KILLED
                next_crash = now + args.crash_every
            if now >= next_restart:
                await svc.stop_workers()
                svc.close()
                svc = DocumentIngestionService(config=_config(root))  # recovery runs here
                await svc.start_workers()
                restarts += 1
                next_restart = now + args.restart_every
            samples.append({"t": round(now - started, 1), **_tree_sample()})
            await asyncio.sleep(interval)
        # Drain: everything submitted must end.
        drain_until = time.monotonic() + args.drain
        while time.monotonic() < drain_until and len(ready_at) < len(submitted):
            for job_id in [j for j in submitted if j not in ready_at]:
                status = await asyncio.to_thread(svc.job_status, tenant_id="soak", job_id=job_id,
                                                 actor_id=SYSTEM_ACTOR)
                if status and status["state"] in {"ready", "rejected", "dead_letter", "cancelled"}:
                    ready_at[job_id] = time.monotonic()
            await asyncio.sleep(1)
        states: dict[str, int] = {}
        for job_id in submitted:
            status = svc.job_status(tenant_id="soak", job_id=job_id, actor_id=SYSTEM_ACTOR) or {}
            states[status.get("state", "missing")] = states.get(status.get("state", "missing"), 0) + 1
        # A crash after the last restart is recovered by the running service's
        # sweep (once a minute), not only at the next start.
        from kazma_core.documents import ingestion

        sweep_until = time.monotonic() + 2 * ingestion._INTAKE_SWEEP_SECONDS + 10
        admission = svc.admissions.snapshot(tenant_id="soak")
        while admission.get("pending", 0) and time.monotonic() < sweep_until:
            await asyncio.sleep(2)
            admission = svc.admissions.snapshot(tenant_id="soak")
    finally:
        await svc.stop_workers()
        svc.close()
    latencies = [ready_at[j] - submitted[j] for j in ready_at]
    scratch = sum(
        1 for folder in (".upload-staging", ".stage", "parser-runs", "operation-runs")
        for _ in (root / folder).glob("*") if (root / folder).is_dir()
    )
    elapsed = time.monotonic() - started
    return {
        "duration_seconds": round(elapsed, 1),
        "submitted": len(submitted),
        "finished": len(ready_at),
        "throughput_per_minute": round(len(ready_at) / max(elapsed, 1) * 60, 2),
        "latency_seconds": {
            "p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
        },
        "states": states,
        "restarts": restarts,
        "crashes_injected": crashes,
        "admission_end": admission,
        "scratch_left": scratch,
        "resources": {
            "max_rss_mib": max((s["rss_mib"] for s in samples), default=None),
            "max_handles": max((s["handles"] for s in samples), default=None),
            "samples": samples[:: max(1, len(samples) // 200)],
        },
        "errors": errors[:200],
        "error_count": len(errors),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--duration", type=float, default=600.0, help="seconds of load")
    parser.add_argument("--rate", type=int, default=20, help="uploads per minute")
    parser.add_argument("--restart-every", type=float, default=900.0)
    parser.add_argument("--crash-every", type=float, default=1200.0)
    parser.add_argument("--drain", type=float, default=600.0, help="seconds to let work finish")
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--use-env", action="store_true",
        help="load the .env ladder and use its configured backends (Postgres, container "
             "workers); by default the soak runs on private SQLite stores and touches no database",
    )
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="kazma-soak-"))
    data_dir = args.data_dir or str(work / "data")
    if args.use_env:
        from kazma_core.env_files import load_env_files

        load_env_files()
    else:
        # Never reach a database by accident: a soak writes thousands of rows.
        for name in ("KAZMA_DB_BACKEND", "KAZMA_DOCUMENTS_METADATA_BACKEND", "KAZMA_DOCUMENTS_JOBS_BACKEND"):
            os.environ[name] = "sqlite"
        os.environ.pop("KAZMA_DATABASE_URL", None)
    os.environ["KAZMA_DATA_DIR"] = data_dir
    from kazma_core.config_store import get_config_store

    get_config_store().set("documents.enabled", True, category="documents")
    report = asyncio.run(_soak(args, work / "store", data_dir))
    report["profile"] = {
        "worker_isolation": os.environ.get("KAZMA_DOCUMENT_WORKER_ISOLATION", "process"),
        "data_dir": data_dir,
    }
    healthy = (
        report["finished"] == report["submitted"]
        and report["admission_end"].get("pending", 0) == 0
        and report["scratch_left"] == 0
    )
    report["healthy"] = healthy
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
