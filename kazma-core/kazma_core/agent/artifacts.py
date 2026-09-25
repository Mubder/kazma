"""Durable turn-artifact store (context-integrity S1-2).

The fix that makes context loss survivable. Scratchpad findings and
outbound-draft proposals live in SQLite keyed ``(tenant_id, thread_id, key)``
so they survive deterministic trim, turn boundaries, process restarts, and a
corrupt LangGraph checkpoint. The graph state holds a read-through cache; the
store is the durable source of truth.

House patterns (not optional — AGENTS.md §8/§15E/§21F):
  - Connection opened through ``apply_sqlite_pragmas()`` (WAL +
    busy_timeout=5000 + synchronous=NORMAL) — the shared helper every other
    store uses; never hand-roll the pragmas.
  - DB lives under ``kazma-data/`` → covered by the universal WAL-safe
    backup for free, no new backup path.
  - Writes here are turn-artifact writes, off the hot chat-recall read path
    (the ops/state split rationale): short-lived per-call connections, no
    long-lived cursors.

Env override ``KAZMA_ARTIFACTS_DB`` (absolute path) for tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable

__all__ = [
    "ArtifactStore",
    "get_artifact_store",
    "reset_artifact_store",
]

#: ``evidence(tenant_id, text) -> (via, used_at, used_ref)`` when *text*
#: verifiably went out, else None. See :meth:`ArtifactStore.heal_legacy_posted`.
PublishEvidence = Callable[[str, str], "tuple[str, float, str] | None"]

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_artifacts (
    tenant_id    TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'finding',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (tenant_id, thread_id, key)
);
CREATE INDEX IF NOT EXISTS idx_agent_artifacts_key ON agent_artifacts(key);
CREATE INDEX IF NOT EXISTS idx_agent_artifacts_updated ON agent_artifacts(updated_at);
"""

# Retention (GC is wired into the existing commitment-GC cadence, not a new
# sweeper): per-thread cap + age-out. Scratchpad entries churn fast; a
# proposal awaiting approval must NOT age out from under a pending card, so
# proposals get a longer horizon.
_MAX_PER_THREAD = 128
_MAX_AGE_DAYS_FINDING = 14.0
_MAX_AGE_DAYS_PROPOSAL = 90.0

_PROPOSAL_PREFIX = "proposal:"


def _content_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()[:32]


class ArtifactStore:
    """SQLite-backed durable store for scratchpad findings and proposals."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    # ── internals ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        try:
            from kazma_core.config_store import apply_sqlite_pragmas

            apply_sqlite_pragmas(conn)
        except Exception:  # pragma: no cover - helper always present in prod
            pass
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _put(
        self,
        tenant_id: str,
        thread_id: str,
        key: str,
        value: str,
        kind: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_artifacts
                    (tenant_id, thread_id, key, value, kind, created_at, updated_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, thread_id, key) DO UPDATE SET
                    value = excluded.value,
                    kind = excluded.kind,
                    updated_at = excluded.updated_at,
                    content_hash = excluded.content_hash
                """,
                (
                    tenant_id or "default",
                    thread_id or "_default",
                    str(key),
                    str(value),
                    kind,
                    now,
                    now,
                    _content_hash(str(value)),
                ),
            )

    # ── scratchpad ───────────────────────────────────────────────────

    def put_scratchpad(
        self, tenant_id: str, thread_id: str, key: str, value: str
    ) -> None:
        """Durable write-through from apply_scratchpad_write (kind=finding)."""
        self._put(tenant_id, thread_id, f"scratchpad:{key[:80]}", value, "finding")

    def list_scratchpad(
        self, thread_id: str, *, tenant_id: str = "default"
    ) -> dict[str, str]:
        """Read-through for the working-memory anchor: key → value."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value FROM agent_artifacts
                WHERE tenant_id = ? AND thread_id = ?
                  AND kind = 'finding' AND key LIKE 'scratchpad:%'
                ORDER BY updated_at ASC
                """,
                (tenant_id or "default", thread_id or "_default"),
            ).fetchall()
        out: dict[str, str] = {}
        for k, v in rows:
            out[str(k)[len("scratchpad:"):]] = str(v)
        return out

    # ── proposals (S1-3) ─────────────────────────────────────────────
    #
    # One row holds a SET of drafts; each item carries its own state. A
    # draft is "used" once it was posted or booked (``used_at``/``used_via``/
    # ``used_ref`` on the item). The row's ``kind`` is DERIVED: it becomes
    # ``proposal_posted`` only when every item is used, and nothing sets it
    # any other way.
    #
    # Until 2026-09-25 posting ONE item flipped the whole row, so after 4 of
    # 11 drafts went out the other 7 vanished from X Studio and from any
    # reader, and moved onto the 14-day age-out meant for spent sets.

    @staticmethod
    def _parse_ref(ref: str) -> tuple[str, int | None] | None:
        """Split a proposal ref into (row key, item number or None).

        Accepts a proposal id (``prop_x``), an item id (``prop_x:3``), the
        ``prop_x#3`` form, and the stored key (``proposal:prop_x``). The one
        grammar every reader and writer of proposal refs uses.
        """
        ref = str(ref or "").strip()
        if not ref:
            return None
        item_no: int | None = None
        if "#" in ref:
            base, _, num = ref.partition("#")
            try:
                item_no = int(num)
                ref = base.strip()
            except ValueError:
                item_no = None
        # Full item ids ("prop_x:3") decompose into proposal key + item number.
        base, sep, tail = ref.rpartition(":")
        if sep and base.startswith("prop_") and tail.isdigit():
            item_no = int(tail)
            ref = base
        key = ref if ref.startswith(_PROPOSAL_PREFIX) else f"{_PROPOSAL_PREFIX}{ref}"
        return key, item_no

    @staticmethod
    def _item_matches(item: dict[str, Any], item_no: int | None) -> bool:
        if item_no is None:
            return True
        return str(item.get("id", "")).endswith(f":{item_no}")

    def save_proposal(
        self,
        tenant_id: str,
        thread_id: str,
        kind: str,
        items: list[Any],
    ) -> dict[str, Any]:
        """Persist an enumerated set of outbound drafts; returns stable IDs.

        The proposal is one artifact row whose value is JSON:
        ``{"kind": ..., "items": [{"id": ..., "text": ...}, ...]}``.
        IDs resolve across threads/restarts — approval must never depend on
        the drafts still being in conversation context.
        """
        clean = [str(i).strip() for i in (items or []) if str(i).strip()]
        if not clean:
            raise ValueError("save_proposal requires at least one non-empty item")
        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        payload = {
            "proposal_id": proposal_id,
            "kind": str(kind or "drafts")[:40],
            "items": [
                {"id": f"{proposal_id}:{n}", "text": t[:8000]}
                for n, t in enumerate(clean, start=1)
            ],
            "created_at": time.time(),
            "thread_id": thread_id or "",
        }
        self._put(
            tenant_id,
            thread_id,
            f"{_PROPOSAL_PREFIX}{proposal_id}",
            json.dumps(payload, ensure_ascii=False),
            "proposal",
        )
        return payload

    def resolve_proposal(
        self, ref: str, *, tenant_id: str = "default"
    ) -> dict[str, Any] | None:
        """Resolve a proposal id, a single item id, or the id + '#N' form.

        Returns ``{"proposal_id", "kind", "items": [...], "texts": [...]}``
        (single-item refs return a one-item list) or None when the id does
        not resolve. Items carry their own ``used_at``/``used_via`` state.
        """
        parsed = self._parse_ref(ref)
        if parsed is None:
            return None
        key, item_no = parsed
        tenant = tenant_id or "default"
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value FROM agent_artifacts
                WHERE key = ? AND tenant_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (key, tenant),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row[0]))
        except (TypeError, ValueError, AttributeError):
            return None
        items = [
            i for i in list(payload.get("items") or [])
            if isinstance(i, dict) and self._item_matches(i, item_no)
        ]
        if not items:
            return None
        return {
            "proposal_id": str(payload.get("proposal_id") or key[len(_PROPOSAL_PREFIX):]),
            "kind": str(payload.get("kind") or "drafts"),
            "items": items,
            "texts": [str(i.get("text") or "") for i in items],
        }

    def stored_text_for(
        self, ref: str, *, tenant_id: str = "default"
    ) -> str | None:
        """Exact stored text when *ref* names exactly ONE draft, else None.

        A publish sends one draft. A bare id of a multi-item set used to
        return item 1 here, so X Studio and the schedule API would post the
        first draft (and mark it) while the chat gate refused the same id;
        the rule is now the gate's everywhere: one call, one item.
        """
        info = self.resolve_proposal(ref, tenant_id=tenant_id)
        if not info or len(info.get("items") or []) != 1:
            return None
        text = str((info.get("texts") or [""])[0] or "").strip()
        return text or None

    def _proposal_payloads(
        self, *, tenant_id: str, kinds: tuple[str, ...], limit_rows: int
    ) -> list[tuple[dict[str, Any], str, str, float]]:
        placeholders = ",".join("?" * len(kinds))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT value, kind, thread_id, updated_at
                FROM agent_artifacts
                WHERE tenant_id = ? AND kind IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (tenant_id or "default", *kinds, limit_rows),
            ).fetchall()
        out: list[tuple[dict[str, Any], str, str, float]] = []
        for value, kind, thread_id, updated_at in rows:
            try:
                payload = json.loads(str(value))
            except (TypeError, ValueError, AttributeError):
                continue
            if isinstance(payload, dict):
                out.append((payload, str(kind), str(thread_id or ""), float(updated_at or 0)))
        return out

    @staticmethod
    def _item_used(item: dict[str, Any], row_kind: str) -> bool:
        # A fully-used row predating per-item state has no marks at all;
        # every item of it counts as used, which is what the row claims.
        if item.get("used_at"):
            return True
        return row_kind == "proposal_posted"

    def list_proposals(
        self,
        *,
        tenant_id: str = "default",
        limit: int = 50,
        include_posted: bool = False,
    ) -> list[dict[str, Any]]:
        """Newest outbound drafts, flattened to one row per item.

        X Studio lists these so a saved proposal survives trim and is still
        approvable from the composer. Used items stay out unless asked, and
        that is decided per ITEM: posting draft 1 of a set leaves 2..N here.
        """
        kinds = ("proposal", "proposal_posted") if include_posted else ("proposal",)
        bounded = max(1, min(int(limit or 50), 200))
        out: list[dict[str, Any]] = []
        for payload, kind, thread_id, updated_at in self._proposal_payloads(
            tenant_id=tenant_id, kinds=kinds, limit_rows=bounded
        ):
            created = payload.get("created_at") or updated_at
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                used = self._item_used(item, kind)
                if used and not include_posted:
                    continue
                out.append(
                    {
                        "id": str(item.get("id") or ""),
                        "proposal_id": str(payload.get("proposal_id") or ""),
                        "text": text,
                        "kind": str(payload.get("kind") or kind),
                        "thread_id": thread_id,
                        "created_at": float(created or 0),
                        "posted": used,
                        "used_at": float(item.get("used_at") or 0) or None,
                        "used_via": str(item.get("used_via") or ""),
                        "used_ref": str(item.get("used_ref") or ""),
                    }
                )
        return out[:bounded]

    def list_proposal_sets(
        self,
        *,
        tenant_id: str = "default",
        include_used: bool = False,
        limit_sets: int = 50,
    ) -> list[dict[str, Any]]:
        """Saved proposals, newest first, each with ALL its items and their state.

        The model-facing reader (``list_proposals`` tool) groups by set so it
        can say "7 of 11 unused". Sets with no unused item are left out
        unless *include_used*.
        """
        kinds = ("proposal", "proposal_posted") if include_used else ("proposal",)
        bounded = max(1, min(int(limit_sets or 50), 200))
        sets: list[dict[str, Any]] = []
        for payload, kind, thread_id, updated_at in self._proposal_payloads(
            tenant_id=tenant_id, kinds=kinds, limit_rows=bounded
        ):
            items = []
            for item in payload.get("items") or []:
                if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                    continue
                items.append({**item, "used": self._item_used(item, kind)})
            if not items:
                continue
            unused = sum(1 for i in items if not i["used"])
            if unused == 0 and not include_used:
                continue
            sets.append(
                {
                    "proposal_id": str(payload.get("proposal_id") or ""),
                    "kind": str(payload.get("kind") or "drafts"),
                    "created_at": float(payload.get("created_at") or updated_at or 0),
                    "thread_id": thread_id,
                    "items": items,
                    "unused": unused,
                }
            )
        sets.sort(key=lambda s: s["created_at"], reverse=True)
        return sets

    def proposal_set(
        self, ref: str, *, tenant_id: str = "default"
    ) -> dict[str, Any] | None:
        """One set shaped like :meth:`list_proposal_sets` entries, or None.

        An item ref returns the set with just that item; ``unused`` still
        counts the whole set.
        """
        parsed = self._parse_ref(ref)
        if parsed is None:
            return None
        key, item_no = parsed
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value, kind, thread_id, updated_at FROM agent_artifacts
                WHERE key = ? AND tenant_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (key, tenant_id or "default"),
            ).fetchone()
        if row is None:
            return None
        value, kind, thread_id, updated_at = row
        try:
            payload = json.loads(str(value))
        except (TypeError, ValueError, AttributeError):
            return None
        every = [
            {**i, "used": self._item_used(i, str(kind))}
            for i in (payload.get("items") or [])
            if isinstance(i, dict) and str(i.get("text") or "").strip()
        ]
        chosen = [i for i in every if self._item_matches(i, item_no)]
        if not chosen:
            return None
        return {
            "proposal_id": str(payload.get("proposal_id") or key[len(_PROPOSAL_PREFIX):]),
            "kind": str(payload.get("kind") or "drafts"),
            "created_at": float(payload.get("created_at") or updated_at or 0),
            "thread_id": str(thread_id or ""),
            "items": chosen,
            "unused": sum(1 for i in every if not i["used"]),
            "total": len(every),
        }

    def proposal_posted(
        self,
        ref: str,
        *,
        tenant_id: str = "default",
        via: str = "",
        used_ref: str = "",
    ) -> int:
        """Mark exactly the draft(s) *ref* names as used; returns how many.

        An item id marks that item. A bare proposal id marks every item —
        publishing surfaces only ever pass one item (one call, one item).
        The set becomes ``proposal_posted`` when its LAST item is used, and
        is kept for audit either way, never deleted here.
        """
        parsed = self._parse_ref(ref)
        if parsed is None:
            return 0
        key, item_no = parsed
        tenant = tenant_id or "default"
        now = time.time()
        with self._lock, self._connect() as conn:
            # One writer at a time for the read-modify-write below: two
            # surfaces posting different items of one set must not lose a mark.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT thread_id, value FROM agent_artifacts
                WHERE key = ? AND tenant_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (key, tenant),
            ).fetchone()
            if row is None:
                return 0
            thread_id, value = row
            try:
                payload = json.loads(str(value))
            except (TypeError, ValueError, AttributeError):
                return 0
            items = [i for i in (payload.get("items") or []) if isinstance(i, dict)]
            marked = 0
            for item in items:
                if not self._item_matches(item, item_no) or item.get("used_at"):
                    continue
                item["used_at"] = now
                item["used_via"] = str(via or "")[:40]
                if used_ref:
                    item["used_ref"] = str(used_ref)[:80]
                marked += 1
            if not marked:
                return 0
            payload["items"] = items
            new_value = json.dumps(payload, ensure_ascii=False)
            kind = (
                "proposal_posted"
                if items and all(i.get("used_at") for i in items)
                else "proposal"
            )
            conn.execute(
                """
                UPDATE agent_artifacts
                SET value = ?, kind = ?, updated_at = ?, content_hash = ?
                WHERE tenant_id = ? AND thread_id = ? AND key = ?
                """,
                (new_value, kind, now, _content_hash(new_value), tenant, thread_id, key),
            )
        return marked

    def legacy_posted_count(self) -> int:
        """Sets stamped used as a whole, with no per-item state (pre-2026-09-25)."""
        n = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT value FROM agent_artifacts WHERE kind = 'proposal_posted'"
            ).fetchall()
        for (value,) in rows:
            try:
                items = json.loads(str(value)).get("items") or []
            except (TypeError, ValueError, AttributeError):
                continue
            if items and not any(isinstance(i, dict) and i.get("used_at") for i in items):
                n += 1
        return n

    def heal_legacy_posted(self, evidence: PublishEvidence) -> dict[str, int]:
        """Give back drafts an earlier build hid by stamping their whole set.

        A ``proposal_posted`` row whose items carry no marks was written by
        the whole-set rule, so only SOME of its items may have gone out. Each
        item is checked against *evidence*; the evidenced ones are marked,
        the rest become unused again, and the row's kind is re-derived. A
        row where no item has evidence is left exactly as it is: nothing
        here guesses which drafts went out.
        """
        report = {"rows_healed": 0, "items_restored": 0, "rows_unproven": 0}
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT tenant_id, thread_id, key, value FROM agent_artifacts
                WHERE kind = 'proposal_posted'
                """
            ).fetchall()
            for tenant, thread_id, key, value in rows:
                try:
                    payload = json.loads(str(value))
                except (TypeError, ValueError, AttributeError):
                    continue
                items = [i for i in (payload.get("items") or []) if isinstance(i, dict)]
                if not items or any(i.get("used_at") for i in items):
                    continue  # already per-item: nothing legacy about it
                proven = 0
                for item in items:
                    try:
                        hit = evidence(str(tenant), str(item.get("text") or ""))
                    except (sqlite3.Error, OSError, ValueError, TypeError):
                        logger.debug("[artifacts] evidence lookup failed", exc_info=True)
                        hit = None
                    if hit:
                        via, at, ref = hit
                        item["used_at"] = float(at or time.time())
                        item["used_via"] = str(via or "legacy")[:40]
                        if ref:
                            item["used_ref"] = str(ref)[:80]
                        proven += 1
                if not proven:
                    report["rows_unproven"] += 1
                    continue
                payload["items"] = items
                new_value = json.dumps(payload, ensure_ascii=False)
                kind = "proposal_posted" if proven == len(items) else "proposal"
                # updated_at is kept: the set's retention clock does not restart.
                conn.execute(
                    """
                    UPDATE agent_artifacts SET value = ?, kind = ?, content_hash = ?
                    WHERE tenant_id = ? AND thread_id = ? AND key = ?
                    """,
                    (new_value, kind, _content_hash(new_value), tenant, thread_id, key),
                )
                report["rows_healed"] += 1
                report["items_restored"] += len(items) - proven
        return report

    # ── GC (wired into the commitment-GC cadence, not a new sweeper) ──

    def gc_sweep(
        self,
        *,
        max_per_thread: int = _MAX_PER_THREAD,
    ) -> dict[str, int]:
        """Per-thread cap + age-out. Returns counts of evicted rows."""
        now = time.time()
        evicted = 0
        with self._connect() as conn:
            # age-out by kind
            for kind, days in (
                ("finding", _MAX_AGE_DAYS_FINDING),
                ("proposal", _MAX_AGE_DAYS_PROPOSAL),
                ("proposal_posted", _MAX_AGE_DAYS_FINDING),
            ):
                cur = conn.execute(
                    "DELETE FROM agent_artifacts WHERE kind = ? AND updated_at < ?",
                    (kind, now - days * 86400.0),
                )
                evicted += cur.rowcount or 0
            # per-thread cap (oldest first)
            threads = conn.execute(
                "SELECT DISTINCT tenant_id, thread_id FROM agent_artifacts"
            ).fetchall()
            for tenant, thread in threads:
                n = conn.execute(
                    "SELECT COUNT(*) FROM agent_artifacts WHERE tenant_id=? AND thread_id=?",
                    (tenant, thread),
                ).fetchone()[0]
                if n > max_per_thread:
                    cur = conn.execute(
                        """
                        DELETE FROM agent_artifacts WHERE rowid IN (
                            SELECT rowid FROM agent_artifacts
                            WHERE tenant_id=? AND thread_id=?
                            ORDER BY updated_at ASC LIMIT ?
                        )
                        """,
                        (tenant, thread, n - max_per_thread),
                    )
                    evicted += cur.rowcount or 0
        if evicted:
            logger.info("[artifacts] GC evicted %d rows", evicted)
        return {"evicted": evicted}


# ── the model's reader (``list_proposals`` tool) ─────────────────────

_POSTED_VIA = frozenset({"x_post", "x_studio_post"})
_SCHEDULED_VIA = frozenset({"x_schedule_post", "book_x_post", "x_studio_schedule"})
_READER_CHAR_BUDGET = 24000


def _when(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def _item_state(item: dict[str, Any]) -> str:
    if not item.get("used"):
        return "unused"
    via = str(item.get("used_via") or "")
    ref = str(item.get("used_ref") or "")
    at = _when(item["used_at"]) if item.get("used_at") else "earlier"
    if via in _POSTED_VIA:
        return f"posted {at}" + (f" (tweet {ref})" if ref else "")
    if via in _SCHEDULED_VIA:
        return f"scheduled {at}" + (f" (booking #{ref})" if ref else "")
    return f"used {at}" + (f" via {via}" if via else "")


def describe_proposals(sets: list[dict[str, Any]], *, include_used: bool) -> str:
    """Render saved proposal sets for the model: ids, state, verbatim text.

    Returns the plain body; the tool wraps it in the untrusted-data fence
    (drafts can quote fetched pages and other people's tweets).
    """
    lines: list[str] = []
    used_chars = 0
    hidden = 0
    for s in sets:
        items = [i for i in s["items"] if include_used or not i.get("used")]
        block: list[str] = []
        # Budgeted per DRAFT, not per set: one set can hold 128 drafts of up
        # to 8000 chars. The first draft always shows (it is under budget).
        for item in items:
            entry = "\n".join(
                [f"  [{item.get('id')}] {_item_state(item)}"]
                + ["    " + ln for ln in str(item.get("text") or "").splitlines()]
            )
            if used_chars + len(entry) > _READER_CHAR_BUDGET and (lines or block):
                hidden += 1
                continue
            block.append(entry)
            used_chars += len(entry)
        if block:
            head = (
                f"{s['proposal_id']} — {s['kind']}, saved {_when(s['created_at'])}, "
                f"{s['unused']} of {s.get('total', len(s['items']))} unused"
            )
            lines.append(head + "\n" + "\n".join(block))
    body = "\n\n".join(lines)
    if hidden:
        body += (
            f"\n\n… {hidden} more item(s) not shown. Pass proposal_id=<set id> "
            "to read one set in full."
        )
    return body


# ── singleton ────────────────────────────────────────────────────────

_store: ArtifactStore | None = None
_store_lock = threading.Lock()


def _default_db_path() -> str:
    override = (os.environ.get("KAZMA_ARTIFACTS_DB") or "").strip()
    if override:
        return override
    from kazma_core.paths import data_dir

    return os.path.join(str(data_dir()), "agent_artifacts.db")


def _x_publish_evidence() -> PublishEvidence | None:
    """Evidence from Kazma's own X records: the post ledger, then the schedule.

    Only files that already exist are opened — a heal must never create an
    X store on an install that has never used X.
    """
    try:
        from kazma_core.paths import data_dir

        root = data_dir()
    except (ImportError, OSError):
        return None
    ledger = None
    booked: dict[tuple[str, str], tuple[float, str]] = {}
    try:
        from kazma_core.x_api.ledger import XPostLedger, normalize_text

        if (root / "x_posts.db").exists():
            ledger = XPostLedger(root / "x_posts.db")
        if (root / "x_scheduled.db").exists():
            from kazma_core.x_api.schedule import XScheduledStore

            for post in XScheduledStore(root / "x_scheduled.db").list_all(limit=1000):
                booked.setdefault(
                    (post.tenant_id, normalize_text(post.text)),
                    (post.created_at, str(post.id)),
                )
    except (ImportError, OSError, sqlite3.Error):
        logger.debug("[artifacts] X evidence unavailable", exc_info=True)
        return None
    if ledger is None and not booked:
        return None

    def evidence(tenant_id: str, text: str) -> tuple[str, float, str] | None:
        if not str(text or "").strip():
            return None
        if ledger is not None:
            hit = ledger.first_post_for(text)
            if hit:
                return ("x_post", float(hit.get("created_at") or 0), str(hit.get("tweet_id") or ""))
        slot = booked.get((tenant_id or "default", normalize_text(text)))
        if slot:
            return ("x_schedule_post", slot[0], slot[1])
        return None

    return evidence


def _heal_once(store: ArtifactStore) -> None:
    """Run the whole-set repair once per process, before anyone reads a list.

    It must run before the GC sweep, which ages a fully-used set out after
    14 days: a set wrongly stamped used would take its unposted drafts with
    it. Never raises — a store that cannot heal still has to serve.
    """
    try:
        if not store.legacy_posted_count():
            return
        evidence = _x_publish_evidence()
        if evidence is None:
            return
        report = store.heal_legacy_posted(evidence)
        if report["items_restored"]:
            logger.warning(
                "[artifacts] restored %d unposted draft(s) in %d proposal(s) that an "
                "earlier build marked posted along with a sibling",
                report["items_restored"], report["rows_healed"],
            )
        if report["rows_unproven"]:
            logger.info(
                "[artifacts] %d fully-posted proposal(s) left as they are: no post or "
                "booking record names any of their drafts",
                report["rows_unproven"],
            )
    except Exception:
        logger.warning("[artifacts] legacy posted-state repair skipped", exc_info=True)


def get_artifact_store() -> ArtifactStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                store = ArtifactStore(_default_db_path())
                _heal_once(store)
                _store = store
    return _store


def reset_artifact_store() -> None:
    """Test helper: drop the singleton so a new env/db path takes effect."""
    global _store
    with _store_lock:
        _store = None
