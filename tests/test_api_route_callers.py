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
    "/api/agents": "the agent list as JSON; the Agents page reads /api/agents/status",
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


def calls_in(text: str) -> set[str]:
    """Every /api path a source file's string literals name."""
    found: set[str] = set()
    for m in _LITERAL.finditer(text):
        literal = next(g for g in m.groups() if g is not None)
        k = literal.find("/api/")
        if k >= 0:
            found.add(re.split(r"[?#\s'\"]", placeholders(literal[k:]))[0])
    return found


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
def calls() -> set[str]:
    found: set[str] = set()
    for f in _client_files():
        found |= calls_in(f.read_text(encoding="utf-8", errors="replace"))
    return found


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
    prefix it is built from by concatenation ('/api/x/reply/' + action)."""
    rx = _route_rx(path)
    for call in calls:
        if rx.match(call.rstrip("/") or "/"):
            return True
        if call.endswith("/") and len(call) > len("/api/") and path.startswith(call):
            return True
    return False


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
    gone = sorted(p for p in NOT_CALLED_BY_A_PAGE if p not in api_routes)
    assert not gone, f"declared routes the app no longer serves -- remove them: {gone}"
    called_now = sorted(p for p in NOT_CALLED_BY_A_PAGE if is_called(p, calls))
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
    src = "fetch('/api/x/reply/' + action, {method: 'POST'})"
    assert is_called("/api/x/reply/approve", calls_in(src))
    assert not is_called("/api/x/replies", calls_in(src))


def test_server_side_python_is_not_a_caller() -> None:
    """The error text that once made a dead route look called."""
    assert not any(
        p.is_relative_to(REPO / "kazma-ui" / "kazma_ui") and p.suffix == ".py"
        for p in _client_files()
    )
    assert not any(
        p.is_relative_to(REPO / "kazma-gateway") for p in _client_files()
    )
