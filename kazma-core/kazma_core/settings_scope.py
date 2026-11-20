"""Whose each settings key is: the install's, a space's or a person's
(Spaces S1-23).

``install``  One value for the whole install, the admin's: Settings -> Install.
``space``    A space's own: its connected accounts (mail, calendar, X,
             GitHub), its chats' state, its Soul. The owner's space keeps the
             key as it is; every other space keeps its own under
             ``tenant.<id>.`` (``tenant_isolation.tenant_key``, the X and
             GitHub keys), or inside a mapping the key holds per space
             (``email.accounts_by_space``, ``self_improvement.space_evolution``),
             or a chat's key names its chat, whose space is its thread's record.
             Settings -> This space, through each account's own routes.
``account``  A person's own: their sign-in and the browsers signed in as
             them. Settings -> My account.

Every key product code writes, and every key ``kazma.yaml`` ships, has a
scope here; ``tests/test_settings_scope.py`` fails on one that has none (the
longest pattern decides, as in ``settings_restore.KEY_RULES``; a key under
``tenant.<id>.`` is that space's).

**Nothing here acts unless Spaces is enforced.** Then the install's own
writers -- Settings' generic save, a batch save, the agent's ``config_save``
-- write install keys only (:func:`install_writer_refusal`): a space's key
written there would land in the owner's space whatever space the request is
in, and a person's in no one's. And they write settings only
(``settings_restore.classify``): Kazma's own records -- the identity store,
its record of resets, sign-ins, grants, the Soul -- have their own writers,
or the admin could add themselves to any space (S1-25).

No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ACCOUNT",
    "INSTALL",
    "SCOPE_RULES",
    "SPACE",
    "install_writer_refusal",
]

INSTALL = "install"
SPACE = "space"
ACCOUNT = "account"


@dataclass(frozen=True)
class _ScopeRule:
    """Whose the keys under ``pattern`` are (a whole key, or a prefix ending
    in ``.``; the longest matching rule decides)."""

    pattern: str
    scope: str
    why: str


SCOPE_RULES: tuple[_ScopeRule, ...] = (
    # ── a person's own ──────────────────────────────────────────────────
    _ScopeRule("account.", ACCOUNT, "the owner's sign-in: name, password, API tokens; another "
              "account's sign-in is in the identity store"),
    _ScopeRule("web_session.", ACCOUNT, "a browser signed in as one account"),
    # ── a space's own ───────────────────────────────────────────────────
    _ScopeRule("connectors.x.", SPACE, "the owner's space's X account (every other space's is "
              "under tenant.<id>., S1-17 part b)"),
    _ScopeRule("connectors.x.reply.", INSTALL, "the reply bot's policy: continuing authority, the "
              "admin's (it answers the owner's account)"),
    _ScopeRule("connectors.github.", SPACE, "the owner's space's GitHub sign-in and the GitHub App "
              "(every other space's is under tenant.<id>., S1-17 part c)"),
    _ScopeRule("email.accounts", SPACE, "the owner's space's extra mail accounts"),
    _ScopeRule("email.accounts_by_space", SPACE, "the extra mail accounts of every other space, "
              "by space (S1-17 part a)"),
    _ScopeRule("self_improvement.agent_evolution", SPACE, "the owner's space's Soul (S1-10)"),
    _ScopeRule("self_improvement.space_evolution", SPACE, "every other space's Soul, by space (S1-10)"),
    _ScopeRule("self_improvement.pending_evolution", SPACE, "Soul changes waiting for review, "
              "the owner's space's"),
    # A chat's own state: each key names its chat or thread, whose space is
    # the thread's record (S1-08).
    _ScopeRule("active_thread.", SPACE, "the chat each chat-app sender is on"),
    _ScopeRule("session.", SPACE, "who owns each chat, and where it was last used"),
    _ScopeRule("long_task.", SPACE, "a chat's long-task budget and continue context"),
    _ScopeRule("yolo.", SPACE, "a chat's YOLO window"),
    _ScopeRule("plan_mode.", SPACE, "a chat's plan mode"),
    _ScopeRule("hitl_grant.", SPACE, "approvals given for a chat's tools"),
    _ScopeRule("task_grant.", SPACE, "approvals given for one task"),
    _ScopeRule("path_grant.", SPACE, "folders granted to one chat"),
    _ScopeRule("path_grant_index.", SPACE, "the index of a chat's folder grants"),
    _ScopeRule("hitl.last_stale_notice.", SPACE, "when a chat was last told its card expired"),
    _ScopeRule("chat.models", SPACE, "each chat app's own model, by chat"),
    _ScopeRule("swarm.approval.", SPACE, "a swarm request's shared approval; it records its "
              "space and asker (S1-15)"),
    # ── the install's ───────────────────────────────────────────────────
    _ScopeRule("acquisition.", INSTALL, "the web acquisition journal, allowances and monitor: "
              "the install's; each crawl row names its library or folder"),
    _ScopeRule("agent.", INSTALL, "how Kazma behaves: language, personality, commitment, budgets"),
    _ScopeRule("agent_skills.", INSTALL, "Agent Skills switched on or off"),
    _ScopeRule("a2a.", INSTALL, "whether other agents may call Kazma"),
    _ScopeRule("appearance.", INSTALL, "the interface's look (one for the install; a look per "
              "person is later)"),
    _ScopeRule("auth.", INSTALL, "sign-ins in progress (each attempt records its space and person)"),
    _ScopeRule("backup.", INSTALL, "the restore drill's runs"),
    _ScopeRule("backups.", INSTALL, "where and how often backups go"),
    _ScopeRule("checkpoints.", INSTALL, "how long a chat's step history is kept"),
    _ScopeRule("connectors.", INSTALL, "the chat apps and integrations: the install's bots"),
    _ScopeRule("containers.", INSTALL, "which container engine Kazma uses"),
    _ScopeRule("context.", INSTALL, "how much of a chat the model is given"),
    _ScopeRule("cron.", INSTALL, "the scheduler's zone, catch-up, budgets and bounds"),
    _ScopeRule("database.", INSTALL, "installs that share the store"),
    _ScopeRule("documents.", INSTALL, "document processing"),
    _ScopeRule("embedding.", INSTALL, "the embedding model"),
    _ScopeRule("gateway.", INSTALL, "the chat apps' rate limits, hints and private notices"),
    _ScopeRule("git.", INSTALL, "the agent's commit identity"),
    _ScopeRule("guard.", INSTALL, "where the guard pages"),
    _ScopeRule("identity.", INSTALL, "the identity store and whether Spaces is enforced"),
    _ScopeRule("kb.", INSTALL, "the retired crawl jobs list"),
    _ScopeRule("knowledge.", INSTALL, "Knowledge Library search"),
    _ScopeRule("llm.", INSTALL, "the LLM connection kazma.yaml or the environment sets"),
    _ScopeRule("logging.", INSTALL, "log levels and tracing"),
    _ScopeRule("mcp.", INSTALL, "MCP servers and their sign-ins (the machine's, Development)"),
    _ScopeRule("memory.", INSTALL, "memory settings and the memory sweeps' records"),
    _ScopeRule("models.", INSTALL, "model profiles, details, prices and per-task defaults"),
    _ScopeRule("notifications.", INSTALL, "which messages go where, the push key and the "
              "browsers subscribed (the owner's, until each person's with S2-05)"),
    _ScopeRule("observability.", INSTALL, "the digest's and weekly report's runs"),
    _ScopeRule("pipelines.", INSTALL, "the shipped research pipelines"),
    _ScopeRule("platform.", INSTALL, "the older multi-user mode's users"),
    _ScopeRule("providers.", INSTALL, "LLM providers"),
    _ScopeRule("proxy.", INSTALL, "the scraping proxy"),
    _ScopeRule("registry.", INSTALL, "the model chosen, found and checked"),
    _ScopeRule("research.", INSTALL, "the papers index (each paper is in its space's folder, S1-19)"),
    _ScopeRule("runs.", INSTALL, "the run supervisor's automatic first steps"),
    _ScopeRule("safety.", INSTALL, "approval and safety settings"),
    _ScopeRule("security.", INSTALL, "the sign-in secret, the disclosure key, the execution policy"),
    _ScopeRule("shortcuts", INSTALL, "keyboard shortcuts (one for the install)"),
    _ScopeRule("skills.", INSTALL, "skills switched on or off"),
    _ScopeRule("storage.", INSTALL, "where the stores live"),
    _ScopeRule("support.", INSTALL, "where problem reports go"),
    _ScopeRule("swarm.", INSTALL, "the swarm's workers, breakers, output and retention"),
    _ScopeRule("system.", INSTALL, "Kazma's own stamps and records"),
    _ScopeRule("system_prompt", INSTALL, "the base system prompt"),
    _ScopeRule("time_travel.", INSTALL, "snapshot retention"),
    _ScopeRule("tools.", INSTALL, "tools switched on or off"),
    _ScopeRule("ui.", INSTALL, "the interface's title and direction"),
    _ScopeRule("voice.", INSTALL, "voice input and output"),
    _ScopeRule("workspace.", INSTALL, "the machine's workspace and the folders granted outside it"),
)


def _matches(pattern: str, key: str) -> bool:
    if pattern.endswith("."):
        return key.startswith(pattern) or key == pattern[:-1]
    return key == pattern or key.startswith(pattern + ".")


def _scope_of_key(key: str) -> str | None:
    """``install``, ``space`` or ``account`` for *key*, None when no rule
    names it. A key under ``tenant.<id>.`` is that space's."""
    name = str(key or "")
    parts = name.split(".")
    if len(parts) > 2 and parts[0] == "tenant":
        return SPACE
    best: _ScopeRule | None = None
    for rule in SCOPE_RULES:
        if _matches(rule.pattern, name) and (best is None or len(rule.pattern) > len(best.pattern)):
            best = rule
    return best.scope if best is not None else None


def install_writer_refusal(key: str) -> str | None:
    """Why the install's own writers (Settings' generic save, a batch save,
    the agent's ``config_save``) refuse *key*, or None. Only once Spaces is
    enforced, and only for a key that is a space's or a person's: each is
    changed on its own page, in its own space or account."""
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return None
    scope = _scope_of_key(key)
    if scope == SPACE:
        return (f"'{key}' belongs to a space: it is changed in that space, on its own page "
                "(Settings -> This space), never as an install setting.")
    if scope == ACCOUNT:
        return (f"'{key}' belongs to a person: it is changed in their own account "
                "(Settings -> My account), never as an install setting.")
    from kazma_core.settings_restore import SETTING, classify

    kind = classify(key)
    if kind != SETTING:
        # Kazma's own record -- the identity store and its record of resets,
        # sign-ins, grants -- is written only by its own code: written here,
        # the admin could add themselves to any space (S1-25, plan 4.10).
        return (f"'{key}' is Kazma's own record ({kind}), written only by its own code, "
                "never as a setting.")
    return None
