"""Bounded release-certification checks for document intelligence."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import platform
import shutil
import sys
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import DocumentConfig, get_document_config
from .errors import DocumentParseError
from .hostile_corpus import hostile_corpus_manifest, write_hostile_corpus
from .sandbox import SandboxResult
from .service import DocumentService
from .sniff import sniff_document

__all__ = [
    "CertificationCheck",
    "certify_hostile_corpus",
    "run_document_certification",
    "run_performance_smoke",
]

_CHECK_STATUSES = frozenset({"PASS", "CONDITIONAL", "NOT RUN", "FAIL"})


class _ProcessTreeSampler:
    """Sample native RSS in the parent and its workers; never claim a hard peak."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.metrics: dict[str, int | float | str | bool | None] = {
            "tree_rss_sampled_peak_bytes": None, "worker_rss_sampled_peak_bytes": None,
            "resource_sample_count": 0, "worker_processes_observed": 0,
            "resource_sampling_interval_seconds": 0.01,
        }

    def start(self) -> None:
        try:
            import psutil
        except ImportError:
            self.metrics["resource_sampling_status"] = "unavailable"
            return
        root = psutil.Process()
        seen: set[tuple[int, float]] = set()

        def sample() -> None:
            while not self.stop.is_set():
                try:
                    children = root.children(recursive=True)
                    rss = root.memory_info().rss
                    worker_rss = 0
                    for child in children:
                        try:
                            worker_rss += child.memory_info().rss
                            seen.add((child.pid, child.create_time()))
                        except psutil.Error:
                            continue  # A worker can exit between enumeration and inspection.
                    self.metrics["tree_rss_sampled_peak_bytes"] = max(
                        int(self.metrics["tree_rss_sampled_peak_bytes"] or 0), rss + worker_rss)
                    self.metrics["worker_rss_sampled_peak_bytes"] = max(
                        int(self.metrics["worker_rss_sampled_peak_bytes"] or 0), worker_rss)
                    self.metrics["resource_sample_count"] += 1
                    self.metrics["worker_processes_observed"] = len(seen)
                    self.metrics["resource_sampling_status"] = "sampled"
                except psutil.Error:
                    self.metrics["resource_sampling_status"] = "incomplete"
                self.stop.wait(0.01)

        self.thread = threading.Thread(target=sample, name="document-cert-resources", daemon=True)
        self.thread.start()

    def finish(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1)


@dataclass(frozen=True, slots=True)
class CertificationCheck:
    name: str
    status: str
    detail: str
    metrics: dict[str, int | float | str | bool | None] | None = None

    def __post_init__(self) -> None:
        if self.status not in _CHECK_STATUSES:
            raise ValueError(f"invalid certification status: {self.status}")

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        if self.metrics is None:
            value.pop("metrics")
        return value


def _process_resource_count() -> tuple[str, int] | None:
    try:
        import psutil

        process = psutil.Process()
        if os.name == "nt":
            return "handles", int(process.num_handles())
        return "file_descriptors", int(process.num_fds())
    except (AttributeError, ImportError, OSError):
        return None


async def run_performance_smoke(
    root: str | Path,
    *,
    iterations: int = 3,
    concurrency: int = 2,
    duration_limit_seconds: float = 30.0,
    event_loop_stall_limit_seconds: float = 0.75,
    peak_memory_limit_bytes: int = 128 * 1024 * 1024,
) -> CertificationCheck:
    """Exercise isolated parsing while measuring bounded event-loop/resources."""

    if iterations <= 0 or concurrency <= 0:
        raise ValueError("iterations and concurrency must be positive")
    work_root = Path(root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    source = work_root / "performance-smoke.txt"
    source.write_text(("Kazma bounded document smoke.\n" * 2048), encoding="utf-8")
    config = replace(
        get_document_config(),
        storage_root=work_root / "store",
        worker_timeout_seconds=min(get_document_config().worker_timeout_seconds, 20),
        worker_memory_mb=min(get_document_config().worker_memory_mb, 512),
        ocr_enabled=False,
    )
    # Pre-clean any residual parser-runs from prior runs
    parser_runs = config.storage_root / "parser-runs"
    if parser_runs.is_dir():
        shutil.rmtree(parser_runs, ignore_errors=True)
    service = DocumentService(config=config)
    semaphore = asyncio.Semaphore(concurrency)
    stop_heartbeat = asyncio.Event()
    max_stall = 0.0

    async def heartbeat() -> None:
        nonlocal max_stall
        interval = 0.01
        while not stop_heartbeat.is_set():
            before = asyncio.get_running_loop().time()
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
            except TimeoutError:
                elapsed = asyncio.get_running_loop().time() - before
                max_stall = max(max_stall, max(0.0, elapsed - interval))

    async def parse_once() -> None:
        async with semaphore:
            result = await service.read_transient(
                source,
                approved_path=source,
                max_chars=1024,
                fence=True,
            )
            if not result.fenced or "<kazma:data" not in result.text:
                raise AssertionError("performance smoke produced unfenced content")

    threads_before = threading.active_count()
    resources_before = _process_resource_count()
    resource_sampler = _ProcessTreeSampler()
    resource_sampler.start()
    tracemalloc.start()
    started = time.monotonic()
    beat = asyncio.create_task(heartbeat(), name="document-cert-heartbeat")
    error: str | None = None
    try:
        await asyncio.gather(*(parse_once() for _ in range(iterations)))
    except Exception as exc:  # noqa: BLE001 - result must report a safe failure
        error = f"{type(exc).__name__}: {exc}"
    finally:
        stop_heartbeat.set()
        await beat
        resource_sampler.finish()
    duration = time.monotonic() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    await asyncio.sleep(0.2)
    gc.collect()
    gc.collect()  # Second pass to catch generation-2 objects
    threads_after = threading.active_count()
    resources_after = _process_resource_count()
    parser_runs = config.storage_root / "parser-runs"
    residual_runs = (
        len([item for item in parser_runs.iterdir() if item.is_dir()])
        if parser_runs.is_dir()
        else 0
    )
    resource_delta: int | None = None
    resource_name = "unavailable"
    if resources_before is not None and resources_after is not None:
        resource_name = resources_after[0]
        resource_delta = resources_after[1] - resources_before[1]
    metrics: dict[str, int | float | str | bool | None] = {
        "iterations": iterations,
        "concurrency": concurrency,
        "duration_seconds": round(duration, 6),
        "documents_per_second": round(iterations / max(duration, 0.000001), 3),
        "max_event_loop_stall_seconds": round(max_stall, 6),
        "tracemalloc_peak_bytes": int(peak),
        "tracemalloc_scope": "parent_python_allocations_only",
        "thread_delta": threads_after - threads_before,
        "resource_kind": resource_name,
        "resource_delta": resource_delta,
        "residual_parser_run_directories": residual_runs,
        **resource_sampler.metrics,
    }
    failures: list[str] = []
    if error:
        failures.append(error)
    if duration > duration_limit_seconds:
        failures.append(
            f"duration {duration:.3f}s exceeded {duration_limit_seconds:.3f}s"
        )
    if max_stall > event_loop_stall_limit_seconds:
        failures.append(
            f"event-loop stall {max_stall:.3f}s exceeded "
            f"{event_loop_stall_limit_seconds:.3f}s"
        )
    if peak > peak_memory_limit_bytes:
        failures.append(
            f"tracemalloc peak {peak} exceeded {peak_memory_limit_bytes} bytes"
        )
    if threads_after - threads_before > concurrency + 3:
        failures.append("worker/reader thread growth exceeded the bounded allowance")
    if resource_delta is not None and resource_delta > concurrency + 6:
        # On Windows, subprocess handle cleanup is deferred to GC;
        # accept a 4× margin to account for this platform behaviour.
        if os.name != "nt" or resource_delta > (concurrency + 6) * 4:
            failures.append(f"{resource_name} growth exceeded the bounded allowance")
    if residual_runs:
        if os.name == "nt":
            # Windows may hold file handles briefly; a single residual dir
            # from a timed-out parser is acceptable.
            if residual_runs > 1:
                failures.append(
                    "isolated parser run directories were not cleaned "
                    f"({residual_runs} residual)"
                )
        else:
            failures.append("isolated parser run directories were not cleaned")
    if failures:
        return CertificationCheck(
            "bounded_performance_event_loop_resources",
            "FAIL",
            "; ".join(failures),
            metrics,
        )
    return CertificationCheck(
        "bounded_performance_event_loop_resources",
        "PASS",
        "Bounded parser work stayed off the event loop and released per-run resources.",
        metrics,
    )


def _case_config(
    base: DocumentConfig,
    root: Path,
    overrides: dict[str, Any],
) -> DocumentConfig:
    return replace(
        base,
        storage_root=root / "store",
        ocr_enabled=False,
        worker_timeout_seconds=min(base.worker_timeout_seconds, 20),
        worker_memory_mb=min(base.worker_memory_mb, 512),
        **overrides,
    )


def certify_hostile_corpus(root: str | Path) -> CertificationCheck:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path, manifest = write_hostile_corpus(root / "hostile-corpus")
    committed = (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "documents"
        / "hostile_manifest.json"
    )
    if committed.is_file():
        from kazma_core.documents.hostile_corpus import canonical_manifest

        expected = json.loads(committed.read_text(encoding="utf-8"))
        # Canonical comparison: sha256/byte_size are ZIP-container artifacts
        # whose DEFLATE streams differ across zlib builds — byte equality
        # with the committed manifest is unattainable cross-platform
        # (deep-audit 2026-08-19 CI triage).
        if canonical_manifest(expected) != canonical_manifest(manifest):
            return CertificationCheck(
                "hostile_corpus",
                "FAIL",
                "Generated hostile-corpus manifest differs from the reviewed committed manifest.",
                {"cases": len(manifest["cases"]), "manifest": str(manifest_path)},
            )

    base = get_document_config()
    failures: list[str] = []
    fenced = 0
    rejected = 0
    for raw_case in manifest["cases"]:
        if not isinstance(raw_case, dict):
            failures.append("manifest contains a non-object case")
            continue
        case_id = str(raw_case["id"])
        path = manifest_path.parent / str(raw_case["filename"])
        overrides = dict(raw_case.get("config_overrides") or {})
        config = _case_config(base, root / case_id, overrides)
        expected_codes = {str(code) for code in raw_case.get("expected_codes") or []}
        stage = str(raw_case["stage"])
        disposition = str(raw_case["disposition"])
        try:
            if stage == "sniff":
                sniff_document(path, config)
                outcome: object = None
            elif stage == "parse":
                outcome = DocumentService(config=config).read_transient_sync(
                    path,
                    approved_path=path,
                    max_chars=4096,
                    fence=True,
                )
            elif stage == "sandbox":
                from . import service as service_module

                original = service_module.run_isolated_subprocess

                def no_result(request: Any) -> SandboxResult:
                    return SandboxResult(
                        command=request.command,
                        returncode=137,
                        stdout=b"",
                        stderr=b"",
                        duration_seconds=0.001,
                        timed_out=False,
                        output_limit_exceeded=False,
                        resource_limits_enforced=True,
                        resource_limit_degraded_reason=None,
                    )

                service_module.run_isolated_subprocess = no_result
                try:
                    outcome = DocumentService(config=config).read_transient_sync(
                        path,
                        approved_path=path,
                        max_chars=4096,
                        fence=True,
                    )
                finally:
                    service_module.run_isolated_subprocess = original
            else:
                failures.append(f"{case_id}: unknown certification stage {stage}")
                continue
        except DocumentParseError as exc:
            if disposition == "reject":
                if expected_codes and exc.code not in expected_codes:
                    failures.append(
                        f"{case_id}: rejection code {exc.code} not in "
                        f"{sorted(expected_codes)}"
                    )
                else:
                    rejected += 1
            else:
                failures.append(f"{case_id}: unexpectedly rejected with {exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{case_id}: leaked {type(exc).__name__} outside typed boundary")
            continue

        if disposition == "reject":
            failures.append(f"{case_id}: hostile sample was accepted")
            continue
        text = str(getattr(outcome, "text", ""))
        if (
            not bool(getattr(outcome, "fenced", False))
            or '<kazma:data source="document" untrusted="true">' not in text
            or "</kazma:data>" not in text
        ):
            failures.append(f"{case_id}: accepted untrusted content was not fenced")
        else:
            fenced += 1

    metrics: dict[str, int | float | str | bool | None] = {
        "cases": len(manifest["cases"]),
        "rejected": rejected,
        "fenced": fenced,
        "manifest": str(manifest_path),
    }
    if failures:
        return CertificationCheck("hostile_corpus", "FAIL", "; ".join(failures), metrics)
    return CertificationCheck(
        "hostile_corpus",
        "PASS",
        "Every hostile sample was rejected or retained only inside the prompt fence.",
        metrics,
    )


def _capability_check(root: Path) -> CertificationCheck:
    config = replace(get_document_config(), storage_root=root / "capability-store")
    health = DocumentService(config=config).health()
    optional: list[str] = []
    for group, identity in (
        ("renderers", "renderer_id"),
        ("mutators", "renderer_id"),
    ):
        for item in health.get(group, []):
            if isinstance(item, dict) and item.get("readiness") != "ready":
                optional.append(f"{item.get(identity)}={item.get('readiness')}")
    ocr = health.get("ocr")
    if isinstance(ocr, dict) and ocr.get("readiness") != "ready":
        optional.append(f"ocr={ocr.get('readiness')}")
    parsers = health.get("parsers", [])
    ready_parsers = sum(
        1
        for item in parsers
        if isinstance(item, dict) and item.get("readiness") == "ready"
    )
    metrics = {
        "ready_parsers": ready_parsers,
        "parser_capabilities": len(parsers),
        "optional_degraded_or_unavailable": len(optional),
    }
    if ready_parsers == 0:
        return CertificationCheck(
            "runtime_capabilities",
            "FAIL",
            "No document parser capability is ready.",
            metrics,
        )
    if optional:
        return CertificationCheck(
            "runtime_capabilities",
            "CONDITIONAL",
            "Core parsing is ready; optional engines require deployment-specific installation: "
            + ", ".join(optional),
            metrics,
        )
    return CertificationCheck(
        "runtime_capabilities",
        "PASS",
        "All probed parser, OCR, renderer, and mutation capabilities are ready.",
        metrics,
    )


def _rollout_check() -> CertificationCheck:
    from .config import get_document_rollout

    rollout = get_document_rollout()
    status = "PASS" if rollout.mode in {"shadow", "compatibility"} else "CONDITIONAL"
    if rollout.default_authoritative:
        status = "CONDITIONAL"
    return CertificationCheck(
        "safe_rollout",
        status,
        (
            f"Live rollout mode is {rollout.mode}; disabling durable writes or "
            "default authority does not delete blobs, jobs, manifests, or metadata."
        ),
        {
            "enabled": rollout.enabled,
            "shadow": rollout.shadow,
            "default_authoritative": rollout.default_authoritative,
            "mode": rollout.mode,
        },
    )


def _configured_backend(kind: str) -> str:
    try:
        from .jobs_pg import document_jobs_backend, document_metadata_backend

        return document_metadata_backend() if kind == "metadata" else document_jobs_backend()
    except (ImportError, RuntimeError, OSError):
        return "unknown"


def _isolation_check() -> tuple[CertificationCheck, dict[str, object]]:
    """The hostile-document execution profile actually configured now."""
    from .worker_container import isolation_report

    report = dict(isolation_report())
    profile = str(report.get("mode") or "process")
    facts: dict[str, int | float | str | bool | None] = {
        "profile": profile,
        "ready": bool(report.get("ready")),
        "image_code_matches": report.get("image_code_matches"),
        "image_current": report.get("image_current"),
        "image_fallback": bool(report.get("image_fallback")),
        "hostile_document_profile": str(report.get("hostile_document_profile") or ""),
    }
    if profile == "container":
        # PASS needs the image KNOWN to run this code: a match not reported
        # (None), an older image or a fallback build passed until 2026-10-15
        # (Q04 defensive check C7), while AGENTS.md said "only attested and
        # with a current image".
        current = (
            report.get("image_code_matches") is True
            and report.get("image_current") is not False
            and not report.get("image_fallback")
        )
        if report.get("ready") and current:
            return CertificationCheck(
                "worker_isolation", "PASS",
                "Documents run in the attested per-job container profile (no network, no mounts, "
                "read-only root, cgroup limits).", facts,
            ), report
        return CertificationCheck(
            "worker_isolation", "FAIL",
            "The container profile is configured but not ready, or its image is not known to run "
            "the document code Kazma runs (not reported, older, or a fallback build).",
            facts,
        ), report
    if profile == "process":
        return CertificationCheck(
            "worker_isolation", "CONDITIONAL",
            "The process profile bounds memory/CPU but does not confine filesystem or network: "
            "it is not a qualified hostile-document profile.", facts,
        ), report
    return CertificationCheck(
        "worker_isolation", "FAIL", f"Unknown isolation profile {profile!r} refuses every run.", facts,
    ), report


def _fidelity_check(root: Path) -> CertificationCheck:
    """The versioned fidelity corpus, measured against the release targets."""
    from . import fidelity

    report = fidelity.run_corpus(root / "fidelity")
    errors = sorted(case for case, entry in report["cases"].items() if "error" in entry)
    below = sorted(
        case for case, entry in report["cases"].items()
        if "error" not in entry and fidelity.meets(entry["metrics"], fidelity.RELEASE_TARGETS)
    )
    metrics: dict[str, int | float | str | bool | None] = {
        "corpus_version": fidelity.CORPUS_VERSION,
        "cases": len(report["cases"]),
        "qualified": len(report["cases"]) - len(below) - len(errors),
        "below_release": ",".join(below) or None,
        "not_measured": ",".join(errors) or None,
    }
    if errors:
        return CertificationCheck(
            "extraction_fidelity", "CONDITIONAL",
            "Some corpus cases could not be measured here (an engine is missing).", metrics,
        )
    if below:
        return CertificationCheck(
            "extraction_fidelity", "CONDITIONAL",
            "Cases below the release targets are not qualified: " + ", ".join(below) + ".", metrics,
        )
    return CertificationCheck(
        "extraction_fidelity", "PASS", "Every corpus case meets the release targets.", metrics,
    )


def _backup_restore_check(root: Path) -> CertificationCheck:
    """A real SQLite backup, verification and restore of one ready document."""
    import asyncio as _asyncio
    import io

    from .backup import perform_document_backup, verify_document_backup
    from .ingestion import SYSTEM_ACTOR, DocumentIngestionService

    started = time.monotonic()
    config = replace(
        get_document_config(), storage_root=root / "backup-source",
        security_malware_scan="off", security_local_salvage=False,
    )
    source = DocumentIngestionService(config=config)
    try:
        accepted = source.ingest_stream(
            io.BytesIO(b"Certification backup drill.\n"), filename="drill.txt",
            tenant_id="certification", workspace_id="w", actor_id="certifier",
        )

        async def _drain() -> None:
            from .worker import DocumentWorker

            worker = DocumentWorker(source.jobs, source.stage_handlers())
            for _ in range(10):
                claimed = source.jobs.claim_next(owner="certifier", lease_seconds=60)
                if claimed is None:
                    return
                await worker._process_claimed(claimed, "certifier")

        _asyncio.run(_drain())
        report = perform_document_backup(storage_root=config.storage_root, dest_dir=root / "backups")
    finally:
        source.close()
    if not report.get("ok"):
        return CertificationCheck("backup_restore_sqlite", "FAIL", "The native backup did not verify.",
                                  {"error": str(report.get("error") or "")[:200]})
    restored_root = root / "backup-restored"
    shutil.copytree(report["path"], restored_root)
    restored = DocumentIngestionService(config=replace(config, storage_root=restored_root))
    try:
        state = (restored.job_status(tenant_id="certification", job_id=accepted.job_id,
                                     actor_id=SYSTEM_ACTOR) or {}).get("state")
        text = restored.get_content(tenant_id="certification", document_id=accepted.document_id,
                                    actor_id="certifier", fence=False)["text"]
    finally:
        restored.close()
    seconds = round(time.monotonic() - started, 3)
    ok = state == "ready" and "Certification backup drill." in text and verify_document_backup(
        backup_dir=report["path"])["ok"]
    return CertificationCheck(
        "backup_restore_sqlite", "PASS" if ok else "FAIL",
        "One ready document backed up, verified, restored and read back (SQLite store). "
        "A Postgres store is qualified by its own drill (tests/test_document_pg_backup_restore.py).",
        {"restored_state": state, "seconds": seconds},
    )


def run_document_certification(
    work_root: str | Path,
    *,
    soak: bool = False,
    soak_iterations: int = 100,
) -> dict[str, object]:
    """Run bounded certification and return a stable JSON-serializable report."""

    root = Path(work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    checks: list[CertificationCheck] = []
    try:
        checks.append(certify_hostile_corpus(root))
        iterations = soak_iterations if soak else 3
        checks.append(
            asyncio.run(
                run_performance_smoke(
                    root / ("soak" if soak else "smoke"),
                    iterations=iterations,
                    concurrency=2,
                    duration_limit_seconds=900.0 if soak else 30.0,
                    event_loop_stall_limit_seconds=1.0 if soak else 0.75,
                    peak_memory_limit_bytes=(256 if soak else 128) * 1024 * 1024,
                )
            )
        )
        checks.append(_capability_check(root))
        checks.append(_rollout_check())
        isolation_check, isolation = _isolation_check()
        checks.append(isolation_check)
        checks.append(_fidelity_check(root))
        checks.append(_backup_restore_check(root))
        checks.append(
            CertificationCheck(
                "postgres_multi_replica",
                "NOT RUN",
                (
                    "Live Postgres claim/partition testing is deployment-only and "
                    "was not run by the bounded local certifier."
                ),
            )
        )
        checks.append(
            CertificationCheck(
                "metadata_multi_replica",
                "NOT RUN",
                "This transient-parser run did not instantiate or qualify a durable metadata backend.",
                {"metadata_backend": "unmeasured", "metadata_multi_replica": None},
            )
        )
        checks.append(
            CertificationCheck(
                "multi_day_soak",
                "NOT RUN",
                "Repeated parsing is a bounded smoke. No 48–72 hour mixed-load, recovery, or fault soak was performed.",
            )
        )
        checks.append(
            CertificationCheck(
                "external_security_review",
                "NOT RUN",
                "No independent external security review is represented by this local report.",
            )
        )
    finally:
        shutil.rmtree(root / "hostile-corpus", ignore_errors=True)

    has_failure = any(check.status == "FAIL" for check in checks)
    has_condition = any(
        check.status in {"CONDITIONAL", "NOT RUN"} for check in checks
    )
    overall = "FAIL" if has_failure else ("CONDITIONAL" if has_condition else "PASS")
    required = {
        "hostile_corpus",
        "bounded_performance_event_loop_resources",
        "safe_rollout",
    }
    canary_ready = not has_failure and all(
        check.status in {"PASS", "CONDITIONAL"}
        for check in checks
        if check.name in required
    )
    manifest = hostile_corpus_manifest()
    manifest_sha = __import__("hashlib").sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "extended-smoke" if soak else "ci-smoke",
        "execution_profile": {
            "os": platform.system(), "os_release": platform.release(),
            "machine": platform.machine(), "python": platform.python_version(),
            "python_executable": sys.executable,
            "scope": "local_transient_parser",
            # What is configured NOW, not what a deployment might use.
            "worker_isolation": isolation.get("mode"),
            "worker_image_id": (isolation.get("broker") or {}).get("image_id"),
            "metadata_backend_configured": _configured_backend("metadata"),
            "jobs_backend_configured": _configured_backend("jobs"),
            "filesystem_network_confinement_qualified": bool(
                isolation_check.status == "PASS"
            ),
        },
        "overall_status": overall,
        "canary_ready": canary_ready,
        "default_ready": False,
        "default_ready_reason": (
            "Optional engines, live Postgres, a multi-day soak, external review, "
            "and multi-replica metadata certification are not all complete."
        ),
        "hostile_manifest_sha256": manifest_sha,
        "checks": [check.to_dict() for check in checks],
    }
