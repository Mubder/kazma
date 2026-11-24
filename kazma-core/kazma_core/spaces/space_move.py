"""Move a space to another install (Spaces S4-06).

A shared space's owner downloads a sealed bundle ("Move this space"), and
the admin of the other install takes it in ("Bring in a space"), choosing the
person who will own it there. On the new install it is a shared space with
that person as its owner and only member.

**Only the owner arrives** (the owner's decision, after the review). The
person who makes the bundle must own the space; on the new install they are
the person chosen. Everyone else in the space leaves it on arrival, as a
member who leaves does (S2-12), and is invited again there:

* their memory entry does not travel (a fact filed under ``member_<id>``, the
  entity, their About me and suggestions, a change to such a fact);
* their work does not travel (a scheduled job, a swarm task, a research
  session, an X post or composer of theirs);
* their share of the space's documents does not travel (an access row
  granting them one);
* the accounts they connected do not travel: a secret the vault records
  another person stored (``stored_by``), with every setting of its
  connection; one with no record travels only from a space nobody but its
  owner was ever in;
* what they wrote stays the space's -- its chats, its memories of them,
  their documents -- written by :data:`identity.DEPARTED_ACCOUNT`: no account
  of the new install, so work naming them is refused there as a departed
  person's (``identity.work_scope``).

**The bundle** (:func:`export_bundle`) is one file sealed whole with a
password the person chooses (``security.sealed_file``: scrypt, then
AES-256-GCM over every byte, the entries' names and which entries there
are). Inside, a zip: every row store's rows that move (:data:`MOVE_RULES`),
the space's settings that move (its connections' settings, as stored: a
secret is a ``vault://`` pointer), its secrets as the vault seals them with
the space's key and the keys themselves (inside the seal: **the install's
vault key never leaves it**; on the other install the keys are wrapped with
that install's own, and here the space gets a new key as the bundle is made,
so the one that left opens nothing kept here), its files, and
``bundle.json``: what it holds, entry by entry. Written straight into the seal: no plain copy on disk. Refused
before it is written when the space is bigger than an import takes.

**Never moved** (:data:`MOVE_RULES` ``out``, each with its reason): ledgers
(model spend, the forget journal, the action and audit records), outboxes
and alerts (a reminder's message, an X notice), the old install's own state
(a chat app's routing, approval cards, Time Travel's steps), and what is
rebuilt here (vector codes, passages, coverage records). A chat's state
(grants, YOLO windows, pointers) never moves.

**Live work arrives stopped**, as a move is a copy and the old install still
runs it: a scheduled job arrives cancelled, to be restored here (and to the
new owner's own chat); a swarm task cancelled (retried here); a research
session interrupted; an X post due to publish waits for approval, one being
sent is an outcome not known; an X thread being published is cancelled. A
chat paused at an approval card arrives with the card expired, and a chat
cut half way through a turn is never continued here.

**The import** (:func:`import_bundle`) refuses before it writes anything
(:class:`SpaceMoveRefused`, each with a code the pages word): the install
does not enforce Spaces, is on Postgres, the password, a changed byte, an
entry that is not listed or listed and missing; anything of the space here
already; a row that is not the space's, a row of a table that never moves,
another person's entry, work or account, a person of this install named by
a row; an id here already; a setting outside the space's own, or one whose
value its rule refuses; a pointer to a secret that is not in the bundle. It
writes with an import journal first (:data:`JOURNAL_KEY`) and the identity
store last; what takes a space with no member for a left one leaves a space
in the journal alone (:func:`importing_spaces`). A half-done import is taken
back, and one a stop cut off is taken back at the next start, or by the next
import (:func:`settle_unfinished_imports`).

**Not moved:** a personal space; a project folder (the space arrives as a
plain shared space; the folder's files stay on the old machine); the
Knowledge Library's vectors and keyword index (rebuilt here).

Blocking: call it off the event loop.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets as _secrets
import shutil
import sqlite3
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from kazma_core.spaces import space_data
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, spaces_enforced, valid_space_id

__all__ = [
    "ACCOUNT_FIELDS",
    "FORMAT",
    "JOURNAL_KEY",
    "MAX_BUNDLE_BYTES",
    "MOVE_RULES",
    "SpaceMoveRefused",
    "export_bundle",
    "import_bundle",
    "importing_spaces",
    "settle_at_start",
    "settle_unfinished_imports",
]

logger = logging.getLogger(__name__)

#: The bundle's format; an import refuses any other.
FORMAT = "kazma-space-bundle/2"
#: The largest bundle the import route takes (its body limit): the largest
#: body any route takes (``kazma_ui.body_limits.DOCUMENT_UPLOAD_CEILING``).
#: An export bigger than this is refused.
MAX_BUNDLE_BYTES = 2 * 1024**3
#: The most a bundle's entries may unpack to (an import's bound).
MAX_UNPACKED_BYTES = 16 * 1024**3
#: The most one JSON entry (``bundle.json``, ``settings.json``, ``keys.json``)
#: or one row may hold: read whole, so bounded.
MAX_JSON_BYTES = 64 * 1024**2
#: The import journal: the spaces whose import began and has not finished.
JOURNAL_KEY = "system.spaces.importing"
#: The columns (and JSON keys) that hold a person's account id.
ACCOUNT_FIELDS = frozenset({
    "asker", "author", "creator", "created_by", "updated_by", "owner_id", "actor_id", "said_by", "opener",
    "account", "account_id", "granted_by", "booker", "principal_id", "requested_by", "decided_by",
    "deleted_by", "actor", "stored_by", "answered_by", "approvers",
})
#: Keys whose values name people by their names: emptied on the way out.
_NAME_FIELDS = frozenset({"answered_by_names"})
#: The store name the space's checkpoints travel under, wherever they were
#: kept (``checkpoints.db``, ``checkpoints_<space>.db``); they land in the
#: space's own checkpoint file here (``paths.space_checkpoints_db``).
CHECKPOINTS = "checkpoints"
_MEMBER = "member_"
_FIRST = ("thread_scopes.db", "swarm_tasks.db")
_IMPORT_LOCK = threading.Lock()
_ENTRY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")


class SpaceMoveRefused(ValueError):
    """Nothing was moved: ``code`` says why, ``status`` the HTTP answer."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ── what moves, table by table ──────────────────────────────────────────


@dataclass(frozen=True)
class MoveRule:
    """How one table's rows move.

    ``how``: ``moves`` (its rows travel), ``stopped`` (live work: they travel
    and are stopped on arrival, :data:`_STOPS`) or ``out`` (never: *why*).
    *work*: whose work a row is (a column, or ``column:$.path`` of a JSON
    column): a row of anyone but the exporter does not travel. *entries*:
    columns that may name a member's memory entry: a row naming another
    member's does not travel. *grant*: a column naming the person a row
    grants something (an access row): another person's does not travel.
    """

    how: str
    why: str = ""
    work: str = ""
    entries: tuple[str, ...] = ()
    grant: str = ""


def _moves(*, work: str = "", entries: tuple[str, ...] = (), grant: str = "") -> MoveRule:
    return MoveRule("moves", work=work, entries=entries, grant=grant)


def _stopped(*, work: str = "") -> MoveRule:
    return MoveRule("stopped", work=work)


def _out(why: str) -> MoveRule:
    return MoveRule("out", why=why)


_LEDGER = "a ledger of the old install: no row of it crosses (spend, a decision record, an audit)"
#: Every table of every row store, and how its rows move. A table a walk
#: finds with no rule here refuses the export and the import.
MOVE_RULES: dict[tuple[str, str], MoveRule] = {
    ("agent_artifacts.db", "agent_artifacts"): _moves(),
    ("task_ledgers.db", "task_ledgers"): _moves(),
    # memory: the space's memories travel; another member's entry does not
    ("memory_state.db", "beliefs"): _moves(entries=("subject", "object")),
    ("memory_state.db", "episodes"): _moves(),
    ("memory_state.db", "entities"): _moves(entries=("id",)),
    ("memory_state.db", "entity_merges"): _moves(entries=("source_entity_id", "target_entity_id")),
    ("memory_state.db", "entity_merges_archive"): _moves(entries=("source_entity_id", "target_entity_id")),
    ("memory_state.db", "memory_forgotten"): _moves(),
    ("memory_state.db", "memory_profile"): _moves(),
    ("memory_state.db", "memory_member_profile"): _moves(work="account"),
    ("memory_state.db", "memory_profile_suggestions"): _moves(work="account"),
    ("memory_state.db", "memory_summaries"): _moves(),
    ("memory_state.db", "memory_summary_parts"): _moves(),
    ("memory_state.db", "memory_summary_sources"): _moves(),
    ("memory_state.db", "memory_changes"): _moves(entries=("subject",)),
    ("memory_state.db", "memory_summary_periods"): _moves(),
    ("memory_state.db", "graph_associations"): _moves(entries=("group_root", "member")),
    ("memory_state.db", "procedural_dags"): _moves(),
    ("memory_state.db", "beliefs_archive"): _moves(entries=("original_belief_json:$.subject",
                                                            "original_belief_json:$.object")),
    ("memory_state.db", "memory_forget_applied"): _out("the install's forget journal marks"),
    ("memory_state.db", "memory_reconcile_state"): _out("the install's reconcile cursors"),
    ("memory_state.db", "memory_mirror_outbox"): _out("an outbox of the old install's Postgres copy"),
    ("memory_state.db", "memory_graph_outbox"): _out("an outbox of the old install's Neo4j copy"),
    ("memory_state.db", "memory_vector_codes"): _out("derived from the vectors: rebuilt here by its sweep"),
    ("memory_state.db", "episode_passages"): _out("derived from the memories: rebuilt here by its sweep"),
    ("memory_state.db", "episode_passage_state"): _out("derived from the memories: rebuilt here by its sweep"),
    ("memory_state.db", "memory_change_seq"): _out("the install's change counter"),
    ("memory_state.db", "memory_sweep_cursors"): _out("the install's sweep cursors"),
    ("memory_ops.db", "memory_task_queue"): _out("the old install's queued memory work: turn reconcile "
                                                  "rebuilds what a missing turn needs here"),
    ("memory_ops.db", "memory_audit_log"): _out(_LEDGER),
    ("memory_ops.db", "memory_recall_records"): _out(_LEDGER),
    ("memory_ops.db", "memory_deep_passes"): _out(_LEDGER),
    ("memory_ops.db", "memory_incidents"): _out("the old install's open memory incidents"),
    ("memory_ops.db", "commitments"): _out("the old install's pending decisions and their record"),
    ("memory_ops.db", "commitment_events"): _out(_LEDGER),
    ("memory_forget_journal.db", "forget_journal"): _out(_LEDGER),
    ("memory_forget_journal.db", "forget_remote_pending"): _out("the old install's pending remote deletes"),
    # chats
    (CHECKPOINTS, "checkpoints"): _moves(),
    (CHECKPOINTS, "writes"): _moves(),
    ("snapshots.db", "snapshots"): _out("Time Travel's steps are never carried to another install"),
    ("time_travel_audit.db", "time_travel_audit"): _out(_LEDGER),
    ("chat_sessions.db", "sessions"): _moves(),
    ("chat_sessions_spool.db", "spool"): _moves(),
    # test mode's transcript store, with chat_sessions.db's table
    ("chat_sessions_test.db", "sessions"): _moves(),
    ("sessions.db", "sessions"): _out("the old install's chat-app routing"),
    ("thread_scopes.db", "thread_scopes"): _moves(),
    ("answer_quality.db", "answer_quality"): _out(_LEDGER),
    ("hitl_gates.db", "hitl_gates"): _out("the old install's approval cards; a card a moved chat was "
                                          "waiting on arrives expired"),
    ("hitl_gates.db", "hitl_watchers"): _out("the old install's processes"),
    ("run_incidents.db", "run_incidents"): _out("the old install's incidents: about work that ran there"),
    ("documents.db", "document_audit_events"): _out("a ledger: the old install's record of what was done"),
    ("documents.db", "document_intake_reservations"): _out("the old install's uploads in flight"),
    # work
    ("cron.db", "cron_jobs"): _stopped(work="creator"),
    ("cron.db", "cron_deliveries"): _out("the old install's outbox of messages: one taken here would be "
                                         "sent to its address"),
    ("cron.db", "cron_events"): _out(_LEDGER),
    ("cron.db", "cron_alerts"): _out("the old install's outbox of alerts: one taken here would page this "
                                     "install's admin"),
    ("swarm_tasks.db", "swarm_tasks"): _stopped(work="metadata:$.asker"),
    ("swarm_tasks.db", "swarm_worker_metrics"): _out("counts per worker of the old install"),
    ("pipeline_logs.db", "pipeline_logs"): _out(_LEDGER),
    ("research_sessions.db", "research_sessions"): _stopped(work="asker"),
    ("activity.db", "activity"): _out(_LEDGER),
    ("llm_calls.db", "llm_calls"): _out(_LEDGER),
    # X
    ("x_scheduled.db", "x_scheduled_posts"): _out("the legacy X schedule: its posts are X operations"),
    ("x_replies.db", "x_replies"): _out("the reply bot answers the owner's account only"),
    ("x_replies.db", "x_reply_history"): _out("the reply bot answers the owner's account only"),
    ("x_replies.db", "x_mentions_cursor"): _out("the reply bot answers the owner's account only"),
    ("x_replies.db", "x_closed_threads"): _out("the reply bot answers the owner's account only"),
    ("x_replies.db", "x_thread_permissions"): _out("the reply bot answers the owner's account only"),
    ("x_replies.db", "x_notification_outbox"): _out("the old install's outbox of X notices"),
    ("x_publications.db", "x_operations"): _stopped(work="metadata:$.booker"),
    ("x_publications.db", "x_operation_events"): _out(_LEDGER),
    ("x_publications.db", "x_projection_outbox"): _out("the old install's outbox of X projections"),
    ("x_publications.db", "x_decisions"): _out("the old install's pending decisions"),
    ("x_publications.db", "x_notification_outbox"): _out("the old install's outbox of X notices"),
    ("x_publications.db", "x_composers"): _moves(work="actor"),
    ("x_publications.db", "x_composer_history"): _moves(work="actor"),
    ("x_publications.db", "x_threads"): _stopped(work="actor"),
    ("x_publications.db", "x_thread_events"): _out(_LEDGER),
    # Knowledge
    ("knowledge.db", "knowledge_libraries"): _moves(),
    ("knowledge.db", "knowledge_chunks"): _moves(),
    ("knowledge.db", "knowledge_pages_gone"): _moves(),
    ("knowledge.db", "knowledge_vector_status"): _out("a coverage record of the old install's vectors"),
    ("knowledge.db", "knowledge_chunks_fts"): _out("the keyword index: written here by Knowledge's code"),
    ("knowledge.db", "knowledge_meta"): _out("the store's own version marks"),
    # documents
    ("documents.db", "documents"): _moves(),
    ("documents.db", "document_blobs"): _moves(),
    ("documents.db", "document_versions"): _moves(),
    ("documents.db", "document_artifacts"): _moves(),
    ("documents.db", "document_acl"): _moves(grant="principal_id"),
    ("documents.db", "document_tombstones"): _moves(),
    ("documents.db", "document_chunks"): _moves(),
    ("documents.db", "document_jobs"): _moves(),
    ("documents.db", "document_job_events"): _out(_LEDGER),
    ("documents.db", "document_audit_gc_control"): _out("the audit prune's own switch"),
    ("documents.db", "document_intake_mutex"): _out("the intake admission's lock row"),
    ("sandbox_emails.db", "messages"): _moves(),
}


def _rule(store: str, table: str) -> MoveRule:
    rule = MOVE_RULES.get((store, table))
    if rule is None:
        raise SpaceMoveRefused("unknown_table", 409, f"The table {table} of {store} says nothing of how it moves.")
    return rule


def _logical_store(name: str, path: Path) -> str | None:
    """The name a row store travels under: checkpoints under one name; a
    store kept only for tests or a repair copy, None (never moved)."""
    if name in ("checkpoints.db", "checkpoints_*.db"):
        return CHECKPOINTS
    if "*" in name:
        return None  # a repair copy; test mode's families of transcript stores
    return name


# ── people ──────────────────────────────────────────────────────────────


def _people(registry: dict[str, Any]) -> set[str]:
    """Every person the install's identity store knows: its accounts and the
    ones its record names."""
    known = {str(a) for a in (registry.get("accounts") or {})}
    known |= {str(e.get("account")) for e in registry.get("events") or () if isinstance(e, dict) and e.get("account")}
    known.add(OWNER_ACCOUNT_ID)
    return known


def _entry_of(value: Any) -> str | None:
    """The account a member entry names (``member_<id>``), else None."""
    if isinstance(value, str) and value.startswith(_MEMBER) and len(value) > len(_MEMBER):
        return value[len(_MEMBER):]
    return None


def _json_value(raw: Any, path: str) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    for step in path.removeprefix("$.").split("."):
        value = value.get(step) if isinstance(value, dict) else None
    return value


def _column_value(row: dict[str, Any], spec: str) -> Any:
    """*spec* is a column, or ``column:$.path`` inside a JSON column."""
    if ":" in spec:
        column, path = spec.split(":", 1)
        return _json_value(row.get(column), path)
    return row.get(spec)


def _rewrite(value: Any, swap: Callable[[str, str], str], key: str = "") -> tuple[bool, Any]:
    """*value* with every person field and member entry passed through
    *swap(key, value)*, inside JSON text too."""
    if isinstance(value, dict):
        changed, out = False, {}
        for k, v in value.items():
            c, out[k] = _rewrite(v, swap, str(k))
            changed = changed or c
        return changed, out
    if isinstance(value, list):
        results = [_rewrite(v, swap, key) for v in value]
        return any(c for c, _ in results), [v for _, v in results]
    if isinstance(value, str):
        if value[:1] in "{[" and len(value) <= MAX_JSON_BYTES:
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict | list):
                changed, walked = _rewrite(parsed, swap, key)
                return (True, json.dumps(walked, ensure_ascii=False)) if changed else (False, value)
        swapped = swap(key, value)
        return swapped != value, swapped
    return False, value


def _rewrite_state(value: Any, swap: Callable[[str, str], str], key: str = "") -> tuple[bool, Any]:
    """:func:`_rewrite` through a checkpoint's own objects: an interrupt's
    card, a send's argument, a tuple."""
    from langgraph.types import Interrupt, Send

    if isinstance(value, Interrupt):
        changed, card = _rewrite_state(value.value, swap, key)
        return changed, replace(value, value=card) if changed else value
    if isinstance(value, Send):
        changed, arg = _rewrite_state(value.arg, swap, key)
        return changed, Send(value.node, arg) if changed else value
    if isinstance(value, dict):
        walked = {k: _rewrite_state(v, swap, str(k)) for k, v in value.items()}
        return any(c for c, _ in walked.values()), {k: v for k, (_, v) in walked.items()}
    if type(value) in (list, tuple):
        results = [_rewrite_state(v, swap, key) for v in value]
        return any(c for c, _ in results), type(value)(v for _, v in results)
    return _rewrite(value, swap, key)


def _rewrite_checkpoint(row: dict[str, Any], swap: Callable[[str, str], str]) -> dict[str, Any]:
    """A checkpoint row with every person field inside its state passed
    through *swap*: a card names who asked, who answers it and their names.
    ``ValueError`` when the state cannot be read. A state of another kind
    than LangGraph writes is left as it is: the strict serializer that loads
    a chat reads no other (never a pickle)."""
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde

    row = dict(row)
    kind = str(row.get("type") or "")
    serde = kazma_checkpoint_serde()
    for column in ("checkpoint", "value"):
        blob = row.get(column)
        if not isinstance(blob, bytes) or kind not in ("msgpack", "json"):
            continue
        try:
            if kind == "json":
                changed, state = _rewrite(json.loads(blob), swap)
                if changed:
                    row[column] = json.dumps(state, ensure_ascii=False).encode("utf-8")
            else:
                changed, state = _rewrite_state(serde.loads_typed((kind, blob)), swap)
                if changed:
                    row["type"], row[column] = serde.dumps_typed(state)
        except (ValueError, TypeError, NotImplementedError) as exc:
            if isinstance(exc, SpaceMoveRefused):
                raise
            raise ValueError("a checkpoint's state cannot be read") from exc
    meta = row.get("metadata")
    if isinstance(meta, bytes):
        try:
            changed, state = _rewrite(json.loads(meta), swap)
        except (ValueError, UnicodeDecodeError) as exc:
            if isinstance(exc, SpaceMoveRefused):
                raise
            return row  # not JSON: holds no person field
        if changed:
            row["metadata"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    return row


# ── secrets ─────────────────────────────────────────────────────────────


def _secret_group(name: str, space: str) -> str:
    """The connection a secret belongs to: its settings and secrets travel,
    or stay, together."""
    for prefix in (f"cfg:tenant.{space}.connectors.x.", f"cfg-url:tenant.{space}.connectors.x."):
        if name.startswith(prefix):
            return "connectors.x"
    for prefix in (f"cfg:tenant.{space}.connectors.github.", f"cfg-url:tenant.{space}.connectors.github."):
        if name.startswith(prefix):
            return "connectors.github"
    inner = name.removeprefix(f"space:{space}:")
    parts = inner.split(".")
    if inner != name:
        if parts[:2] == ["email", "account"] and len(parts) > 2:
            return ".".join(parts[:3])
        if parts[0] in ("email", "calendar") and len(parts) > 1:
            return ".".join(parts[:2])
    return name


def _stored_by(row: dict[str, Any]) -> str:
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except ValueError:
        return ""
    return str(meta.get("stored_by") or "") if isinstance(meta, dict) else ""


def _only_ever(registry: dict[str, Any], space: str, exporter: str) -> bool:
    """Whether nobody but *exporter* was ever in *space*: its members now,
    and every account its record names in it."""
    for m in registry.get("memberships") or ():
        if m.get("space") == space and str(m.get("account")) != exporter:
            return False
    for e in registry.get("events") or ():
        if isinstance(e, dict) and e.get("space") == space and e.get("account") and str(e["account"]) != exporter:
            return False
    return True


def _travelling_secrets(space: str, exporter: str, registry: dict[str, Any],
                        rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    """``(rows that travel, groups that stay)``: a connection travels when
    the exporter stored every secret of it (one with no record of who stored
    it, only from a space nobody else was ever in)."""
    alone = _only_ever(registry, space, exporter)
    staying: set[str] = set()
    for row in rows:
        who = _stored_by(row)
        if not (who == exporter or (not who and alone)):
            staying.add(_secret_group(str(row["name"]), space))
    kept = [row for row in rows if _secret_group(str(row["name"]), space) not in staying]
    return kept, staying


# ── settings ────────────────────────────────────────────────────────────

#: The space's own settings that move (``tenant.<space>.`` + these), each
#: with what it holds: ``secret`` (one ``vault://`` pointer), ``flag``,
#: ``number``, ``text`` or ``account`` (who is signed in). A list, so a
#: setting added later stays until it is said to move: the first lists moved
#: ``connectors.x.`` whole, and with it the files X drafting reads
#: (the second review of S4-06).
_MOVED_SETTINGS: dict[str, str] = {
    "connectors.x.enabled": "flag",
    "connectors.x.handle": "text",
    "connectors.x.account": "account",
    "connectors.x.max_posts_per_day": "number",
    "connectors.x.max_posts_per_month": "number",
    "connectors.x.duplicate_window_days": "number",
    "connectors.x.max_chars": "number",
    "connectors.x.max_mentions": "number",
    "connectors.x.max_hashtags": "number",
    "connectors.x.max_cashtags": "number",
    "connectors.x.drafting.post_length": "text",
    "connectors.x.api_key": "secret",
    "connectors.x.api_key_secret": "secret",
    "connectors.x.access_token": "secret",
    "connectors.x.access_token_secret": "secret",
    "connectors.x.bearer_token": "secret",
    "connectors.x.oauth2.client_id": "text",
    "connectors.x.oauth2.client_secret": "secret",
    "connectors.x.oauth2.access_token": "secret",
    "connectors.x.oauth2.refresh_token": "secret",
    "connectors.x.oauth2.expires_at": "number",
    "connectors.x.oauth2.account": "account",
    "connectors.github.token": "secret",
    "connectors.github.oauth_token": "secret",
    "connectors.github.oauth_scope": "text",
    "connectors.github.oauth_token_type": "text",
}
#: What of X and GitHub stays, and why (a prefix when it ends with a dot or
#: an underscore). Every ``connectors.x.`` and ``connectors.github.`` key the
#: code names is here or above.
_STAYING_SETTINGS: dict[str, str] = {
    "connectors.x.drafting.reference_files": "paths of files of the old machine, read whole by X drafting",
    "connectors.x.reply.": "the reply bot answers the owner's account only",
    "connectors.x.ai": "the X model: a provider of the old install",
    "connectors.x.ai_limits": "what the X model may spend, decided on each install",
    "connectors.x.ai_usage": "a ledger: the X model's calls",
    "connectors.x.read_usage": "a ledger: the X API's reads",
    "connectors.github.app_": "the install's GitHub App",
    "connectors.github.oauth_state": "a sign-in in progress on the old install",
}
_TEXT_MAX = 4_000
_MAPPINGS = dict(space_data._BY_SPACE_MAPPINGS)
_ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def _setting_group(base: str) -> str:
    return "connectors.x" if base.startswith("connectors.x.") else "connectors.github"


def _moved_setting(key: str, space: str) -> str | None:
    """The base key of *key* when it is a setting of *space* that moves."""
    prefix = f"tenant.{space}."
    base = key.removeprefix(prefix)
    return base if key.startswith(prefix) and base in _MOVED_SETTINGS else None


def _holds_what_it_should(base: str, value: Any) -> bool:
    """Whether *value* is what the moved setting *base* holds."""
    from kazma_core.settings_validation import SettingRejected, validate_setting

    kind = _MOVED_SETTINGS[base]
    if kind == "secret":
        return isinstance(value, str) and len(list(_pointers(value))) == 1
    if any(True for _ in _pointers(value)):
        return False
    if kind == "flag":
        right = isinstance(value, bool | int) or str(value).lower() in ("true", "false", "1", "0", "yes", "no", "on", "off")
    elif kind == "number":
        right = isinstance(value, int | float) and not isinstance(value, bool)
    elif kind == "account":
        right = isinstance(value, dict) and len(value) <= 20 and all(
            isinstance(v, str | int | float | bool | type(None))
            or (isinstance(v, list) and len(v) <= 100 and all(isinstance(s, str) for s in v))
            for v in value.values())
    else:
        right = isinstance(value, str) and len(value) <= _TEXT_MAX
    if not right:
        return False
    try:
        validate_setting(base, value)
    except SettingRejected:
        return False
    return True


def _pointers(value: Any) -> Iterator[str]:
    from kazma_core.config_store import _VAULT_REF_PREFIX

    if isinstance(value, str) and value.startswith(_VAULT_REF_PREFIX):
        yield value[len(_VAULT_REF_PREFIX):]
    elif isinstance(value, dict):
        for item in value.values():
            yield from _pointers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _pointers(item)


def _space_settings(space: str, staying: set[str], secret_names: set[str],
                    left_out: dict[str, int]) -> dict[str, dict[str, Any]]:
    """The space's settings that move, as stored, each with its category:
    its connections' settings (a connection that stays leaves all of them,
    and one holding a value an import would refuse stays, counted) and its
    entries of the mappings kept by space (an extra mail account that stays
    leaves its entry)."""
    from kazma_core.config_store import get_config_store

    found: dict[str, dict[str, Any]] = {}
    for category, group in get_config_store().get_all().items():
        for key, value in group.items():
            base = _moved_setting(key, space)
            if base is not None:
                if _setting_group(base) in staying or any(p not in secret_names for p in _pointers(value)):
                    continue
                if not _holds_what_it_should(base, value):
                    left_out["settings"] = left_out.get("settings", 0) + 1
                    continue
                found[key] = {"category": category, "value": value}
            elif key in _MAPPINGS:
                holder = value
                for step in _MAPPINGS[key]:
                    holder = holder.get(step) if isinstance(holder, dict) else None
                if not isinstance(holder, dict) or space not in holder:
                    continue
                entry = holder[space]
                if key == "email.accounts_by_space" and isinstance(entry, list):
                    entry = [a for a in entry if not (isinstance(a, dict)
                                                       and f"email.account.{a.get('alias')}" in staying)]
                found[key] = {"category": category, "value": {space: entry}}
    return found


def _checked_mapping(key: str, entry: Any) -> None:
    """A mapping entry's value is the shape its reader takes."""
    bad = SpaceMoveRefused("bad_setting", 409, f"The bundle's {key} holds a value this install does not take.")
    if key == "email.accounts_by_space":
        if not isinstance(entry, list) or len(entry) > 50:
            raise bad
        for account in entry:
            if not isinstance(account, dict) or not _ALIAS.fullmatch(str(account.get("alias") or "")):
                raise bad
            if not all(isinstance(v, str | int | float | bool | type(None)) for v in account.values()):
                raise bad
    elif key == "self_improvement.space_evolution":
        from kazma_core.safety.prompt_fence import is_override_delta

        agents = entry.get("agents") if isinstance(entry, dict) else None
        if not isinstance(agents, dict) or set(entry) - {"agents"}:
            raise bad
        for held in agents.values():
            if not isinstance(held, dict) or not isinstance(held.get("soul", ""), str) \
                    or not isinstance(held.get("history", []), list):
                raise bad
            if is_override_delta(str(held.get("soul") or "")):
                raise SpaceMoveRefused("bad_setting", 409, "The bundle's lessons hold an instruction to override.")
    else:
        raise bad


def _check_settings(space: str, settings: dict[str, Any], secret_names: set[str]) -> None:
    """Only the key forms that are the space's own, each value as its rule
    takes it, each pointer to a secret the bundle carries."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    for key, held in settings.items():
        if not isinstance(held, dict) or "value" not in held or set(held) - {"category", "value"}:
            raise SpaceMoveRefused("not_a_bundle", 400, "A setting of the bundle is not as written.")
        value = held["value"]
        if key in _MAPPINGS:
            if not isinstance(value, dict) or set(value) != {space}:
                raise SpaceMoveRefused("foreign_setting", 409, f"The bundle's {key} names another space.")
            _checked_mapping(key, value[space])
            current = store.get(key)
            for step in _MAPPINGS[key]:
                current = current.get(step) if isinstance(current, dict) else None
            if isinstance(current, dict) and space in current:
                raise SpaceMoveRefused("id_collides", 409, f"The space's entry of {key} is here already.")
            continue
        base = _moved_setting(key, space)
        if base is None:
            raise SpaceMoveRefused("foreign_setting", 409, f"The bundle carries a setting that does not move: {key}.")
        if not _holds_what_it_should(base, value):
            raise SpaceMoveRefused("bad_setting", 409, f"The bundle's {key} holds a value this install does not take.")
        if store.get(key) is not None:
            raise SpaceMoveRefused("id_collides", 409, f"The setting {key} is here already.")
        for pointer in _pointers(value):
            from kazma_core.security.vault import sealing_space

            if sealing_space(pointer, space) != space:
                raise SpaceMoveRefused("foreign_secret", 409, "A setting points at a secret that is not the space's.")
            if pointer not in secret_names:
                raise SpaceMoveRefused("missing_secret", 409, "A setting points at a secret the bundle does not carry.")


# ── export ──────────────────────────────────────────────────────────────


def _space_folders(space: str) -> dict[str, Path]:
    """``{"space": its own folder, "documents": its document files}``."""
    from kazma_core.documents import storage
    from kazma_core.documents.config import get_document_storage_root
    from kazma_core.spaces import files as space_files

    return {"space": space_files.space_dir(space),
            "documents": Path(get_document_storage_root()) / storage.space_folder(space)}


def _space_secrets(space: str, by: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from kazma_core.security.vault import SpaceKeyMissing, get_vault
    from kazma_core.spaces import space_keys

    if not space_data._vault_rows(space) and not space_data._vault_keys(space):
        return [], []
    vault = get_vault()
    if vault is None:
        raise SpaceMoveRefused("vault_unavailable", 503, "The vault cannot be opened; nothing was made.")
    try:
        keys, rows = vault.export_space_sealed(space)
        # The keys leave with the bundle: here the space has a new one from
        # now on (S4-07), so a bundle opened later opens nothing kept here.
        space_keys.change_key(space, by=by)
    except (SpaceKeyMissing, space_keys.KeyChangeRefused) as exc:
        raise SpaceMoveRefused("secrets_unreadable", 409, "A secret of the space cannot be opened; "
                                                          "nothing was made.") from exc
    return keys, rows


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any) -> bytes:
    try:
        return base64.b64decode(str(text or ""), validate=True)
    except ValueError:
        raise SpaceMoveRefused("not_a_bundle", 400, "The bundle holds a value that is not base64.") from None


@dataclass
class _Export:
    space: str
    exporter: str
    people: set[str]
    #: Bytes written so far, against :data:`MAX_BUNDLE_BYTES`.
    written: Callable[[], int]
    entries: dict[str, int] = field(default_factory=dict)
    left_out: dict[str, int] = field(default_factory=dict)

    def swap(self, key: str, value: str) -> str:
        """A person field naming anyone but the exporter names a departed
        person; a member entry is left as it is (its row's rule decides)."""
        from kazma_core.spaces.identity import DEPARTED_ACCOUNT

        if key in _NAME_FIELDS:
            return ""
        if key in ACCOUNT_FIELDS and value != self.exporter and value in self.people:
            return DEPARTED_ACCOUNT
        entry = _entry_of(value)
        if entry is not None and entry != self.exporter:
            return f"{_MEMBER}{DEPARTED_ACCOUNT}"
        return value

    def keeps(self, rule: MoveRule, row: dict[str, Any]) -> bool:
        """Whether *row* travels: another member's entry, work or share
        stays."""
        for spec in rule.entries:
            entry = _entry_of(_column_value(row, spec))
            if entry is not None and entry != self.exporter:
                return False
        if rule.work:
            who = str(_column_value(row, rule.work) or "")
            if who and who != self.exporter:
                return False
        if rule.grant:
            who = str(row.get(rule.grant) or "")
            if who and who != self.exporter and who in self.people:
                return False
        return True

    def check_size(self) -> None:
        if self.written() > MAX_BUNDLE_BYTES:
            raise SpaceMoveRefused("too_big", 413, "The space is bigger than a bundle another install takes.")


def _must_own(space: str, by: str) -> dict[str, Any]:
    from kazma_core.spaces import identity

    registry = identity.read_registry()
    if not any(m.get("space") == space and str(m.get("account")) == by and m.get("role") == "owner"
               for m in registry.get("memberships") or ()):
        raise SpaceMoveRefused("not_the_owner", 403, "Only an owner of the space moves it.")
    return registry


def _files_bytes(space: str) -> int:
    total = 0
    for where in _space_folders(space).values():
        if where.is_dir():
            total += sum(p.stat().st_size for p in where.rglob("*") if p.is_file())
    return total


def export_bundle(space: str, target: Path, password: str, *, by: str) -> dict[str, Any]:
    """Write the space's bundle to *target*, sealed with *password*. *by* is
    the owner moving it (its owner on the other install). Returns the
    bundle's manifest (counts only)."""
    from kazma_core.security.sealed_file import MIN_PASSWORD, SealedWriter

    found = space_data._checked(space)
    if found.get("personal_of"):
        raise SpaceMoveRefused(
            "personal_space_stays", 409,
            "A personal space stays with its person; export it to keep a copy. A shared space can move.")
    if len(password or "") < MIN_PASSWORD:
        raise SpaceMoveRefused("password_too_short", 400, f"The password is at least {MIN_PASSWORD} characters.")
    registry = _must_own(space, by)
    if _files_bytes(space) > MAX_BUNDLE_BYTES:
        raise SpaceMoveRefused("too_big", 413, "The space's files are bigger than a bundle another install takes.")
    keys, rows = _space_secrets(space, by)
    rows, staying = _travelling_secrets(space, by, registry, rows)
    named = {str(row["key_id"]) for row in rows}
    keys = [key for key in keys if str(key["key_id"]) in named or key.get("state") == "active"]
    ctx, tables = space_data._walk(space)
    writer = SealedWriter(target, password, purpose=FORMAT)
    plan = _Export(space=space, exporter=by, people=_people(registry), written=writer.tell)
    manifest: dict[str, Any] = {
        "format": FORMAT, "space": space, "name": str(found.get("name") or ""), "exported_by": by,
        "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tables": {}, "files": 0,
        "secrets": len(rows), "keys": len(keys), "settings": 0,
    }
    try:
        with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zipped:
            _write_tables(zipped, plan, ctx, tables, manifest)
            secret_names = {str(row["name"]) for row in rows}
            settings = _space_settings(space, staying, secret_names, plan.left_out)
            manifest["settings"] = len(settings)
            _put(zipped, plan, "settings.json", json.dumps(settings, ensure_ascii=False, default=str).encode("utf-8"))
            for row in rows:
                _, row["metadata"] = _rewrite(row.get("metadata") or "{}", plan.swap)
            _put(zipped, plan, "secrets.jsonl", "".join(
                json.dumps({**row, "encrypted_value": _b64(bytes(row["encrypted_value"])),
                            "nonce": _b64(bytes(row["nonce"]))}) + "\n" for row in rows).encode("utf-8"))
            _put(zipped, plan, "keys.json", json.dumps([
                {"key_id": k["key_id"], "state": k["state"], "created_at": k["created_at"],
                 "retired_at": k.get("retired_at"), "key": _b64(bytes(k["key"]))} for k in keys]).encode("utf-8"))
            for part, where in _space_folders(space).items():
                if not where.is_dir():
                    continue
                for path in sorted(where.rglob("*")):
                    if path.is_file():
                        name = f"files/{part}/{path.relative_to(where).as_posix()}"
                        zipped.write(path, name)
                        plan.entries[name] = path.stat().st_size
                        manifest["files"] += 1
                        plan.check_size()
            manifest["entries"] = dict(sorted(plan.entries.items()))
            manifest["left_out"] = plan.left_out
            zipped.writestr("bundle.json", json.dumps(manifest))
        plan.check_size()
        writer.finish()
    finally:
        writer.abandon()  # after finish, nothing: an unfinished file never stays
        for key in keys:
            key["key"] = b""
    logger.info("[spaces] space bundle made space=%s by=%s tables=%d files=%d secrets=%d", space, by,
                len(manifest["tables"]), manifest["files"], manifest["secrets"])
    return manifest


def _put(zipped: zipfile.ZipFile, plan: _Export, name: str, data: bytes) -> None:
    zipped.writestr(name, data)
    plan.entries[name] = len(data)
    plan.check_size()


def _write_tables(zipped: zipfile.ZipFile, plan: _Export, ctx: space_data._Context,
                  tables: list[space_data._Found], manifest: dict[str, Any]) -> None:
    by_name: dict[tuple[str, str], list[space_data._Found]] = {}
    for item in tables:
        store = _logical_store(item.store, item.path)
        if store is None or item.rule.how in ("keep", "follows"):
            continue  # a store that never moves; a table that holds no space's rows
        rule = _rule(store, item.table)
        if rule.how == "out" or not item.rows:
            continue
        by_name.setdefault((store, item.table), []).append(item)
    for (store, table), items in sorted(by_name.items()):
        rule = MOVE_RULES[(store, table)]
        name = f"stores/{store}/{table}.jsonl"
        count = 0
        with zipped.open(name, "w", force_zip64=True) as out:
            for item in items:
                for row in space_data._rows(item, ctx):
                    if not plan.keeps(rule, row):
                        plan.left_out[f"{store}:{table}"] = plan.left_out.get(f"{store}:{table}", 0) + 1
                        continue
                    if store == CHECKPOINTS:
                        try:
                            row = {k: space_data._jsonable(v)
                                   for k, v in _rewrite_checkpoint(_decoded(row), plan.swap).items()}
                        except SpaceMoveRefused:
                            raise
                        except ValueError:  # a state this build cannot read: it could not be resumed there
                            plan.left_out[f"{store}:{table}"] = plan.left_out.get(f"{store}:{table}", 0) + 1
                            continue
                    moved = {k: _rewrite(v, plan.swap, k)[1] for k, v in row.items()}
                    out.write((json.dumps(moved, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
                    count += 1
                    plan.check_size()
        if count:
            manifest["tables"][f"{store}:{table}"] = count
        plan.entries[name] = zipped.getinfo(name).file_size


# ── reading a bundle ────────────────────────────────────────────────────


@dataclass
class _Bundle:
    zipped: zipfile.ZipFile
    manifest: dict[str, Any]
    space: str
    exporter: str
    #: ``{store: [table, ...]}`` from the entries' names.
    tables: dict[str, list[str]] = field(default_factory=dict)

    def lines(self, store: str, table: str) -> Iterator[dict[str, Any]]:
        with self.zipped.open(f"stores/{store}/{table}.jsonl") as handle:
            while True:
                raw = handle.readline(MAX_JSON_BYTES + 1)
                if not raw:
                    return
                if len(raw) > MAX_JSON_BYTES:
                    raise SpaceMoveRefused("too_big", 413, "A row of the bundle is bigger than a row may be.")
                text = raw.decode("utf-8").strip()
                if not text:
                    continue
                row = json.loads(text)
                if not isinstance(row, dict):
                    raise SpaceMoveRefused("not_a_bundle", 400, "A row of the bundle is not an object.")
                yield row

    def json(self, name: str, default: Any) -> Any:
        if name not in self.manifest.get("entries", {}):
            return default
        if self.zipped.getinfo(name).file_size > MAX_JSON_BYTES:
            raise SpaceMoveRefused("too_big", 413, f"The bundle's {name} is bigger than it may be.")
        try:
            return json.loads(self.zipped.read(name).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SpaceMoveRefused("not_a_bundle", 400, f"The bundle's {name} is not as written.") from None


def _open_bundle(reader: Any) -> _Bundle:
    """The zip inside the opened seal, its entries checked against its own
    list (sealed with it): an entry not listed, or listed and missing, or of
    another size, refuses it."""
    try:
        zipped = zipfile.ZipFile(reader)
    except zipfile.BadZipFile:
        raise SpaceMoveRefused("not_a_bundle", 400, "This is not a space bundle.") from None
    try:
        info = zipped.getinfo("bundle.json")
    except KeyError:
        zipped.close()
        raise SpaceMoveRefused("not_a_bundle", 400, "This is not a space bundle.") from None
    if info.file_size > MAX_JSON_BYTES:
        zipped.close()
        raise SpaceMoveRefused("too_big", 413, "The bundle's list of entries is bigger than it may be.")
    try:
        manifest = json.loads(zipped.read("bundle.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        zipped.close()
        raise SpaceMoveRefused("not_a_bundle", 400, "This is not a space bundle.") from None
    handed = False
    try:
        space = str(manifest.get("space") or "") if isinstance(manifest, dict) else ""
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not valid_space_id(space) \
                or space == OWNER_SPACE_ID or not isinstance(manifest.get("entries"), dict):
            raise SpaceMoveRefused("not_a_bundle", 400, "This is not a space bundle this build reads.")
        exporter = str(manifest.get("exported_by") or "")
        if not valid_space_id(exporter):
            raise SpaceMoveRefused("not_a_bundle", 400, "The bundle names no owner it was made by.")
        listed = {str(k): v for k, v in manifest["entries"].items()}
        present = {i.filename: i.file_size for i in zipped.infolist() if not i.is_dir()}
        present.pop("bundle.json", None)
        if set(present) != set(listed) or any(present[n] != listed[n] for n in listed):
            raise SpaceMoveRefused("changed", 400, "The bundle's entries are not the ones it lists.")
        if sum(present.values()) > MAX_UNPACKED_BYTES:
            raise SpaceMoveRefused("too_big", 413, "The bundle unpacks to more than an import takes.")
        bundle = _Bundle(zipped=zipped, manifest=manifest, space=space, exporter=exporter)
        for name in present:
            parts = PurePosixPath(name).parts
            if name.startswith("stores/"):
                if len(parts) != 3 or not parts[2].endswith(".jsonl") or not all(
                        _ENTRY_NAME.fullmatch(p) for p in parts[1:]):
                    raise SpaceMoveRefused("not_a_bundle", 400, "The bundle names a store it should not.")
                bundle.tables.setdefault(parts[1], []).append(parts[2][: -len(".jsonl")])
            elif name.startswith("files/"):
                continue
            elif name not in ("settings.json", "secrets.jsonl", "keys.json"):
                raise SpaceMoveRefused("not_a_bundle", 400, "The bundle holds an entry it should not.")
        handed = True
        return bundle
    finally:
        if not handed:
            zipped.close()


# ── the checks ──────────────────────────────────────────────────────────


def _decoded(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            if set(value) != {"base64"}:
                raise SpaceMoveRefused("not_a_bundle", 400, "A row holds a value this build does not read.")
            out[key] = _unb64(value["base64"])
        elif isinstance(value, list):
            raise SpaceMoveRefused("not_a_bundle", 400, "A row holds a value this build does not read.")
        else:
            out[key] = value
    return out


def _bundle_context(bundle: _Bundle) -> space_data._Context:
    """The ids rows of other tables are found by, read from the bundle's own
    rows (as ``space_data._context`` reads them from the stores)."""
    space = bundle.space
    ctx = space_data._Context(space=space)
    for key in ("threads", "summaries", "memory_rows", "commitments", "jobs", "swarm_tasks", "libraries"):
        ctx.ids[key] = []
    picks = (
        ("threads", "thread_scopes.db", "thread_scopes", "thread_id", "space_id"),
        ("summaries", "memory_state.db", "memory_summaries", "id", "tenant_id"),
        ("memory_rows", "memory_state.db", "episodes", "id", "tenant_id"),
        ("memory_rows", "memory_state.db", "beliefs", "id", "tenant_id"),
        ("jobs", "cron.db", "cron_jobs", "job_id", "tenant_id"),
        ("libraries", "knowledge.db", "knowledge_libraries", "id", "tenant_id"),
    )
    for key, store, table, column, owner in picks:
        if table in bundle.tables.get(store, ()):
            ctx.ids[key] += [str(r[column]) for r in bundle.lines(store, table) if r.get(owner) == space]
    if "swarm_tasks" in bundle.tables.get("swarm_tasks.db", ()):
        for row in bundle.lines("swarm_tasks.db", "swarm_tasks"):
            if _json_value(row.get("metadata"), "$.tenant_id") == space:
                ctx.ids["swarm_tasks"].append(str(row.get("id")))
    return ctx


def _belongs(row: dict[str, Any], rule: space_data.TableRule, ctx: space_data._Context) -> bool:
    """Whether *row* is the space's by the rule its store finds them by."""
    if rule.how == "column":
        return row.get(rule.column) == ctx.space
    if rule.how == "json":
        return _json_value(row.get(rule.column), rule.path) == ctx.space
    if rule.how == "thread":
        return str(row.get("thread_id") or "") in set(ctx.ids["threads"])
    if rule.how == "ids":
        return str(row.get(rule.column) or "") in set(ctx.ids[rule.ids])
    return False  # keep, follows: no space's rows


@dataclass
class _Target:
    """One store table of this install a bundle table lands in."""

    store: str
    path: Path
    table: str
    rule: space_data.TableRule
    move: MoveRule
    columns: dict[str, str]
    #: Primary key columns; empty for a table whose key is its row id.
    key: tuple[str, ...]
    #: The integer row id column, given a new number on insert.
    renumbered: str = ""
    virtual: bool = False


def _checkpoint_schema(conn: sqlite3.Connection) -> None:
    """The schema every checkpoint file has, made by LangGraph's own saver."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde

    SqliteSaver(conn, serde=kazma_checkpoint_serde()).setup()


def _read_only(path: Path) -> sqlite3.Connection:
    """*path* to be read while a bundle is checked. The space's own
    checkpoint file is made only once the bundle is taken: until then its
    schema alone, in memory."""
    if path.is_file():
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    _checkpoint_schema(conn)
    return conn


def _targets(bundle: _Bundle) -> dict[tuple[str, str], _Target]:
    from kazma_core.paths import space_checkpoints_db
    from kazma_core.spaces import row_stores

    row_stores.make_row_stores()
    paths = {name: path for name, path in space_data._store_paths() if "*" not in name}
    found: dict[tuple[str, str], _Target] = {}
    for store, tables in bundle.tables.items():
        if store == CHECKPOINTS:
            name, path = "checkpoints_*.db", space_checkpoints_db(bundle.space)
        elif store in paths and _logical_store(store, paths[store]) == store:
            name, path = store, paths[store]
        else:
            raise SpaceMoveRefused("unknown_table", 409, f"This install keeps no store {store} a space moves into.")
        with closing(_read_only(path)) as conn:
            present = dict(space_data._tables(conn))
            for table in tables:
                move = _rule(store, table)
                if move.how == "out":
                    raise SpaceMoveRefused("not_moved", 409, f"The bundle carries rows of {table}, which never move.")
                if table not in present:
                    raise SpaceMoveRefused("unknown_table", 409, f"This install has no table {table} in {store}.")
                info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                columns = {str(r[1]): str(r[2] or "") for r in info}
                rule = space_data._table_rule(name, table, set(columns), virtual_fts=present[table])
                if rule is None or rule.how in ("keep", "follows"):
                    raise SpaceMoveRefused("unknown_table", 409, f"The table {table} of {store} has no space rule.")
                keys = sorted((r[5], str(r[1]), str(r[2] or "")) for r in info if r[5])
                renumbered = keys[0][1] if len(keys) == 1 and "INT" in keys[0][2].upper() else ""
                found[(store, table)] = _Target(
                    store=store, path=path, table=table, rule=rule, move=move, columns=columns,
                    key=() if renumbered else tuple(k[1] for k in keys), renumbered=renumbered,
                    virtual=present[table])
    return found


class _Import:
    """The person rules of one import: the exporter is the new owner here,
    a departed person stays departed, and a person of this install is never
    named by a bundle."""

    def __init__(self, exporter: str, owner: str, here: set[str]) -> None:
        from kazma_core.spaces.identity import DEPARTED_ACCOUNT

        self.exporter = exporter
        self.owner = owner
        self.here = here
        self.departed = DEPARTED_ACCOUNT

    def check_row(self, move: MoveRule, row: dict[str, Any], table: str) -> None:
        for spec in move.entries:
            entry = _entry_of(_column_value(row, spec))
            if entry is not None and entry != self.exporter:
                raise SpaceMoveRefused("foreign_member", 409, f"A row of {table} is another member's entry.")
        if move.work:
            who = str(_column_value(row, move.work) or "")
            if who and who != self.exporter:
                raise SpaceMoveRefused("members_work", 409, f"A row of {table} is another person's work.")
        if move.grant:
            who = str(row.get(move.grant) or "")
            if who and who not in (self.exporter,) and (who == self.departed or who in self.here):
                raise SpaceMoveRefused("foreign_person", 409, f"A row of {table} grants another person access.")

        def look(key: str, value: str) -> str:
            if key in ACCOUNT_FIELDS and value not in (self.exporter, self.departed) and value in self.here:
                raise SpaceMoveRefused("foreign_person", 409, f"A row of {table} names a person of this install.")
            entry = _entry_of(value)
            if entry is not None and entry not in (self.exporter, self.departed):
                raise SpaceMoveRefused("foreign_member", 409, f"A row of {table} names another member's entry.")
            return value

        for key, value in row.items():
            _rewrite(value, look, key)
        if table in ("checkpoints", "writes") and "type" in row:
            try:
                _rewrite_checkpoint(row, look)
            except SpaceMoveRefused:
                raise
            except ValueError:
                raise SpaceMoveRefused("not_a_bundle", 400, "A chat's state cannot be read.") from None

    def swap(self, key: str, value: str) -> str:
        if key in _NAME_FIELDS:
            return ""
        if key in ACCOUNT_FIELDS and value == self.exporter:
            return self.owner
        if _entry_of(value) == self.exporter:
            return f"{_MEMBER}{self.owner}"
        return value


def _check_rows(bundle: _Bundle, targets: dict[tuple[str, str], _Target], ctx: space_data._Context,
                people: _Import) -> None:
    """Every row the space's and allowed to move, every column known, no key
    here already."""
    for (store, table), target in targets.items():
        with closing(_read_only(target.path)) as conn:
            for raw in bundle.lines(store, table):
                row = _decoded(raw)
                if not _belongs(row, target.rule, ctx):
                    raise SpaceMoveRefused("foreign_row", 409,
                                           f"The bundle carries a row of {table} that is not the space's.")
                unknown = set(row) - set(target.columns)
                if unknown:
                    raise SpaceMoveRefused("unknown_column", 409,
                                           f"This install's {table} has no column {sorted(unknown)[0]}.")
                people.check_row(target.move, row, table)
                if target.key and not target.virtual:
                    where = " AND ".join(f'"{c}" IS ?' for c in target.key)
                    if conn.execute(f'SELECT 1 FROM "{table}" WHERE {where} LIMIT 1',
                                    tuple(row.get(c) for c in target.key)).fetchone():
                        raise SpaceMoveRefused("id_collides", 409, f"A row of {table} is here already.")


def _nothing_of_the_space_here(space: str) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity

    reason = ""
    if space in identity.read_registry()["spaces"]:
        reason = "the identity store"
    elif sum(item.rows for item in space_data._walk(space)[1]):
        reason = "a store's rows"
    elif any(key.startswith(f"tenant.{space}.") for group in get_config_store().get_all().values() for key in group):
        reason = "a setting"
    elif space_data._vault_rows(space) or space_data._vault_keys(space):
        reason = "a secret"
    elif any(path.exists() for path in _space_folders(space).values()):
        reason = "a folder"
    if reason:
        raise SpaceMoveRefused("space_exists", 409, f"This install holds the space already ({reason}).")


def _file_entries(bundle: _Bundle) -> list[tuple[str, PurePosixPath, str]]:
    """``(part, path inside the part's folder, entry name)``, each checked."""
    out = []
    for name in bundle.manifest["entries"]:
        if not name.startswith("files/"):
            continue
        parts = PurePosixPath(name).parts
        if len(parts) < 3 or parts[1] not in ("space", "documents") or any(
                p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
            raise SpaceMoveRefused("not_a_bundle", 400, "The bundle names a file it should not.")
        out.append((parts[1], PurePosixPath(*parts[2:]), name))
    return out


def _check_threads(threads: list[str]) -> None:
    from kazma_core.spaces import threads as thread_store

    for thread in threads:
        if thread_store.record_of(thread) is not None:
            raise SpaceMoveRefused("id_collides", 409, "A chat of the bundle is here already.")


def _secret_rows(bundle: _Bundle, space: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "secrets.jsonl" not in bundle.manifest["entries"]:
        if int(bundle.manifest.get("secrets") or 0):
            raise SpaceMoveRefused("changed", 400, "The bundle's secrets are not the ones it lists.")
        return rows
    with bundle.zipped.open("secrets.jsonl") as handle:
        while True:
            raw = handle.readline(MAX_JSON_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_JSON_BYTES:
                raise SpaceMoveRefused("too_big", 413, "A secret of the bundle is bigger than a secret may be.")
            if not raw.strip():
                continue
            row = json.loads(raw.decode("utf-8"))
            if not isinstance(row, dict):
                raise SpaceMoveRefused("not_a_bundle", 400, "A secret of the bundle is not as written.")
            rows.append({**row, "encrypted_value": _unb64(row.get("encrypted_value")),
                         "nonce": _unb64(row.get("nonce"))})
    if len(rows) != int(bundle.manifest.get("secrets") or 0):
        raise SpaceMoveRefused("changed", 400, "The bundle's secrets are not the ones it lists.")
    return rows


def _keys(bundle: _Bundle) -> list[dict[str, Any]]:
    held = bundle.json("keys.json", [])
    if not isinstance(held, list) or len(held) != int(bundle.manifest.get("keys") or 0):
        raise SpaceMoveRefused("changed", 400, "The bundle's keys are not the ones it lists.")
    keys = []
    for item in held:
        if not isinstance(item, dict):
            raise SpaceMoveRefused("not_a_bundle", 400, "A key of the bundle is not as written.")
        keys.append({"key_id": str(item.get("key_id") or ""), "state": item.get("state"),
                     "created_at": item.get("created_at"), "retired_at": item.get("retired_at"),
                     "key": _unb64(item.get("key"))})
    return keys


# ── writing ─────────────────────────────────────────────────────────────

_NOW = Callable[[], str]


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _owner_address(owner: str) -> str:
    """Where the new owner's own reminders go here (S3-05): their linked
    chat, the admin's operator Telegram, else none."""
    from kazma_core.spaces import chat_links

    if owner == OWNER_ACCOUNT_ID:
        from kazma_core.tools.send_message import operator_telegram_target

        return operator_telegram_target()
    return chat_links.address_of(owner)


def _stop_cron(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    row["delivery_target"] = ctx["address"]
    row.update(fire_owner="", lease_until=0, fire_attempt=0, fire_due="", pending_edit="", pending_cancel=0,
               delivery_state="", delivery_error="")
    if str(row.get("status") or "") in ("pending", "running", "delivering"):
        row.update(status="cancelled", finished_at=ctx["now"], last_outcome="moved",
                   last_result="Moved from another install: restore it to run it here.")
    return row


def _stop_swarm(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except ValueError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    for key in ("source_delivery_target", "source_platform"):
        meta.pop(key, None)
    status = str(row.get("status") or "")
    if status in ("pending", "running", "paused"):
        meta["interrupted_by"] = "moved"
        meta["execution_outcome"] = "none_invoked" if status == "pending" else "unknown"
        row["status"] = "cancelled"
        row["completed_at"] = ctx["now"]
    row["metadata"] = json.dumps(meta, ensure_ascii=False)
    return row


def _stop_research(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if str(row.get("status") or "") in ("pending", "running", "cancelling"):
        row["status"] = "interrupted"
        row["message"] = "Moved from another install: start it again here."
    return row


def _stop_x_operation(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    state = str(row.get("state") or "")
    if state in ("scheduled", "deferred"):
        row["state"] = "awaiting_approval"
        row["reason"] = "Moved from another install: approve it to publish from here."
    elif state == "sending":
        row["state"] = "outcome_unknown"
    row["lease_until"] = 0
    return row


def _stop_x_thread(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    if str(row.get("state") or "") == "running":
        row.update(state="cancelled", owner="", lease_until=0, token="")
    return row


#: How live work is stopped on arrival, table by table (every ``stopped``
#: rule has one).
_STOPS: dict[tuple[str, str], Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    ("cron.db", "cron_jobs"): _stop_cron,
    ("swarm_tasks.db", "swarm_tasks"): _stop_swarm,
    ("research_sessions.db", "research_sessions"): _stop_research,
    ("x_publications.db", "x_operations"): _stop_x_operation,
    ("x_publications.db", "x_threads"): _stop_x_thread,
}


def _write_rows(bundle: _Bundle, targets: dict[tuple[str, str], _Target], people: _Import,
                stop_ctx: dict[str, Any]) -> int:
    by_path: dict[Path, list[_Target]] = {}
    for target in targets.values():
        by_path.setdefault(target.path, []).append(target)

    def _order(path: Path) -> int:
        stores = {t.store for t in by_path[path]}
        return 0 if stores & set(_FIRST) else 1

    written = 0
    for path in sorted(by_path, key=_order):
        items = by_path[path]
        checkpoints = any(t.store == CHECKPOINTS for t in items)
        if checkpoints:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(str(path), check_same_thread=False)) as made:
                _checkpoint_schema(made)
        with closing(sqlite3.connect(str(path), timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                for target in items:
                    if target.virtual:
                        continue  # an index: written by its store's own code below
                    stop = _STOPS.get((target.store, target.table))
                    for raw in bundle.lines(target.store, target.table):
                        row = {k: _rewrite(v, people.swap, k)[1] for k, v in _decoded(raw).items()}
                        if checkpoints:
                            row = _rewrite_checkpoint(row, people.swap)
                        if stop is not None:
                            row = stop(row, stop_ctx)
                        if target.renumbered:
                            row.pop(target.renumbered, None)
                        columns = ", ".join(f'"{c}"' for c in row)
                        cur = conn.execute(
                            f'INSERT INTO "{target.table}" ({columns}) VALUES ({", ".join("?" * len(row))})',
                            tuple(row.values()))
                        written += 1
                        if target.store == "knowledge.db" and target.table == "knowledge_chunks":
                            _index_chunk(conn, cur.lastrowid, row)
                conn.execute("COMMIT")
                committed = True
            except sqlite3.IntegrityError as exc:
                raise SpaceMoveRefused("id_collides", 409, f"A row of {path.name} is here already.") from exc
            finally:
                if not committed:
                    conn.execute("ROLLBACK")
    return written


def _index_chunk(conn: sqlite3.Connection, rowid: int | None, row: dict[str, Any]) -> None:
    """A moved chunk's keyword index row, as Knowledge writes it (keyed by the
    chunk's own new row id)."""
    if rowid is None or not row.get("active", 1) or row.get("tombstoned", 0):
        return
    from kazma_core.stores.knowledge import KnowledgeStore

    KnowledgeStore._fts_insert(conn, int(rowid), str(row.get("content") or ""), str(row.get("library_id") or ""),
                               str(row.get("source_url") or ""), str(row.get("id") or ""))


def _expire_waiting_cards(space: str, threads: list[str]) -> int:
    """A moved chat paused at an approval card: the card was asked on the
    old install, where it may be answered too, so here it is expired (the
    registry's own transition), never answered. Returns the cards."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from kazma_core.paths import space_checkpoints_db
    from kazma_core.safety import hitl_gates

    path = space_checkpoints_db(space)
    if not threads or not path.is_file():
        return 0
    expired = 0
    with closing(sqlite3.connect(str(path), check_same_thread=False)) as conn:
        saver = SqliteSaver(conn, serde=kazma_checkpoint_serde())
        for thread in threads:
            found = saver.get_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
            for _task, channel, value in getattr(found, "pending_writes", None) or ():
                if channel != "__interrupt__":
                    continue
                for interrupt in value if isinstance(value, list | tuple) else (value,):
                    gate_id = str(getattr(interrupt, "id", "") or getattr(interrupt, "interrupt_id", "") or "")
                    if not gate_id:
                        continue
                    payload = getattr(interrupt, "value", None)
                    card = payload if isinstance(payload, dict) else {}
                    hitl_gates.register_gate(hitl_gates.GateRow(
                        gate_id=gate_id, thread_id=thread, tool=str(card.get("tool") or card.get("tool_name") or ""),
                        tenant_id=space, message="asked on another install before the space moved here"))
                    if hitl_gates.timeout_gate(gate_id) is not None:
                        expired += 1
    return expired


def _write_settings(space: str, settings: dict[str, Any], people: _Import) -> int:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    for key, held in settings.items():
        category = str(held.get("category") or "general")
        _changed, value = _rewrite(held["value"], people.swap)
        if key in _MAPPINGS:
            inside = _MAPPINGS[key]

            def _add(current: Any, inside: tuple[str, ...] = inside, value: Any = value) -> Any:
                current = current if isinstance(current, dict) else {}
                holder = current
                for step in inside:
                    holder = holder.setdefault(step, {})
                if space in holder:
                    raise SpaceMoveRefused("id_collides", 409, "The space's entry is here already.")
                holder[space] = value[space]
                return current

            store.atomic_update(key, _add, category)
        else:
            store.set(key, value, category)
    return len(settings)


def _write_files(bundle: _Bundle, entries: list[tuple[str, PurePosixPath, str]]) -> int:
    folders = _space_folders(bundle.space)
    staged = {part: where.with_name(where.name + f".moving-{_secrets.token_hex(4)}")
              for part, where in folders.items()}
    try:
        for part, inside, name in entries:
            path = staged[part].joinpath(*inside.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.zipped.open(name) as src, open(path, "wb") as out:
                shutil.copyfileobj(src, out)
        for part, where in folders.items():
            if staged[part].exists():
                where.parent.mkdir(parents=True, exist_ok=True)
                staged[part].replace(where)
    finally:
        for path in staged.values():
            shutil.rmtree(path, ignore_errors=True)
    return len(entries)


def _journal(space: str, *, add: bool, by: str = "") -> None:
    from kazma_core.config_store import get_config_store

    def _change(current: Any) -> Any:
        held = dict(current) if isinstance(current, dict) else {}
        if add:
            held[space] = {"by": by, "started_at": _iso_now()}
        else:
            held.pop(space, None)
        return held

    get_config_store().atomic_update(JOURNAL_KEY, _change, "system")


def _take_back_failures() -> tuple[type[BaseException], ...]:
    """What a take-back can fail with: a store's file, its database, the
    vault, the settings store away, a walk that refuses."""
    from kazma_core.config_availability import ConfigStoreUnavailableError

    return (OSError, sqlite3.Error, ValueError, ConfigStoreUnavailableError)


def _take_back(space: str, earlier: space_data._Context | None = None) -> None:
    """Remove everything of *space* this install holds: the space was not
    here before its import began (checked), so all of it is the import's."""
    from kazma_core.paths import space_checkpoints_db
    from kazma_core.security.vault import get_vault
    from kazma_core.spaces import threads

    ctx, found = space_data._walk(space, earlier)
    space_data._erase_rows(found, ctx)
    own = space_checkpoints_db(space)  # its own file, made by the import
    for path in (own, own.with_name(own.name + "-wal"), own.with_name(own.name + "-shm")):
        path.unlink(missing_ok=True)
    space_data._erase_settings(space, ctx.ids["threads"])
    for thread in ctx.ids["threads"]:
        threads.forget_thread(thread)  # its record went with the rows; its cached space too
    if space_data._vault_rows(space) or space_data._vault_keys(space):
        vault = get_vault()
        if vault is None:
            raise SpaceMoveRefused("vault_unavailable", 503, "The vault cannot be opened.")
        vault.erase_space(space)
    for where in _space_folders(space).values():
        if where.exists():
            shutil.rmtree(where)
        for staged in where.parent.glob(where.name + ".moving-*") if where.parent.is_dir() else ():
            shutil.rmtree(staged, ignore_errors=True)


def importing_spaces() -> frozenset[str]:
    """The spaces an import is writing now, or left half written by a stop:
    in the import journal. Their people are recorded last, so what reads "no
    member left" or "no such space" leaves them alone. **Read what is stored
    first, then this, then the identity store**: the journal is written
    before anything of the space and emptied after it is recorded, so one
    read after the other never misses it. Blocking (settings)."""
    from kazma_core.config_store import get_config_store

    held = get_config_store().get(JOURNAL_KEY)
    return frozenset(str(space) for space in held) if isinstance(held, dict) else frozenset()


def settle_at_start() -> None:
    """:func:`settle_unfinished_imports` as Kazma starts: a stop in the
    middle of an import leaves nothing of it until the next one. Nothing is
    read where Spaces is not enforced (no space is brought in there).
    Blocking."""
    from kazma_core.spaces.identity import IdentityUnavailable

    if not spaces_enforced():
        return
    try:
        settle_unfinished_imports()
    except (IdentityUnavailable, *_take_back_failures()):  # the next import or start tries again
        logger.warning("[spaces] an unfinished import could not be looked for at start", exc_info=True)


def settle_unfinished_imports() -> list[str]:
    """Take back every import a stop cut off (its space is in the journal and
    not in the identity store), never one that is running. Returns the spaces
    taken back; one whose take back failed stays in the journal for the next
    call."""
    with _IMPORT_LOCK:
        return _settle()


def _settle() -> list[str]:
    """:func:`settle_unfinished_imports`, the import lock held."""
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity

    held = get_config_store().get(JOURNAL_KEY)
    if not isinstance(held, dict) or not held:
        return []
    recorded = identity.read_registry()["spaces"]
    settled: list[str] = []
    for space in list(held):
        if not valid_space_id(space):
            continue
        if space not in recorded:
            try:
                _take_back(space)
            except _take_back_failures():
                logger.warning("[spaces] an unfinished import of space %s could not be taken back yet", space,
                               exc_info=True)
                continue
        _journal(space, add=False)
        settled.append(space)
        logger.warning("[spaces] an unfinished import of space %s was taken back", space)
    return settled


# ── import ──────────────────────────────────────────────────────────────


def import_bundle(source: Path, password: str, *, owner: str, by: str) -> dict[str, Any]:
    """Take the space in *source* into this install, owned by *owner*. *by*
    is the admin doing it. Returns what was taken (counts)."""
    from kazma_core.db.backend import is_postgres
    from kazma_core.memory.state_backend import mirrors_to_postgres
    from kazma_core.security.sealed_file import SealedFileRefused, SealedReader
    from kazma_core.spaces import identity, people

    if not spaces_enforced():
        raise SpaceMoveRefused("spaces_off", 409, "A space is brought in once Spaces is on for this install.")
    if is_postgres() or mirrors_to_postgres():
        raise SpaceMoveRefused("postgres_not_yet", 409, "Bringing a space in is not done on a Postgres install yet.")
    if not identity.is_admin_account(by):
        raise SpaceMoveRefused("admin_only", 403, "Only the admin brings a space in.")
    registry = identity.read_registry()
    if owner not in registry["accounts"]:
        raise SpaceMoveRefused("no_such_account", 400, "There is no such person here.")
    with _IMPORT_LOCK:
        _settle()
        try:
            reader = SealedReader(source, password, purpose=FORMAT, max_bytes=MAX_BUNDLE_BYTES)
        except SealedFileRefused as exc:
            status = 413 if exc.code == "too_big" else 400
            code = "not_a_bundle" if exc.code == "not_sealed" else exc.code
            raise SpaceMoveRefused(code, status, "The bundle was not opened.") from exc
        with closing(reader):
            bundle = _open_bundle(reader)
            with closing(bundle.zipped):
                return _take_in(bundle, owner=owner, by=by, people_module=people,
                                here=_people(registry) - {owner})


def _take_in(bundle: _Bundle, *, owner: str, by: str, people_module: Any, here: set[str]) -> dict[str, Any]:
    from kazma_core.security.vault import SpaceSecretsRefused, get_vault
    from kazma_core.spaces import identity

    space = bundle.space
    people = _Import(bundle.exporter, owner, here)
    try:
        name = people_module._checked_name(str(bundle.manifest.get("name") or space))
    except people_module.PeopleRefused:
        raise SpaceMoveRefused("not_a_bundle", 400, "The bundle's space has no name this install takes.") from None
    _nothing_of_the_space_here(space)
    targets = _targets(bundle)
    ctx = _bundle_context(bundle)
    _check_rows(bundle, targets, ctx, people)
    _check_threads(ctx.ids["threads"])
    rows = _secret_rows(bundle, space)
    for row in rows:
        people.check_row(MoveRule("moves"), {"metadata": row.get("metadata") or "{}"}, "the vault")
        _, row["metadata"] = _rewrite(row.get("metadata") or "{}", people.swap)
    settings = bundle.json("settings.json", {})
    if not isinstance(settings, dict):
        raise SpaceMoveRefused("not_a_bundle", 400, "The bundle's settings are not as written.")
    _check_settings(space, settings, {str(row.get("name") or "") for row in rows})
    entries = _file_entries(bundle)
    keys = _keys(bundle)
    vault = get_vault() if rows or keys else None
    if (rows or keys) and vault is None:
        raise SpaceMoveRefused("vault_unavailable", 503, "The vault cannot be opened; nothing was taken.")
    stop_ctx = {"now": _iso_now(), "address": _owner_address(owner)}
    # Everything checked: write, the journal first, the identity store last.
    _journal(space, add=True, by=by)
    done = False
    try:
        if vault is not None:
            try:
                vault.import_space_sealed(space, keys, rows)
            except SpaceSecretsRefused as exc:
                status = 409 if exc.code in ("space_in_vault", "key_collides", "name_collides") else 400
                code = "id_collides" if exc.code in ("space_in_vault", "key_collides", "name_collides") else (
                    "foreign_secret" if exc.code == "not_the_spaces" else "changed")
                raise SpaceMoveRefused(code, status, "The bundle's secrets were not taken.") from exc
        written = _write_rows(bundle, targets, people, stop_ctx)
        expired = _expire_waiting_cards(space, ctx.ids["threads"])
        _write_settings(space, settings, people)
        files = _write_files(bundle, entries)
        try:
            people_module.record_moved_space(space, name, owner, by=by)
        except (people_module.PeopleRefused, identity.IdentityError) as exc:
            code = exc.code if exc.code in ("space_exists", "no_such_account", "admin_only") else "not_recorded"
            raise SpaceMoveRefused(code, 409, "The space could not be recorded; nothing was brought in.") from exc
        done = True
    finally:
        for key in keys:
            key["key"] = b""
        if not done:
            try:
                _take_back(space, ctx)
                _journal(space, add=False)
            except _take_back_failures():  # the journal keeps it for the next import's settle
                logger.warning("[spaces] the import of space %s was not taken back yet; the next import will",
                               space, exc_info=True)
    _journal(space, add=False)
    logger.info("[spaces] space moved in space=%s owner=%s by=%s rows=%d files=%d secrets=%d cards=%d",
                space, owner, by, written, files, len(rows), expired)
    return {"space": space, "name": str(bundle.manifest.get("name") or ""), "rows": written,
            "settings": len(settings), "secrets": len(rows), "files": files, "cards_expired": expired}
