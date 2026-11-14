"""A problem report: what is needed to understand a problem, and nothing private
(2026-11-14, REMAINING 3.1).

One report, made the same way from every place it is asked for -- the ``?``
beside the bell and "Report this" in the web app, ``kazma report``, ``/report``
in a chat:

* **what is in it** -- the person's own words, where it was asked from (a page,
  a chat app), Kazma's version and commit, the system (OS, Python), the parts
  the install chose and their state, the readiness checks' names and results,
  the open problems' titles, and the last error lines of Kazma's log;
* **what is never in it** -- chats, memories, documents, settings, keys or the
  vault; and every text that goes in passes :func:`redacted`: credentials
  (``errors.redact_secrets``), the home folder (written ``~``), email
  addresses, IP addresses;
* **who sees it first** -- the person: every surface shows :func:`preview`, the
  exact JSON that would be sent, and sends only on their go-ahead;
* **where it goes** -- the setting ``support.report_url`` (Settings ->
  System), by default Kazma's own private inbox
  (``https://status.kazma.ai/v1/report``); empty: reports are only saved here.
  A report that cannot be sent is saved in ``<data dir>/problem_reports/`` and
  the person is told where, so it can go with a GitHub issue instead.

At most :data:`MAX_PER_HOUR` reports leave an install in an hour.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_REPORT_URL", "REPORT_URL_KEY", "SendResult", "collect", "describe", "preview", "redacted", "send"]

REPORT_URL_KEY = "support.report_url"
DEFAULT_REPORT_URL = "https://status.kazma.ai/v1/report"
REPORT_KIND = "kazma-problem-report"
MAX_NOTE = 4000
MAX_ERROR_LINES = 30
MAX_PER_HOUR = 10
_SENT_KEY = "system.support.reports_sent"
_MAX_BYTES = 60_000

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")
_ERROR = re.compile(r'"level":\s*"(ERROR|CRITICAL)"|\s(ERROR|CRITICAL)\s|Traceback \(most recent call last\)')


def redacted(text: Any) -> str:
    """*text* with credentials, the home folder, email and IP addresses taken out."""
    from kazma_core.errors import redact_secrets

    out = redact_secrets(str(text if text is not None else ""))
    homes = {str(Path.home())}
    for name in ("USERPROFILE", "HOME"):
        if os.environ.get(name):
            homes.add(os.environ[name])
    for home in sorted(homes, key=len, reverse=True):
        if len(home) > 3:
            out = re.sub(re.escape(home), "~", out, flags=re.I)
            out = re.sub(re.escape(home.replace("\\", "/")), "~", out, flags=re.I)
    out = _EMAIL.sub("<email>", out)
    out = _IPV4.sub("<ip>", out)
    return _IPV6.sub("<ip>", out)


def _install_parts() -> dict[str, Any]:
    from kazma_core.postgres_service import read_record

    record = read_record()
    install = record.get("install") or {}
    parts = install.get("parts") or {}
    return {
        "mode": install.get("mode"),
        "parts": {name: (state.get("ok") if isinstance(state, dict) else state) for name, state in parts.items()},
        "postgres": bool(record.get("postgres")),
        "container_engine": bool(record.get("container_engine")),
    }


def _readiness(port: int | None) -> dict[str, Any]:
    """The readiness checks' names and results, from the running server."""
    import httpx

    from kazma_core.server_port import server_port

    try:
        resp = httpx.get(f"http://127.0.0.1:{port or server_port()}/health/ready", timeout=5.0)
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"answered": False, "why": type(exc).__name__}
    checks = body.get("checks") if isinstance(body, dict) else None
    shown = {}
    for name, check in (checks or {}).items():
        if isinstance(check, dict):
            shown[str(name)] = check.get("status") or check.get("ok")
    return {"answered": True, "status": resp.status_code, "needs_setup": bool(body.get("needs_setup")),
            "checks": shown}


def _error_lines(log: Path | None = None) -> list[str]:
    from kazma_core.paths import log_file

    path = log or log_file()
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=5000)
    except OSError:
        return []
    lines = [line.rstrip()[:600] for line in tail if _ERROR.search(line)]
    return [redacted(line) for line in lines[-MAX_ERROR_LINES:]]


def _problems() -> list[str]:
    from kazma_core.observability.open_problems import open_problems

    try:
        found = open_problems("en")
    except (OSError, RuntimeError, ValueError, KeyError, ImportError) as exc:
        # A report is still worth sending without them.
        return [f"(open problems could not be read: {type(exc).__name__})"]
    return [redacted(f"{p['source']}: {p['title']}") for p in found.get("problems", [])[:20]]


def collect(note: str, *, where: str = "", port: int | None = None, log: Path | None = None) -> dict[str, Any]:
    """The report for *note*, asked from *where* (blocking: run off the loop)."""
    from kazma_core.version import get_version

    try:
        from kazma_ui.health import get_build_info

        commit = str(get_build_info().get("commit") or "")
    except ImportError:
        commit = ""
    return {
        "kind": REPORT_KIND,
        "version": get_version(),
        "commit": commit,
        "where": redacted(where)[:200],
        "note": redacted(note)[:MAX_NOTE],
        "system": {"os": platform.platform(terse=True), "python": platform.python_version(),
                   "machine": platform.machine()},
        "install": _install_parts(),
        "readiness": _readiness(port),
        "open_problems": _problems(),
        "recent_errors": _error_lines(log),
        "made_at": int(time.time()),
    }


def preview(report: dict[str, Any]) -> str:
    """The report exactly as it would be sent."""
    return json.dumps(report, indent=2, ensure_ascii=False)


@dataclass(frozen=True)
class SendResult:
    """``sent`` with the inbox's ``report_id``, else ``saved`` with the file it
    was kept in and ``why`` it was not sent."""

    sent: bool
    report_id: str = ""
    saved: str = ""
    why: str = ""


def _report_url() -> str:
    from kazma_core.config_store import get_config_store

    value = get_config_store().get(REPORT_URL_KEY, DEFAULT_REPORT_URL)
    return "" if value is None else str(value).strip()


def _save(report: dict[str, Any], why: str) -> SendResult:
    from kazma_core.paths import data_dir

    folder = Path(data_dir()) / "problem_reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(preview(report), encoding="utf-8")
    logger.info("[support] Problem report kept at %s: %s", path, why)
    return SendResult(sent=False, saved=str(path), why=why)


def _within_rate(now: float) -> bool:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    allowed = False

    def update(current: Any) -> list[float]:
        nonlocal allowed
        recent = [float(t) for t in (current or []) if isinstance(t, (int, float)) and now - float(t) < 3600]
        allowed = len(recent) < MAX_PER_HOUR
        return [*recent, now] if allowed else recent

    store.atomic_update(_SENT_KEY, update, category="system")
    return allowed


def send(report: dict[str, Any], *, url: str | None = None, now: float | None = None) -> SendResult:
    """Send *report* to the inbox (blocking). Never raises: a report that
    cannot leave is saved, and the result says where and why."""
    import httpx

    from kazma_core.http_tls import shared_ssl_context

    body = preview(report).encode("utf-8")
    if len(body) > _MAX_BYTES:
        report = {**report, "recent_errors": report.get("recent_errors", [])[-5:]}
        body = preview(report).encode("utf-8")
    target = _report_url() if url is None else url.strip()
    if not target:
        return _save(report, "no inbox is set (support.report_url is empty)")
    if not target.startswith("https://"):
        return _save(report, "the inbox address is not https")
    if not _within_rate(time.time() if now is None else now):
        return _save(report, f"more than {MAX_PER_HOUR} reports in an hour")
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False, verify=shared_ssl_context()) as client:
            resp = client.post(target, content=body, headers={"Content-Type": "application/json",
                                                               "User-Agent": "kazma-problem-report"})
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except (httpx.HTTPError, ValueError) as exc:
        return _save(report, f"the inbox did not answer ({type(exc).__name__})")
    if resp.status_code == 200 and isinstance(data, dict) and data.get("id"):
        logger.info("[support] Problem report sent: %s", data["id"])
        return SendResult(sent=True, report_id=str(data["id"]))
    return _save(report, f"the inbox answered HTTP {resp.status_code} {str(data.get('error') or '')[:80]}".strip())


def describe(result: SendResult) -> str:
    """One sentence for the person, in English (surfaces word it themselves)."""
    if result.sent:
        return f"Sent. Your report's number is {result.report_id}: mention it if you write to us."
    return f"Not sent ({result.why}). It is saved at {result.saved}: you can attach it to a GitHub issue."

