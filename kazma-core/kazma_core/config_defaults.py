"""Shipped defaults that changed, and whether installs already running follow.

``ConfigStore.reconcile_from_yaml`` copies merged shipped/local YAML into
the settings database the first time Kazma boots, and a stored value wins
from then on. So a default changed in a later release never reached an
install that already had the old one -- whether its owner had chosen that
value or never looked at it. Found 2026-09-29: the owner asked for fewer
restart messages; the default could change, and the live install would
still have sent all three, from the copy its first boot stored.

A changed default is declared here, once:

- ``RETIRED_DEFAULTS``: installs follow. At boot, a stored value equal to
  one the key used to ship with is replaced by today's -- once per entry
  (``RETIRED_APPLIED_KEY`` records it), so an owner who picks the old value
  again afterwards keeps it.
- ``NEW_INSTALLS_ONLY``: the new value is for new installs; a stored copy
  of the old one stays. Say why.

Retirements inspect pre-existing rows against raw shipped defaults before
missing keys are seeded. A deliberate fresh local override equal to an old
default is therefore kept; local overrides do not define product retirements.

``tests/test_shipped_config_defaults.py`` holds a snapshot of the values
``kazma.yaml`` ships and fails on a change declared in neither;
``scripts/shipped_defaults.py --write`` refreshes the snapshot once it is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = [
    "NEW_INSTALLS_ONLY",
    "RETIRED_APPLIED_KEY",
    "RETIRED_DEFAULTS",
    "RetiredDefault",
    "entry_id",
    "same_value",
]

#: The entries already applied to this install's settings (a sorted list of
#: ``entry_id``s). Internal.
RETIRED_APPLIED_KEY = "system.config.retired_defaults"


@dataclass(frozen=True)
class RetiredDefault:
    """A settings key whose shipped default changed; installs follow."""

    #: The settings key (dotted, as ConfigStore stores it).
    key: str
    #: Every value the key shipped with before (exact, as stored).
    old: tuple[Any, ...]
    #: The date of the change (YYYY-MM-DD); with ``key``, the entry's id.
    since: str
    #: Why it changed, for the log line and the reader.
    why: str
    #: The key itself is gone from kazma.yaml: a stored copy of an old value
    #: is deleted (``ConfigStore.apply_retired_defaults``), so nothing is left
    #: that reads as a setting. Declared, so a mistyped key is still caught
    #: (tests/test_shipped_config_defaults.py).
    removed: bool = False


RETIRED_DEFAULTS: tuple[RetiredDefault, ...] = (
    RetiredDefault(
        key="mcp.servers",
        old=([
            {
                "auth": {},
                "command": [
                    "npx", "-y", "@modelcontextprotocol/server-filesystem",
                    "${KAZMA_ACTIVE_WORKSPACE}",
                ],
                "enabled": True,
                "name": "filesystem",
                "tool_count": 0,
                "tools": [],
                "transport": "stdio",
                "trust": "approval_required",
                "workspace_bound": True,
            },
        ],),
        since="2026-11-11",
        why=(
            "the shipped filesystem server ran npx with no version, so every start ran "
            "whatever npm served that day; it is pinned to the catalog's version. A list "
            "the owner changed is pinned where it is read (mcp_servers_store._canonical_server)"
        ),
    ),
    RetiredDefault(
        key="appearance.accent_color",
        removed=True,
        old=("#5e6ad2",),
        since="2026-11-09",
        why=(
            "the indigo the v5 palette retired was the code's default accent; Settings "
            "applied it as chosen and any Appearance save stored it, so a stored copy is "
            "the default nobody picked (it is on no swatch): the theme's own accent applies"
        ),
    ),
    RetiredDefault(
        key="llm.input_cost_per_1m",
        removed=True,
        old=(0.15,),
        since="2026-11-08",
        why=(
            "read by nothing: a price is the model's own (kazma_core.model_prices), "
            "and this was gpt-4o-mini's, applied to every model"
        ),
    ),
    RetiredDefault(
        key="llm.output_cost_per_1m",
        removed=True,
        old=(0.6,),
        since="2026-11-08",
        why=(
            "read by nothing: a price is the model's own (kazma_core.model_prices), "
            "and this was gpt-4o-mini's, applied to every model"
        ),
    ),
    RetiredDefault(
        key="models.default",
        removed=True,
        old=("gpt-4o-mini",),
        since="2026-11-08",
        why="read only to add gpt-4o-mini to the model lists of installs with no OpenAI key",
    ),
    RetiredDefault(
        key="models.fallback",
        removed=True,
        old=("gpt-4o-mini",),
        since="2026-11-08",
        why="read by nothing: the backup models are agent.nonstop.failover.chain",
    ),
    RetiredDefault(
        key="models.router",
        removed=True,
        old=("kazma", "litellm"),
        since="2026-11-08",
        why="read by nothing",
    ),
    RetiredDefault(
        key="agent.version",
        old=("0.21.1",),
        since="2026-11-11",
        why=(
            "the version is the release's, not a setting: a stored value is the "
            "shipped one of its day, and follows the release (0.21.2)"
        ),
    ),
    RetiredDefault(
        key="agent.version",
        old=("0.21.0",),
        since="2026-11-08",
        why=(
            "the version is the release's, not a setting: a stored value is the "
            "shipped one of its day, and follows the release (0.21.1)"
        ),
    ),
    RetiredDefault(
        key="agent.version",
        old=("0.20.0",),
        since="2026-11-07",
        why=(
            "the version is the release's, not a setting: a stored value is the "
            "shipped one of its day, and follows the release (0.21.0)"
        ),
    ),
    RetiredDefault(
        key="agent.version",
        old=("0.11.0",),
        since="2026-11-06",
        why=(
            "the version is the release's, not a setting: a stored value is the "
            "shipped one of its day, and follows the release (0.20.0)"
        ),
    ),
    RetiredDefault(
        key="notifications.lifecycle.events",
        old=(["starting", "started", "shutting_down", "startup_failed"],),
        since="2026-09-29",
        why=(
            "a reload sent three messages; one start card now says how long "
            "Kazma was down and whether each chat app connected"
        ),
    ),
    RetiredDefault(
        key="gateway.rate_limits.discord",
        old=(5,),
        since="2026-10-01",
        why=(
            "the inbound flood guard (messages per person per minute) shipped "
            "Discord's own send limit; 30, like Telegram"
        ),
    ),
    RetiredDefault(
        key="gateway.rate_limits.slack",
        old=(1,),
        since="2026-10-01",
        why=(
            "the inbound flood guard shipped Slack's own send limit: one Slack "
            "message a minute, the rest left unanswered; 30, like Telegram"
        ),
    ),
    RetiredDefault(
        key="memory.max_context_tokens",
        old=(128000,),
        since="2026-10-18",
        why=(
            "the context is the model's window (the owner's decision): at "
            "128,000 with a 24,000-token trim a long chat lost its goal; a "
            "model Kazma knows to be smaller keeps its own window"
        ),
    ),
    RetiredDefault(
        key="safety.require_approval_for",
        old=([
            "browser_click",
            "browser_eval_js",
            "browser_fill_form",
            "browser_navigate",
            "cancel_scheduled",
            "code_exec",
            "computer_use",
            "config_save",
            "create_event",
            "delete_event",
            "dispatch_notification",
            "document_cancel",
            "document_redact",
            "edit_scheduled",
            "email_categorize",
            "email_delete",
            "email_send",
            "file_append",
            "file_apply_patch",
            "file_apply_patch_set",
            "file_delete",
            "file_write",
            "git_checkout",
            "git_commit",
            "git_merge",
            "git_pull",
            "git_push",
            "github_comment_issue",
            "github_create_issue",
            "github_create_pr",
            "github_merge_pr",
            "install_agent_skill",
            "install_npm_packages",
            "install_python_packages",
            "memory_admin",
            "memory_delete_entity",
            "memory_invalidate",
            "memory_merge_entities",
            "memory_purge_empty_entities",
            "pdf_fill_form",
            "pdf_redact",
            "python_exec",
            "request_path_access",
            "run_tests",
            "run_unit_tests",
            "schedule_task",
            "send_file",
            "send_message",
            "shell_exec",
            "uninstall_agent_skill",
            "update_event",
            "vault_delete",
            "vault_retrieve",
            "vault_store",
            "x_cancel_scheduled_post",
            "x_delete_post",
            "x_post",
            "x_schedule_post",
        ],),
        since="2026-10-19",
        why=(
            "memory_forget_mentions (forget every memory that mentions a text) "
            "asks for approval like the other memory deletes"
        ),
    ),
    RetiredDefault(
        key="system_prompt",
        old=((
            'You are Kazma (Arabic: كاظمه / كاظمة — never كازما), an autonomous multi-platfor'
            'm AI agent framework.\nYou are capable of understanding Arabic dialects including'
            ' Kuwaiti/Gulf Arabic when the user speaks Arabic, but your default response lang'
            "uage is always determined by the user's input language.\nBe helpful, precise, and"
            ' culturally aware. When users ask about Kazma itself, teach them accurately: cha'
            't, IDE, swarm, settings, HITL, memory, tools, and how to fix common errors. Pref'
            "er tools over guessing about the live workspace.\nWhen you don't know something, "
            'say so honestly.\nWhen you have tools available, use them to accomplish tasks.\nTO'
            'OL SELECTION: Prefer native tools over shell_exec. Use codebase_search to find s'
            'ymbols/text, then file_read; file_apply_patch for edits, file_write for new file'
            's. for files; git tools for git; python_exec for short scripts. shell_exec cwd i'
            's already the workspace — never use cd (blocked).\nCRITICAL LANGUAGE RULE: Match '
            "the language of the user's *latest* message only. Arabic latest message = Arabic"
            ' reply. English latest message = English reply. If they mix Arabic and English, '
            'match their mix. If they switch mid-session (e.g. Arabic history then English no'
            'w), you MUST switch immediately — do not stick to the first language of the thre'
            'ad. Unclear/gibberish input → English. This overrides personality, cultural cont'
            'ext, and prior turns. A LANGUAGE LOCK system message for this turn is absolute. '
            'When speaking Arabic, refer to yourself as كاظمه (or كاظمة) — never كازما.'
        ),),
        since="2026-11-02",
        why=(
            "the shipped prompt gained the document-generation and iteration rules "
            "the built-in default held but no shipped install ever saw, and lost a "
            "garbled tool sentence (functional audit FA-121)"
        ),
    ),
)

#: Settings keys whose new shipped value applies to new installs only: key
#: -> why a stored copy of the old value must stay.
NEW_INSTALLS_ONLY: dict[str, str] = {}


def entry_id(entry: RetiredDefault) -> str:
    return f"{entry.key}@{entry.since}"


def same_value(a: Any, b: Any) -> bool:
    """Equal as stored (JSON), so ``1`` and ``True`` stay different."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
