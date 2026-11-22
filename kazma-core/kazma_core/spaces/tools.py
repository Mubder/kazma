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
* another host space (the shared workspace of 4.11) has none yet: its own
  project folder comes with S1-34, and until then its work would run in the
  owner's folder;
* work with no space bound, or a space whose kind cannot be read, has none.

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
        return _none(
            "a shared space with a project folder has no host tools until it has "
            "its own folder (plan S1-34)"
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
        return None
    return (
        f"Tool '{name}' does not exist in this space ({allowed.why_not}). "
        "It touches the machine Kazma runs on; tell the user what you would have "
        "used it for instead."
    )


def tool_grants() -> Any:
    """The bound person's features (S2-11), or None when no rule applies;
    nothing when they cannot be read (never guessed)."""
    from kazma_core.spaces import features, identity

    try:
        return features.grants_here()
    except identity.IdentityUnavailable:
        bound = current_scope()
        return features.Grants(bound.asker if bound else "", frozenset())


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
