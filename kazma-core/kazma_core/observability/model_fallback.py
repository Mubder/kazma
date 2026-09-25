"""Tell the operator when a model other than the configured one answers.

Kazma replaces the model under the operator in three places, and until
2026-09-25 each of them said so only in the log:

* ``ModelRegistry.get_client`` substitutes another provider when the
  configured one has no usable API key. Live, every boot from 2026-09-16 to
  2026-09-25 built the agent on Z.AI while the operator's DeepSeek key sat in
  the vault, one tenant scope away (see
  :data:`kazma_core.security.vault.INSTALL_SCOPED_CONFIG_SECRETS`).
* the supervisor's failover chain, and
* ``resilient_chat``'s failover chain answer with a backup model when the
  primary keeps failing (``agent.nonstop.failover``, opt-in).

Each reports here. A report goes where the operator looks: the chat
platforms through :func:`kazma_core.observability.ops_alerts.alert`, throttled
per condition, and the web banner through :meth:`AlertDispatcher.post_banner`.
When the configured model serves again the banner clears, and a substitution
that was announced says it is resolved.

Nothing is reported inside a read-only diagnostic (``kazma doctor``, the
health routes): those ask the registry what it WOULD do, which is not a
fallback anyone is living with.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

__all__ = [
    "report_failover",
    "report_model_served",
    "report_provider_usable",
    "report_substitution",
    "reset_fallback_state",
]

#: A substitution lasts until someone fixes a key, so repeating it every 15
#: minutes (the ops-alert default) would be 96 messages a day, and a channel
#: that loud gets muted. Twice a day while it lasts; the banner stays up.
_SUBSTITUTION_REPEAT_S = 12 * 3600.0
#: A failover is an outage of the primary; the default ops-alert pace.
_FAILOVER_REPEAT_S = 900.0


@dataclass
class _Episode:
    kind: str  # "substitution" | "failover"
    configured: str
    used: str
    first_seen: float
    last_seen: float
    count: int = 1
    announced: bool = False
    last_alert: float = 0.0


_lock = threading.Lock()
_active: dict[str, _Episode] = {}


def _suppressed() -> bool:
    from kazma_core.diagnostic_scope import writes_suppressed

    return writes_suppressed()


def _since(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _open(key: str, kind: str, configured: str, used: str) -> tuple[_Episode, bool]:
    now = time.time()
    with _lock:
        episode = _active.get(key)
        if episode is None:
            episode = _active[key] = _Episode(kind, configured, used, now, now)
            return episode, True
        episode.used = used
        episode.last_seen = now
        episode.count += 1
        return episode, False


def _close(key: str) -> _Episode | None:
    if key not in _active:  # the hot path: nothing was ever wrong
        return None
    with _lock:
        return _active.pop(key, None)


def _announce(
    key: str, episode: _Episode, *, subsystem: str, title: str, detail: str,
    repeat_s: float, fresh: bool, link: str = "", link_text: str = "",
) -> None:
    # Only when a repeat is due: alert() logs a WARNING even when it
    # throttles, and a fallback that lasts is hit on every client build.
    now = time.time()
    with _lock:
        due = fresh or now - episode.last_alert >= repeat_s
        if due:
            episode.last_alert = now
    if not due:
        return
    from kazma_core.observability.alerts import AlertDispatcher
    from kazma_core.observability.ops_alerts import alert

    if alert(f"llm.fallback.{key}", title, detail, severity="warn", cooldown_s=repeat_s):
        episode.announced = True
    AlertDispatcher.post_banner(
        subsystem=subsystem, title=title, reason=detail, since=episode.first_seen,
        link=link, link_text=link_text,
    )


def report_substitution(
    *, provider: str, model: str, used_provider: str, used_model: str,
) -> None:
    """The registry built *used_provider* because *provider* has no usable key."""
    if _suppressed():
        return
    name = (provider or "").strip() or "the configured provider"
    key = f"substitution:{name.lower()}"
    configured = f"{name}/{model}" if model else name
    used = f"{used_provider}/{used_model}" if used_model else used_provider
    episode, fresh = _open(key, "substitution", configured, used)
    _announce(
        key, episode,
        subsystem=f"llm-fallback:{name.lower()}",
        title=f"Model fallback: {name} has no usable API key",
        detail=(
            f"Kazma is answering with {used} instead of {configured}. Add or "
            f"fix the {name} key in Settings -> Providers; this clears by itself "
            f"once it works."
        ),
        repeat_s=_SUBSTITUTION_REPEAT_S,
        fresh=fresh,
        link="/settings?tab=providers_connectors",
        link_text="Open Providers",
    )


def report_provider_usable(provider: str) -> None:
    """*provider* resolved a usable key: end its substitution, if one was open."""
    name = (provider or "").strip()
    if not name:
        return
    key = f"substitution:{name.lower()}"
    episode = _close(key)
    if episode is None:
        return
    from kazma_core.observability.alerts import AlertDispatcher

    AlertDispatcher.resolve_alerts_for_subsystem(f"llm-fallback:{name.lower()}")
    logger.info(
        "[model_fallback] %s has a usable key again (%d substitution(s) since %s)",
        name, episode.count, _since(episode.first_seen),
    )
    if episode.announced and not _suppressed():
        from kazma_core.observability.ops_alerts import alert

        alert(
            f"llm.fallback.{key}.resolved",
            f"Model fallback resolved: {name} has a usable key again",
            (
                f"{episode.configured} answers again. It had been replaced by "
                f"{episode.used} {episode.count} time(s) since "
                f"{_since(episode.first_seen)}."
            ),
            severity="info",
            cooldown_s=_SUBSTITUTION_REPEAT_S,
        )


def report_failover(*, model: str, used_model: str, reason: str = "") -> None:
    """The failover chain answered with *used_model* after *model* failed."""
    if _suppressed():
        return
    name = (model or "").strip() or "the primary model"
    key = f"failover:{name.lower()}"
    episode, fresh = _open(key, "failover", name, used_model)
    why = f" ({reason})" if reason else ""
    _announce(
        key, episode,
        subsystem=f"llm-failover:{name.lower()}",
        title=f"Model failover: {name} failed, {used_model} answered",
        detail=(
            f"{name} kept failing{why}, so the failover chain "
            f"(agent.nonstop.failover) answered with {used_model}. This clears "
            f"when {name} answers again."
        ),
        repeat_s=_FAILOVER_REPEAT_S,
        fresh=fresh,
    )


def report_model_served(model: str | None) -> None:
    """*model* itself answered: end its failover episode, if one was open."""
    name = (model or "").strip()
    if not name:
        return
    episode = _close(f"failover:{name.lower()}")
    if episode is None:
        return
    from kazma_core.observability.alerts import AlertDispatcher

    AlertDispatcher.resolve_alerts_for_subsystem(f"llm-failover:{name.lower()}")
    logger.info(
        "[model_fallback] %s answers again (%d failover(s) since %s)",
        name, episode.count, _since(episode.first_seen),
    )


def _active_fallbacks() -> list[dict[str, object]]:
    """Open episodes, oldest first (tests)."""
    with _lock:
        episodes = sorted(_active.values(), key=lambda e: e.first_seen)
        return [
            {
                "kind": e.kind,
                "configured": e.configured,
                "used": e.used,
                "count": e.count,
                "since": _since(e.first_seen),
                "announced": e.announced,
            }
            for e in episodes
        ]


def reset_fallback_state() -> None:
    """Forget open episodes (tests)."""
    with _lock:
        _active.clear()
