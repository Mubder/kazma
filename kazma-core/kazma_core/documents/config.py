"""Live, validated configuration for document intelligence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentConfig",
    "DocumentRollout",
    "get_document_config",
    "get_document_rollout",
]

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class DocumentConfig:
    """Safe runtime defaults and limits for the document subsystem."""

    storage_root: Path
    enabled: bool = True
    shadow: bool = False
    default_authoritative: bool = False
    intake_max_bytes: int = 50 * _MIB
    intake_max_files: int = 10
    remote_fetch_max_redirects: int = 3
    remote_fetch_timeout_seconds: int = 30
    max_pages: int = 500
    max_sheets: int = 100
    max_slides: int = 500
    max_rows_per_sheet: int = 100_000
    max_cells: int = 2_000_000
    max_images: int = 5_000
    max_pixels_per_image: int = 100_000_000
    max_expanded_bytes: int = 256 * _MIB
    max_compression_ratio: int = 100
    max_archive_members: int = 10_000
    max_output_chars_per_page: int = 100_000
    max_output_chars_total: int = 2_000_000
    worker_result_max_bytes: int = 32 * _MIB
    worker_concurrency: int = 2
    worker_lease_seconds: int = 120
    worker_heartbeat_seconds: int = 15
    worker_timeout_seconds: int = 300
    worker_memory_mb: int = 1_024
    worker_max_retries: int = 3
    worker_retry_base_seconds: int = 5
    worker_retry_max_seconds: int = 300
    security_malware_scan: str = "auto"
    security_malware_fail_closed: bool = False
    security_allow_encrypted_documents: bool = False
    security_allow_external_render_resources: bool = False
    security_fence_llm_content: bool = True
    ocr_enabled: bool = True
    ocr_languages: tuple[str, ...] = ("eng", "ara")
    ocr_dpi: int = 200
    ocr_min_text_chars_per_page: int = 40
    ocr_min_confidence: float = 0.75
    ocr_page_concurrency: int = 2
    ocr_subprocess_timeout_seconds: int = 60
    ocr_output_limit_bytes: int = 8 * _MIB
    indexing_enabled: bool = True
    indexing_chunk_tokens: int = 800
    indexing_overlap_tokens: int = 120
    indexing_preserve_tables: bool = True
    indexing_preserve_page_boundaries: bool = True
    retention_original_days: int = 30
    retention_artifact_days: int = 30
    retention_failed_days: int = 7
    retention_audit_days: int = 365
    quota_tenant_bytes: int = 10 * _GIB
    quota_tenant_daily_pages: int = 10_000
    # ── Phase 9: retention / garbage collection ─────────────────────────
    retention_rejected_days: int = 7
    retention_dead_letter_days: int = 14
    retention_tombstone_days: int = 30
    retention_quarantine_days: int = 3
    gc_grace_seconds: int = 3_600
    gc_max_deletions_per_run: int = 500
    gc_enabled: bool = True
    gc_auto_maintain: bool = True
    gc_interval_hours: int = 6
    # ── Phase 9: backpressure / capacity / rate limits ──────────────────
    capacity_max_queued_jobs: int = 5_000
    capacity_max_tenant_queued_jobs: int = 500
    capacity_max_tenant_active_jobs: int = 50
    capacity_intake_rate_per_minute: int = 120
    capacity_intake_bytes_per_minute: int = 512 * _MIB
    capacity_storage_free_floor_bytes: int = 512 * _MIB


@dataclass(frozen=True, slots=True)
class DocumentRollout:
    """Live, low-cost routing controls for canary and rollback."""

    enabled: bool = True
    shadow: bool = False
    default_authoritative: bool = False

    @property
    def mode(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.default_authoritative:
            return "authoritative"
        if self.shadow:
            return "shadow"
        return "compatibility"

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "enabled": self.enabled,
            "shadow": self.shadow,
            "default_authoritative": self.default_authoritative,
            "mode": self.mode,
            "accepting_durable_writes": self.enabled,
            "rollback_preserves_data": True,
        }


def _default_storage_root() -> Path:
    try:
        from kazma_core.paths import data_dir

        return cast(Path, data_dir() / "document-store")
    except Exception as exc:
        logger.warning(
            "[documents.config] Failed to resolve Kazma data directory; using local fallback: %s",
            exc,
        )
        return (Path.cwd() / "kazma-data" / "document-store").resolve()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("expected a boolean")


def _ocr_enabled(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"auto", "enabled"}:
            return True
        if normalized == "disabled":
            return False
    return _coerce_bool(value)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("booleans are not integers")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("expected a whole number")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("expected a positive integer")
    return parsed


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("booleans are not integers")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("expected a whole number")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("expected a non-negative integer")
    return parsed


def _path(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("expected a non-empty path")
    return Path(str(value)).expanduser().resolve()


def _languages(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = value.replace(",", "+").split("+")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError("expected a language string or list")
    normalized = tuple(str(item).strip().lower() for item in items if str(item).strip())
    if not normalized or any(not item.replace("-", "").isalnum() for item in normalized):
        raise ValueError("invalid OCR language list")
    return normalized


def _unit_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("booleans are not confidence values")
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise ValueError("expected a value between 0 and 1")
    return parsed


def _ocr_dpi(value: Any) -> int:
    parsed = _positive_int(value)
    if not 72 <= parsed <= 600:
        raise ValueError("OCR DPI must be between 72 and 600")
    return parsed


def _malware_mode(value: Any) -> str:
    normalized = str(value).strip().lower()
    # Canonicalize to the vocabulary the enforcer (documents/malware.py) and the
    # Settings UI both use: {off, on, auto}. Accept the enabled/disabled aliases
    # for back-compat. Previously this validator accepted ONLY {auto, enabled,
    # disabled} and therefore *rejected* the UI's valid "on"/"off" values, while
    # any "enabled" that did get stored silently degraded to fail-open "auto" in
    # the enforcer (malware.py only knows {off, on, auto}). Unifying here makes
    # "on" reach the strict fail-closed branch and "off" disable scanning.
    alias = {"enabled": "on", "disabled": "off"}
    normalized = alias.get(normalized, normalized)
    if normalized not in {"auto", "on", "off"}:
        raise ValueError("expected auto, on, or off (enabled/disabled aliases accepted)")
    return normalized


def _read(
    store: Any,
    key: str,
    default: _T,
    coerce: Callable[[Any], _T],
    *,
    aliases: tuple[str, ...] = (),
) -> _T:
    raw: Any = None
    selected = key
    for candidate in (key, *aliases):
        raw = store.get(candidate)
        if raw is not None:
            selected = candidate
            break
    if raw is None:
        return default
    try:
        return coerce(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "[documents.config] Invalid ConfigStore value for %s=%r; using %r: %s",
            selected,
            raw,
            default,
            exc,
        )
        return default


def _normalize_rollout(
    enabled: bool,
    shadow: bool,
    default_authoritative: bool,
) -> DocumentRollout:
    if not enabled:
        return DocumentRollout(enabled=False)
    if shadow and default_authoritative:
        logger.warning(
            "[documents.config] documents.shadow and "
            "documents.default_authoritative cannot both be active; "
            "authoritative routing wins"
        )
        shadow = False
    return DocumentRollout(
        enabled=True,
        shadow=shadow,
        default_authoritative=default_authoritative,
    )


def get_document_rollout() -> DocumentRollout:
    """Re-read only the rollout flags from ConfigStore on every decision."""

    defaults = DocumentRollout()
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
    except Exception as exc:
        logger.warning(
            "[documents.config] ConfigStore unavailable for rollout; "
            "using compatibility defaults: %s",
            exc,
        )
        return defaults
    enabled = _read(store, "documents.enabled", defaults.enabled, _coerce_bool)
    shadow = _read(
        store,
        "documents.shadow",
        defaults.shadow,
        _coerce_bool,
        aliases=("documents.shadow_mode",),
    )
    authoritative = _read(
        store,
        "documents.default_authoritative",
        defaults.default_authoritative,
        _coerce_bool,
        aliases=("documents.authoritative_by_default",),
    )
    return _normalize_rollout(enabled, shadow, authoritative)


def get_document_config() -> DocumentConfig:
    """Re-read the ConfigStore and return a validated immutable snapshot."""
    defaults = DocumentConfig(storage_root=_default_storage_root())
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
    except Exception as exc:
        logger.warning(
            "[documents.config] ConfigStore unavailable; using safe defaults: %s",
            exc,
        )
        return defaults

    try:
        config = DocumentConfig(
            storage_root=_read(store, "documents.storage_root", defaults.storage_root, _path),
            enabled=_read(
                store,
                "documents.enabled",
                defaults.enabled,
                _coerce_bool,
            ),
            shadow=_read(
                store,
                "documents.shadow",
                defaults.shadow,
                _coerce_bool,
                aliases=("documents.shadow_mode",),
            ),
            default_authoritative=_read(
                store,
                "documents.default_authoritative",
                defaults.default_authoritative,
                _coerce_bool,
                aliases=("documents.authoritative_by_default",),
            ),
            intake_max_bytes=_read(
                store,
                "documents.intake.max_bytes",
                defaults.intake_max_bytes,
                _positive_int,
                aliases=("documents.intake_max_bytes",),
            ),
            intake_max_files=_read(
                store,
                "documents.intake.max_files",
                defaults.intake_max_files,
                _positive_int,
                aliases=("documents.intake_max_files",),
            ),
            remote_fetch_max_redirects=_read(
                store,
                "documents.intake.remote_fetch_max_redirects",
                defaults.remote_fetch_max_redirects,
                _nonnegative_int,
            ),
            remote_fetch_timeout_seconds=_read(
                store,
                "documents.intake.remote_fetch_timeout_seconds",
                defaults.remote_fetch_timeout_seconds,
                _positive_int,
            ),
            max_pages=_read(store, "documents.limits.max_pages", defaults.max_pages, _positive_int),
            max_sheets=_read(
                store, "documents.limits.max_sheets", defaults.max_sheets, _positive_int
            ),
            max_slides=_read(
                store, "documents.limits.max_slides", defaults.max_slides, _positive_int
            ),
            max_rows_per_sheet=_read(
                store,
                "documents.limits.max_rows_per_sheet",
                defaults.max_rows_per_sheet,
                _positive_int,
            ),
            max_cells=_read(
                store, "documents.limits.max_cells", defaults.max_cells, _positive_int
            ),
            max_images=_read(
                store, "documents.limits.max_images", defaults.max_images, _positive_int
            ),
            max_pixels_per_image=_read(
                store,
                "documents.limits.max_pixels_per_image",
                defaults.max_pixels_per_image,
                _positive_int,
            ),
            max_expanded_bytes=_read(
                store,
                "documents.limits.max_expanded_bytes",
                defaults.max_expanded_bytes,
                _positive_int,
                aliases=("documents.limits.max_uncompressed_bytes",),
            ),
            max_compression_ratio=_read(
                store,
                "documents.limits.max_compression_ratio",
                defaults.max_compression_ratio,
                _positive_int,
            ),
            max_archive_members=_read(
                store,
                "documents.limits.max_archive_members",
                defaults.max_archive_members,
                _positive_int,
            ),
            max_output_chars_per_page=_read(
                store,
                "documents.limits.max_output_chars_per_page",
                defaults.max_output_chars_per_page,
                _positive_int,
            ),
            max_output_chars_total=_read(
                store,
                "documents.limits.max_output_chars_total",
                defaults.max_output_chars_total,
                _positive_int,
            ),
            worker_result_max_bytes=_read(
                store,
                "documents.workers.result_max_bytes",
                defaults.worker_result_max_bytes,
                _positive_int,
            ),
            worker_concurrency=_read(
                store,
                "documents.workers.concurrency",
                defaults.worker_concurrency,
                _positive_int,
            ),
            worker_lease_seconds=_read(
                store,
                "documents.workers.lease_seconds",
                defaults.worker_lease_seconds,
                _positive_int,
                aliases=("documents.worker.lease_seconds",),
            ),
            worker_heartbeat_seconds=_read(
                store,
                "documents.workers.heartbeat_seconds",
                defaults.worker_heartbeat_seconds,
                _positive_int,
            ),
            worker_timeout_seconds=_read(
                store,
                "documents.workers.timeout_seconds",
                defaults.worker_timeout_seconds,
                _positive_int,
            ),
            worker_memory_mb=_read(
                store,
                "documents.workers.memory_mb",
                defaults.worker_memory_mb,
                _positive_int,
            ),
            worker_max_retries=_read(
                store,
                "documents.workers.max_attempts",
                defaults.worker_max_retries,
                _nonnegative_int,
                aliases=("documents.worker.max_retries",),
            ),
            worker_retry_base_seconds=_read(
                store,
                "documents.workers.retry_base_seconds",
                defaults.worker_retry_base_seconds,
                _positive_int,
            ),
            worker_retry_max_seconds=_read(
                store,
                "documents.workers.retry_max_seconds",
                defaults.worker_retry_max_seconds,
                _positive_int,
            ),
            security_malware_scan=_read(
                store,
                "documents.security.malware_scan",
                defaults.security_malware_scan,
                _malware_mode,
            ),
            security_malware_fail_closed=_read(
                store,
                "documents.security.malware_fail_closed",
                defaults.security_malware_fail_closed,
                _coerce_bool,
            ),
            security_allow_encrypted_documents=_read(
                store,
                "documents.security.allow_encrypted_documents",
                defaults.security_allow_encrypted_documents,
                _coerce_bool,
            ),
            security_allow_external_render_resources=_read(
                store,
                "documents.security.allow_external_render_resources",
                defaults.security_allow_external_render_resources,
                _coerce_bool,
            ),
            security_fence_llm_content=_read(
                store,
                "documents.security.fence_llm_content",
                defaults.security_fence_llm_content,
                _coerce_bool,
            ),
            ocr_enabled=_read(store, "documents.ocr.enabled", defaults.ocr_enabled, _ocr_enabled),
            ocr_languages=_read(
                store, "documents.ocr.languages", defaults.ocr_languages, _languages
            ),
            ocr_dpi=_read(store, "documents.ocr.dpi", defaults.ocr_dpi, _ocr_dpi),
            ocr_min_text_chars_per_page=_read(
                store,
                "documents.ocr.min_text_chars_per_page",
                defaults.ocr_min_text_chars_per_page,
                _nonnegative_int,
            ),
            ocr_min_confidence=_read(
                store,
                "documents.ocr.min_confidence",
                defaults.ocr_min_confidence,
                _unit_float,
            ),
            ocr_page_concurrency=_read(
                store,
                "documents.ocr.page_concurrency",
                defaults.ocr_page_concurrency,
                _positive_int,
            ),
            ocr_subprocess_timeout_seconds=_read(
                store,
                "documents.ocr.subprocess_timeout_seconds",
                defaults.ocr_subprocess_timeout_seconds,
                _positive_int,
            ),
            ocr_output_limit_bytes=_read(
                store,
                "documents.ocr.output_limit_bytes",
                defaults.ocr_output_limit_bytes,
                _positive_int,
            ),
            indexing_enabled=_read(
                store,
                "documents.indexing.enabled",
                defaults.indexing_enabled,
                _coerce_bool,
            ),
            indexing_chunk_tokens=_read(
                store,
                "documents.indexing.chunk_tokens",
                defaults.indexing_chunk_tokens,
                _positive_int,
            ),
            indexing_overlap_tokens=_read(
                store,
                "documents.indexing.overlap_tokens",
                defaults.indexing_overlap_tokens,
                _nonnegative_int,
            ),
            indexing_preserve_tables=_read(
                store,
                "documents.indexing.preserve_tables",
                defaults.indexing_preserve_tables,
                _coerce_bool,
            ),
            indexing_preserve_page_boundaries=_read(
                store,
                "documents.indexing.preserve_page_boundaries",
                defaults.indexing_preserve_page_boundaries,
                _coerce_bool,
            ),
            retention_original_days=_read(
                store,
                "documents.retention.original_days",
                defaults.retention_original_days,
                _nonnegative_int,
            ),
            retention_artifact_days=_read(
                store,
                "documents.retention.artifact_days",
                defaults.retention_artifact_days,
                _nonnegative_int,
            ),
            retention_failed_days=_read(
                store,
                "documents.retention.failed_days",
                defaults.retention_failed_days,
                _nonnegative_int,
            ),
            retention_audit_days=_read(
                store,
                "documents.retention.audit_days",
                defaults.retention_audit_days,
                _nonnegative_int,
            ),
            quota_tenant_bytes=_read(
                store,
                "documents.quotas.tenant_bytes",
                defaults.quota_tenant_bytes,
                _positive_int,
            ),
            quota_tenant_daily_pages=_read(
                store,
                "documents.quotas.tenant_daily_pages",
                defaults.quota_tenant_daily_pages,
                _positive_int,
            ),
            retention_rejected_days=_read(
                store,
                "documents.retention.rejected_days",
                defaults.retention_rejected_days,
                _nonnegative_int,
            ),
            retention_dead_letter_days=_read(
                store,
                "documents.retention.dead_letter_days",
                defaults.retention_dead_letter_days,
                _nonnegative_int,
            ),
            retention_tombstone_days=_read(
                store,
                "documents.retention.tombstone_days",
                defaults.retention_tombstone_days,
                _nonnegative_int,
            ),
            retention_quarantine_days=_read(
                store,
                "documents.retention.quarantine_days",
                defaults.retention_quarantine_days,
                _nonnegative_int,
            ),
            gc_grace_seconds=_read(
                store,
                "documents.gc.grace_seconds",
                defaults.gc_grace_seconds,
                _nonnegative_int,
            ),
            gc_max_deletions_per_run=_read(
                store,
                "documents.gc.max_deletions_per_run",
                defaults.gc_max_deletions_per_run,
                _positive_int,
            ),
            gc_enabled=_read(
                store,
                "documents.gc.enabled",
                defaults.gc_enabled,
                _coerce_bool,
            ),
            gc_auto_maintain=_read(
                store,
                "documents.gc.auto_maintain",
                defaults.gc_auto_maintain,
                _coerce_bool,
            ),
            gc_interval_hours=_read(
                store,
                "documents.gc.interval_hours",
                defaults.gc_interval_hours,
                _positive_int,
            ),
            capacity_max_queued_jobs=_read(
                store,
                "documents.capacity.max_queued_jobs",
                defaults.capacity_max_queued_jobs,
                _positive_int,
            ),
            capacity_max_tenant_queued_jobs=_read(
                store,
                "documents.capacity.max_tenant_queued_jobs",
                defaults.capacity_max_tenant_queued_jobs,
                _positive_int,
            ),
            capacity_max_tenant_active_jobs=_read(
                store,
                "documents.capacity.max_tenant_active_jobs",
                defaults.capacity_max_tenant_active_jobs,
                _positive_int,
            ),
            capacity_intake_rate_per_minute=_read(
                store,
                "documents.capacity.intake_rate_per_minute",
                defaults.capacity_intake_rate_per_minute,
                _positive_int,
            ),
            capacity_intake_bytes_per_minute=_read(
                store,
                "documents.capacity.intake_bytes_per_minute",
                defaults.capacity_intake_bytes_per_minute,
                _positive_int,
            ),
            capacity_storage_free_floor_bytes=_read(
                store,
                "documents.capacity.storage_free_floor_bytes",
                defaults.capacity_storage_free_floor_bytes,
                _nonnegative_int,
            ),
        )
        if config.indexing_overlap_tokens >= config.indexing_chunk_tokens:
            logger.warning(
                "[documents.config] indexing overlap must be smaller than chunk size; "
                "using default overlap %s",
                defaults.indexing_overlap_tokens,
            )
            config = replace(
                config,
                indexing_overlap_tokens=defaults.indexing_overlap_tokens,
            )
        rollout = _normalize_rollout(
            config.enabled,
            config.shadow,
            config.default_authoritative,
        )
        config = replace(
            config,
            enabled=rollout.enabled,
            shadow=rollout.shadow,
            default_authoritative=rollout.default_authoritative,
        )
        return config
    except Exception as exc:
        logger.warning(
            "[documents.config] Failed to read document settings; using safe defaults: %s",
            exc,
            exc_info=True,
        )
        return defaults
