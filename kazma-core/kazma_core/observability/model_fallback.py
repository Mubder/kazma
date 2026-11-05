"""Tell the operator when a model other than the configured one answers.

Kazma replaces the model under the operator in three places, and until
2026-09-25 each of them said so only in the log:

* ``ModelRegistry.get_client`` substitutes another provider when the
  configured one has no usable API key. Live, every boot from 2026-09-16 to
  2026-09-25 built the agent on Z.AI while the operator's DeepSeek key sat in
  the vault, one tenant scope away (see
  :data:`kazma_core.security.vault.INSTALL_SCOPED_SECRETS`).
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
import queue
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "report_failover",
    "report_model_served",
    "report_provider_usable",
    "report_rebind_failed",
    "report_rebind_ok",
    "report_substitution",
    "reset_fallback_state",
    "restore_owed_fallbacks",
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
    #: Its announcement is on record in ``outage_pages`` (substitutions only).
    marked: bool = False


_lock = threading.Lock()
_active: dict[str, _Episode] = {}

#: A substitution's page promises "this clears by itself once it works", and
#: the note that says so is sent by the process that sees the key work. Its
#: episode lived in ``_active`` only, so a restart in between (a reload, a
#: deploy, the guard) left the page with no note. The announced episode is
#: kept in ``outage_pages`` under this prefix, and a new process takes it
#: back at boot (:func:`restore_owed_fallbacks`).
_MARK_PREFIX = "llm.fallback.substitution:"

#: ``get_client`` runs on the event loop too (the supervisor's failover), so
#: the marks are written by one daemon thread, in the order asked: a note
#: then its settle never land the other way round.
_marks: queue.SimpleQueue[tuple[str, str, dict[str, Any]] | threading.Event] = queue.SimpleQueue()
_writer: threading.Thread | None = None


def _write_marks() -> None:
    from kazma_core.observability.outage_pages import note_outage_page, settle_outage_page

    while True:
        item = _marks.get()
        if isinstance(item, threading.Event):
            item.set()
            continue
        action, key, fields = item
        if action == "note":
            note_outage_page(key, **fields)
        else:
            settle_outage_page(key)


def _keep_mark(action: str, key: str, **fields: Any) -> None:
    global _writer
    _marks.put((action, _MARK_PREFIX + key.removeprefix("substitution:"), fields))
    with _lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_write_marks, name="model-fallback-marks", daemon=True)
            _writer.start()


def _flush_marks(timeout: float = 5.0) -> bool:
    """Wait for the marks asked so far to be written (tests)."""
    if _writer is None:
        return True
    done = threading.Event()
    _marks.put(done)
    return done.wait(timeout)


def restore_owed_fallbacks() -> int:
    """Take back the announced substitutions an earlier process left open.

    Called once at boot, before the registry builds its first client, and
    never on the event loop (it reads a file). A provider whose key now works
    then ends its episode with the resolved note; one still without a key is
    not announced again before its repeat is due. Returns how many.
    """
    from kazma_core.observability.outage_pages import owed_outage_pages

    owed = owed_outage_pages(_MARK_PREFIX)
    restored = 0
    with _lock:
        for mark_key, mark in owed.items():
            name = mark_key.removeprefix(_MARK_PREFIX)
            key = f"substitution:{name}"
            if key in _active:
                continue
            since = float(mark.get("since") or 0) or time.time()
            paged_at = float(mark.get("paged_at") or since)
            configured, _, used = str(mark.get("cause") or "").partition("|")
            _active[key] = _Episode(
                "substitution",
                configured or name,
                used or "another provider",
                since,
                paged_at,
                announced=True,
                last_alert=paged_at,
                marked=True,
            )
            restored += 1
    if restored:
        logger.info(
            "[model_fallback] %d announced substitution(s) still open from before the restart", restored
        )
    return restored


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
        if episode.kind == "substitution" and not episode.marked:
            episode.marked = True
            _keep_mark(
                "note", key, since=episode.first_seen,
                cause=f"{episode.configured}|{episode.used}",
            )
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

        sent = alert(
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
        if episode.marked:
            if sent:
                _keep_mark("settle", key)
            else:
                logger.warning(
                    "[model_fallback] the note that %s works again was not sent; "
                    "it stays owed", name,
                )
    elif episode.marked and not _suppressed():
        _keep_mark("settle", key)


def report_rebind_failed(*, provider: str, error: str) -> None:
    """A key was saved for *provider* and the running agent could not rebuild
    its client: it keeps answering with the client it had, built for the key
    before (FA-110: this was a DEBUG line)."""
    if _suppressed():
        return
    name = (provider or "").strip() or "the provider"
    key = f"rebind:{name.lower()}"
    episode, fresh = _open(key, "rebind", name, "the previous client")
    _announce(
        key, episode,
        subsystem=f"llm-rebind:{name.lower()}",
        title=f"The new {name} key is saved but not in use yet",
        detail=(
            f"Kazma could not rebuild its model client after the {name} key was "
            f"saved ({error}), so it keeps answering with the client it had. "
            f"Reload Kazma to use the new key; this clears once a client is built."
        ),
        repeat_s=_SUBSTITUTION_REPEAT_S,
        fresh=fresh,
        link="/settings?tab=providers_connectors",
        link_text="Open Providers",
    )


def report_rebind_ok(provider: str) -> None:
    """The running agent took *provider*'s new key: end a rebind failure."""
    name = (provider or "").strip() or "the provider"
    episode = _close(f"rebind:{name.lower()}")
    if episode is None:
        return
    from kazma_core.observability.alerts import AlertDispatcher

    AlertDispatcher.resolve_alerts_for_subsystem(f"llm-rebind:{name.lower()}")
    logger.info("[model_fallback] the agent took the new %s key", name)


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
    """Forget open episodes (tests): what a restart forgets.

    Marks already asked for are written first, so none lands in the next
    test's folder.
    """
    _flush_marks(timeout=2.0)
    with _lock:
        _active.clear()
