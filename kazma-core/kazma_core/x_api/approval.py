"""Bind a held reply to its reviewed account, policy and generation pipeline."""

from __future__ import annotations

import sqlite3
import time
from typing import Any


def capture_basis(reply_config: Any) -> dict[str, Any]:
    """Capture before generation; inability to bind leaves a review-only draft."""
    from kazma_core.x_api.account_binding import credential_revision
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.model_selection import _read_selection, current_x_selection, validate_selection
    from kazma_core.x_api.qualification import pipeline_fingerprint

    cfg = get_x_config()
    if not cfg.account_id:
        return {}
    selected = current_x_selection()
    if selected is not None and selected != validate_selection(_read_selection()):
        return {}
    return {"version": 1, "account_id": cfg.account_id,
            "credential_revision": credential_revision(cfg.credentials),
            "pipeline": pipeline_fingerprint(reply_config), "expires_at": time.time() + 86400}


def binding_hold(basis: Any) -> str:
    """Read current state; never infer a legacy draft's missing approval basis."""
    from kazma_core.x_api.account_binding import credential_revision
    from kazma_core.x_api.config import get_x_config
    from kazma_core.x_api.qualification import pipeline_fingerprint
    from kazma_core.x_api.stance import get_reply_config

    if (not isinstance(basis, dict) or basis.get("version") != 1
            or type(basis.get("expires_at")) not in (int, float)
            or not time.time() < basis["expires_at"] <= time.time() + 86401):
        return "Draft approval binding is missing or expired. Retry and review a fresh draft."
    cfg = get_x_config()
    if (not cfg.account_id or basis.get("account_id") != cfg.account_id
            or basis.get("credential_revision") != credential_revision(cfg.credentials)):
        return "X account or credentials changed. Verify the account, then retry and review this draft."
    try:
        current = pipeline_fingerprint(get_reply_config())
    except (OSError, sqlite3.Error, RuntimeError):
        return "The current X policy and model binding could not be checked. Draft remains held."
    if basis.get("pipeline") != current:
        return "X subject policy, models or pipeline changed. Retry and review a fresh draft."
    return ""


def evidence_binding_hold(decision: dict[str, Any]) -> str:
    """A reviewed factual citation must still be fresh, authorized and unchanged."""
    import hashlib

    from kazma_core.stores.knowledge import get_knowledge_store
    from kazma_core.x_api.evidence import source_hold
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.stance import get_reply_config

    checks = decision.get("checks", [])
    claimed = {ident for check in checks if check.get("check") == "evidence"
               for claim in check.get("claims", []) if claim.get("kind") == "fact"
               for ident in claim.get("source_ids", [])}
    if not claimed:
        return ""
    sources = {source.get("source_id"): source for source in decision.get("evidence", {}).get("sources", [])}
    if claimed - sources.keys():
        return "A reviewed factual citation is missing. Retry and review a fresh draft."
    subject = get_reply_config().subject_by_id(str(decision.get("subject_id") or ""))
    age = subject.evidence_max_age_days if subject else 30
    store, tenant = get_knowledge_store(), x_tenant_id()
    current = store.get_chunks_by_ids(list(claimed))
    for ident in claimed:
        source = sources[ident]
        hold = source_hold(source, max_age_days=age)
        if hold:
            return hold + " Retry and review a fresh draft."
        library = store.get_library_for_tenant(source["library_id"], tenant)
        row = current.get(ident)
        if (not library or library.get("archived") or row is None
                or any(row.get(key) != source[key] for key in ("library_id", "document_id", "version_id"))
                or hashlib.sha256(str(row.get("content") or "").encode()).hexdigest() != source["content_hash"]):
            return "A reviewed source changed or is no longer authorized/active. Retry and review a fresh draft."
    return ""
