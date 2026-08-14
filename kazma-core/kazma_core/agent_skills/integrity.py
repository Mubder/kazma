"""Integrity verification ("signing") for Agent Skills.

Agent Skills (the agentskills.io ``SKILL.md`` format) are **text-only** — they
are prompt-injected, not executed — so "signing" here means **HMAC-SHA256
integrity verification** of the ``SKILL.md`` content:

* At install time (:func:`compute_skill_signature`) the SHA-256 checksum of the
  ``SKILL.md`` text and an HMAC-SHA256 of that checksum (keyed by
  :func:`get_kazma_secret`) are written into ``.kazma-install.json`` alongside
  the skill.
* At load/activation time (:func:`verify_skill`) the checksum + signature are
  recomputed and compared (constant-time). A **mismatch fails closed** (the
  skill body is refused — possible tampering). A skill with **no stored
  checksum** (unsigned) loads with a prominent warning (backward-compat), not a
  hard failure.

This mirrors the existing Hub-skill HMAC pattern (``hub/loader.py`` /
``hub/cli.py``) but applies it to the text format users actually install.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ALGO",
    "VerifyResult",
    "compute_checksum",
    "compute_signature",
    "compute_skill_signature",
    "read_install_meta",
    "verify_skill",
    "write_install_meta",
]

logger = logging.getLogger(__name__)

ALGO = "hmac-sha256"
_INSTALL_META = ".kazma-install.json"


@dataclass(slots=True)
class VerifyResult:
    """Outcome of an Agent-Skill integrity check."""

    ok: bool
    reason: str = ""
    signed: bool = False  # True if a checksum/signature was present


def compute_checksum(skill_md_text: str) -> str:
    """SHA-256 hex digest of the SKILL.md text."""
    return hashlib.sha256(skill_md_text.encode("utf-8")).hexdigest()


def compute_signature(checksum: str, secret: str) -> str:
    """HMAC-SHA256 hex digest of *checksum* keyed by *secret*.

    Matches the Hub-skill verifier formula: HMAC is over the hex-encoded
    checksum string, not the raw file bytes.
    """
    return _hmac.new(secret.encode("utf-8"), checksum.encode("utf-8"), hashlib.sha256).hexdigest()


def _get_secret() -> str:
    """Resolve the signing secret via the unified getter (never raw env)."""
    try:
        from kazma_core.config_store import get_kazma_secret

        return (get_kazma_secret() or "").strip()
    except Exception:
        return ""


def compute_skill_signature(skill_md_text: str, *, secret: str | None = None) -> dict[str, str]:
    """Compute the integrity fields for a SKILL.md body.

    Returns a dict with ``checksum`` (always), ``signature`` (only when a
    secret is available), and ``algo``. Intended to be merged into the install
    meta written at install time.
    """
    checksum = compute_checksum(skill_md_text)
    out: dict[str, str] = {"checksum": checksum, "algo": ALGO}
    sec = (secret if secret is not None else _get_secret())
    if sec:
        out["signature"] = compute_signature(checksum, sec)
    return out


def read_install_meta(skill_dir: Path) -> dict[str, Any]:
    """Read the ``.kazma-install.json`` next to a skill; empty dict if absent.

    Returns the full parsed dict (callers extract ``source``/``checksum``/
    ``signature`` as needed) rather than only the source string.
    """
    meta = skill_dir / _INSTALL_META
    if not meta.is_file():
        return {}
    try:
        import json

        return json.loads(meta.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def write_install_meta(skill_dir: Path, meta: dict[str, Any]) -> None:
    """Write (overwrite) the ``.kazma-install.json`` next to a skill."""
    import json

    (skill_dir / _INSTALL_META).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def verify_skill(skill_md_path: Path, *, meta: dict[str, Any] | None = None) -> VerifyResult:
    """Verify the integrity of a SKILL.md against its stored install meta.

    Behavior:
      * **No stored checksum** (unsigned skill): ``ok=True``, ``signed=False``,
        logs a prominent warning. The skill still loads (backward-compat) but
        the caller can surface the "unsigned" status.
      * **Checksum present, matches**: ``ok=True``, ``signed=True``. If a
        signature is also present it is verified too (fail-closed on mismatch).
      * **Checksum present, mismatch (tamper)**: ``ok=False`` — fail closed.
      * **Signature present but no secret available**: ``ok=False`` — fail
        closed (can't authenticate a signed skill).

    Args:
        skill_md_path: absolute path to the ``SKILL.md`` file.
        meta: pre-read install meta; if None, read from the sibling
            ``.kazma-install.json``.
    """
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except Exception as exc:
        return VerifyResult(ok=False, reason=f"cannot read SKILL.md: {exc}")

    if meta is None:
        meta = read_install_meta(skill_md_path.parent)

    stored_checksum = str(meta.get("checksum") or "").strip()
    stored_signature = str(meta.get("signature") or "").strip()

    # Unsigned skill: warn but allow (backward-compat).
    if not stored_checksum:
        logger.warning(
            "[AgentSkill] '%s' has no integrity checksum — loading unsigned. "
            "Reinstall or run 'kazma agent-skills sign' to sign it.",
            skill_md_path.parent.name,
        )
        return VerifyResult(ok=True, reason="unsigned (no checksum stored)", signed=False)

    actual_checksum = compute_checksum(text)
    if not _hmac.compare_digest(stored_checksum, actual_checksum):
        logger.warning(
            "[AgentSkill] '%s' checksum MISMATCH — possible tampering. Refusing.",
            skill_md_path.parent.name,
        )
        return VerifyResult(
            ok=False, reason="checksum mismatch (SKILL.md may have been tampered with)", signed=True
        )

    # Checksum OK. If a signature is present, verify it too.
    if stored_signature:
        secret = _get_secret()
        if not secret:
            return VerifyResult(
                ok=False,
                reason="skill is signed but KAZMA_SECRET is unavailable — cannot verify",
                signed=True,
            )
        expected_sig = compute_signature(actual_checksum, secret)
        if not _hmac.compare_digest(stored_signature, expected_sig):
            logger.warning(
                "[AgentSkill] '%s' signature MISMATCH — possible tampering. Refusing.",
                skill_md_path.parent.name,
            )
            return VerifyResult(
                ok=False, reason="signature mismatch (skill may have been re-signed by an attacker)",
                signed=True,
            )
    else:
        # Checksum-only meta while a secret IS configured. The plain
        # checksum is publicly computable, so an attacker with write access
        # to the skill tree can rewrite SKILL.md AND the checksum; simply
        # deleting the signature field used to downgrade verification to
        # ok=True (signature-strip). Fail closed — remediation is reinstall
        # or 'kazma agent-skills sign'.
        secret = _get_secret()
        if secret:
            logger.warning(
                "[AgentSkill] '%s' stores a checksum but no signature while "
                "KAZMA_SECRET is configured — refusing (stripped meta or "
                "pre-signing install). Reinstall or run 'kazma agent-skills sign'.",
                skill_md_path.parent.name,
            )
            return VerifyResult(
                ok=False,
                reason=(
                    "checksum-only meta rejected: signature missing while "
                    "KAZMA_SECRET is configured (reinstall or sign the skill)"
                ),
                signed=False,
            )

    return VerifyResult(ok=True, reason="verified", signed=True)
