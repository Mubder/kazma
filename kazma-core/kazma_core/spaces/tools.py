"""Host tools exist only in a host space (Spaces S1-20, plan 4.8 and 4.11).

Every agent tool is in one group:

``development``  The feature "Development": the shell, git, the GitHub
                 tools, the project's code index and checks, the MCP servers
                 the admin set up, path grants, the machine's skill folders.
``admin``        The admin's tools: settings and vault, package and skill
                 installs, the machine's processes and logs, computer use,
                 the MCP test, the chat apps' senders (they send through the
                 install's bots, as the owner, until stage 3).
``space``        A space's own work: memory, documents, knowledge, schedules,
                 swarm, research, files in the space's folder, code in a
                 container, its connected accounts.
``outside``      The web, the browser, images and translation.

A tool that is in no group is a host tool of the ``development`` group
(:func:`_tool_group`): an MCP server's tool, or a tool added at run time.
``tests/test_host_tools_absent.py`` fails on a registered tool with no group.

**Nothing here acts unless Spaces is enforced.** Then, for the work bound to
a space (:func:`host_allowance`):

* a sandboxed space has no host tool at all;
* the owner's space has the admin's tools, and Development's while the
  owner's Development switch is on (``identity.holds_development``, on at
  first: N28);
* another host space (a shared space with a project folder, 4.11 and
  S2-12) has Development for each person who holds it, the admin too
  (``identity.holds_development``: on at first for the admin, N28; turned on
  per person on People, N1), working in that space's own folder (S1-34);
  the admin's own tools are never there;
* work with no space bound, or a space whose kind cannot be read, has none.

A person other than the admin who holds Development has their always-ask
actions and folder grants in a shared space with a project folder approved
by the admin (decision N21): the card is made and goes to the admin
(``spaces.approvals``, S2-05). Until S2-05 such a call was refused here.

**A person other than the admin runs no project code on the host** (the
S2-12 review; the owner's decision 3, plan 4.8: members' code runs only in a
container, S4-05). Their Development there is the tools of
:data:`MEMBER_DEVELOPMENT`, each declared with why it starts no host process
that runs the project's code: the shell, the test runner, the linter and
formatter, Kazma's own metrics, and every MCP server the admin set up are
refused (a server may be opted in per space later). Their git runs with
hooks and the repository's programs off (``security.git_auth.git_command_env``)
and they write nothing under ``.git``. The filesystem MCP server is refused
in a shared space for everyone (N27). The admin's own Development is
unchanged.

The rule is applied where a model is offered tools (``skills.switches.offered``,
a swarm worker's list) and again where a tool is called
(``LocalToolRegistry._execute_inner``, the MCP branch of
``UnifiedToolExecutor``): hiding a tool from the list alone is never enough.
The person's features (S2-11, ``spaces.features``) are asked at the same
places, by :func:`tool_refusal`: a tool of a feature that is off for the
person is neither offered nor run.
The host pages and routes follow the same rule (the tenant middleware,
``kazma_ui.route_scopes.is_development_route``).

Blocking (the identity store): async callers use a thread.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, current_scope, spaces_enforced

__all__ = [
    "ADMIN",
    "DEVELOPMENT",
    "GROUPS",
    "MEMBER_DEVELOPMENT",
    "OUTSIDE",
    "SPACE",
    "HostAllowance",
    "development_here",
    "forget_allowance",
    "host_allowance",
    "offered_here",
    "remember_allowance",
    "tool_grants",
    "tool_refusal",
]

#: The feature "Development" (plan 4.8): host tools of a host space.
DEVELOPMENT = frozenset({
    "shell_exec",
    "git_status", "git_log", "git_diff", "git_commit", "git_checkout", "git_merge",
    "git_pull", "git_push",
    "github_list_issues", "github_create_issue", "github_comment_issue",
    "github_create_pr", "github_merge_pr",
    "codebase_search", "codebase_status", "repository_metrics",
    "lint_code", "format_code", "run_unit_tests",
    "mcp_list_resources", "mcp_read_resource", "mcp_list_prompts", "mcp_get_prompt",
    "request_path_access",
    "list_agent_skills", "search_agent_skills", "activate_skill",
})

#: The Development tools a person other than the admin may use in a shared
#: space with a project folder, each with why it starts no host process that
#: runs the project's code (the S2-12 review, decision 3). Every other
#: Development tool -- and every MCP server's -- is refused to them until
#: members' code runs in a container (S4-05).
MEMBER_DEVELOPMENT: dict[str, str] = {
    "git_status": "git with hooks and the repository's programs off (git_auth.git_command_env)",
    "git_log": "git with hooks and the repository's programs off",
    "git_diff": "git with hooks and the repository's programs off",
    "git_commit": "git with hooks and the repository's programs off, --no-verify",
    "git_checkout": "git with hooks and the repository's programs off",
    "git_merge": "git with hooks and the repository's programs off",
    "git_pull": "git with hooks and the repository's programs off, none of the machine's credentials",
    "git_push": "git with hooks and the repository's programs off, --no-verify, none of the machine's credentials",
    "github_list_issues": "GitHub's API with the space's own connection",
    "github_create_issue": "GitHub's API with the space's own connection",
    "github_comment_issue": "GitHub's API with the space's own connection",
    "github_create_pr": "GitHub's API with the space's own connection",
    "github_merge_pr": "GitHub's API with the space's own connection",
    "codebase_search": "Kazma's own code index, read in Kazma's process",
    "codebase_status": "Kazma's own code index, read in Kazma's process",
    "request_path_access": "a folder grant, which the admin approves (N21, spaces.approvals)",
    "list_agent_skills": "lists skill folders; runs nothing",
    "search_agent_skills": "searches the skills marketplace; runs nothing",
    "activate_skill": "adds a skill's text to the turn; runs nothing",
}

#: The admin's own tools (plan 4.11: "Admin tools are not Development"): in
#: the admin's personal space only, whatever the Development switch says.
ADMIN = frozenset({
    "config_read", "config_save",
    "vault_store", "vault_retrieve", "vault_list", "vault_delete",
    "install_agent_skill", "uninstall_agent_skill",
    "install_python_packages", "install_npm_packages", "check_environment",
    "get_system_stats", "list_active_processes", "read_system_logs",
    "computer_use", "mcp_test_server",
    "send_message", "dispatch_notification", "send_approval_request",
})

#: Outside tools (plan 4.8): the web, the browser, images, translation.
OUTSIDE = frozenset({
    "web_search", "web_search_duckduckgo", "read_url", "read_url_to_file",
    "crawl_page", "crawl_site",
    "browser_navigate", "browser_click", "browser_fill_form", "browser_extract_text",
    "browser_eval_js", "browser_screenshot",
    "generate_image", "generate_ui_mockup", "download_image", "analyze_image",
    "arabic_translate",
})

#: A space's own tools.
SPACE = frozenset({
    "analyze_local_image", "cancel_research_session", "cancel_scheduled",
    "check_swarm_task", "context_info", "convert_document", "create_event",
    "critique_synthesis_gaps", "current_datetime", "delete_event", "digest_research_file",
    "discard_proposal", "dispatch_swarm", "document_cancel", "document_convert",
    "document_import", "document_index", "document_read", "document_redact",
    "document_search", "document_status", "edit_scheduled", "email_accounts",
    "email_analyze", "email_categorize", "email_delete", "email_get", "email_list",
    "email_send", "execute_db_query", "export_session", "file_append",
    "file_apply_patch", "file_apply_patch_set", "file_delete", "file_list", "file_read",
    "file_search", "file_write", "find_free_slots", "generate_docx",
    "generate_markdown_doc", "generate_pdf", "generate_pptx", "generate_xlsx",
    "handoff_to_worker", "hijri_convert", "insert_diacritics", "inspect_db_schema",
    "knowledge_create_library", "knowledge_ingest_site", "knowledge_ingest_url",
    "knowledge_list_libraries", "knowledge_search", "list_events", "list_proposals",
    "list_research_chunks", "list_research_papers", "list_runs", "list_scheduled",
    "memory_admin", "memory_delete_entity", "memory_forget_mentions", "memory_invalidate",
    "memory_link_entities", "memory_list_beliefs", "memory_list_entities",
    "memory_merge_entities", "memory_purge_empty_entities", "memory_read", "memory_list", "memory_search",
    "memory_store",
    "ocr_document", "parse_document", "pdf_fill_form", "pdf_info", "pdf_merge",
    "pdf_redact", "pdf_split", "plan_research_queries", "python_exec", "read_document",
    "read_research_chunk", "research_readiness", "research_session_status",
    "run_research_pipeline", "run_status", "save_proposal", "schedule_task", "send_file",
    "spawn_agent", "spawn_agents", "sqlite_query", "start_deep_research",
    "summarize_research_file", "synthesize_from_digests", "task_ledger_update",
    "update_event", "update_scratchpad",
    "x_account", "x_api_act", "x_api_operations", "x_api_read", "x_cancel_scheduled_post",
    "x_delete_post", "x_download_media", "x_draft_posts", "x_list_scheduled", "x_post",
    "x_schedule_post", "x_status", "x_upload_media",
})

#: group -> its tools.
GROUPS: dict[str, frozenset[str]] = {
    "development": DEVELOPMENT, "admin": ADMIN, "space": SPACE, "outside": OUTSIDE,
}

_HOST_GROUPS = ("development", "admin")


def _tool_group(name: str) -> str:
    """The group of the tool *name*; a tool in no group (an MCP server's, a
    tool added at run time) is ``development``: host, never guessed safe."""
    for group, names in GROUPS.items():
        if name in names:
            return group
    return "development"


@dataclass(frozen=True)
class HostAllowance:
    """Which host groups the bound work may use, and why not when not."""

    development: bool
    admin: bool
    why_not: str = ""
    #: Why the admin's tools are not here, when that differs from *why_not*
    #: (a shared space with a project folder: Development, never them).
    why_not_admin: str = ""
    #: A shared space with a project folder (S2-12): no filesystem MCP
    #: server (N27), whoever asks.
    shared: bool = False
    #: A person other than the admin holding Development there: only
    #: :data:`MEMBER_DEVELOPMENT`, no project code on the host (the review).
    member: bool = False

    def allows(self, group: str) -> bool:
        if group == "development":
            return self.development
        if group == "admin":
            return self.admin
        return True


_EVERYTHING = HostAllowance(development=True, admin=True)

#: The allowance the tenant middleware worked out for the page being drawn,
#: so the sidebar can leave out the Workspace and IDE entries with no read.
_page_allowance: contextvars.ContextVar[HostAllowance | None] = contextvars.ContextVar(
    "kazma_host_allowance", default=None
)


def _none(why: str) -> HostAllowance:
    return HostAllowance(development=False, admin=False, why_not=why)


def host_allowance() -> HostAllowance:
    """The host groups the work bound now may use (see the module's rules)."""
    if not spaces_enforced():
        return _EVERYTHING
    bound = current_scope()
    if bound is None:
        return _none("no space is bound to this work")
    from kazma_core.spaces import files as space_files
    from kazma_core.spaces import identity

    try:
        kind = space_files.space_kind(bound.space_id)
    except space_files.SpaceKindUnknown as exc:
        return _none(str(exc))
    if kind != "host":
        return _none("host tools do not exist in this space")
    if bound.space_id != OWNER_SPACE_ID:
        # A shared space with a project folder (S2-12): Development for the
        # people who hold it, in the space's own folder; the admin's own
        # tools stay in the admin's personal space.
        try:
            development = identity.holds_development(bound.asker)
            admin = identity.is_admin_account(bound.asker)
        except (identity.IdentityUnavailable, identity.IdentityError) as exc:
            return _none(f"the account could not be read: {exc}")
        return HostAllowance(
            development=development,
            admin=False,
            why_not="" if development else "Development is not switched on for this person",
            why_not_admin="the admin's own tools are only in the admin's personal space",
            shared=True,
            member=development and not admin,
        )
    try:
        admin = identity.is_admin_account(bound.asker)
        development = admin and identity.holds_development(bound.asker)
    except (identity.IdentityUnavailable, identity.IdentityError) as exc:
        return _none(f"the account could not be read: {exc}")
    if not admin:
        return _none("host tools are the admin's")
    return HostAllowance(
        development=development,
        admin=True,
        why_not="" if development else "Development is switched off for this account",
    )


def _host_refusal(name: str, allowance: HostAllowance | None = None) -> str | None:
    """Why the tool *name* is neither offered nor run in the bound space, or
    None when it may be. Only host tools are ever refused here."""
    group = _tool_group(name)
    if group not in _HOST_GROUPS:
        return None
    allowed = allowance if allowance is not None else host_allowance()
    if allowed.allows(group):
        if allowed.member and name not in MEMBER_DEVELOPMENT:
            return (f"Tool '{name}' does not exist for this person here: it runs the project's code "
                    "or a server on the machine Kazma runs on, which only the admin may until a "
                    "person's code runs in a container. Tell the user what you would have used it for.")
        if allowed.shared and _filesystem_server_tool(name):
            return (f"Tool '{name}' does not exist in this space: the filesystem server serves the "
                    "admin's own space (N27). Use Kazma's own file tools here.")
        return None
    why = allowed.why_not_admin if group == "admin" and allowed.why_not_admin else allowed.why_not
    return (
        f"Tool '{name}' does not exist in this space ({why}). "
        "It touches the machine Kazma runs on; tell the user what you would have "
        "used it for instead."
    )


def _filesystem_server_tool(name: str) -> bool:
    """True for a tool of the filesystem MCP server -- the one Kazma ships,
    or any server bound to the machine's workspace (``workspace_bound``) --
    named ``mcp__<server>__...``."""
    if not name.startswith("mcp__"):
        return False
    server = name[len("mcp__"):].split("__", 1)[0]
    if server == "filesystem":
        return True
    from kazma_core.mcp_servers_store import list_mcp_servers

    try:
        servers = list_mcp_servers()
    except (OSError, ValueError, RuntimeError):
        return True  # a store that cannot say: refused, never guessed safe
    return any(str(s.get("name")) == server and s.get("workspace_bound") for s in servers)


def tool_grants() -> Any:
    """The bound person's features (S2-11), or None when no rule applies;
    when they cannot be read, grants marked ``unreadable``: every tool is
    refused with a line saying why, an always-on one included (fail closed,
    the S2-01 second review: an empty grant let a switched-off chat or
    memory tool run)."""
    from kazma_core.spaces import features, identity

    try:
        return features.grants_here()
    except identity.IdentityUnavailable:
        bound = current_scope()
        return features.Grants(bound.asker if bound else "", frozenset(), unreadable=True)


def tool_refusal(name: str, allowance: HostAllowance | None = None, grants: Any = ...) -> str | None:
    """Why the tool *name* is neither offered nor run for the work bound now:
    a host tool the space may not have (S1-20), or a tool of a feature that
    is off for the person (S2-11). None when it may be. Blocking."""
    host = _host_refusal(name, allowance)
    if host is not None:
        return host
    if not spaces_enforced():
        return None
    from kazma_core.spaces import features

    return features.tool_refusal(name, tool_grants() if grants is ... else grants)


def offered_here(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*definitions* without the host tools the bound space may not have and
    the tools of features the person does not have. One identity read for
    the whole list (a thread for async callers)."""
    if not spaces_enforced():
        return list(definitions or [])
    allowance = host_allowance()
    grants = tool_grants()
    return [
        d for d in definitions or []
        if tool_refusal(str(((d or {}).get("function") or {}).get("name") or ""), allowance, grants) is None
    ]


def remember_allowance(allowance: HostAllowance) -> contextvars.Token:
    """Keep *allowance* for the page being drawn (the tenant middleware)."""
    return _page_allowance.set(allowance)


def forget_allowance(token: contextvars.Token) -> None:
    _page_allowance.reset(token)


def development_here() -> bool:
    """Whether the page being drawn offers the Development pages (Workspace,
    IDE). No I/O: as before Spaces while it is not enforced; once enforced,
    what the tenant middleware worked out, and nothing it did not."""
    if not spaces_enforced():
        return True
    allowance = _page_allowance.get()
    return bool(allowance is not None and allowance.development)
