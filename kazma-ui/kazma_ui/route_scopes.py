"""Whose data each route touches: one table for every route (Spaces S1-05).

Spaces puts several people on one Kazma. Each person works in a space, and
the plan's rule 4 asks for one answer to "is this route the install's, a
space's or a person's". This module is that answer. Every route of the built
app has one of four scopes:

``install``  About the install as a whole: updates, backups, providers and
             model keys, the chat-app bots, system settings, the guard,
             people and spaces administration, metrics, health details. It
             reads no space's content. Changing it is the admin's; whether a
             signed-in member may read one (the model list, the swarm's
             workers) stays the role tables' answer (``platform_rbac``, the
             swarm's ``route_roles``).
``space``    Reads or writes one space's data: chats, memory, documents,
             knowledge, scheduled jobs, swarm tasks, research, X, mail and
             calendar, files, approvals, runs, the workspace and IDE, Time
             Travel. The request names its space and the caller is a member.
``account``  About the signed-in person, whatever space they are in: their
             profile, password, sessions, API tokens, notifications and
             display preferences.
``open``     Answered before sign-in: the login page and routes, the health
             probes, static files, webhooks that carry their own signature,
             the web app manifest and service worker, sign-in callbacks.

This is a declaration. It changes no route's behaviour. Other items enforce
it: S1-06 binds the space for every ``space`` route and checks membership,
and S1-25 keeps the admin out of spaces they are not a member of.

How a route is classed: by what its handler reads and writes, never by its
name. A route that shows a space's content (words, titles, files, tool
arguments) is ``space``, even where today it shows every space's at once.
A route that sets both an install setting and something narrower is classed
by the more sensitive of the two (install, then space, then account); the
comment beside its rule says so.

The table is ordered and the first rule that matches decides, so a specific
rule stands before the general rule of its area. A new route that no rule
matches fails ``tests/test_route_scopes.py``, and so does a rule that
decides no route.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "ACCOUNT",
    "INSTALL",
    "OPEN",
    "ROUTE_SCOPES",
    "ROUTE_SCOPE_RULES",
    "SPACE",
    "Rule",
    "compile_rules",
    "deciding_rule",
    "sample_requests",
    "scope_of",
    "unclassed",
]

INSTALL = "install"
SPACE = "space"
ACCOUNT = "account"
OPEN = "open"

ROUTE_SCOPES = (INSTALL, SPACE, ACCOUNT, OPEN)

#: A rule's pattern, then its scope. A pattern is:
#:
#: * ``"/api/memory"``: that path and everything under it. A prefix ends at a
#:   path segment: it matches ``/api/memory/v2/x``, never ``/api/memoryx``.
#: * ``"/api/models$"``: that path alone (a trailing ``$``).
#: * ``("POST", "/api/x")``: either form, for one method only. Kept rare:
#:   only where the methods of one path differ in scope.
#:
#: A ``{name}`` segment in a pattern stands for any one segment.
Pattern = str | tuple[str, str]

ROUTE_SCOPE_RULES: tuple[tuple[Pattern, str], ...] = (
    # ── Before sign-in ────────────────────────────────────────────────────
    ("/$", OPEN),  # sends the browser to /chat
    ("/login$", OPEN),
    ("/static", OPEN),
    ("/favicon.ico$", OPEN),
    ("/sw.js$", OPEN),
    ("/manifest.webmanifest$", OPEN),
    ("/security.txt$", OPEN),
    ("/.well-known/security.txt$", OPEN),
    # The A2A agent card: other agents read it to find Kazma (404 while off).
    ("/.well-known/agent-card.json$", OPEN),
    # Links to the public guides on kazma.ai.
    ("/docs/guide", OPEN),
    ("/health$", OPEN),
    ("/health/live$", OPEN),
    ("/health/ready$", OPEN),
    ("/health/deep$", OPEN),
    ("/api/status$", OPEN),
    # Sign-in itself. Status and sign-out answer with or without a session
    # (the login page asks one, an expired session uses the other), so they
    # are open; what they say about a signed-in person is that person's own.
    ("/api/auth/login$", OPEN),
    ("/api/auth/logout$", OPEN),
    ("/api/auth/status$", OPEN),
    ("/api/auth/oidc/start$", OPEN),
    ("/api/auth/oidc/callback$", OPEN),
    # Each webhook checks the platform's own signature or secret.
    ("/api/webhooks", OPEN),
    # Sign-in callbacks the provider redirects the browser to. Each takes
    # one recorded attempt, bound to the browser that started it.
    ("/api/github/oauth/callback", OPEN),
    ("/api/email/oauth/gmail/callback", OPEN),
    ("/api/email/oauth/microsoft/callback", OPEN),
    # The MCP preset catalog: names and commands Kazma ships, no install data.
    ("/api/mcp/presets$", OPEN),

    # ── The signed-in person ──────────────────────────────────────────────
    ("/api/auth/me$", ACCOUNT),
    # The spaces the person is a member of (the space switcher reads it).
    ("/api/account", ACCOUNT),
    # Password, sessions and API tokens.
    ("/api/settings/account", ACCOUNT),
    # Theme, accent and keyboard shortcuts. One set is stored for the install
    # today; with spaces each person keeps their own (plan 4.6, Settings).
    ("/api/settings/appearance$", ACCOUNT),
    ("/api/settings/shortcuts", ACCOUNT),
    # Web Push and the "turn finished" notice are per account (plan 4.6).
    ("/api/push", ACCOUNT),
    ("/api/notifications/turn-complete$", ACCOUNT),
    # The bell and the open-problems list. Today one list for the install;
    # S1-32 makes each the person's own: their spaces' notices, and the
    # install's for the admin.
    ("/api/alerts", ACCOUNT),
    ("/api/problems", ACCOUNT),

    # ── The install ───────────────────────────────────────────────────────
    ("/api/settings", INSTALL),
    ("/settings$", INSTALL),
    ("/packages$", INSTALL),  # sends the browser to Settings -> Packages
    ("/setup$", INSTALL),
    ("/api/setup", INSTALL),
    ("/api/system", INSTALL),
    ("/api/backup", INSTALL),
    # People and spaces administration.
    ("/api/saas", INSTALL),
    ("/api/security", INSTALL),
    ("/api/divisions", INSTALL),
    # A problem report is the install's diagnostics, sent by the admin. The
    # chat excerpt it may carry is a chat of a space the admin is a member of.
    ("/api/support", INSTALL),
    # The chat-app bots, model providers and their keys.
    ("/api/gateway", INSTALL),
    ("/api/connectors", INSTALL),
    ("/api/providers", INSTALL),
    ("/api/provider", INSTALL),
    ("/api/models", INSTALL),
    ("/v1/models$", INSTALL),
    ("/api/ollama", INSTALL),
    # MCP servers and skills are set up once, by the admin (plan 4.8).
    ("/api/mcp", INSTALL),
    ("/mcp$", INSTALL),
    ("/api/skills", INSTALL),
    ("/skills$", INSTALL),
    # The machine and the process: load, counters, API schema.
    ("/api/telemetry", INSTALL),
    ("/api/metrics$", INSTALL),
    ("/metrics$", INSTALL),
    ("/health/details$", INSTALL),
    ("/health/activity$", INSTALL),
    ("/docs", INSTALL),
    ("/redoc$", INSTALL),
    ("/openapi.json$", INSTALL),
    # Spend, usage and answer-quality counts for the whole install.
    ("/api/dashboard", INSTALL),
    # Mounted only with KAZMA_CHAOS_ENABLED.
    ("/api/chaos", INSTALL),
    # A pipeline design is checked against the install's worker registry;
    # nothing is stored.
    ("/api/pipelines", INSTALL),
    ("/api/a2a/status$", INSTALL),
    # The memory engine's own maintenance: its task queue (ids, states and
    # errors, no content), reconsolidation and the benchmark.
    ("/api/memory/v2/queue", INSTALL),
    ("/api/memory/v2/reconsolidate$", INSTALL),
    ("/api/memory/v2/eval", INSTALL),
    # The document platform's engines, retention settings and store upkeep.
    ("/api/documents/health$", INSTALL),
    ("/api/documents/ops/readiness$", INSTALL),
    ("/api/documents/ops/retention$", INSTALL),
    ("/api/documents/ops/maintenance", INSTALL),
    # The acquisition services' own account: its balance, its readiness.
    ("/api/kb/services", INSTALL),
    ("/api/research/ready$", INSTALL),
    ("/api/research/acquisition/credits$", INSTALL),
    # The swarm's engine is one for the install: workers, templates,
    # breakers, the autoscaler, where results are sent. Listed one by one,
    # so a new swarm route is classed on purpose.
    ("/api/swarm/workers/{name}/logs$", SPACE),  # a worker's log quotes its tasks
    ("/api/swarm/workers", INSTALL),
    ("/api/swarm/templates", INSTALL),
    ("/api/swarm/circuit-breakers$", INSTALL),
    ("/api/swarm/autoscaler", INSTALL),
    ("/api/swarm/start$", INSTALL),
    ("/api/swarm/stop$", INSTALL),
    ("/api/swarm/models$", INSTALL),
    ("/api/swarm/output-target$", INSTALL),
    # The automatic first steps for stuck work: one setting for the install.
    ("/api/runs/runbooks", INSTALL),
    # Which voice services the install has, and how they are set.
    ("/api/voice/providers$", INSTALL),
    ("/api/voice/status$", INSTALL),
    ("/api/voice/voices$", INSTALL),
    ("/api/voice/stt-models$", INSTALL),
    ("/api/voice/livekit/status$", INSTALL),

    # ── A space ───────────────────────────────────────────────────────────
    # Chats, their approvals and their saved steps.
    ("/chat$", SPACE),
    ("/api/chat", SPACE),
    ("/ws/chat", SPACE),
    ("/api/approve", SPACE),
    ("/api/pending-approvals", SPACE),
    ("/replay$", SPACE),
    ("/api/replay", SPACE),
    # The saved steps of every chat, with each chat's title. An admin view of
    # the whole store today; with spaces it lists the space's own chats.
    ("/api/sessions", SPACE),
    # Memory, the lessons learned from a space's turns, imported history.
    ("/memory$", SPACE),
    ("/api/memory", SPACE),
    ("/api/commitment", SPACE),
    ("/api/import/history", SPACE),
    ("/documents$", SPACE),
    ("/api/documents", SPACE),
    ("/knowledge$", SPACE),
    ("/api/kb", SPACE),
    ("/scheduled$", SPACE),
    ("/api/scheduled", SPACE),
    # Swarm tasks and their approvals. The status lists each worker with the
    # task it is running.
    ("/swarm$", SPACE),
    ("/api/swarm/tasks", SPACE),
    ("/api/swarm/dispatch$", SPACE),
    ("/api/swarm/approvals", SPACE),
    ("/api/swarm/status$", SPACE),
    # Research sessions and reports. The acquisition status is the install's
    # allowance, and names one research job's report when asked by its id.
    ("/research$", SPACE),
    ("/api/research", SPACE),
    ("/api/runs", SPACE),
    # Connected accounts belong to the space that connected them (plan 4.6):
    # X today, mail, calendar and GitHub with spaces.
    ("/x$", SPACE),
    ("/api/x", SPACE),
    ("/api/email", SPACE),
    ("/api/calendar", SPACE),
    ("/api/github", SPACE),
    # The project folder of a host space: files, git, the IDE (plan 4.11).
    ("/workspace$", SPACE),
    ("/api/workspace", SPACE),
    ("/api/workspaces", SPACE),
    ("/api/git", SPACE),
    ("/api/bookmarks", SPACE),
    ("/ide$", SPACE),
    ("/api/ide", SPACE),
    # Speech in a chat: the person's words in, the reply read aloud.
    ("/api/voice/stt$", SPACE),
    ("/api/voice/tts$", SPACE),
    ("/api/voice/livekit/token$", SPACE),
    ("/ws/voice$", SPACE),
    # Another agent's request runs one read-only turn (A2A).
    ("/api/a2a$", SPACE),
    # Traces of tool and model calls: what each turn did, with its arguments.
    # One feed for the process today; with spaces, the space's own.
    ("/dashboard$", SPACE),
    ("/ws/dashboard$", SPACE),
    ("/agents$", SPACE),
    ("/api/agents", SPACE),
)


@dataclass(frozen=True)
class Rule:
    """One compiled row of the table."""

    pattern: Pattern
    scope: str
    method: str | None
    segments: tuple[str, ...]
    exact: bool

    def matches(self, method: str, segments: tuple[str, ...]) -> bool:
        if self.method is not None and self.method != method:
            return False
        if len(segments) < len(self.segments):
            return False
        if self.exact and len(segments) != len(self.segments):
            return False
        return all(
            want == have or _is_parameter(want)
            for want, have in zip(self.segments, segments)
        )


def _is_parameter(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _segments(path: str) -> tuple[str, ...]:
    """The path's segments: no query, no empty segment, no trailing slash."""
    return tuple(part for part in (path or "").split("?", 1)[0].split("/") if part)


def compile_rules(rules: Iterable[tuple[Pattern, str]]) -> tuple[Rule, ...]:
    """The table in the form :func:`scope_of` reads. Refuses a scope that is
    not one of :data:`ROUTE_SCOPES` and a pattern that is not a path."""
    compiled: list[Rule] = []
    for pattern, scope in rules:
        if scope not in ROUTE_SCOPES:
            raise ValueError(f"unknown scope {scope!r} for {pattern!r}")
        method, path = (None, pattern) if isinstance(pattern, str) else pattern
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"a pattern is a path that starts with '/': {pattern!r}")
        exact = path.endswith("$")
        segments = _segments(path[:-1] if exact else path)
        if not exact and not segments:
            raise ValueError("a prefix needs a segment: '/' alone is written '/$'")
        compiled.append(
            Rule(
                pattern=pattern,
                scope=scope,
                method=method.upper() if method else None,
                segments=segments,
                exact=exact,
            )
        )
    return tuple(compiled)


_RULES = compile_rules(ROUTE_SCOPE_RULES)


def deciding_rule(
    method: str, path: str, rules: tuple[Rule, ...] | None = None
) -> Rule | None:
    """The first rule that matches ``method path``, or None."""
    wanted = (method or "").upper()
    if wanted == "HEAD":
        wanted = "GET"
    segments = _segments(path)
    for rule in _RULES if rules is None else rules:
        if rule.matches(wanted, segments):
            return rule
    return None


def scope_of(
    method: str, path: str, rules: tuple[Rule, ...] | None = None
) -> str | None:
    """The scope of ``method path``: the first matching rule's, None when no
    rule matches.

    *path* is the route's own path as the router holds it
    (``/api/documents/{document_id}``) or a request's path. Code that
    enforces a scope asks with the matched route's path, so the answer is
    about the handler that will run.
    """
    rule = deciding_rule(method, path, rules)
    return rule.scope if rule is not None else None


def sample_requests(
    scope: str, rules: tuple[Rule, ...] | None = None
) -> list[tuple[str, str]]:
    """One ``(method, path)`` per rule of *scope*, for a test that drives the
    table: the rule's own path, a parameter filled with ``x1``, with GET
    where the rule names no method. Only a request its rule decides."""
    out: list[tuple[str, str]] = []
    for rule in _RULES if rules is None else rules:
        if rule.scope != scope:
            continue
        method = rule.method or "GET"
        path = "/" + "/".join("x1" if _is_parameter(s) else s for s in rule.segments)
        if deciding_rule(method, path, rules) is rule:
            out.append((method, path))
    return out


def unclassed(
    routes: Iterable[tuple[str, str]], rules: tuple[Rule, ...] | None = None
) -> list[str]:
    """The ``(method, path)`` pairs no rule matches, as ``"METHOD path"``."""
    return sorted(
        {
            f"{method.upper()} {path}"
            for method, path in routes
            if scope_of(method, path, rules) is None
        }
    )
