"""authorize_effect — the single policy gate (Commitment Layer §3.4 / §4 / §R2.6).

Phase 1: audit-only (still the path used by the ``LocalToolRegistry.execute``
choke, which has no turn context). Phase 2 adds the full decision logic for the
``remind`` act — the reference implementation (plan §3.7). Other acts stay
audit-only until their Phase 4 resolvers ship; the memory act's corruption
half is already blocked at the data layer (``mutate_belief`` source-trust gate).

Decision mapping (§3.4):
  - read-only                                   → allow (audit)
  - remind, unambiguous + memory anchor         → **allow + rewrite** (the gate
                                                  computes the correct fire_at
                                                  via resolve_remind and rewrites
                                                  the tool args — this is what
                                                  makes the CoPilot schedule path
                                                  impossible to get wrong)
  - remind, ambiguous (relative + nearby event) → **clarify** (persist a
                                                  needs_clarify commitment; the
                                                  tool_worker interrupts on it)
  - remind, unsatisfiable                       → deny
  - fail-closed unregistered mutator (opt-in)   → deny

Every decision persists a :class:`Commitment` row (the §8.2 "silent allows must
still audit" rule) — allows as ``ready`` (the caller flips to ``committed`` on
successful execution), clarifies as ``needs_clarify`` (24h TTL).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from kazma_core.safety.side_effects import (
    SecurityTier,
    SemanticTier,
    ToolEffectProfile,
    get_effect_profile,
    is_read_only,
)

logger = logging.getLogger(__name__)

__all__ = ["EffectDecision", "authorize_effect"]


@dataclass
class EffectDecision:
    """Outcome of authorizing one tool effect.

    ``decision`` ∈ {"allow", "deny", "clarify", "confirm"}:
      * allow    — execute (with ``rewritten_args`` if present, else the original).
      * deny     — do not execute; return an error to the model.
      * clarify  — interrupt with a targeted question (a pending commitment is
                   persisted; the tool_worker suspends via interrupt()).
      * confirm  — interrupt for explicit OK (critical acts); Phase 3 wires the
                   combined-card UX. Treated like clarify until then.
    """
    decision: str
    reason: str
    profile: ToolEffectProfile
    audit: dict[str, Any] = field(default_factory=dict)
    commitment_id: str | None = None
    rewritten_args: dict[str, Any] | None = None
    clarify_question: str | None = None
    # Phase 3: when decision is clarify/confirm, the options shown on the
    # unified HITL card. Each option carries a slots_patch applied on resume
    # (plan §4.3). An empty list → free-text clarify (no discrete choices).
    options: list[dict[str, Any]] = field(default_factory=list)


def _args_digest(args: dict[str, Any] | None) -> str:
    if not args:
        return ""
    try:
        h = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()
        return h[:12]
    except Exception:
        return ""


def authorize_effect(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    # Phase 2 resolution inputs (only the tool_worker gate supplies these):
    user_text: str | None = None,
    request_at: datetime | None = None,
    memory_beliefs: list[dict[str, Any]] | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    tenant_id: str = "default",
    # Phase 1 knobs:
    enforce_unknown_mutators: bool = False,
    cfg: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> EffectDecision:
    """Authorize one tool effect.

    Phase 1 callers (``LocalToolRegistry.execute``) pass only ``tool_name`` /
    ``args`` / ``enforce_unknown_mutators`` → audit-only. The Phase 2
    ``tool_worker`` gate additionally passes ``user_text`` / ``request_at`` /
    ``memory_beliefs`` / thread context → full remind-act resolution kicks in.
    """
    profile = get_effect_profile(tool_name)
    ctx = context or {}
    audit = {
        "tool": tool_name,
        "effect": profile.effect.value,
        "security_tier": profile.security_tier.value,
        "semantic_tier": profile.semantic_tier.value,
        "act": profile.act,
        "registered": profile.registered,
        "read_only": is_read_only(tool_name),
        "source": ctx.get("source", "unknown"),
        "thread_id": thread_id or ctx.get("thread_id"),
        "args_digest": _args_digest(args),
    }

    # Kill-switch (AGENTS.md §20E): ``KAZMA_COMMITMENT_ENABLED=0`` must disable
    # the WHOLE layer. The graph-side gate (tool_worker_node) already honors it;
    # without this, ``LocalToolRegistry.execute`` (the registry/IDE/swarm choke)
    # kept enforcing exec/config/outbound resolvers + swarm-scope even after the
    # operator set the kill-switch — so the layer could not actually be turned
    # off. Audit-only allow (no enforcement) when disabled.
    try:
        from .constraints import is_commitment_enabled
        if not is_commitment_enabled():
            audit["kill_switch"] = "disabled"
            return EffectDecision(
                decision="allow",
                reason="commitment layer disabled (KAZMA_COMMITMENT_ENABLED=0)",
                profile=profile, audit=audit,
            )
    except Exception:  # noqa: BLE001
        # Fail-open: never let the kill-switch check itself block tool exec.
        logger.debug("[commitment] kill-switch check failed; continuing enabled")

    # Phase 1 enforcement: fail-closed unregistered mutators (opt-in only).
    if (enforce_unknown_mutators
            and not profile.registered
            and profile.security_tier == SecurityTier.UNSAFE):
        logger.warning(
            "[commitment] DENY %s — unregistered mutator (fail-closed) source=%s",
            tool_name, ctx.get("source", "unknown"),
        )
        return EffectDecision(
            decision="deny",
            reason="unregistered mutator (fail-closed); not in side-effect registry",
            profile=profile, audit=audit,
        )

    # Phase 5 swarm scope-token (§3.11): a worker mutator outside its inherited
    # scope is denied — the privilege-escalation guard. Only active when a
    # worker scope is bound (current_scope() is None for the main agent) AND
    # agent.commitment.swarm_scope_enforce is on (default ON since 2026-08-15).
    from .scope import current_scope, is_act_within_scope
    _scope = current_scope()
    if _scope is not None:
        try:
            from .config import get_commitment_config
            if bool(get_commitment_config().get("swarm_scope_enforce")):
                _ok, _why = is_act_within_scope(
                    profile.act, profile.semantic_tier, _scope)
                if not _ok:
                    logger.warning("[commitment] DENY %s — worker scope: %s", tool_name, _why)
                    audit["scope_denied"] = _why
                    return EffectDecision(
                        decision="deny", reason=f"worker scope: {_why}",
                        profile=profile, audit=audit,
                    )
        except Exception:
            logger.warning(
                "[commitment] swarm scope check failed — deny (not fail-open)",
                exc_info=True,
            )
            return EffectDecision(
                decision="deny",
                reason="worker scope: verification failed closed",
                profile=profile,
                audit=audit,
            )

    # Phase 2: act-specific resolution. Remind is the reference impl (§3.7);
    # resolve_remind was measured at 0 false-allow on held-out goldens (G2).
    if (profile.act == "remind"
            and user_text
            and request_at is not None
            and memory_beliefs is not None):
        return _resolve_remind_act(
            profile, tool_name, args or {}, user_text, request_at, memory_beliefs,
            audit=audit, thread_id=thread_id, turn_id=turn_id,
            tenant_id=tenant_id, cfg=cfg, source=ctx.get("source", "unknown"),
        )

    # Phase 4: cancel_job resolver — verify the job_id is a real pending job on
    # this thread (catches hallucinated / wrong-thread / already-terminal ids).
    # Only the graph path supplies thread_id; the registry choke stays audit-only.
    if profile.act == "cancel_job" and thread_id:
        return _resolve_cancel_job_act(
            profile, tool_name, args or {}, audit=audit,
            thread_id=thread_id, tenant_id=tenant_id, cfg=cfg,
            source=ctx.get("source", "unknown"),
        )

    # Phase 4 expand: exec / send_outbound / config_change semantic resolvers.
    # These add a denylist/allowlist/protected-key check BEFORE the security HITL
    # card (the gate runs first, then the HITL split). mutate_fs (containment in
    # IdeService.resolve) and delegate (HMAC trust at skill-load) stay audit-only.
    if profile.act == "exec" and args is not None:
        return _resolve_exec_act(
            profile, tool_name, args, audit=audit,
            thread_id=thread_id, tenant_id=tenant_id, cfg=cfg,
            source=ctx.get("source", "unknown"))
    if profile.act == "send_outbound" and args is not None:
        return _resolve_send_outbound_act(
            profile, tool_name, args, audit=audit,
            thread_id=thread_id, tenant_id=tenant_id, cfg=cfg,
            source=ctx.get("source", "unknown"))
    if profile.act == "config_change" and args is not None:
        return _resolve_config_change_act(
            profile, tool_name, args, audit=audit,
            thread_id=thread_id, tenant_id=tenant_id, cfg=cfg,
            source=ctx.get("source", "unknown"))

    # Everything else: audit-only allow (memory corruption is gated at
    # mutate_belief; other acts' resolvers arrive in Phase 4).
    logger.info(
        "[commitment] allow %s tier=%s/%s act=%s (audit-only) source=%s",
        tool_name, profile.security_tier.value, profile.semantic_tier.value,
        profile.act, ctx.get("source", "unknown"),
    )
    return EffectDecision(
        decision="allow",
        reason=("read-only" if profile.semantic_tier == SemanticTier.NONE
                else "audit-only (act resolver pending)"),
        profile=profile, audit=audit,
    )


def _effective_mode(cfg: dict[str, Any] | None, thread_id: str | None) -> str:
    """Resolve the commitment mode, honoring the per-thread security YOLO.

    Security YOLO ("stop asking me", per-thread + TTL) is the user's explicit
    opt-out of approval prompts; the commitment gate's clarify/confirm cards
    ARE approval prompts, so an active YOLO must silence them too — otherwise
    the user approves once and the next semantic check interrupts again
    (incident 2026-08-16: "YOLO keeps asking for permission").

    Scope of the bypass: remind / cancel_job, and the empty-outbound
    allowlist path. The exec denylist, a *populated* outbound allowlist,
    and config protected-keys keep enforcing. The memory source-trust
    gate (belief_mutation) is independent — the overwrite class stays blocked.
    """
    from .config import get_commitment_config

    mode = (cfg or {}).get("mode") if isinstance(cfg, dict) and cfg.get("mode") else (
        get_commitment_config().get("mode", "balanced"))
    if mode != "yolo" and thread_id:
        try:
            from kazma_core.safety.yolo import is_yolo_active

            if is_yolo_active(thread_id):
                return "yolo"
        except Exception:
            logger.debug("[commitment] yolo bridge check failed", exc_info=True)
    return mode


def _build_remind_clarify_options(res) -> list[dict[str, Any]]:
    """Build the discrete options for a remind clarify card (plan §4.3).

    Each option carries a ``slots_patch`` (a partial args dict) the tool_worker
    applies on resume. For remind the slot is ``timing`` (ISO fire_at). The
    resolver computed both candidate fire_ats; the card offers both + cancel.
    """
    opts: list[dict[str, Any]] = []
    fa = getattr(res, "option_fire_ats", {}) or {}
    ma = fa.get("memory_anchor")
    fn = fa.get("from_now")
    if ma is not None:
        opts.append({"id": "memory_anchor",
                     "label": f"Use the memory-anchored date ({ma.date()})",
                     "slots_patch": {"timing": ma.isoformat()}})
    if fn is not None:
        opts.append({"id": "from_now",
                     "label": f"Use the from-now date ({fn.date()})",
                     "slots_patch": {"timing": fn.isoformat()}})
    opts.append({"id": "cancel", "label": "Cancel", "slots_patch": None})
    return opts


def _resolve_remind_act(
    profile: ToolEffectProfile,
    tool_name: str,
    args: dict[str, Any],
    user_text: str,
    request_at: datetime,
    memory_beliefs: list[dict[str, Any]],
    *,
    audit: dict[str, Any],
    thread_id: str | None,
    turn_id: str | None,
    tenant_id: str,
    cfg: dict[str, Any] | None,
    source: str,
) -> EffectDecision:
    """Resolve a remind tool call: anchor to memory, compute fire_at, decide.

    Mode-aware (plan §9 Phase 6):
      * yolo        — semantic bypass (audit-only allow; security HITL still
                      applies separately).
      * strict      — widen the relevance window so more turns clarify.
      * autonomous  — a clarify with a from-now candidate is allowed with the
                      candidate (less friction; the incident-class overwrite is
                      still blocked at the memory gate).
      * balanced    — resolve_remind's decision stands (default).
    """
    # Lazy imports (store → paths; relative_time is standalone).
    from .relative_time import parse_absolute_timing, resolve_remind as _resolve
    from .relative_time import validate_timing_against_memory
    from .store import Commitment, create_commitment

    # Security YOLO active on this thread → semantic bypass too (see
    # _effective_mode; incident 2026-08-16 "YOLO keeps asking").
    mode = _effective_mode(cfg, thread_id)

    if mode == "yolo":
        logger.info("[commitment] yolo mode — semantic bypass for %s source=%s",
                    tool_name, source)
        return EffectDecision("allow", "yolo mode (semantic bypassed)", profile, audit)

    # ── PR4 (Class C): structured args first ──────────────────────────
    # If the model already put an ABSOLUTE ISO time in args.timing, validate it
    # against memory (CoPilot guard) and allow when consistent — instead of
    # re-parsing the chat text, which fails on a bare "yes" and caused the
    # over-clarify loop (incident 2026-08-12). Only relative/absent timing
    # falls through to chat-text resolution.
    _timing_arg = str((args or {}).get("timing") or "").strip()
    _consistency = "not_absolute"
    if _timing_arg and mode != "strict":
        _consistency, _matched = validate_timing_against_memory(_timing_arg, memory_beliefs)
        _abs_dt = parse_absolute_timing(_timing_arg)  # non-None when not_absolute is False
        if _abs_dt and _consistency in ("consistent", "no_memory"):
            _anchor = ("absolute" if _consistency == "no_memory"
                       else str(_matched.get("predicate") or "absolute") if _matched else "absolute")
            _rewritten = dict(args)
            _rewritten["timing"] = _abs_dt.isoformat()
            req_ts = request_at.timestamp() if hasattr(request_at, "timestamp") else time.time()
            _c = Commitment(
                thread_id=thread_id or "", turn_id=turn_id, act="remind",
                tool_name=tool_name, goal_text=(user_text or "")[:200],
                args_digest=_args_digest(args), request_at=req_ts, tenant_id=tenant_id,
                slots={"fire_at": _abs_dt.isoformat(), "anchor": _anchor},
                conflicts=[], confidence=1.0,
            )
            _c.status = "ready"
            _c.policy_decision = "allow"
            _cid = create_commitment(_c, cfg=cfg)
            logger.info(
                "[commitment] allow+rewrite (args-first) %s fire_at=%s "
                "consistency=%s anchor=%s cid=%s source=%s",
                tool_name, _abs_dt.isoformat(), _consistency, _anchor, _cid, source,
            )
            return EffectDecision(
                decision="allow", reason=f"absolute timing ({_consistency})",
                profile=profile, audit=audit, commitment_id=_cid, rewritten_args=_rewritten,
            )
        # _consistency == "conflict" → a belief exists but timing is far from it
        # (possible CoPilot overwrite): fall through to chat resolver, which
        # clarifies. "not_absolute" → relative timing: also fall through.

    # strict widens the relevance window (more clarifies); others use default.
    window = timedelta(days=30) if mode == "strict" else None
    res = _resolve(user_text, request_at=request_at, memory_beliefs=memory_beliefs,
                   relevance_window=window)

    # Chat text often has no time words on agent-initiated retries (the time
    # lives in args.timing) — fall back to resolving the timing arg itself
    # before reporting "no time expression found" (incident 2026-08-16).
    if res.decision == "clarify" and _timing_arg:
        _res_t = _resolve(_timing_arg, request_at=request_at,
                          memory_beliefs=memory_beliefs, relevance_window=window)
        _adopt = False
        if _res_t.decision == "allow" and _res_t.fire_at is not None:
            _has_abs = any(e.kind == "absolute" for e in _res_t.time_expressions)
            if _has_abs and _consistency not in ("consistent", "no_memory"):
                # Unvalidated absolute timing (PR4 judged conflict, or strict
                # mode skipped PR4) — validate against memory before allowing;
                # this is the CoPilot overwrite guard.
                _cons_t, _ = validate_timing_against_memory(_timing_arg, memory_beliefs)
                _adopt = _cons_t in ("consistent", "no_memory")
            else:
                _adopt = True
        elif _res_t.decision == "clarify" and _res_t.time_expressions:
            # A clarify with parsed expressions is more actionable than
            # "no time expression found" (it carries option_fire_ats).
            _adopt = True
        if _adopt:
            res = _res_t

    # autonomous: a nearby-event clarify with a from-now candidate → allow it.
    if (mode == "autonomous" and res.decision == "clarify"
            and res.candidate_fire_at is not None):
        res.decision = "allow"
        res.fire_at = res.candidate_fire_at
        res.reason = (f"autonomous mode — allowed with from-now candidate "
                      f"{res.fire_at.date()} (memory gate still guards overwrites)")

    req_ts = request_at.timestamp() if hasattr(request_at, "timestamp") else time.time()
    commitment = Commitment(
        thread_id=thread_id or "", turn_id=turn_id, act="remind",
        tool_name=tool_name, goal_text=(user_text or "")[:200],
        args_digest=_args_digest(args), request_at=req_ts, tenant_id=tenant_id,
        slots={
            "fire_at": res.fire_at.isoformat() if res.fire_at else None,
            "anchor": res.anchor,
        },
        conflicts=list(res.conflicts),
        confidence=1.0 if res.decision == "allow" else 0.4,
    )

    if res.decision == "allow" and res.fire_at is not None:
        # Rewrite the schedule args to the CORRECT fire_at — this is the fix
        # for the CoPilot schedule path. Whatever the model put in `timing`,
        # the anchored, memory-checked ISO date wins.
        rewritten = dict(args)
        rewritten["timing"] = res.fire_at.isoformat()
        commitment.status = "ready"
        commitment.policy_decision = "allow"
        cid = create_commitment(commitment, cfg=cfg)
        logger.info(
            "[commitment] allow+rewrite %s fire_at=%s anchor=%s cid=%s source=%s",
            tool_name, res.fire_at.isoformat(), res.anchor, cid, source,
        )
        return EffectDecision(
            decision="allow", reason=res.reason, profile=profile, audit=audit,
            commitment_id=cid, rewritten_args=rewritten,
        )

    if res.decision == "clarify":
        commitment.status = "needs_clarify"
        commitment.policy_decision = "clarify"
        cid = create_commitment(commitment, cfg=cfg)  # 24h TTL
        logger.info(
            "[commitment] clarify %s anchor=%s cid=%s — %s source=%s",
            tool_name, res.anchor, cid, res.reason, source,
        )
        return EffectDecision(
            decision="clarify", reason=res.reason, profile=profile, audit=audit,
            commitment_id=cid, clarify_question=res.reason,
            options=_build_remind_clarify_options(res),
        )

    # deny (rare for remind — e.g. unsatisfiable)
    commitment.status = "aborted"
    commitment.policy_decision = "deny"
    cid = create_commitment(commitment, cfg=cfg)
    logger.info("[commitment] deny %s cid=%s — %s", tool_name, cid, res.reason)
    return EffectDecision(
        decision="deny", reason=res.reason or "remind unresolved", profile=profile,
        audit=audit, commitment_id=cid,
    )


def _resolve_cancel_job_act(
    profile: ToolEffectProfile,
    tool_name: str,
    args: dict[str, Any],
    *,
    audit: dict[str, Any],
    thread_id: str | None,
    tenant_id: str,
    cfg: dict[str, Any] | None,
    source: str,
) -> EffectDecision:
    """Resolve a cancel_scheduled call (plan §3.5).

    ``cancel_scheduled`` takes a ``job_id``. The resolver verifies that job_id
    refers to a REAL, PENDING job on this thread before allowing — this catches
    hallucinated / wrong-thread / already-terminal job_ids (the model often
    invents ids it never listed). When the target doesn't match, it clarifies
    WITH THE ACTUAL pending list so the user/model can pick the right one,
    instead of confidently "canceling" something that doesn't cancel.
    """
    from .constraints import cron_pending_jobs
    from .store import Commitment, create_commitment

    # Security YOLO active on this thread → semantic bypass too (see
    # _effective_mode; incident 2026-08-16 "YOLO keeps asking").
    mode = _effective_mode(cfg, thread_id)
    if mode == "yolo":
        logger.info("[commitment] yolo — semantic bypass for %s source=%s", tool_name, source)
        return EffectDecision("allow", "yolo mode (semantic bypassed)", profile, audit)

    job_id = (args or {}).get("job_id")
    pending = cron_pending_jobs(thread_id=thread_id, tenant_id=tenant_id)

    if pending is None:
        logger.info(
            "[commitment] cancel_job %s — verification unavailable, clarify",
            job_id,
        )
        return EffectDecision(
            "clarify",
            "cancel_job: cannot verify pending jobs (scheduler unavailable)",
            profile,
            audit,
            clarify_question="Could not list pending jobs. Cancel anyway?",
        )

    commitment = Commitment(
        thread_id=thread_id or "", act="cancel_job", tool_name=tool_name,
        goal_text=f"cancel {job_id}", args_digest=_args_digest(args),
        request_at=time.time(), tenant_id=tenant_id,
        slots={"job_id": job_id}, confidence=1.0,
    )

    if job_id and any(j["job_id"] == job_id for j in pending):
        commitment.status = "ready"
        commitment.policy_decision = "allow"
        cid = create_commitment(commitment, cfg=cfg)
        logger.info("[commitment] allow cancel_job %s cid=%s source=%s", job_id, cid, source)
        return EffectDecision(
            decision="allow",
            reason=f"cancel_job: {job_id} is a pending job on this thread",
            profile=profile, audit=audit, commitment_id=cid,
        )

    # Cross-thread / legacy fallback (incident 2026-08-16): jobs scheduled
    # before thread capture carry thread_id='' and reminders get cancelled
    # from a different interface than they were booked on (Telegram → Web).
    # The thread-scoped lookup never matched those, so a VALID cancel
    # clarified forever. The exact job_id match against ALL pending jobs is
    # still the hallucination guard — a invented id matches nothing.
    all_pending = cron_pending_jobs(thread_id=None, tenant_id=tenant_id)
    if job_id and all_pending and any(j["job_id"] == job_id for j in all_pending):
        commitment.status = "ready"
        commitment.policy_decision = "allow"
        cid = create_commitment(commitment, cfg=cfg)
        logger.info(
            "[commitment] allow cancel_job %s cid=%s (cross-thread match) source=%s",
            job_id, cid, source,
        )
        return EffectDecision(
            decision="allow",
            reason=f"cancel_job: {job_id} is a pending job (cross-thread)",
            profile=profile, audit=audit, commitment_id=cid,
        )

    # not found / not pending / hallucinated → clarify WITH the actual pending
    # jobs as discrete options. Options are required here: an option-less
    # clarify maps Approve → "cancel" in build_resume_value, which turned
    # every approval into "cancelled by the user" (incident 2026-08-16).
    commitment.status = "needs_clarify"
    commitment.policy_decision = "clarify"
    commitment.confidence = 0.3
    cid = create_commitment(commitment, cfg=cfg)
    listing_src = all_pending if all_pending else pending
    listing = "; ".join(f"{j['job_id']} ({(j['prompt'] or '')[:40]})" for j in listing_src[:5]
                        ) or "(no pending jobs)"
    q = (f"job_id {job_id!r} is not a pending job. "
         f"Current pending jobs: {listing}. Provide the correct job_id.")
    options: list[dict[str, Any]] = []
    for j in (listing_src or [])[:5]:
        options.append({
            "id": f"job_{j['job_id']}",
            "label": f"Cancel {j['job_id']} — {(j['prompt'] or '')[:40]}",
            "slots_patch": {"job_id": j["job_id"]},
        })
    options.append({"id": "cancel", "label": "Don't cancel anything", "slots_patch": None})
    logger.info("[commitment] clarify cancel_job %s cid=%s — not pending/matched", job_id, cid)
    return EffectDecision(
        decision="clarify", reason=q, profile=profile, audit=audit,
        commitment_id=cid, clarify_question=q, options=options,
    )


# ── Phase 4 expand: exec / send_outbound / config_change resolvers ─────────

import re as _re  # noqa: E402

# Catastrophic command patterns (irreversible / system-wide). These DENY before
# the security HITL card even shows — the model should never execute these.
# Coverage notes (audit finding): the previous set only matched the literal
# `rm -[f] /` and a narrow `|(sh|bash|zsh)`, so `rm -r /`, `rm -rf ~`,
# `rm -rf .`, `rm -rf *`, `rm -rf /home`, `curl|python|perl`, `chmod -R 777
# /etc` all reached the HITL card. Broadened below. System-dir targets use a
# trailing `(?!\S)` so `rm -rf /home` is denied but `rm -rf /home/user/proj`
# (a legitimate scoped delete) is not.
_EXEC_DENYLIST = [
    # rm with a recursive flag targeting a catastrophic location.
    _re.compile(
        r"\brm\s+[^;|&\n]*?-[a-zA-Z]*r[a-zA-Z]*\s+"
        r"(?:"
        r"/(?![\w/])"                                                       # /           (root)
        r"|/\*"                                                              # /*          (root glob)
        r"|/(?:home|etc|root|var|usr|boot|bin|sbin|lib|opt|proc|sys)(?!\S)"  # /<sysdir> bare
        r"|[\"']?(?:~|\$HOME|\$PWD)[\"']?(?!\S)"                            # home dir (quoted-tolerant)
        r"|[\"']?\.[\"']?(?!\S)"                                            # .           (cwd wipe)
        r"|[\"']?\*[\"']?(?!\S)"                                            # *           (cwd glob)
        r")",
        _re.IGNORECASE,
    ),
    _re.compile(r":\s*\(\)\s*\{.*:.*\|.*&.*\}", _re.IGNORECASE),  # fork bomb
    # Remote content piped to ANY interpreter (sh/bash/zsh/python/perl/ruby/node).
    _re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh|python\d?|perl|ruby|node)\b", _re.IGNORECASE),
    _re.compile(r"\bdd\b\s+.*of\s*=\s*/dev/", _re.IGNORECASE),  # dd to device
    _re.compile(r"\bmkfs\b", _re.IGNORECASE),  # format filesystem
    _re.compile(r">\s*/dev/(sd|nvme|hd|disk)", _re.IGNORECASE),  # redirect to raw disk
    _re.compile(r"\b(?:shutdown|reboot|halt|poweroff|init\s+0)\b", _re.IGNORECASE),
    # chmod world-writable/recursive on system locations.
    _re.compile(
        r"\bchmod\s+(?:-R\s+)?[0-7]{3,4}\s+"
        r"(?:/(?![\w/])|/\*|/(?:home|etc|root|var|usr|boot|bin|sbin|lib|opt)(?!\S)|~)",
        _re.IGNORECASE,
    ),
]

# Protected config keys — mutating these could DISABLE the safety layer itself.
# (A self-protection measure: the agent can't turn off its own gates via config.)
_CONFIG_PROTECTED_PREFIXES = (
    "safety.", "agent.commitment.", "notifications.lifecycle.",
)


def _resolve_exec_act(profile, tool_name, args, *, audit, thread_id, tenant_id, cfg, source):
    """exec resolver (denylist + cwd pin, plan §5 / WS5)."""
    from .store import Commitment, create_commitment

    _raw_cmd = args.get("command", "")
    # Normalize list-form commands (e.g. ["rm","-rf","/"]) to a string so the
    # denylist regexes can match — str([...]) produces the repr, which silently
    # bypassed every pattern (audit finding).
    if isinstance(_raw_cmd, (list, tuple)):
        command = " ".join(str(x) for x in _raw_cmd)
    else:
        command = str(_raw_cmd)
    # 1. Denylist: catastrophic commands → deny (before the HITL card).
    for pat in _EXEC_DENYLIST:
        if pat.search(command):
            c = Commitment(thread_id=thread_id or "", act="exec", tool_name=tool_name,
                           goal_text=command[:200], args_digest=_args_digest(args),
                           request_at=time.time(), tenant_id=tenant_id,
                           slots={"command": command[:500]}, confidence=0.0)
            c.status = "aborted"; c.policy_decision = "deny"
            cid = create_commitment(c, cfg=cfg)
            logger.warning("[commitment] DENY exec — catastrophic pattern matched: %s", command[:80])
            return EffectDecision("deny", f"exec denylist: catastrophic pattern in command",
                                  profile, audit, commitment_id=cid)
    # 2. cwd pin: if a cwd is provided, verify it's within the workspace root.
    cwd = args.get("cwd")
    if cwd:
        try:
            from kazma_core.ide.workspace_scope import resolve_workspace_root
            root = resolve_workspace_root()
            if root:
                from pathlib import Path
                p = Path(cwd).resolve()
                root_path = Path(root).resolve()
                # Real path containment, not a raw string prefix —
                # startswith() let sibling dirs like "...\kazma-evil" pass
                # "...\kazma" and skipped the clarify (audit finding).
                try:
                    p.relative_to(root_path)
                    _outside = False
                except ValueError:
                    _outside = True
                if _outside:
                    c = Commitment(thread_id=thread_id or "", act="exec", tool_name=tool_name,
                                   goal_text=command[:200], args_digest=_args_digest(args),
                                   request_at=time.time(), tenant_id=tenant_id,
                                   slots={"command": command[:500], "cwd": str(cwd)},
                                   confidence=0.3)
                    c.status = "needs_clarify"; c.policy_decision = "clarify"
                    cid = create_commitment(c, cfg=cfg)
                    q = f"cwd {cwd!r} is outside the workspace root. Run in-workspace?"
                    return EffectDecision("clarify", q, profile, audit, commitment_id=cid,
                                          clarify_question=q)
        except Exception:
            logger.warning(
                "[commitment] cwd pin check failed — clarify (not fail-open)",
                exc_info=True,
            )
            c = Commitment(
                thread_id=thread_id or "",
                act="exec",
                tool_name=tool_name,
                goal_text=command[:200],
                args_digest=_args_digest(args),
                request_at=time.time(),
                tenant_id=tenant_id,
                slots={"command": command[:500], "cwd": str(cwd)},
                confidence=0.2,
            )
            c.status = "needs_clarify"
            c.policy_decision = "clarify"
            cid = create_commitment(c, cfg=cfg)
            q = "Could not verify cwd is inside the workspace. Run anyway?"
            return EffectDecision(
                "clarify",
                q,
                profile,
                audit,
                commitment_id=cid,
                clarify_question=q,
            )
    # 3. Safe command → allow (the HITL security card still applies separately).
    # Audit trail: the exec resolver returns before the generic allow logging,
    # so "silent allows" skipped the audit entirely (§8.2) — log here (audit).
    logger.info(
        "[commitment] allow exec %s (no denylist match; HITL still applies) source=%s",
        tool_name, source,
    )
    return EffectDecision("allow", "exec: no denylist match (HITL still applies)",
                          profile, audit)


def _resolve_send_outbound_act(profile, tool_name, args, *, audit, thread_id, tenant_id, cfg, source):
    """send_outbound resolver (target allowlist, plan §5 / WS5)."""
    from .store import Commitment, create_commitment
    from .config import get_commitment_config

    target = str(args.get("to") or args.get("target") or args.get("recipient") or "")
    allowlist = (get_commitment_config().get("outbound_allowed_targets") or [])
    # Empty allowlist: balanced/autonomous stay HITL-permissive. Strict
    # refuses to send until the operator names at least one target.
    if not allowlist:
        mode = _effective_mode(cfg, thread_id)
        if mode == "strict":
            q = "No outbound allowlist is configured. Name an approved target or send anyway?"
            c = Commitment(
                thread_id=thread_id or "",
                act="send_outbound",
                tool_name=tool_name,
                goal_text=f"send to {target[:100]}",
                args_digest=_args_digest(args),
                request_at=time.time(),
                tenant_id=tenant_id,
                slots={"target": target[:500]},
                confidence=0.2,
            )
            c.status = "needs_clarify"
            c.policy_decision = "clarify"
            cid = create_commitment(c, cfg=cfg)
            return EffectDecision(
                "clarify",
                q,
                profile,
                audit,
                commitment_id=cid,
                clarify_question=q,
            )
        logger.info(
            "[commitment] allow outbound %s (no allowlist configured; HITL applies) source=%s",
            tool_name, source,
        )
        return EffectDecision("allow", "outbound: no target allowlist configured (HITL applies)",
                              profile, audit)
    if target and target in allowlist:
        logger.info(
            "[commitment] allow outbound %s target=%r (allowlisted) source=%s",
            tool_name, target[:100], source,
        )
        return EffectDecision("allow", f"outbound: target {target!r} is allowlisted", profile, audit)
    # Unknown target → clarify (with the allowlist so the user picks a valid one).
    c = Commitment(thread_id=thread_id or "", act="send_outbound", tool_name=tool_name,
                   goal_text=f"send to {target[:100]}", args_digest=_args_digest(args),
                   request_at=time.time(), tenant_id=tenant_id,
                   slots={"target": target[:500]}, confidence=0.3)
    c.status = "needs_clarify"; c.policy_decision = "clarify"
    cid = create_commitment(c, cfg=cfg)
    q = (f"target {target!r} is not in the approved allowlist "
         f"({', '.join(allowlist[:5])}). Confirm the target before sending.")
    return EffectDecision("clarify", q, profile, audit, commitment_id=cid, clarify_question=q)


def _resolve_config_change_act(profile, tool_name, args, *, audit, thread_id, tenant_id, cfg, source):
    """config_change resolver (protected-key denylist, plan §5 / WS5).

    Self-protection: the agent cannot mutate config keys that would disable its
    own safety layer (safety.*, agent.commitment.*, lifecycle notifications).
    """
    from .store import Commitment, create_commitment

    key = str(args.get("key") or "")
    if key and any(key.startswith(p) for p in _CONFIG_PROTECTED_PREFIXES):
        c = Commitment(thread_id=thread_id or "", act="config_change", tool_name=tool_name,
                       goal_text=f"config {key}", args_digest=_args_digest(args),
                       request_at=time.time(), tenant_id=tenant_id,
                       slots={"key": key[:500]}, confidence=0.0)
        c.status = "aborted"; c.policy_decision = "deny"
        cid = create_commitment(c, cfg=cfg)
        logger.warning("[commitment] DENY config — protected key %s", key)
        return EffectDecision("deny", f"config: key {key!r} is protected (safety-critical)",
                              profile, audit, commitment_id=cid)
    logger.info(
        "[commitment] allow config %s (key not protected) source=%s",
        tool_name, source,
    )
    return EffectDecision("allow", "config: key is not protected", profile, audit)
