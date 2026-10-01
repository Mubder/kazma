"""Back up and restore Settings, keeping the owner's keys.

Settings -> System ("Download backup", "Restore backup...") and Settings ->
Import/Export share this module.

**A backup** (:func:`backup_text`) holds what a restore can use and nothing
else: every setting the install stores -- the settings database's rows, not
kazma.yaml's defaults, so a restore never pins an old default -- one row per
key, with keys as their ``vault://`` references. Kazma's running state and
every credential (the sign-in secret, the password, signed-in browsers, who
may use the install) are left out: a restore never writes them, so a backup
has no reason to carry them. A key kept in plain text (no vault) and a
password inside a URL are written as ``****``.

**A restore** (:func:`_plan_restore`, then :func:`_apply_restore`):

- writes the backup's settings, each through the check the Settings page
  applies (:mod:`kazma_core.settings_validation`); a value the page would
  refuse is listed, never written;
- never writes Kazma's running state or a credential;
- never replaces a key the install holds now. A key it does not hold comes
  back when the backup's reference opens in this install's vault -- so a key
  this vault still has is never entered again -- and is listed to enter again
  in Settings when it does not;
- merges list settings whose entries have names (providers, MCP servers,
  mail accounts) by name, and mappings by field: an entry or field added
  since the backup stays, and every entry keeps the keys it holds now;
- restores the learned Soul only where the install has none;
- leaves a default boot has retired (:mod:`kazma_core.config_defaults`) as
  it is now;
- deletes nothing.

``_plan_restore`` only reads: the page shows its plan and writes only after the
owner confirms that plan (its ``digest``). ``_apply_restore`` writes it in one
``batch_set`` and keeps an undo record (:data:`UNDO_KEY`):
:func:`_plan_undo` / :func:`_apply_undo` put back what the restore changed,
except a key, which stays. ``tests/test_settings_restore_classes.py`` fails
on a settings key product code writes that :data:`KEY_RULES` does not name.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml

from kazma_core.config_defaults import RETIRED_DEFAULTS, same_value
from kazma_core.config_store import (
    is_masked_secret_placeholder,
    is_sensitive_config_key,
    is_vault_ref,
)
from kazma_core.security.url_credentials import mask_url_credentials, url_has_credentials
from kazma_core.settings_validation import SettingRejected, base_key, validate_setting

__all__ = [
    "BACKUP_MARKER",
    "CREDENTIAL",
    "KEY_RULES",
    "LEARNED",
    "MAX_BACKUP_BYTES",
    "RUNTIME",
    "SETTING",
    "UNDO_KEY",
    "KeyRule",
    "NotABackup",
    "PlanChanged",
    "RestorePlan",
    "UndoPlan",
    "backup_text",
    "classify",
    "restore",
    "undo",
]

logger = logging.getLogger(__name__)

SETTING, RUNTIME, CREDENTIAL, LEARNED = "setting", "runtime", "credential", "learned"

#: The first key of a backup this module writes, and its format.
BACKUP_MARKER = "kazma_settings_backup"
_BACKUP_FORMAT = 1
#: What the last restore changed, for its undo. Kazma's own state: never
#: backed up or restored (it is under ``system.``).
UNDO_KEY = "system.settings.restore_undo"
#: The largest backup a restore reads.
MAX_BACKUP_BYTES = 10 * 1024 * 1024
#: The most values a backup may hold: a YAML alias bomb is small text and an
#: enormous walk.
_MAX_NODES = 200_000
_MASK = "****"
#: Fields that name an entry of a list setting: providers and MCP servers by
#: ``name``, mail accounts by ``alias``.
_ENTRY_NAME_FIELDS = ("name", "alias", "id")
_MISSING: Any = object()


@dataclass(frozen=True)
class KeyRule:
    """How backup and restore treat the keys under ``pattern``.

    ``pattern`` is a whole key, or a prefix ending in ``.``; the longest
    matching rule decides. A key no rule names is a setting (every value
    kazma.yaml ships is one); ``tests/test_settings_restore_classes.py``
    requires every key product code writes to be named here."""

    pattern: str
    kind: str
    why: str


KEY_RULES: tuple[KeyRule, ...] = (
    # ── Kazma's own state: not in a backup; what the install holds stays ──
    KeyRule("system.", RUNTIME, "boot and stop stamps, the installs sharing this store, "
            "retired-default markers, installed extras, the health check's canary, "
            "the last restore's undo record"),
    KeyRule("active_thread.", RUNTIME, "the chat each chat-app sender is on"),
    KeyRule("session.", RUNTIME, "who owns each chat, and where it was last used"),
    KeyRule("long_task.", RUNTIME, "a chat's long-task budget and continue context"),
    KeyRule("yolo.", RUNTIME, "a chat's YOLO window"),
    KeyRule("plan_mode.", RUNTIME, "a chat's plan mode"),
    KeyRule("hitl_grant.", RUNTIME, "approvals given for a chat's tools"),
    KeyRule("task_grant.", RUNTIME, "approvals given for one task"),
    KeyRule("path_grant.", RUNTIME, "folders granted to one chat"),
    KeyRule("path_grant_index.", RUNTIME, "the index of a chat's folder grants"),
    KeyRule("hitl.last_stale_notice.", RUNTIME, "when a chat was last told its card expired"),
    KeyRule("swarm.approval.", RUNTIME, "a swarm task's shared approval"),
    KeyRule("swarm.breaker.", RUNTIME, "circuit-breaker state and probe leases"),
    KeyRule("registry.active_chat_model", RUNTIME, "a mirror of the model in use"),
    KeyRule("registry.discovered_models", RUNTIME, "models found at providers; found again"),
    KeyRule("workspace.selected_path", RUNTIME, "the folder last selected; it follows the workspace in use"),
    KeyRule("embedding.rebuild_status", RUNTIME, "a memory rebuild's progress"),
    KeyRule("kb.ingest_jobs", RUNTIME, "Knowledge Library crawl jobs"),
    KeyRule("research.papers_index", RUNTIME, "papers research found"),
    KeyRule("memory.v2.last_reconsolidation", RUNTIME, "when reconsolidation last ran"),
    KeyRule("memory.v2.rehydrate", RUNTIME, "the recovery sweep's progress"),
    KeyRule("memory.v2.turn_reconcile", RUNTIME, "the turn reconcile's cursor"),
    KeyRule("observability.daily_digest.last_sent", RUNTIME, "when the daily digest was last sent"),
    KeyRule("observability.firing_ledger.last_run", RUNTIME, "when the weekly report last ran"),
    KeyRule("backup.restore_drill.", RUNTIME, "when the restore drill last ran"),
    KeyRule("auth.oidc.state", RUNTIME, "a single sign-on in progress"),
    KeyRule("auth.oidc.state_exp", RUNTIME, "a single sign-on in progress"),
    KeyRule("auth.oidc.pkce_verifier", RUNTIME, "a single sign-on in progress"),
    KeyRule("self_improvement.pending_evolution", RUNTIME, "Soul changes waiting for review"),
    # ── Credentials: not in a backup, never restored ──────────────────────
    KeyRule("security.", CREDENTIAL, "the sign-in secret and the disclosure key"),
    KeyRule("account.", CREDENTIAL, "the owner's sign-in: name, password, API tokens"),
    KeyRule("web_session.", CREDENTIAL, "signed-in browsers: a restore must never bring back "
            "a session that was signed out"),
    KeyRule("platform.users", CREDENTIAL, "who may use this install, and their roles"),
    KeyRule("notifications.push.vapid_private_key", CREDENTIAL, "the key push messages are signed with"),
    KeyRule("notifications.push.vapid_public_key", CREDENTIAL, "the browsers' half of that key"),
    KeyRule("notifications.push.subscriptions", CREDENTIAL, "the browsers' push addresses, bound to "
            "this install's signing key; a browser subscribes again by itself"),
    KeyRule("mcp.oauth.", CREDENTIAL, "MCP servers' sign-ins: a disconnected server stays disconnected"),
    # ── Learned: restored only where the install has none ────────────────
    KeyRule("self_improvement.agent_evolution", LEARNED, "the Soul Kazma learned"),
    # ── Settings product code writes (named so the class gate sees them) ──
    KeyRule("agent.", SETTING, "how Kazma behaves: language, personality, commitment"),
    KeyRule("agent_skills.enabled.", SETTING, "Agent Skills switched on or off"),
    KeyRule("appearance.", SETTING, "the interface's look"),
    KeyRule("backups.", SETTING, "where and how often backups go"),
    KeyRule("checkpoints.retention_days", SETTING, "how long a chat's step history is kept"),
    KeyRule("connectors.", SETTING, "chat apps and integrations (their keys follow the key rule)"),
    KeyRule("context.", SETTING, "how much of a chat the model is given"),
    KeyRule("cron.timezone", SETTING, "the reminders' time zone"),
    KeyRule("database.shared_store.acknowledged_peers", SETTING, "installs the owner said may share the store"),
    KeyRule("documents.", SETTING, "document processing: intake, OCR, workers, retention"),
    KeyRule("email.accounts", SETTING, "extra mail accounts (back only where this vault still holds the sign-in)"),
    KeyRule("embedding.", SETTING, "the embedding model memory and the Knowledge Library use"),
    KeyRule("knowledge.", SETTING, "Knowledge Library search"),
    KeyRule("llm.", SETTING, "the LLM connection kazma.yaml or the environment sets"),
    KeyRule("logging.", SETTING, "log levels"),
    KeyRule("mcp.servers", SETTING, "MCP servers (merged by name; their keys follow the key rule)"),
    KeyRule("memory.", SETTING, "memory settings"),
    KeyRule("models.", SETTING, "model profiles and per-task defaults"),
    KeyRule("notifications.", SETTING, "which messages go where"),
    KeyRule("providers.", SETTING, "LLM providers (merged by name; their keys follow the key rule)"),
    KeyRule("proxy.", SETTING, "the scraping proxy (its password follows the key rule)"),
    KeyRule("registry.active_model", SETTING, "the model chosen"),
    KeyRule("registry.active_provider", SETTING, "the provider chosen"),
    KeyRule("safety.", SETTING, "approval and safety settings"),
    KeyRule("shortcuts", SETTING, "keyboard shortcuts"),
    KeyRule("skills.", SETTING, "skills switched on or off, and their settings"),
    KeyRule("swarm.output_target", SETTING, "where swarm results go"),
    KeyRule("swarm.task_retention_days", SETTING, "how long finished swarm tasks are kept"),
    KeyRule("time_travel.", SETTING, "snapshot retention"),
    KeyRule("tools.", SETTING, "tools switched on or off, and their settings"),
    KeyRule("voice.", SETTING, "voice input and output"),
    KeyRule("workspace.extra_roots", SETTING, "folders granted outside the workspace"),
)


def _matches(pattern: str, key: str) -> bool:
    if pattern.endswith("."):
        return key.startswith(pattern)
    return key == pattern or key.startswith(pattern + ".")


def _rule_for(key: str) -> KeyRule | None:
    """The rule that decides *key* (tenant prefix ignored), or None."""
    base = base_key(key)
    best: KeyRule | None = None
    for rule in KEY_RULES:
        if _matches(rule.pattern, base) and (best is None or len(rule.pattern) > len(best.pattern)):
            best = rule
    return best


def classify(key: str) -> str:
    """``setting``, ``runtime``, ``credential`` or ``learned``."""
    rule = _rule_for(key)
    return rule.kind if rule else SETTING


class NotABackup(ValueError):
    """The text is not something a restore can read; the message says why."""


# ── reading the store ───────────────────────────────────────────────────────


def _stored_rows(store: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Every stored row as it is on disk (references unresolved), and its
    category."""
    rows: dict[str, Any] = {}
    categories: dict[str, str] = {}
    for category, values in (store.get_all() or {}).items():
        for key, value in (values or {}).items():
            rows[key] = value
            categories[key] = category
    return rows, categories


def _decode(value: Any) -> tuple[Any, bool]:
    """A container stored as JSON text (``mcp.servers``) as the container, and
    whether it was text; anything else as it is."""
    if isinstance(value, str) and value[:1] in ("[", "{"):
        try:
            return json.loads(value), True
        except ValueError:
            return value, False
    return value, False


def _same(a: Any, b: Any) -> bool:
    return same_value(_decode(a)[0], _decode(b)[0])


def _present(value: Any) -> bool:
    return value is not _MISSING and value not in (None, "", [], {})


def _is_whole(key: str, rows: set[str]) -> bool:
    """A mapping at *key* is one stored value, not a branch of keys."""
    if key in rows:
        return True
    if any(row.startswith(key + ".") for row in rows):
        return False
    rule = _rule_for(key)
    return rule is not None and not rule.pattern.endswith(".") and rule.pattern == base_key(key)


def _flatten(node: dict[str, Any], rows: set[str], prefix: str = "") -> Iterator[tuple[str, Any]]:
    """A nested settings document as stored keys: a mapping at a key the
    store holds as one value stays one value; any other mapping is walked."""
    for name, value in node.items():
        key = f"{prefix}.{name}" if prefix else str(name)
        if isinstance(value, dict) and not _is_whole(key, rows):
            if value:
                yield from _flatten(value, rows, key)
            continue
        yield key, value


def _current(store: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """(stored rows, every value in effect, row categories): in effect is what
    ``get()`` answers -- kazma.yaml's values with the stored rows over them."""
    rows, categories = _stored_rows(store)
    effective: dict[str, Any] = {}
    defaults = getattr(store, "yaml_defaults", None)
    shipped = defaults() if callable(defaults) else {}
    if isinstance(shipped, dict):
        effective.update(_flatten(shipped, set(rows)))
    effective.update(rows)
    return rows, effective, categories


# ── the backup ──────────────────────────────────────────────────────────────


def _mask_backup_secrets(path: str, value: Any) -> Any:
    """*value* (stored under *path*) as a backup holds it: a key as its vault
    reference; a key kept in plain text, and a password inside a URL, as
    ``****``. Containers stored as JSON text are walked too (``mcp.servers``)."""
    if isinstance(value, dict):
        return {k: _mask_backup_secrets(f"{path}.{k}", v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_backup_secrets(path, item) for item in value]
    if not isinstance(value, str) or not value or is_vault_ref(value):
        return value
    inner, as_text = _decode(value)
    if as_text:
        return json.dumps(_mask_backup_secrets(path, inner), ensure_ascii=False)
    if is_sensitive_config_key(path):
        return _MASK
    return mask_url_credentials(value)


def _kazma_version() -> str:
    try:
        from kazma_core.version import get_version

        return str(get_version())
    except (ImportError, OSError, ValueError):
        return ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _backup_document(store: Any) -> dict[str, Any]:
    """What a backup holds (see the module docstring)."""
    rows, _categories = _stored_rows(store)
    settings: dict[str, Any] = {}
    for key in sorted(rows):
        if classify(key) in (RUNTIME, CREDENTIAL):
            continue
        settings[key] = _mask_backup_secrets(base_key(key), rows[key])
    return {
        BACKUP_MARKER: _BACKUP_FORMAT,
        "created_at": _now(),
        "kazma_version": _kazma_version(),
        "settings": settings,
    }


def backup_text(store: Any, fmt: str = "yaml") -> str:
    """The backup as YAML (the default) or JSON text."""
    doc = _backup_document(store)
    if fmt == "json":
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ── reading a backup ────────────────────────────────────────────────────────


def _count_nodes(node: Any, budget: list[int]) -> None:
    budget[0] -= 1
    if budget[0] < 0:
        raise NotABackup("This file holds far more values than a settings backup; nothing was read.")
    if isinstance(node, dict):
        for value in node.values():
            _count_nodes(value, budget)
    elif isinstance(node, list):
        for value in node:
            _count_nodes(value, budget)


@dataclass
class _Document:
    rows: dict[str, Any] | None
    nested: dict[str, Any] | None
    meta: dict[str, Any]


def _read(text: str) -> _Document:
    if len(text.encode("utf-8", errors="replace")) > MAX_BACKUP_BYTES:
        raise NotABackup("The file is larger than a settings backup can be (10 MB).")
    data: Any = _MISSING
    if text.lstrip()[:1] in ("{", "["):
        try:
            data = json.loads(text)
        except ValueError:
            data = _MISSING
    if data is _MISSING:
        try:
            data = yaml.safe_load(text)
        except (yaml.YAMLError, ValueError, RecursionError):
            raise NotABackup("The file is not YAML or JSON.") from None
    if not isinstance(data, dict) or not data:
        raise NotABackup("The file holds no settings (a backup is a YAML or JSON mapping).")
    try:
        _count_nodes(data, [_MAX_NODES])
    except RecursionError:
        raise NotABackup("The file nests deeper than a settings backup does.") from None
    if BACKUP_MARKER in data:
        fmt = data.get(BACKUP_MARKER)
        settings = data.get("settings")
        if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt < 1:
            raise NotABackup("The file names a backup format Kazma does not know.")
        if fmt > _BACKUP_FORMAT:
            raise NotABackup("The backup was made by a newer Kazma; update Kazma to restore it.")
        if not isinstance(settings, dict):
            raise NotABackup("The backup holds no settings.")
        meta = {
            "format": "backup",
            "created_at": str(data.get("created_at") or ""),
            "kazma_version": str(data.get("kazma_version") or ""),
        }
        return _Document({str(k): v for k, v in settings.items()}, None, meta)
    groups = list(data.values())
    if all(isinstance(g, dict) for g in groups) and any("." in str(k) for g in groups for k in g):
        # Settings -> Import/Export's earlier export: stored keys grouped by category.
        rows: dict[str, Any] = {}
        for group in groups:
            rows.update({str(k): v for k, v in group.items()})
        return _Document(rows, None, {"format": "grouped"})
    return _Document(None, data, {"format": "nested"})


# ── the plan ────────────────────────────────────────────────────────────────


@dataclass
class RestorePlan:
    """What a restore writes, and what it leaves. Key NAMES only leave this
    object through :meth:`summary` -- never a value."""

    meta: dict[str, Any] = field(default_factory=dict)
    writes: list[tuple[str, Any, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    lists: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    unchanged: int = 0
    kept_runtime: list[str] = field(default_factory=list)
    kept_credentials: list[str] = field(default_factory=list)
    kept_keys: list[str] = field(default_factory=list)
    kept_learned: list[str] = field(default_factory=list)
    kept_retired: list[str] = field(default_factory=list)
    keys_restored: list[str] = field(default_factory=list)
    keys_to_reenter: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)
    not_selected: int = 0

    @property
    def digest(self) -> str:
        """Names exactly what :func:`_apply_restore` would write: the page
        sends it back, and a plan that changed since the preview is refused."""
        payload = json.dumps(
            sorted([key, _decode(value)[0], cat] for key, value, cat in self.writes),
            sort_keys=True, default=str, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def summary(self) -> dict[str, Any]:
        return {
            "backup": dict(self.meta),
            "changed": sorted(self.changed),
            "lists": {k: v for k, v in sorted(self.lists.items())},
            "unchanged": self.unchanged,
            "kept": {
                "runtime": len(self.kept_runtime),
                "credentials": len(self.kept_credentials),
                "keys": len(self.kept_keys),
                "learned": len(self.kept_learned),
                "retired_defaults": len(self.kept_retired),
            },
            "kept_keys": sorted(self.kept_keys),
            "keys_restored": sorted(self.keys_restored),
            "keys_to_reenter": sorted(self.keys_to_reenter),
            "refused": dict(sorted(self.refused.items())),
            "not_selected": self.not_selected,
            "digest": self.digest,
        }


def _named_entries(value: Any) -> tuple[list[Any], str, bool] | None:
    """A list setting whose entries all have a name: (entries, the field
    naming them, stored as JSON text)."""
    items, as_text = _decode(value)
    if not isinstance(items, list) or not items:
        return None
    for ident in _ENTRY_NAME_FIELDS:
        if all(isinstance(i, dict) and str(i.get(ident) or "").strip() for i in items):
            return items, ident, as_text
    return None


def _entry_name(item: Any, ident: str) -> str:
    return str(item.get(ident) or "").strip() if isinstance(item, dict) else ""


class _Planner:
    def __init__(
        self,
        effective: dict[str, Any],
        categories: dict[str, str],
        vault_has: Callable[[str], bool],
        sections: frozenset[str] | None,
    ) -> None:
        self.effective = effective
        self.categories = categories
        self.vault_has = functools.cache(vault_has)
        self.sections = sections
        self.plan = RestorePlan()

    def _opens(self, ref: str) -> bool:
        return self.vault_has(ref[len("vault://"):])

    def _holds_key(self, value: Any) -> bool:
        """The install holds a usable key here: not empty, not stars, and a
        reference that opens."""
        if value is _MISSING or value is None:
            return False
        if isinstance(value, str):
            text = value.strip()
            if not text or is_masked_secret_placeholder(text):
                return False
            return self._opens(text) if is_vault_ref(text) else True
        return bool(value)

    @staticmethod
    def _secretish(path: str, value: Any) -> bool:
        if value is _MISSING or value is None or isinstance(value, (dict, list)):
            return False
        if isinstance(value, str) and value and (
            is_vault_ref(value) or is_masked_secret_placeholder(value) or url_has_credentials(value)
        ):
            return True
        return is_sensitive_config_key(path)

    def _key_leaf(self, label: str, backup: Any, current: Any) -> Any:
        """A key: the one held now stays; the backup's comes back only where
        none is held and it opens here. ``_MISSING`` = nothing to store."""
        plan = self.plan
        if self._holds_key(current):
            if (
                _present(backup)
                and not is_masked_secret_placeholder(backup)
                and not _same(backup, current)
            ):
                plan.kept_keys.append(label)
            return current
        if not _present(backup):
            return current
        if is_masked_secret_placeholder(backup) or (is_vault_ref(backup) and not self._opens(backup)):
            plan.keys_to_reenter.append(label)
            return current
        plan.keys_restored.append(label)
        return backup

    def _signed_in(self, path: str, item: dict[str, Any]) -> bool:
        """An extra mail account comes back only while this vault still holds
        its sign-in; any other list entry carries its own keys."""
        if base_key(path) != "email.accounts":
            return True
        alias = str(item.get("alias") or "").strip()
        return any(self.vault_has(f"email.account.{alias}.{f}") for f in ("refresh_token", "password"))

    def _merge_list(self, path: str, backup: list[Any], current: list[Any], ident: str) -> list[Any]:
        held: dict[str, Any] = {}
        for item in current:
            name = _entry_name(item, ident)
            if name and name not in held:
                held[name] = item
        out: list[Any] = []
        added: list[str] = []
        updated: list[str] = []
        used: set[str] = set()
        for item in backup:
            name = _entry_name(item, ident)
            if name in used:
                continue  # a name twice in the backup: the first one counts
            used.add(name)
            label = f"{path}:{name}"
            now = held.get(name)
            if now is None:
                if not self._signed_in(path, item):
                    self.plan.keys_to_reenter.append(label)
                    continue
                out.append(self._merge(label, item, _MISSING))
                added.append(name)
                continue
            merged = self._merge(label, item, now)
            if not _same(merged, now):
                updated.append(name)
            out.append(merged)
        # Entries added since the backup stay, after the backup's.
        out.extend(item for item in current if _entry_name(item, ident) not in used)
        if added or updated:
            self.plan.lists[path] = {"added": added, "updated": updated}
        return out

    def _merge(self, path: str, backup: Any, current: Any) -> Any:
        """The value to store at *path*, or ``_MISSING``."""
        if self._secretish(path, backup) or self._secretish(path, current):
            return self._key_leaf(path, backup, current)
        entries = _named_entries(backup)
        if entries is not None:
            items, ident, as_text = entries
            held = _named_entries(current) if current is not _MISSING else None
            if held is not None and held[1] == ident:
                merged = self._merge_list(path, items, held[0], ident)
            elif not _present(current):
                merged = self._merge_list(path, items, [], ident)
            else:
                return backup  # the install holds something else here: the backup's replaces it
            return json.dumps(merged, ensure_ascii=False) if as_text else merged
        inner, as_text = _decode(backup)
        if isinstance(inner, dict):
            now, _ = _decode(current) if current is not _MISSING else ({}, False)
            now = now if isinstance(now, dict) else {}
            out: dict[str, Any] = {}
            for name, value in inner.items():
                merged = self._merge(f"{path}.{name}", value, now.get(name, _MISSING))
                if merged is not _MISSING:
                    out[name] = merged
            for name, value in now.items():
                out.setdefault(name, value)  # fields added since the backup stay
            return json.dumps(out, ensure_ascii=False) if as_text else out
        return backup

    def decide(self, key: str, value: Any) -> None:
        plan = self.plan
        base = base_key(key)
        if self.sections is not None and base.split(".", 1)[0] not in self.sections:
            plan.not_selected += 1
            return
        kind = classify(key)
        if kind == RUNTIME:
            plan.kept_runtime.append(key)
            return
        if kind == CREDENTIAL:
            plan.kept_credentials.append(key)
            return
        current = self.effective.get(key, _MISSING)
        if kind == LEARNED and _present(current):
            plan.kept_learned.append(key)
            return
        if _present(current) and any(
            entry.key == base and any(same_value(value, old) for old in entry.old)
            for entry in RETIRED_DEFAULTS
        ):
            plan.kept_retired.append(key)
            return
        kept_before = len(plan.kept_keys)
        merged = self._merge(key, value, current)
        if merged is _MISSING:
            return
        try:
            merged, category = validate_setting(key, merged)
        except SettingRejected as exc:
            plan.refused[key] = str(exc)
            return
        if current is not _MISSING and _same(merged, current):
            if len(plan.kept_keys) == kept_before:
                plan.unchanged += 1
            return
        if not category:
            category = self.categories.get(key) or (base.split(".", 1)[0] if "." in base else "general")
        plan.writes.append((key, merged, category))
        plan.changed.append(key)


def _vault_has(name: str) -> bool:
    """Whether this install's vault holds the secret *name*."""
    try:
        from kazma_core.security.vault import get_vault, retrieve_scoped

        vault = get_vault()
        if vault is None:
            return False
        return retrieve_scoped(name, vault) is not None
    except Exception:  # noqa: BLE001 -- a vault that cannot be read opens nothing
        logger.warning("[restore] the vault could not be read for %s", name, exc_info=True)
        return False


def _plan_restore(
    text: str,
    store: Any,
    *,
    sections: list[str] | None = None,
    vault_has: Callable[[str], bool] | None = None,
) -> RestorePlan:
    """What restoring the backup *text* would write. Reads only.

    *sections*: restore only keys under these first segments (``agent``,
    ``providers``...). Raises :class:`NotABackup` for text a restore cannot
    read."""
    doc = _read(text)
    rows, effective, categories = _current(store)
    wanted = frozenset(s.strip() for s in sections if s and s.strip()) if sections else None
    planner = _Planner(effective, categories, vault_has or _vault_has, wanted)
    planner.plan.meta = dict(doc.meta)
    source = doc.rows.items() if doc.rows is not None else _flatten(doc.nested or {}, set(rows))
    for key, value in source:
        if isinstance(value, dict) and not value:
            continue
        planner.decide(key, value)
    return planner.plan


class PlanChanged(RuntimeError):
    """The settings changed between the preview the owner confirmed and the
    write; nothing was written. ``summary`` is the plan as it is now."""

    def __init__(self, summary: dict[str, Any]) -> None:
        super().__init__("The settings changed since the preview; nothing was written. Preview again.")
        self.summary = summary


def restore(
    text: str,
    store: Any,
    *,
    dry_run: bool = False,
    sections: list[str] | None = None,
    expect: str | None = None,
    vault_has: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Preview (*dry_run*) or apply a restore of the backup *text*.

    The preview runs as a read-only diagnostic: a write anywhere under it
    raises instead of landing. An apply with *expect* (the preview's
    ``digest``) writes only that plan: if the settings changed since, it
    raises :class:`PlanChanged` and writes nothing."""
    from kazma_core.diagnostic_scope import read_only_diagnostic

    if dry_run:
        with read_only_diagnostic("settings restore preview"):
            plan = _plan_restore(text, store, sections=sections, vault_has=vault_has)
        return {"dry_run": True, "plan": plan.summary(), "restored": 0}
    plan = _plan_restore(text, store, sections=sections, vault_has=vault_has)
    if expect and expect != plan.digest:
        raise PlanChanged(plan.summary())
    written = _apply_restore(plan, store)
    return {"dry_run": False, "plan": plan.summary(), "restored": written}


def undo(store: Any, *, dry_run: bool = False, expect: str | None = None) -> dict[str, Any]:
    """Preview or apply the undo of the last restore. ``available`` is False
    when there is nothing to undo."""
    from kazma_core.diagnostic_scope import read_only_diagnostic

    if dry_run:
        with read_only_diagnostic("settings restore undo preview"):
            plan = _plan_undo(store)
        if plan is None:
            return {"dry_run": True, "available": False}
        return {"dry_run": True, "available": True, "plan": plan.summary(), "reverted": 0}
    plan = _plan_undo(store)
    if plan is None:
        return {"dry_run": False, "available": False, "reverted": 0}
    if expect and expect != plan.digest:
        raise PlanChanged(plan.summary())
    done = _apply_undo(plan, store)
    return {"dry_run": False, "available": True, "plan": plan.summary(), "reverted": done}


def _apply_restore(plan: RestorePlan, store: Any) -> int:
    """Write the plan in one batch, and keep what each key held before for
    :func:`_plan_undo`. Returns how many settings were written."""
    if not plan.writes:
        return 0
    before, _ = _stored_rows(store)
    written = store.batch_set(plan.writes)
    after, _ = _stored_rows(store)
    record = {
        "at": _now(),
        "backup": dict(plan.meta),
        "keys": {
            key: {"existed": key in before, "before": before.get(key), "after": after.get(key)}
            for key, _value, _category in plan.writes
        },
    }
    try:
        # As JSON text: a mapping would be walked for nested secrets on the way in.
        store.set(UNDO_KEY, json.dumps(record, ensure_ascii=False, default=str), category="internal")
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning("[restore] the undo record could not be saved", exc_info=True)
    logger.info(
        "[restore] %d setting(s) restored from a backup; kept as they are: %d runtime, "
        "%d credential(s), %d key(s); %d key(s) brought back from the vault, %d to enter again, "
        "%d refused",
        written, len(plan.kept_runtime), len(plan.kept_credentials), len(plan.kept_keys),
        len(plan.keys_restored), len(plan.keys_to_reenter), len(plan.refused),
    )
    return written


# ── undo ────────────────────────────────────────────────────────────────────


@dataclass
class UndoPlan:
    """What undoing the last restore writes. Key names only leave through
    :meth:`summary`."""

    at: str
    reverts: list[tuple[str, Any, str]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    kept_keys: list[str] = field(default_factory=list)
    changed_since: list[str] = field(default_factory=list)
    already: int = 0
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        payload = json.dumps(
            [sorted([k, v, c] for k, v, c in self.reverts), sorted(self.deletes)],
            sort_keys=True, default=str, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def summary(self) -> dict[str, Any]:
        return {
            "restored_at": self.at,
            "reverted": sorted([k for k, _v, _c in self.reverts] + self.deletes),
            "kept_keys": sorted(self.kept_keys),
            "changed_since": sorted(self.changed_since),
            "already": self.already,
            "digest": self.digest,
        }


def _holds_secret(key: str, value: Any) -> bool:
    if is_sensitive_config_key(base_key(key)):
        return True
    return "vault://" in json.dumps(value, default=str)


def _undo_record(store: Any) -> dict[str, Any] | None:
    raw = _stored_rows(store)[0].get(UNDO_KEY)
    if not isinstance(raw, str):
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("keys"), dict):
        return None
    return record


def _plan_undo(store: Any) -> UndoPlan | None:
    """What undoing the last restore would write; None when there is nothing
    to undo. A setting changed again since the restore is left as it is now;
    a key the restore brought back stays."""
    record = _undo_record(store)
    if record is None or record.get("undone_at"):
        return None
    rows, categories = _stored_rows(store)
    plan = UndoPlan(at=str(record.get("at") or ""), record=record)
    for key, entry in sorted(record["keys"].items()):
        if not isinstance(entry, dict):
            continue
        now = rows.get(key, _MISSING)
        existed = bool(entry.get("existed"))
        before = entry.get("before")
        if (existed and now is not _MISSING and same_value(now, before)) or (not existed and now is _MISSING):
            plan.already += 1
            continue
        if now is _MISSING or not same_value(now, entry.get("after")):
            plan.changed_since.append(key)
            continue
        if existed:
            plan.reverts.append((key, before, categories.get(key) or "general"))
        elif _holds_secret(key, now):
            plan.kept_keys.append(key)
        else:
            plan.deletes.append(key)
    return plan


def _apply_undo(plan: UndoPlan, store: Any) -> int:
    """Put back what the last restore changed; the record is then spent."""
    done = store.batch_set(plan.reverts) if plan.reverts else 0
    for key in plan.deletes:
        if store.delete(key):
            done += 1
    record = dict(plan.record)
    record["undone_at"] = _now()
    store.set(UNDO_KEY, json.dumps(record, ensure_ascii=False, default=str), category="internal")
    logger.info(
        "[restore] the last restore was undone: %d setting(s) put back; %d changed since, left as they are",
        done, len(plan.changed_since),
    )
    return done
