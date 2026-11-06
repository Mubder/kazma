"""Start read-only from the settings' local copy (resilience plan A4).

With the database away at boot, Kazma waited and served nothing: the chat
apps were silent for the whole outage. When the database is still away
:data:`READ_ONLY_AFTER_S` into a boot and a copy of the settings exists
(``kazma_core.settings_snapshot``), the boot gives up waiting
(:class:`ReadOnlyStart`) and ``create_app`` serves this instead:

* the chat apps, built from the copy, answer each chat's first message of
  the outage once -- "Kazma's database is away. Your message is kept and
  will be answered when it is back." -- in the sender's language, and every
  message is kept in the chat inbox (``chat_inbox.db``);
* ``/health/ready`` answers not ready, database away, with the server's own
  watch of it (``database_availability``), so the guard holds a restart;
* the web pages show one page saying how long the database has been away;
  every write (any ``/api`` request but a read of health) is refused with
  the stable code ``database_away_read_only``;
* every 10 s it signs in to the database once; when it answers, Kazma asks
  its guard for a reload (or exits for its supervisor to start it): the
  normal start then answers the kept messages, in order
  (:func:`drain_inbox`).

The copy is only read, never written back. ``KAZMA_READ_ONLY_START=0``
turns the mode off (the boot waits, as before).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any

# Module level: FastAPI resolves the routes' string annotations here.
from fastapi import Request

from kazma_core.settings_snapshot import SettingsReadOnly

if TYPE_CHECKING:
    from kazma_core.settings_snapshot import SnapshotSettings

logger = logging.getLogger(__name__)

__all__ = ["READ_ONLY_AFTER_S", "ReadOnlyStart", "create_read_only_app", "drain_inbox", "read_only_after"]

#: How long a boot waits for its database before starting read-only.
READ_ONLY_AFTER_S = 60.0
#: How often the read-only server asks the database whether it is back.
PROBE_EVERY_S = 10.0
CODE = SettingsReadOnly.code


class ReadOnlyStart(Exception):
    """The boot gave up waiting: start read-only from the settings' copy."""

    def __init__(self, since: float) -> None:
        super().__init__("database away: starting read-only from the settings' copy")
        self.since = since


def _enabled() -> bool:
    return (os.environ.get("KAZMA_READ_ONLY_START") or "1").strip().lower() not in ("0", "off", "false", "no")


def read_only_after(elapsed_s: float, *, since: float) -> None:
    """``wait_for_settings_store``'s hook: past :data:`READ_ONLY_AFTER_S`
    with a copy to start from, stop waiting (raise :class:`ReadOnlyStart`)."""
    from kazma_core.settings_snapshot import has_snapshot

    if elapsed_s >= READ_ONLY_AFTER_S and _enabled() and has_snapshot():
        logger.warning("[read-only] the database has been away %.0f s at boot: starting read-only "
                       "from the settings' copy", elapsed_s)
        raise ReadOnlyStart(since)


# -- the inbox -------------------------------------------------------------------


def _inbox_path():
    from kazma_core.paths import data_dir

    return os.path.join(str(data_dir()), "chat_inbox.db")


def _inbox():
    import sqlite3

    conn = sqlite3.connect(_inbox_path(), timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, "
                 "sender_id TEXT, text TEXT, context TEXT, received REAL, outage REAL, told INTEGER, "
                 "state TEXT DEFAULT 'kept')")
    return conn


def keep(msg: Any, outage: float) -> bool:
    """Keep *msg*; True when its chat has not been told yet in this outage."""
    import json

    conn = _inbox()
    committed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        told = conn.execute("SELECT 1 FROM inbox WHERE sender_id = ? AND outage = ? AND told = 1",
                            (msg.sender_id, outage)).fetchone() is None
        conn.execute("INSERT INTO inbox (platform, sender_id, text, context, received, outage, told) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (msg.platform, msg.sender_id, msg.text, json.dumps(msg.context_metadata or {}, default=str),
                      float(getattr(msg, "timestamp", None) or time.time()), outage, 1 if told else 0))
        conn.execute("COMMIT")
        committed = True
        return told
    finally:
        if not committed and conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()


async def drain_inbox(gateway: Any) -> int:
    """At a normal start: hand every kept message to the gateway, in the
    order received, and mark it handed. Returns how many."""
    import json

    from kazma_gateway.gateway import IncomingMessage

    def rows() -> list[tuple]:
        if not os.path.exists(_inbox_path()):
            return []
        conn = _inbox()
        try:
            return conn.execute("SELECT id, platform, sender_id, text, context, received FROM inbox "
                                "WHERE state = 'kept' ORDER BY id").fetchall()
        finally:
            conn.close()

    def handed(row_id: int) -> None:
        # Handed on: the text is the chat's now; the row keeps no copy, and
        # rows a week old go.
        conn = _inbox()
        try:
            conn.execute("UPDATE inbox SET state = 'handed', text = '', context = '{}' WHERE id = ?", (row_id,))
            conn.execute("DELETE FROM inbox WHERE state = 'handed' AND received < ?", (time.time() - 7 * 86400,))
        finally:
            conn.close()

    count = 0
    for row_id, platform, sender, text, context, received in await asyncio.to_thread(rows):
        await gateway.queue.put(IncomingMessage(platform=platform, sender_id=sender, text=text,
                                                context_metadata=json.loads(context or "{}"),
                                                timestamp=received))
        await asyncio.to_thread(handed, row_id)
        count += 1
    if count:
        logger.info("[read-only] %d message(s) kept while the database was away were handed on", count)
    return count


# -- the app ---------------------------------------------------------------------


def _notice(text: str) -> str:
    from kazma_core.language_lock import in_their_language

    return in_their_language(
        text,
        "Kazma's database is away. Your message is kept and will be answered when it is back.",
        "قاعدة بيانات Kazma غير متاحة الآن. رسالتك محفوظة وسيُرد عليها عندما تعود.",
    )


def _page(since: float, lang: str) -> str:
    # Since when, not for how long: a time needs no plural in either language.
    at = time.strftime("%H:%M", time.localtime(since))
    if lang == "ar":
        title, body, dir_ = "قاعدة البيانات غير متاحة", (
            f"قاعدة بيانات Kazma غير متاحة منذ الساعة {at}. يعمل Kazma للقراءة فقط: "
            "الرسائل محفوظة وسيُرد عليها عندما تعود."), "rtl"
    else:
        title, body, dir_ = "Database away", (
            f"Kazma's database has been away since {at}. Kazma runs read-only: "
            "messages are kept and answered when it is back."), "ltr"
    return (f'<!doctype html><html lang="{lang}" dir="{dir_}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><meta http-equiv="refresh" content="30"></head>'
            '<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 16px">'
            f'<h1>{title}</h1><p role="status">{body}</p></body></html>')


def create_read_only_app(start: ReadOnlyStart, *, probe: Any = None, on_back: Any = None) -> Any:
    """The read-only server (see the module docstring). *probe* says whether
    the database answers; *on_back* runs when it does (both injectable)."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    from kazma_core.settings_snapshot import load_snapshot

    app = FastAPI(title="Kazma (read-only)")
    state: dict[str, Any] = {"since": start.since, "probed_at": time.time(), "gateway": None, "task": None,
                             "lang": "en"}

    def _database_answers() -> bool:
        from kazma_core.db.backend import get_database_url
        from kazma_core.db.postgres_pool import refusal_reason

        url = get_database_url()
        return bool(url) and refusal_reason(url) is None

    async def _watch() -> None:
        check = probe or _database_answers
        while True:
            await asyncio.sleep(PROBE_EVERY_S)
            answers = await asyncio.to_thread(check)
            state["probed_at"] = time.time()
            # The guard keeps the start open while the boot says what it waits for.
            from kazma_core.config_availability import write_boot_status

            await asyncio.to_thread(write_boot_status, None if answers else "database", since=state["since"])
            if answers:
                logger.warning("[read-only] the database answers again: restarting normally")
                await _stop_gateway()
                await asyncio.to_thread(on_back or _restart_normally)
                return

    async def _stop_gateway() -> None:
        gateway = state.get("gateway")
        if gateway is not None:
            await gateway.stop()

    @app.on_event("startup")
    async def _startup() -> None:
        from kazma_core.config_store import set_config_store

        snapshot = await asyncio.to_thread(load_snapshot)
        if snapshot is not None:
            set_config_store(snapshot)  # every settings read in this process reads the copy
            if snapshot.get("agent.language") in ("ar", "en"):
                state["lang"] = snapshot.get("agent.language")
            try:
                state["gateway"] = await _start_gateway(snapshot, state)
            except (OSError, RuntimeError, ValueError, ImportError):
                # The page and health still serve; the chat apps stay silent.
                logger.exception("[read-only] the chat apps could not start from the settings' copy")
        from kazma_core.background import spawn_background

        state["task"] = spawn_background(_watch(), name="read-only-watch")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = state.get("task")
        if task is not None:
            task.cancel()
        await _stop_gateway()

    def _availability() -> dict[str, Any]:
        return {"state": "away", "confirmed": True, "since": state["since"],
                "probe_age_s": round(time.time() - state["probed_at"], 1),
                "probe_stale_after_s": 6 * PROBE_EVERY_S, "read_only": True}

    @app.middleware("http")
    async def _language(request: Request, call_next: Any) -> Any:
        # The main app's one answer: the reader's choice, else the install's
        # (``agent.language`` in the settings' copy).
        from kazma_ui.app import cookie_language
        from kazma_ui.i18n import set_request_language

        set_request_language(cookie_language(request, state["lang"]))
        return await call_next(request)

    @app.get("/health/live")
    def _live() -> JSONResponse:
        return JSONResponse({"status": "read_only", "read_only": True})

    @app.get("/health/ready")
    def _ready() -> JSONResponse:
        return JSONResponse({"status": "not_ready", "ready": False, "read_only": True,
                             "checks": {"database": "away"}, "database_availability": _availability()},
                            status_code=503)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def _everything(path: str, request: Request):
        if request.method != "GET" or path.startswith("api/"):
            return JSONResponse({"ok": False, "code": CODE, "error": "Kazma's database is away: Kazma "
                                 "runs read-only until it is back."}, status_code=503)
        from kazma_ui.i18n import current_language

        return HTMLResponse(_page(state["since"], current_language()), status_code=503)

    return app


async def _start_gateway(snapshot: SnapshotSettings, state: dict[str, Any]) -> Any:
    from kazma_gateway.chat_adapters import ChatAdapters
    from kazma_gateway.gateway import GatewayManager, OutboundMessage

    gateway = GatewayManager()

    async def _keep(msg: Any) -> None:
        tell = await asyncio.to_thread(keep, msg, state["since"])
        if tell:
            sent = await gateway.send(OutboundMessage(target_id=msg.reply_target(), text=_notice(msg.text),
                                                      context_metadata=dict(msg.context_metadata or {})))
            if not sent:
                logger.warning("[read-only] the notice did not reach %s", msg.platform)

    gateway.on_message(_keep)
    await asyncio.to_thread(ChatAdapters(gateway, snapshot).build)
    await gateway.start()
    return gateway


def _restart_normally() -> None:
    """Ask the guard for a reload; with no guard, end the process so its
    supervisor starts it again."""
    from kazma_core.observability.supervisor_watch import request_guard_reload

    if not request_guard_reload("the database is back after a read-only start"):
        logger.warning("[read-only] no guard to reload this server: exiting for a normal start")
        os._exit(0)
