"""Document-platform observability: metrics, spans, and correlation logging.

Phase 9 observability for the document intelligence platform. Three concerns,
all **safe to no-op** and carrying **no document content**:

1. **Process metrics** — reuses the :mod:`kazma_core.metrics` convention: a
   single optional ``prometheus_client`` import, module-level metric objects
   created only when the library is present, and ``record_*`` / ``set_*``
   helpers that are cheap no-ops otherwise. Label cardinality is bounded:
   ``stage`` / ``parser`` / ``reason`` / ``outcome`` / ``operation`` are
   drawn from small closed sets, sanitized on the way in. **Per-tenant quota
   is deliberately NOT a Prometheus label** (unbounded tenant cardinality);
   it is exposed through the query API (:func:`tenant_quota_snapshot`) and as
   aggregate top-level gauges only.

2. **Spans** — :func:`document_span` is an optional OpenTelemetry span context
   manager. If ``opentelemetry`` is not installed (the default), it yields a
   no-op handle. It never raises and never blocks work.

3. **Correlation logging** — :func:`correlation_extra` builds the structured
   ``extra=`` dict for ``logging`` calls with the canonical document
   correlation fields (tenant/workspace/document/version/job/attempt/parser/
   stage/outcome). It **never** carries content, filenames, redaction terms,
   or secrets; callers pass only IDs and safe codes.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "correlation_extra",
    "document_span",
    "record_dead_letter",
    "record_generation_failure",
    "record_indexing",
    "record_intake",
    "record_intake_rejection",
    "record_pages",
    "record_parser",
    "record_redaction_failure",
    "record_sandbox_termination",
    "record_stage",
    "set_queue_gauges",
    "set_storage_gauges",
    "tenant_quota_snapshot",
]

# ── Optional prometheus_client (graceful degradation, mirrors metrics.py) ──
try:
    from prometheus_client import Counter, Gauge, Histogram

    _PROM = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _PROM = False
    logger.debug("[documents.telemetry] prometheus_client not installed — metrics disabled")

# Correlation fields that may appear in structured log extras. Deliberately
# excludes anything content-bearing (text, filename, terms, secrets).
_CORRELATION_KEYS = (
    "tenant_id",
    "workspace_id",
    "document_id",
    "version_id",
    "job_id",
    "attempt",
    "parser",
    "stage",
    "outcome",
)

# Bounded label sanitizer — prevents cardinality blow-ups from unexpected
# values and strips anything that is not a safe token.
_LABEL_RE = re.compile(r"[^a-z0-9_.-]+")
_MAX_LABEL = 48


def _safe_label(value: Any, *, default: str = "unknown") -> str:
    text = _LABEL_RE.sub("_", str(value).strip().lower())[:_MAX_LABEL].strip("_")
    return text or default


if _PROM:
    INTAKE_FILES = Counter(
        "kazma_documents_intake_files_total",
        "Documents accepted or rejected at intake",
        ["outcome"],  # accepted|rejected
    )
    INTAKE_BYTES = Counter(
        "kazma_documents_intake_bytes_total",
        "Bytes accepted at intake (streamed, post-limit)",
    )
    INTAKE_REJECTIONS = Counter(
        "kazma_documents_intake_rejections_total",
        "Intake rejections by safe reason code",
        ["reason"],
    )
    STAGE_TOTAL = Counter(
        "kazma_documents_stage_total",
        "Per-stage processing outcomes",
        ["stage", "outcome"],  # outcome = success|failure|cancelled
    )
    STAGE_LATENCY = Histogram(
        "kazma_documents_stage_latency_seconds",
        "Per-stage processing latency",
        ["stage"],
    )
    PARSER_TOTAL = Counter(
        "kazma_documents_parser_total",
        "Parser invocations by outcome",
        ["parser", "outcome"],
    )
    PAGES_TOTAL = Counter(
        "kazma_documents_pages_total",
        "Pages processed by kind",
        ["kind"],  # parsed|ocr
    )
    SANDBOX_TERMINATIONS = Counter(
        "kazma_documents_sandbox_terminations_total",
        "Sandbox containment terminations by reason",
        ["reason"],  # timeout|oom|output|degraded
    )
    INDEX_LATENCY = Histogram(
        "kazma_documents_index_latency_seconds",
        "Indexing latency per document",
    )
    INDEX_CHUNKS = Counter(
        "kazma_documents_index_chunks_total",
        "Chunks emitted by the indexer",
    )
    GENERATION_FAILURES = Counter(
        "kazma_documents_generation_failures_total",
        "Generation/conversion verification failures by operation",
        ["operation"],
    )
    REDACTION_FAILURES = Counter(
        "kazma_documents_redaction_failures_total",
        "Redaction verification failures",
    )
    DEAD_LETTER_TOTAL = Counter(
        "kazma_documents_dead_letter_total",
        "Jobs that reached dead-letter",
    )
    QUEUE_DEPTH = Gauge(
        "kazma_documents_queue_depth",
        "Pending + retry-waiting document jobs",
    )
    QUEUE_OLDEST_AGE = Gauge(
        "kazma_documents_queue_oldest_age_seconds",
        "Age of the oldest claimable document job",
    )
    ACTIVE_LEASES = Gauge(
        "kazma_documents_active_leases",
        "Currently leased (in-flight) document jobs",
    )
    RETRY_WAITING = Gauge(
        "kazma_documents_retry_waiting",
        "Jobs in retry_wait backoff",
    )
    DEAD_LETTER_CURRENT = Gauge(
        "kazma_documents_dead_letter_current",
        "Jobs currently in dead_letter",
    )
    STORAGE_LOGICAL = Gauge(
        "kazma_documents_storage_logical_bytes",
        "Logical bytes referenced across all versions/artifacts",
    )
    STORAGE_PHYSICAL = Gauge(
        "kazma_documents_storage_physical_bytes",
        "Physical (deduplicated) bytes stored on disk",
    )
    STORAGE_DEDUP = Gauge(
        "kazma_documents_storage_dedup_ratio",
        "logical/physical dedup ratio (1.0 = no dedup)",
    )
else:  # pragma: no cover - stubs for type checkers / no-prometheus installs
    INTAKE_FILES = INTAKE_BYTES = INTAKE_REJECTIONS = None
    STAGE_TOTAL = STAGE_LATENCY = PARSER_TOTAL = PAGES_TOTAL = None
    SANDBOX_TERMINATIONS = INDEX_LATENCY = INDEX_CHUNKS = None
    GENERATION_FAILURES = REDACTION_FAILURES = DEAD_LETTER_TOTAL = None
    QUEUE_DEPTH = QUEUE_OLDEST_AGE = ACTIVE_LEASES = RETRY_WAITING = None
    DEAD_LETTER_CURRENT = STORAGE_LOGICAL = STORAGE_PHYSICAL = STORAGE_DEDUP = None


# ── Counter/histogram helpers (no-op without prometheus) ────────────────


def record_intake(*, accepted: bool, byte_size: int = 0) -> None:
    """Record one intake attempt (accepted or rejected) and its byte size."""
    if not _PROM:
        return
    INTAKE_FILES.labels(outcome="accepted" if accepted else "rejected").inc()
    if accepted and byte_size > 0:
        INTAKE_BYTES.inc(int(byte_size))


def record_intake_rejection(reason: str) -> None:
    """Record an intake rejection by safe reason code."""
    if not _PROM:
        return
    INTAKE_REJECTIONS.labels(reason=_safe_label(reason, default="rejected")).inc()


def record_stage(stage: str, outcome: str, *, latency_seconds: float | None = None) -> None:
    """Record a stage outcome (success|failure|cancelled) and optional latency."""
    if not _PROM:
        return
    stage_l = _safe_label(stage, default="stage")
    STAGE_TOTAL.labels(stage=stage_l, outcome=_safe_label(outcome, default="unknown")).inc()
    if latency_seconds is not None and latency_seconds >= 0:
        STAGE_LATENCY.labels(stage=stage_l).observe(float(latency_seconds))


def record_parser(parser: str, outcome: str) -> None:
    """Record a parser invocation outcome."""
    if not _PROM:
        return
    PARSER_TOTAL.labels(
        parser=_safe_label(parser, default="parser"),
        outcome=_safe_label(outcome, default="unknown"),
    ).inc()


def record_pages(count: int, *, kind: str = "parsed") -> None:
    """Record pages processed (kind = parsed|ocr)."""
    if not _PROM or count <= 0:
        return
    PAGES_TOTAL.labels(kind=_safe_label(kind, default="parsed")).inc(int(count))


def record_sandbox_termination(reason: str) -> None:
    """Record a sandbox containment termination (timeout|oom|output|degraded)."""
    if not _PROM:
        return
    SANDBOX_TERMINATIONS.labels(reason=_safe_label(reason, default="degraded")).inc()


def record_indexing(*, chunks: int = 0, latency_seconds: float | None = None) -> None:
    """Record an indexing pass — chunk count and latency."""
    if not _PROM:
        return
    if chunks > 0:
        INDEX_CHUNKS.inc(int(chunks))
    if latency_seconds is not None and latency_seconds >= 0:
        INDEX_LATENCY.observe(float(latency_seconds))


def record_generation_failure(operation: str) -> None:
    """Record a generation/conversion verification failure."""
    if not _PROM:
        return
    GENERATION_FAILURES.labels(operation=_safe_label(operation, default="generate")).inc()


def record_redaction_failure() -> None:
    """Record a redaction verification failure."""
    if not _PROM:
        return
    REDACTION_FAILURES.inc()


def record_dead_letter() -> None:
    """Record a job reaching dead-letter."""
    if not _PROM:
        return
    DEAD_LETTER_TOTAL.inc()


def set_queue_gauges(
    *,
    depth: int,
    oldest_age_seconds: float,
    active_leases: int,
    retry_waiting: int,
    dead_letter: int,
) -> None:
    """Set the live queue gauges from a repository snapshot."""
    if not _PROM:
        return
    QUEUE_DEPTH.set(max(0, int(depth)))
    QUEUE_OLDEST_AGE.set(max(0.0, float(oldest_age_seconds)))
    ACTIVE_LEASES.set(max(0, int(active_leases)))
    RETRY_WAITING.set(max(0, int(retry_waiting)))
    DEAD_LETTER_CURRENT.set(max(0, int(dead_letter)))


def set_storage_gauges(*, logical_bytes: int, physical_bytes: int) -> None:
    """Set logical/physical storage gauges and compute the dedup ratio."""
    if not _PROM:
        return
    logical = max(0, int(logical_bytes))
    physical = max(0, int(physical_bytes))
    STORAGE_LOGICAL.set(logical)
    STORAGE_PHYSICAL.set(physical)
    STORAGE_DEDUP.set(round(logical / physical, 4) if physical > 0 else 1.0)


# ── Per-tenant quota query API (NOT a prometheus label) ──────────────────


def tenant_quota_snapshot(
    repository: Any,
    *,
    tenant_id: str,
    quota_bytes: int,
) -> dict[str, Any]:
    """Return one tenant's quota consumption for the query API.

    Deliberately NOT emitted as a per-tenant Prometheus label to avoid
    unbounded cardinality. Callers (capacity/audit APIs) surface this on
    demand for a single tenant.
    """
    used = 0
    try:
        used = int(repository.tenant_referenced_blob_bytes(tenant_id=tenant_id))
    except Exception:  # noqa: BLE001 - telemetry must never raise
        logger.debug("[documents.telemetry] tenant quota read failed", exc_info=True)
    quota = max(1, int(quota_bytes))
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "used_ratio": round(min(1.0, used / quota), 4),
        "remaining_bytes": max(0, quota - used),
    }


# ── Correlation logging ──────────────────────────────────────────────────


def correlation_extra(**fields: Any) -> dict[str, Any]:
    """Build the structured ``extra=`` dict for a document log call.

    Only the canonical correlation keys are kept; ``None`` values are
    dropped and every value is coerced to a short safe string. Callers must
    NOT pass content, filenames, redaction terms, or secrets — this helper
    is for IDs and safe codes only.
    """
    out: dict[str, Any] = {}
    for key in _CORRELATION_KEYS:
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            continue
        if key == "attempt":
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            out[key] = str(value)[:128]
    return out


# ── Optional OpenTelemetry span (no-op safe) ─────────────────────────────


class _NoopSpan:
    """A span handle that does nothing (OpenTelemetry not installed)."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401 - trivial
        return None

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        return None


@contextmanager
def document_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Optional OpenTelemetry span for a document stage/operation.

    Uses ``opentelemetry`` only if it is already installed; otherwise yields
    a no-op handle. Never raises, never blocks work. Attributes must be safe
    correlation values (IDs / codes) — never content.
    """
    try:
        from opentelemetry import trace  # type: ignore
    except Exception:  # noqa: BLE001 - the common, dependency-free path
        yield _NoopSpan()
        return

    tracer = trace.get_tracer("kazma.documents")
    safe_attrs = {
        f"kazma.document.{k}": (v if isinstance(v, (str, int, float, bool)) else str(v))
        for k, v in attributes.items()
        if v is not None
    }
    try:
        span_context = tracer.start_as_current_span(name, attributes=safe_attrs)
    except Exception:  # noqa: BLE001 - telemetry setup must never break processing
        logger.debug("[documents.telemetry] span %s failed", name, exc_info=True)
        yield _NoopSpan()
        return
    with span_context as span:
        yield span
