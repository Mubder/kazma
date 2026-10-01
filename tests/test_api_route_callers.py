"""Every /api route has a caller, or a stated reason it has none (AUD-015).

"What the owner can change has a control" (AGENTS.md, UI conventions): a
route that changes something the owner decides gets a control on its page,
or a reason it has none. The audit of 2026-09-30 found 55 /api routes that no
page, TUI, CLI or script calls. Some were fine (OAuth redirects land on
them), some were a documented API, three were dead stubs -- removed -- and
one was a Settings backup the page could make but never restore.

This gate keeps the inventory honest: each route with no caller is declared
below with its reason, and a declaration goes stale (and fails) the day the
route gains a caller or disappears.

A caller is a string literal in a page script, a template, the TUI, the CLI
or a script. Server-side Python does not count: its strings are error texts
and log lines ("POST /api/workspace/select first") -- one of those once made
a dead route look called.

Since 2026-10-01 each METHOD of a route needs a caller with that method too
(read from the call around the literal: ``fetch``'s ``method:``, htmx's
``hx-post``, a Python client's ``.delete(``...). Path matching alone let
``DELETE /api/settings/{key:path}`` -- it deleted any setting by name --
pass as "called" because the pages called other ``/api/settings/...`` paths,
and six more uncalled methods hid the same way.

Also since 2026-10-01, a prefix built by concatenation
(``'/api/research/' + id``) reaches a route only where the route's next
segment is a parameter, or a word the same file quotes (``'approve'`` for
``'/api/x/reply/' + action``). Any route under the prefix used to count:
``/api/research/eval`` looked called through ``'/api/research/' + id``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_API_ROUTES_DOC = "docs/docs/reference/api-routes.md"
_DOCS_API = f"the published Document API ({_API_ROUTES_DOC})"
_DIVISIONS = (
    "division authorization -- active only with KAZMA_DIVISION or "
    "agent.division set; its requests are decided over the API"
)
_TRIAGE = "vulnerability-report triage for operators; reports are submitted from the page"

#: Routes no page or client calls, each with the reason it has no control.
NOT_CALLED_BY_A_PAGE: dict[str, str] = {
    # ── reached from outside the product's own client code ──────────────
    "/api/email/oauth/gmail/callback": "Google's OAuth redirect lands here",
    "/api/email/oauth/microsoft/callback": "Microsoft's OAuth redirect lands here",
    "/api/github/oauth/callback": "GitHub's OAuth redirect lands here",
    "/api/auth/oidc/callback": "the OIDC identity provider's sign-in redirect lands here",
    "/api/calendar/oauth/google/start": (
        "the sign-in as a link (302 to Google, listed in api-routes.md); the "
        "calendar card uses /oauth/google/start.json"
    ),
    "/api/chat/files/{file_id}": (
        "linked from chat replies: send_file and generate_image write this URL "
        "into the Markdown they hand the model (AGENTS §16 B2)"
    ),
    "/api/metrics": "a Prometheus scrape target (text format)",
    # ── the published Document API ──────────────────────────────────────
    "/api/documents/import": _DOCS_API,
    "/api/documents/generate": _DOCS_API,
    "/api/documents/merge": _DOCS_API,
    "/api/documents/{document_id}/fill-form": _DOCS_API,
    "/api/documents/{document_id}/artifacts": _DOCS_API,
    "/api/documents/{document_id}/versions": _DOCS_API,
    "/api/documents/search": _DOCS_API,
    "/api/documents/ops/retention": _DOCS_API,
    # The page archives with POST .../delete; the REST spelling of the same
    # call (one implementation) is part of the documented API.
    "DELETE /api/documents/{document_id}": _DOCS_API,
    "/api/documents/jobs/{job_id}": (
        "job status for a client that polls; the page streams "
        "/jobs/{job_id}/events"
    ),
    # ── mail and calendar ────────────────────────────────────────────────
    "/api/email/presets": (
        "provider server presets for API clients (guide/email-integration.md)"
    ),
    "/api/email/protocol/disconnect": (
        "documented (guide/email-integration.md); the page disconnects the same "
        "accounts with /gmail/disconnect and /oauth/microsoft/disconnect, which "
        "call the same disconnect_protocol"
    ),
    # ── the IDE service over HTTP (AGENTS §10) ──────────────────────────
    "/api/ide/codebase": "the IdeService codebase read, documented in products/ide.md",
    "/api/ide/list": (
        "IdeService's directory listing over HTTP; the IDE and Workspace pages "
        "list files with /api/workspace/files"
    ),
    "/api/ide/apply_patch": (
        "IdeService's patch over HTTP, HITL-gated in the tool registry (AGENTS "
        "§10B); the web editor saves whole files with /api/ide/write"
    ),
    "/api/ide/apply_patch_set": (
        "IdeService's multi-file patch and verify run over HTTP, HITL-gated "
        "(AGENTS §10B); the web editor saves whole files with /api/ide/write"
    ),
    # ── workspace and GitHub ────────────────────────────────────────────
    "/api/workspace/select": (
        "activates a folder through WorkspaceStore, the binding source (AGENTS "
        "§10A); the page switches with /api/workspaces/switch"
    ),
    "/api/workspace/tree": (
        "a directory tree of the active folder; the pages list it with "
        "/api/workspace/files"
    ),
    "/api/workspace/recent": "recently changed files of the active folder, for API clients",
    "/api/github/branches": (
        "branch list for API clients; the GitHub panel shows commits, pull "
        "requests, issues, releases and workflows"
    ),
    # ── operator reports and diagnostics (read-only) ────────────────────
    "/api/security/hardening": (
        "the offline hardening report for operators (guide/security-and-safety.md)"
    ),
    "/api/security/deps": (
        "the skill-manifest dependency scan for operators (guide/security-and-safety.md)"
    ),
    "/api/system/debug/registry": (
        "the model registry's live state, masked, for an operator debugging a provider"
    ),
    "/api/system/config-paths": "where this install's settings, YAML and data live, for operators",
    "/api/telemetry/snapshot": (
        "one telemetry reading for monitors; the page streams /api/telemetry/stream"
    ),
    "/api/voice/status": "voice readiness for operators; Settings reads the voice settings",
    "/api/research/eval": (
        "the structural score of a report for API clients (guide/web-research.md, "
        "Eval API); the Research page shows each run's score from its session"
    ),
    "/api/agents": "the agent list as JSON; the Agents page reads /api/agents/status",
    "GET /api/kb/libraries/{library_id}": (
        "one library's record for API clients; the Knowledge page reads every "
        "library from GET /api/kb/libraries and changes one with PATCH/DELETE here"
    ),
    "/api/settings/models/options": (
        "the model options as one document; the pages read /api/models, "
        "/api/models/profiles and /api/models/saved"
    ),
    "/api/settings/memory/backends/rebuild/status": (
        "rebuild progress for API clients; the Embedder section shows it "
        "through its own status route"
    ),
    "/api/ollama/check": "a local Ollama probe for operators",
    "/api/ollama/pull": (
        "pulls a local model in the background for operators; models are "
        "otherwise pulled with `ollama pull`"
    ),
    "/api/ollama/pulls": "the pulls /api/ollama/pull started",
    # ── division authorization and disclosure triage ────────────────────
    "/api/divisions/status": _DIVISIONS,
    "/api/divisions/requests": _DIVISIONS,
    "/api/divisions/requests/{request_id}/approve": _DIVISIONS,
    "/api/divisions/requests/{request_id}/deny": _DIVISIONS,
    "/api/security/disclosure": _TRIAGE,
    "/api/security/disclosure/{report_id}/acknowledge": _TRIAGE,
    "/api/security/disclosure/{report_id}/status": _TRIAGE,
    # ── settings and push ────────────────────────────────────────────────
    "/api/push/unsubscribe": (
        "a browser's own unsubscribe; push is switched off with the operator's "
        "notification setting the client checks (/api/notifications/turn-complete), "
        "and a subscription that fails with 410 is pruned (kazma_ui/push.py)"
    ),
    # ── the memory graph API, retired with the V1 stack ─────────────────
    "/api/memory/graph": "retired: answers 410 naming /api/memory/v2/graph",
    "/api/memory/graph/stats": "retired: answers 410 naming /api/memory/v2/health",
    "/api/memory/graph/export": "retired: answers 410 naming /api/memory/v2/graph",
    "/api/memory/graph/search": "kept for old clients, repointed to V2 recall (api-routes.md)",
    "/api/memory/graph/clear": (
        "the operator's bulk fact invalidation (api-routes.md): confirm=true, "
        "tenant-scoped, bi-temporal so the history is kept"
    ),
}


# ── the callers ──────────────────────────────────────────────────────────

_LITERAL = re.compile(r"`([^`]*)`|'([^'\n]*)'|\"([^\"\n]*)\"")


def placeholders(text: str) -> str:
    """Replace each ``${...}`` / ``{...}`` (balanced, may hold calls) with X."""
    out: list[str] = []
    i = 0
    while i < len(text):
        dollar = text[i] == "$" and i + 1 < len(text) and text[i + 1] == "{"
        if text[i] == "{" or dollar:
            j, depth = i + (2 if dollar else 1), 1
            while j < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            out.append("X")
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


class Calls(set):
    """Call strings, and for each prefix ('/api/x/' + ...) the words quoted in
    the files that build it: the values its concatenation can take."""

    def __init__(self, items=(), words: dict[str, set[str]] | None = None) -> None:
        super().__init__(items)
        self.words: dict[str, set[str]] = words if words is not None else {}

    def merge(self, other: Calls) -> Calls:
        self |= other
        for prefix, ws in other.words.items():
            self.words.setdefault(prefix, set()).update(ws)
        return self


_QUOTED_WORD = re.compile(r"""(['"`])([A-Za-z][A-Za-z0-9_-]*)\1""")


def quoted_words(text: str) -> set[str]:
    """Every quoted single word in *text* ('approve', 'node'), also one
    inside another literal: ``@click="convAction('retry', c)"``."""
    return {m.group(2) for m in _QUOTED_WORD.finditer(text)}


def _page_words(script: Path, templates: dict[Path, str]) -> set[str]:
    """Words the templates that load *script* quote: a page's buttons pass
    the values its script concatenates (x_studio.html's 'retry')."""
    out: set[str] = set()
    for text in templates.values():
        if script.name in text:
            out |= quoted_words(text)
    return out


def _collect(collect, python_too: bool) -> Calls:
    """Merge *collect*(text[, python]) over every client file, each script's
    prefixes also taking the words of the templates that load it."""
    files = _client_files()
    templates = {
        f: f.read_text(encoding="utf-8", errors="replace") for f in files if f.suffix == ".html"
    }
    found = Calls()
    for f in files:
        text = templates.get(f) or f.read_text(encoding="utf-8", errors="replace")
        got = collect(text, f.suffix == ".py") if python_too else collect(text)
        if f.suffix == ".js" and got.words:
            extra = _page_words(f, templates)
            for prefix in got.words:
                got.words[prefix] |= extra
        found.merge(got)
    return found


def _prefix_words(found: set, text: str) -> dict[str, set[str]]:
    paths = {p if isinstance(p, str) else p[1] for p in found}
    prefixes = {p for p in paths if p.endswith("/")}
    if not prefixes:
        return {}
    words = quoted_words(text)
    return {p: set(words) for p in prefixes}


def calls_in(text: str) -> Calls:
    """Every /api path a source file's string literals name."""
    found: set[str] = set()
    for m in _LITERAL.finditer(text):
        literal = next(g for g in m.groups() if g is not None)
        k = literal.find("/api/")
        if k >= 0:
            found.add(re.split(r"[?#\s'\"]", placeholders(literal[k:]))[0])
    return Calls(found, _prefix_words(found, text))


def _client_files() -> list[Path]:
    """Pages and the out-of-process HTTP clients -- never server-side Python."""
    ui = REPO / "kazma-ui" / "kazma_ui"
    out = [*(ui / "static").rglob("*.js"), *(ui / "templates").rglob("*.html")]
    for pkg in ("kazma-tui", "kazma-cli", "scripts"):
        out += [
            p for p in (REPO / pkg).rglob("*.py")
            if "tests" not in p.parts and not p.parts[-2].endswith("_tests")
        ]
    return out


@pytest.fixture(scope="module")
def calls() -> Calls:
    return _collect(calls_in, python_too=False)


# ── the routes ───────────────────────────────────────────────────────────

def _all_routes(routes) -> list:
    out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        out.extend(_all_routes(inner.routes) if inner is not None else [route])
    return out


@pytest.fixture(scope="module")
def api_routes() -> set[str]:
    from kazma_ui.app import create_app

    return {
        p for r in _all_routes(create_app().routes)
        if (p := getattr(r, "path", "")).startswith("/api/")
    }


def _route_rx(path: str) -> re.Pattern:
    out = ""
    for part in re.split(r"(\{[^}]+\})", path):
        if part.startswith("{"):
            out += r".+" if ":path" in part else r"[^/]+"
        else:
            out += re.escape(part)
    return re.compile("^" + out + "/?$")


def is_called(path: str, calls: set[str]) -> bool:
    """A literal matches the route (X standing for a parameter), or is a
    prefix it is built from by concatenation ('/api/x/reply/' + action).

    A prefix reaches the route only where the route's next segment is a
    parameter or a word the prefix's files quote (when *calls* carries them,
    as ``Calls`` does): ``'/api/research/' + id`` is not ``/api/research/eval``.
    """
    rx = _route_rx(path)
    words = getattr(calls, "words", None)
    for call in calls:
        if rx.match(call.rstrip("/") or "/"):
            return True
        if call.endswith("/") and len(call) > len("/api/") and path.startswith(call):
            nxt = path[len(call):].split("/", 1)[0]
            if words is None or nxt.startswith("{") or nxt in words.get(call, ()):
                return True
    return False


# ── methods (2026-10-01) ─────────────────────────────────────────────────
#
# The path gate below reads paths. DELETE /api/settings/{key:path} -- it
# deleted any setting the path named -- looked called because a page called
# other /api/settings/... paths, and six more uncalled methods hid behind a
# called path the same way. So each METHOD of each route needs a caller with
# that method too, or a declaration: the path's own (every method), or
# "METHOD /path" (that method). A call whose method the code around it does
# not say counts for every method, so the method gate can only find what the
# path gate lets through.

_JS_CALL = re.compile(r"\b(fetch|kazmaSave|kazmaGetJson|EventSource|sendBeacon)\s*\(\s*$")
_JS_DEFAULT = {"fetch": "GET", "kazmaSave": "GET", "kazmaGetJson": "GET", "EventSource": "GET", "sendBeacon": "POST"}
_JS_METHOD = re.compile(r"""\bmethod\s*:\s*['"]([A-Za-z]+)['"]""")
_HTMX = re.compile(r"""\bhx-(get|post|put|patch|delete)\s*=\s*$""", re.I)
_LINK = re.compile(r"""\b(?:href|src)\s*=\s*$""", re.I)
_PY_VERB = re.compile(r"""\.(get|post|put|patch|delete)\(\s*[rRbBfFuU]{0,2}$""")
_PY_REQUEST = re.compile(
    r"""\b(?:_?request|_?api)\(\s*(?:\w+\s*,\s*)?['"](GET|POST|PUT|PATCH|DELETE)['"]\s*,\s*[rRbBfFuU]{0,2}$"""
)
_PY_METHOD = re.compile(r"""\bmethod\s*=\s*['"]([A-Za-z]+)['"]""")


def _rest_of_call(text: str, end: int) -> str:
    """From *end* to the parenthesis that closes the call around it."""
    depth = 0
    for i in range(end, min(len(text), end + 800)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return text[end:i]
            depth -= 1
    return text[end:end + 800]


def literal_method(text: str, start: int, end: int, python: bool) -> str:
    """The HTTP method of the call whose string literal spans
    ``text[start:end]``; "ANY" when the code around it does not say."""
    before = text[max(0, start - 160):start]
    if python:
        for rx in (_PY_REQUEST, _PY_VERB):
            m = rx.search(before)
            if m:
                return m.group(1).upper()
        m = _PY_METHOD.search(_rest_of_call(text, end))
        return m.group(1).upper() if m else "ANY"
    m = _HTMX.search(before)
    if m:
        return m.group(1).upper()
    if _LINK.search(before):
        return "GET"
    m = _JS_CALL.search(before)
    if m:
        option = _JS_METHOD.search(_rest_of_call(text, end))
        return option.group(1).upper() if option else _JS_DEFAULT[m.group(1)]
    return "ANY"


def method_calls_in(text: str, python: bool = False) -> Calls:
    """Every (method, /api path) a source file's string literals name."""
    found: set[tuple[str, str]] = set()
    for m in _LITERAL.finditer(text):
        literal = next(g for g in m.groups() if g is not None)
        k = literal.find("/api/")
        if k >= 0:
            path = re.split(r"[?#\s'\"]", placeholders(literal[k:]))[0]
            found.add((literal_method(text, m.start(), m.end(), python), path))
    return Calls(found, _prefix_words(found, text))


def is_called_with(method: str, path: str, calls: set[tuple[str, str]]) -> bool:
    paths = {p for m, p in calls if m in (method, "ANY")}
    words = getattr(calls, "words", None)
    return is_called(path, Calls(paths, words) if words is not None else paths)


@pytest.fixture(scope="module")
def method_calls() -> Calls:
    return _collect(method_calls_in, python_too=True)


@pytest.fixture(scope="module")
def api_route_methods() -> set[tuple[str, str]]:
    from kazma_ui.app import create_app

    return {
        (method, p)
        for r in _all_routes(create_app().routes)
        if (p := getattr(r, "path", "")).startswith("/api/")
        for method in (getattr(r, "methods", None) or ())
        if method not in ("HEAD", "OPTIONS")
    }


# ── the gate ─────────────────────────────────────────────────────────────

def test_every_api_route_has_a_caller_or_a_reason(api_routes, calls) -> None:
    missing = sorted(
        p for p in api_routes if not is_called(p, calls) and p not in NOT_CALLED_BY_A_PAGE
    )
    assert not missing, (
        "No page, TUI, CLI or script calls these routes. Give each a control "
        "on its page, or declare it in NOT_CALLED_BY_A_PAGE with the reason it "
        f"has none: {missing}"
    )


def test_no_declaration_is_stale(api_routes, calls) -> None:
    paths = [p for p in NOT_CALLED_BY_A_PAGE if " " not in p]
    gone = sorted(p for p in paths if p not in api_routes)
    assert not gone, f"declared routes the app no longer serves -- remove them: {gone}"
    called_now = sorted(p for p in paths if is_called(p, calls))
    assert not called_now, f"declared as uncalled but now called -- remove them: {called_now}"


def test_every_api_route_method_has_a_caller_or_a_reason(api_route_methods, method_calls) -> None:
    missing = sorted(
        f"{m} {p}" for m, p in api_route_methods
        if p not in NOT_CALLED_BY_A_PAGE
        and f"{m} {p}" not in NOT_CALLED_BY_A_PAGE
        and not is_called_with(m, p, method_calls)
    )
    assert not missing, (
        "No page, TUI, CLI or script calls these routes with this method (a "
        "call of another method on the same path is not one). Remove the "
        "route, give it a control, or declare 'METHOD /path' in "
        f"NOT_CALLED_BY_A_PAGE with the reason: {missing}"
    )


def test_no_method_declaration_is_stale(api_route_methods, method_calls) -> None:
    declared = [key.split(" ", 1) for key in NOT_CALLED_BY_A_PAGE if " " in key]
    gone = sorted(f"{m} {p}" for m, p in declared if (m, p) not in api_route_methods)
    assert not gone, f"declared methods the app no longer serves -- remove them: {gone}"
    called_now = sorted(f"{m} {p}" for m, p in declared if is_called_with(m, p, method_calls))
    assert not called_now, f"declared as uncalled but now called -- remove them: {called_now}"


def test_every_reason_says_something() -> None:
    thin = [p for p, why in NOT_CALLED_BY_A_PAGE.items() if len(why.split()) < 4]
    assert not thin, thin


# ── negative controls for the detector itself ───────────────────────────

def test_a_route_nothing_calls_is_flagged() -> None:
    assert not is_called("/api/documents/{document_id}/unindex", {"/api/documents/X/index"})


def test_a_template_expression_with_a_call_is_one_parameter() -> None:
    """The literal that made /unindex look uncalled: the old scan stopped at
    the '(' of encodeURIComponent."""
    src = "fetch(`/api/documents/${encodeURIComponent(this.doc.id)}/unindex`, {})"
    assert calls_in(src) == {"/api/documents/X/unindex"}
    assert is_called("/api/documents/{document_id}/unindex", calls_in(src))


def test_a_concatenation_prefix_counts() -> None:
    src = "const action = ok ? 'approve' : 'deny';" + chr(10) + "fetch('/api/x/reply/' + action, {method: 'POST'})"
    assert is_called("/api/x/reply/approve", calls_in(src))
    assert is_called("/api/x/reply/{reply_id}/notes", calls_in(src))
    assert not is_called("/api/x/replies", calls_in(src))


def test_a_word_inside_an_attribute_counts() -> None:
    """x_studio.html passes 'retry' to the script that builds the path."""
    assert quoted_words('@click="convAction(\'retry\', c)"') == {"retry"}


def test_a_prefix_reaches_a_word_only_its_file_quotes() -> None:
    """Negative control: /api/research/eval looked called through
    '/api/research/' + id, which can only reach /api/research/{task_id}."""
    src = "fetch('/api/research/' + encodeURIComponent(id) + '/export', {method: 'POST'})"
    assert not is_called("/api/research/eval", calls_in(src))
    assert is_called("/api/research/{task_id}/export", calls_in(src))
    assert not is_called_with("GET", "/api/research/eval", method_calls_in(src))
    # Without the words, the old reading: everything under the prefix.
    assert is_called("/api/research/eval", set(calls_in(src)))


def test_a_method_nothing_calls_is_flagged() -> None:
    """The route that deleted any setting: the Settings scripts called other
    /api/settings/... paths, all with other methods."""
    calls = method_calls_in(
        "fetch('/api/settings/mcp/' + encodeURIComponent(name) + '/test', { method: 'POST' });"
        "await window.kazmaSave('/api/settings/single', { method: 'PUT', body: {} });"
    )
    assert is_called("/api/settings/{key:path}", {p for _m, p in calls})
    assert not is_called_with("DELETE", "/api/settings/{key:path}", calls)
    assert is_called_with("PUT", "/api/settings/{key:path}", calls)


@pytest.mark.parametrize(("src", "method"), [
    ("fetch('/api/x')", "GET"),
    ("fetch(`/api/x/${encodeURIComponent(id)}`, { method: 'DELETE' })", "DELETE"),
    ("fetch('/api/x/' + id + '/run', {\n  method: \"POST\",\n})", "POST"),
    ("await window.kazmaSave('/api/x', { method: 'PATCH', body: {} })", "PATCH"),
    ("window.kazmaGetJson('/api/x')", "GET"),
    ("new EventSource('/api/x/stream')", "GET"),
    ("navigator.sendBeacon('/api/x', blob)", "POST"),
    ('<button hx-post="/api/x">', "POST"),
    ('<a href="/api/x/download">', "GET"),
    ("const u = '/api/x';\nfetch(u, { method: 'PUT' })", "ANY"),
])
def test_the_method_comes_from_the_call(src, method) -> None:
    assert {m for m, _p in method_calls_in(src)} == {method}


@pytest.mark.parametrize(("src", "method"), [
    ('client.get(f"{base}/api/x", headers=h)', "GET"),
    ('httpx.post(\n    "/api/x", json={})', "POST"),
    ('await _request(client, "DELETE", f"/api/x/{name}")', "DELETE"),
    ('await self._api("POST", "/api/x")', "POST"),
    ('client.request("PUT", "/api/x")', "PUT"),
    ('url = "/api/x"', "ANY"),
])
def test_the_method_comes_from_a_python_call(src, method) -> None:
    assert {m for m, _p in method_calls_in(src, python=True)} == {method}


def test_server_side_python_is_not_a_caller() -> None:
    """The error text that once made a dead route look called."""
    assert not any(
        p.is_relative_to(REPO / "kazma-ui" / "kazma_ui") and p.suffix == ".py"
        for p in _client_files()
    )
    assert not any(
        p.is_relative_to(REPO / "kazma-gateway") for p in _client_files()
    )
