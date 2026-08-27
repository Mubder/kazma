"""Task Ledger — the durable, structured source of truth for "what we're doing".

Born from the 2026-08-27 "proceed with next → git commit" incident: a
one-word continuation was resolved against CONVERSATION HISTORY, which a
mid-stream Stop had just truncated to a 158-char fragment, so the loudest
signal left in the prompt was the ambient workspace ("uncommitted changes…")
and the model resolved "next" to it. The transcript is a corruptible,
append-only narrative; it must not be the resolution surface for intent.

The Ledger replaces that with an owned, structured object:

* ``goal`` — the mission in one sentence.
* ``steps`` — the declared plan, each with status (pending/running/done/
  failed) and a one-line result.
* ``next_action`` — THE declared next step: the binding target for short
  continuation replies ("proceed", "next", "كمّل"). Extracted deterministically
  from the assistant's own closing lines and plan fences; the model can also
  maintain it deliberately via the ``task_ledger_update`` tool.
* ``findings`` — accumulated results worth keeping (e.g. the green names).
* ``open_questions`` — unresolved forks the user should be asked about.

Lifecycle: one ACTIVE ledger per thread; a topic shift supersedes it (kept
for history). It survives restarts, refreshes, truncated replies, and new
sessions — because it lives in SQLite (WAL), not the transcript.

Resolution contract (see :func:`resolve_continuation`):
* short continuation reply + active ledger with a next_action → BOUND: the
  turn context states exactly which step "next" means (with an escape clause
  for genuinely-new tasks — mirrors the long-task continue protocol).
* continuation reply but NO declared next step → CLARIFY: the supervisor
  locks the turn to clarification-only (tools filtered out) so a misread
  costs a question, never an action.
* anything else → PASS THROUGH to the model unchanged.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

__all__ = [
    "GIT_WRITE_RE",
    "TaskLedger",
    "TaskLedgerStore",
    "extract_next_action",
    "format_ledger_block",
    "get_ledger_store",
    "is_git_write_command",
    "resolve_continuation",
    "reset_ledger_store",
]

_ACTIVE = "active"

_CREATE = """
CREATE TABLE IF NOT EXISTS task_ledgers (
    thread_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'active',
    goal TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    steps TEXT NOT NULL DEFAULT '[]',
    findings TEXT NOT NULL DEFAULT '[]',
    open_questions TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL,
    superseded_by TEXT,
    clarify_pending INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_ledgers_tenant ON task_ledgers(tenant_id, status);
"""

# Migration for pre-clarify_pending databases (CREATE IF NOT EXISTS will
# not add a column to an existing table).
_ALTER_CLARIFY = "ALTER TABLE task_ledgers ADD COLUMN clarify_pending INTEGER NOT NULL DEFAULT 0"


# ── Model ─────────────────────────────────────────────────────────────


@dataclass
class LedgerStep:
    text: str
    status: str = "pending"  # pending | running | done | failed
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "status": self.status, "result": self.result}


@dataclass
class TaskLedger:
    thread_id: str
    goal: str = ""
    next_action: str = ""
    steps: list[LedgerStep] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    status: str = _ACTIVE
    clarify_pending: bool = False
    tenant_id: str = "default"
    updated_at: float = field(default_factory=time.time)
    superseded_by: str | None = None

    # ── mutation helpers (the ONLY sanctioned writers) ────────────────

    def set_plan(self, steps: list[str]) -> None:
        """Replace the plan from a ```plan fence (statuses reset)."""
        self.steps = [LedgerStep(text=str(s).strip()) for s in steps if str(s).strip()]
        self.touch()

    def declare_next(self, action: str) -> None:
        """Set the declared next action — the binding target."""
        action = str(action or "").strip()
        if action:
            self.next_action = action[:400]
            self.touch()

    def mark_step(self, index: int, status: str, result: str = "") -> None:
        if 0 <= index < len(self.steps):
            self.steps[index].status = status
            if result:
                self.steps[index].result = result[:300]
            self.touch()

    def add_finding(self, finding: str) -> None:
        finding = str(finding or "").strip()
        if finding and finding not in self.findings:
            self.findings.append(finding[:300])
            self.touch()

    def add_open_question(self, question: str) -> None:
        question = str(question or "").strip()
        if question and question not in self.open_questions:
            self.open_questions.append(question[:300])
            self.touch()

    def supersede(self, by: str = "") -> None:
        self.status = "superseded"
        self.superseded_by = by[:120] or None
        self.touch()

    def complete(self) -> None:
        self.status = "done"
        self.next_action = ""
        self.touch()

    def touch(self) -> None:
        self.updated_at = time.time()

    # ── serialization ─────────────────────────────────────────────────

    def to_row(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "goal": self.goal,
            "next_action": self.next_action,
            "steps": json.dumps([s.to_dict() for s in self.steps], ensure_ascii=False),
            "findings": json.dumps(self.findings, ensure_ascii=False),
            "open_questions": json.dumps(self.open_questions, ensure_ascii=False),
            "updated_at": self.updated_at,
            "superseded_by": self.superseded_by,
            "clarify_pending": 1 if self.clarify_pending else 0,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> "TaskLedger":
        # sqlite3.Row has no .get() — normalize to a plain dict first.
        if not isinstance(row, dict):
            try:
                row = {k: row[k] for k in row.keys()}
            except Exception:
                return cls(thread_id="")

        def _loads(v: str) -> list[Any]:
            try:
                out = json.loads(v) if v else []
                return out if isinstance(out, list) else []
            except Exception:
                return []

        return cls(
            thread_id=str(row["thread_id"]),
            tenant_id=str(row.get("tenant_id", "default") or "default"),
            status=str(row.get("status", _ACTIVE) or _ACTIVE),
            goal=str(row.get("goal", "") or ""),
            next_action=str(row.get("next_action", "") or ""),
            steps=[LedgerStep(**{k: s.get(k, "" if k != "status" else "pending")
                                 for k in ("text", "status", "result")})
                   for s in _loads(row.get("steps", "[]")) if isinstance(s, dict) and s.get("text")],
            findings=[str(x) for x in _loads(row.get("findings", "[]"))],
            open_questions=[str(x) for x in _loads(row.get("open_questions", "[]"))],
            updated_at=float(row.get("updated_at", 0) or 0),
            superseded_by=row.get("superseded_by"),
            clarify_pending=bool(row.get("clarify_pending", 0)),
        )


# ── Store ─────────────────────────────────────────────────────────────


class TaskLedgerStore:
    """SQLite WAL store — one row per thread, tenant-scoped, never raises
    on read paths (a broken store degrades to 'no ledger')."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "task_ledgers.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE)
                try:
                    conn.execute(_ALTER_CLARIFY)
                except sqlite3.OperationalError:
                    pass  # column already exists
                conn.commit()
            finally:
                conn.close()

    def save(self, ledger: TaskLedger) -> None:
        with self._lock:
            conn = self._connect()
            try:
                row = ledger.to_row()
                conn.execute(
                    "INSERT OR REPLACE INTO task_ledgers "
                    "(thread_id, tenant_id, status, goal, next_action, steps, "
                    " findings, open_questions, updated_at, superseded_by, "
                    " clarify_pending) "
                    "VALUES (:thread_id, :tenant_id, :status, :goal, :next_action, "
                    ":steps, :findings, :open_questions, :updated_at, "
                    ":superseded_by, :clarify_pending)",
                    row,
                )
                conn.commit()
            finally:
                conn.close()

    def active_for(self, thread_id: str, tenant_id: str = "default") -> TaskLedger | None:
        """The ACTIVE ledger for a thread, or None. Never raises."""
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT * FROM task_ledgers "
                    "WHERE thread_id = ? AND status = 'active'",
                    (str(thread_id),),
                )
                row = cur.fetchone()
                return TaskLedger.from_row(row) if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("[task-ledger] read failed", exc_info=True)
            return None

    def get_or_create(self, thread_id: str, tenant_id: str = "default") -> TaskLedger:
        led = self.active_for(thread_id, tenant_id)
        if led is not None:
            return led
        led = TaskLedger(thread_id=str(thread_id), tenant_id=tenant_id or "default")
        self.save(led)
        return led

    def history(self, thread_id: str, limit: int = 10) -> list[TaskLedger]:
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM task_ledgers WHERE thread_id = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (str(thread_id), int(limit)),
                ).fetchall()
                return [TaskLedger.from_row(r) for r in rows]
            finally:
                conn.close()
        except Exception:
            return []


_store: TaskLedgerStore | None = None
_store_lock = threading.Lock()


def get_ledger_store() -> TaskLedgerStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TaskLedgerStore()
    return _store


def reset_ledger_store(db_path: str | Path | None = None) -> TaskLedgerStore:
    """(Re)create the singleton — test isolation helper."""
    global _store
    with _store_lock:
        _store = TaskLedgerStore(db_path)
    return _store


# ── Deterministic extraction ──────────────────────────────────────────

# Declared-next-step patterns. The model habitually closes a narration with
# what it is about to do — that sentence IS the binding target. EN + AR.
_NEXT_PATTERNS = [
    r"(?:^|\n)\s*(?:now|next|then)(?:[,:]\s+|\s+)(.{12,400}?)(?:\.\s|\.$|\n|$)",
    r"(?:^|\n)\s*(?:i(?:'m| am)(?: now)? (?:going to|about to)|i'll|i will)\s+(.{12,400}?)(?:\.\s|\.$|\n|$)",
    r"(?:^|\n)\s*(?:proceeding with|moving on to|continuing with|sweeping|checking)\s+(.{12,400}?)(?:\.\s|\.$|\n|$)",
    r"(?:^|\n)\s*(?:الآن|التالي|ثم)(?:[,:،]\s*|\s+)(.{12,400}?)(?:\n|$)",
    r"(?:^|\n)\s*(?:سأقوم ب|سوف أ|أكمل|نكمل)\s+(.{12,400}?)(?:\n|$)",
]
_NEXT_RE = [re.compile(p, re.IGNORECASE) for p in _NEXT_PATTERNS]

_STRIP_MD = re.compile(r"[*_`>#\[\]()]")


def extract_next_action(text: str) -> str:
    """The LAST declared next step in an assistant reply, or ''.

    Deterministic — no LLM call. Markdown decoration is stripped; the last
    match wins (the model re-declares as it goes, latest = current intent).
    """
    s = str(text or "").strip()
    if not s:
        return ""
    best = ""
    for rx in _NEXT_RE:
        for m in rx.finditer(s):
            cand = _STRIP_MD.sub("", m.group(1)).strip().rstrip(".")
            if len(cand) >= 10:
                best = cand
    return best[:400]


# ── Prompt block ──────────────────────────────────────────────────────

_LEDGER_MARKER = "[KAZMA:TASK-LEDGER"


def format_ledger_block(ledger: TaskLedger, *, binding: str = "") -> str:
    """Compact system block injected every turn. Empty when nothing to say.

    ``binding`` carries the resolved continuation ('proceed' → step X) with
    the standard escape clause: a genuinely-new user task overrides it.
    """
    if ledger.status != _ACTIVE:
        return ""
    lines: list[str] = [_LEDGER_MARKER + "]"]
    if ledger.goal:
        lines.append(f"Task: {ledger.goal[:240]}")
    if ledger.steps:
        done = sum(1 for s in ledger.steps if s.status == "done")
        lines.append(f"Plan: {done}/{len(ledger.steps)} steps done")
        for i, s in enumerate(ledger.steps[-6:], start=max(0, len(ledger.steps) - 6)):
            mark = {"done": "x", "running": ">", "failed": "!"}.get(s.status, " ")
            lines.append(f"  [{mark}] {s.text[:140]}")
    if ledger.next_action:
        lines.append(f"NEXT STEP (declared): {ledger.next_action}")
    for f in ledger.findings[-5:]:
        lines.append(f"  finding: {f[:160]}")
    if binding:
        lines.append("")
        lines.append(
            f"CONTINUATION BINDING: the user's short reply means: {binding} "
            "(the task's declared next step above). If the user's latest "
            "message is clearly a NEW task instead, ignore this binding "
            "and follow the new task."
        )
    return "\n".join(lines)


# ── Resolution ────────────────────────────────────────────────────────


def resolve_continuation(
    user_text: str,
    ledger: TaskLedger | None,
    *,
    is_continuation: bool,
) -> dict[str, Any]:
    """Decide how a turn starts when the ledger is in play.

    Returns one of:
      {"mode": "pass"}                       — nothing to do
      {"mode": "bound", "binding": str}      — inject the binding block
      {"mode": "clarify", "question": str}   — lock the turn to a question
    """
    if not is_continuation:
        return {"mode": "pass"}
    if ledger is None or ledger.status != _ACTIVE:
        return {"mode": "pass"}
    if ledger.clarify_pending:
        # The previous turn ASKED a clarifying question and the user just
        # answered with a short continuation — proceeding to ask again would
        # loop forever (2026-08-27 live report). Unlock: the model proceeds
        # with its recommended option, tools available.
        return {
            "mode": "post_clarify",
            "directive": (
                "The user answered your clarifying question with a short "
                "'go ahead'. Proceed NOW with your recommended/most-likely "
                "option from the options you presented — do not ask again. "
                "Tools are available; CALL them as real tool calls (never "
                "narrate invocations as JSON or code blocks) and execute "
                "the next pipeline steps."
            ),
        }
    if ledger.next_action:
        return {
            "mode": "bound",
            "binding": f'"{ledger.next_action}"',
            "next_action": ledger.next_action,
        }
    # Continuation with no declared target: structural clarify. The goal,
    # if present, shapes the question; the supervisor locks tools off so
    # the ONLY sensible reply is a one-line question back to the user.
    goal = ledger.goal or "the current task"
    return {
        "mode": "clarify",
        "mark_pending": True,
        "question": (
            f'You asked to continue, but the last turn did not record a next '
            f'step. Ask the user ONE short clarifying question about what to '
            f'do next for: {goal}. Do not run any tools this turn, and reply '
            f'with PLAIN PROSE ONLY — no code blocks, no JSON, no tool-call '
            f'markup of any kind.'
        ),
    }


# ── Blast radius: git-write commands ──────────────────────────────────

# Git mutations that must NEVER run under YOLO auto-approval — a misread
# intent must cost a confirmation dialog, not a repo mutation (the
# 2026-08-27 incident: "proceed with next" → git commit).
GIT_WRITE_RE = re.compile(
    r"\bgit\s+(?:commit|push|merge|rebase|revert|reset|checkout\s+(?:--|\.)|"
    r"restore|clean|rm|branch\s+(?:-d|-D)|tag\s+-d|cherry-pick|stash\s+(?:drop|pop)|"
    r"apply|am|swap|subtree|filter-branch|update-ref| symbolic-ref)\b",
    re.IGNORECASE,
)


def is_git_write_command(command: str) -> bool:
    """True for git mutations (commit/push/reset/…) — even under YOLO these
    require an explicit approval card. Read-only git (status/log/diff) is
    exempt."""
    return bool(GIT_WRITE_RE.search(str(command or "")))
