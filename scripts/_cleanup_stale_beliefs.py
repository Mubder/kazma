"""One-shot cleanup: stale weekly-reset diary + duplicate ShipX beliefs.

1. Promote best Grok/ZCode next-reset values to functional predicates
   (``grok_next_reset`` / ``zcode_next_reset``).
2. Soft-invalidate only:
   - old / duplicate *reset schedule* noted rows (not entitlement rules)
   - generic ``next_reset_time`` rows once service-specific facts exist
   - exact-duplicate ShipX overview / WhatsApp-clarification rows

Safe: soft-invalidate (valid_until / invalidated_at). History kept.
Default is dry-run. Pass ``--apply`` to commit.

  .venv/bin/python scripts/_cleanup_stale_beliefs.py --db PATH
  .venv/bin/python scripts/_cleanup_stale_beliefs.py --db PATH --apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (_ROOT / "kazma-core", _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _active(conn: sqlite3.Connection, where: str, params: tuple = ()) -> list[sqlite3.Row]:
    sql = f"""
    SELECT id, subject, predicate, object, predicate_type, valid_from,
           structural_importance, confidence
    FROM beliefs
    WHERE valid_until IS NULL AND invalidated_at IS NULL
      AND ({where})
    ORDER BY valid_from DESC
    """
    return list(conn.execute(sql, params).fetchall())


def _is_reset_schedule_blob(obj: str) -> bool:
    """True for diary rows that are weekly *schedule* facts (safe to rotate away)."""
    t = (obj or "").lower()
    if not t:
        return False
    # Keep operational entitlement rules, peak-hour coeffs, etc.
    if "peak hours" in t or "coefficient" in t or "entitlement rules" in t:
        return False
    if "check long-term memory first" in t or 'my <thing>' in t or "my <thing>" in t:
        return False
    if "glm" in t and "rules" in t:
        return False
    # Must look like a product reset *schedule* (not random "reset password")
    has_svc = any(s in t for s in ("grok", "supergrok", "zcode", "z code"))
    has_reset = bool(
        re.search(
            r"\b(?:next\s+reset|weekly\s+reset|pool\s+reset|reset\s+schedule|"
            r"reset\s+time|(?:grok|zcode|supergrok)\s+reset)\b",
            t,
            re.I,
        )
    ) or ("reset" in t and has_svc and ("weekly" in t or "cron-" in t or "utc" in t))
    has_date = bool(
        re.search(
            r"\b(20\d{2}|august|aug\.?|january|jan|february|feb|march|mar|"
            r"april|apr|may|june|jun|july|jul|september|sep|october|oct|"
            r"november|nov|december|dec|\d{1,2}:\d{2})\b",
            t,
            re.I,
        )
    )
    return bool(has_reset and has_svc and has_date)


def _service_for_blob(obj: str) -> str | None:
    t = (obj or "").lower()
    if "zcode" in t or "z code" in t:
        return "zcode"
    if "grok" in t or "supergrok" in t:
        return "grok"
    return None


def _looks_like_grok_reset(obj: str, pred: str) -> bool:
    blob = (obj or "").lower()
    pred = (pred or "").lower()
    if "zcode" in blob and "grok" not in blob and "supergrok" not in blob:
        return False
    if "grok" in blob or "supergrok" in blob:
        return True
    # Generic next_reset_time without service name: Grok uses 02:48 / Aug 10 pattern
    if pred == "next_reset_time":
        if "08:47" in obj or "2026-08-05" in obj:
            return False
        if "02:48" in obj or "2026-08-10" in obj or "august 10" in blob:
            return True
    return False


def _looks_like_zcode_reset(obj: str, pred: str) -> bool:
    blob = (obj or "").lower()
    pred = (pred or "").lower()
    if "zcode" in blob or "z code" in blob:
        return True
    if "grok" in blob or "supergrok" in blob:
        return False
    if pred == "next_reset_time":
        if "02:48" in obj or "2026-08-10" in obj or "august 10" in blob:
            return False
        if "08:47" in obj or "2026-08-05" in obj:
            return True
    return False


def _pick_best_value(rows: list[sqlite3.Row], service: str) -> str | None:
    """Best object for a service: prefer rows that name the service, then newest."""
    scored: list[tuple[int, float, str]] = []
    for r in rows:
        obj = (r["object"] or "").strip()
        if not obj:
            continue
        pred = (r["predicate"] or "").lower()
        if service == "grok" and not _looks_like_grok_reset(obj, pred):
            continue
        if service == "zcode" and not _looks_like_zcode_reset(obj, pred):
            continue
        # Prefer clean next_reset_time (short datetime) over long diary prose
        blob = obj.lower()
        pred_l = pred
        score = 0
        if pred_l == "next_reset_time":
            score += 5
        if service in blob or (service == "grok" and "supergrok" in blob):
            score += 2
        # Prefer compact values as functional SoT
        if len(obj) < 120:
            score += 1
        scored.append((score, float(r["valid_from"] or 0), obj))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]


def promote_functional(
    conn: sqlite3.Connection, *, apply: bool
) -> tuple[list[str], set[str]]:
    """Write/refresh grok_next_reset + zcode_next_reset from best diary rows.

    Returns (log_lines, functional_predicates_present_or_planned).
    """
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    ensure_primary_schema(conn)
    schedule_rows = [
        r
        for r in _active(
            conn,
            """
            lower(predicate) IN ('noted','next_reset_time','next_reset','weekly_reset')
            OR lower(predicate) LIKE '%next_reset%'
            OR (
              lower(predicate)='noted'
              AND (lower(object) LIKE '%reset%' OR lower(object) LIKE '%grok%' OR lower(object) LIKE '%zcode%')
            )
            """,
        )
        if (r["predicate"] or "").lower() in (
            "noted",
            "next_reset_time",
            "next_reset",
            "weekly_reset",
            "grok_next_reset",
            "zcode_next_reset",
        )
        or _is_reset_schedule_blob(r["object"] or "")
        or (r["predicate"] or "").lower().endswith("_next_reset")
    ]

    msgs: list[str] = []
    present: set[str] = set()
    for svc, pred in (("grok", "grok_next_reset"), ("zcode", "zcode_next_reset")):
        # Prefer already-correct functional
        existing = _active(conn, "predicate=? AND subject=?", (pred, "user"))
        if existing:
            val = existing[0]["object"]
            msgs.append(f"keep functional {pred}={val!r}")
            present.add(pred)
            continue
        val = _pick_best_value(schedule_rows, svc)
        if not val:
            msgs.append(f"no value found for {pred}")
            continue
        msgs.append(f"{'would write' if not apply else 'wrote'} {pred}={val[:80]!r}")
        present.add(pred)  # planned or written — for invalidate planning
        if apply:
            mutate_belief(
                conn,
                "user",
                pred,
                val[:1000],
                ops_conn=None,
                predicate_type="functional",
                confidence=1.0,
                importance=5,
                extraction_method="user_explicit",
                tenant_id="default",
            )
    return msgs, present


def plan_invalidations(
    conn: sqlite3.Connection,
    *,
    planned_functional: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Return [(id, reason), ...] to soft-invalidate."""
    out: list[tuple[str, str]] = []

    # --- reset schedule noted / next_reset_time noise ---
    candidates = _active(
        conn,
        """
        lower(predicate) IN ('noted','next_reset_time')
        OR lower(predicate) LIKE '%reset%'
        """,
    )
    functional = {
        r["predicate"]: r
        for r in _active(
            conn,
            "predicate IN ('grok_next_reset','zcode_next_reset')",
        )
    }
    for p in planned_functional or ():
        functional.setdefault(p, None)  # type: ignore[arg-type]

    for r in candidates:
        pred = (r["predicate"] or "").lower()
        obj = r["object"] or ""
        bid = r["id"]

        # Never kill the functional SoT rows
        if pred in ("grok_next_reset", "zcode_next_reset"):
            continue
        # Keep entitlement / preference notes
        if pred == "noted" and not _is_reset_schedule_blob(obj):
            # next_reset_time handled below; non-schedule noted skip
            if pred == "noted":
                continue

        if pred == "next_reset_time":
            # Drop generic next_reset_time once service-specific functional exists
            # Map by time signature
            if "02:48" in obj or "2026-08-10" in obj or "august 10" in obj.lower():
                if "grok_next_reset" in functional:
                    out.append((bid, "generic next_reset_time superseded by grok_next_reset"))
            elif "08:47" in obj or "2026-08-05" in obj:
                if "zcode_next_reset" in functional:
                    out.append((bid, "generic next_reset_time superseded by zcode_next_reset"))
            else:
                # orphan generic — keep newest one only
                pass
            continue

        if pred == "noted" and _is_reset_schedule_blob(obj):
            svc = _service_for_blob(obj)
            has_aug10 = bool(
                re.search(r"(?:august|aug\.?)\s*10\b|2026-08-10", obj, re.I)
            )
            is_aug3 = bool(
                re.search(
                    r"(?:august|aug\.?)\s*3\b|2026-08-03",
                    obj,
                    re.I,
                )
            ) and not has_aug10
            # Always drop Aug-3-style Grok when we have a newer SoT
            if svc == "grok" and is_aug3:
                out.append((bid, "stale Grok Aug 3 reset (superseded by Aug 10)"))
                continue
            # If functional exists for service, drop all schedule noted for that service
            if svc == "grok" and "grok_next_reset" in functional:
                out.append((bid, "noted Grok reset superseded by grok_next_reset"))
                continue
            if svc == "zcode" and "zcode_next_reset" in functional:
                out.append((bid, "noted ZCode reset superseded by zcode_next_reset"))
                continue
            # No functional yet: keep newest per service, drop older
            # (handled by second pass below)

    # Second pass: without functional, keep newest schedule noted per service
    for svc in ("grok", "zcode"):
        if f"{svc}_next_reset" in functional:
            continue
        noted = [
            r
            for r in candidates
            if (r["predicate"] or "").lower() == "noted"
            and _is_reset_schedule_blob(r["object"] or "")
            and _service_for_blob(r["object"] or "") == svc
        ]
        if len(noted) <= 1:
            continue
        noted.sort(key=lambda r: float(r["valid_from"] or 0), reverse=True)
        for r in noted[1:]:
            out.append((r["id"], f"older {svc} reset noted (kept newest)"))

    # Generic next_reset_time: keep at most one per distinct object, drop rest
    nrt = [r for r in candidates if (r["predicate"] or "").lower() == "next_reset_time"]
    # If functional exists, already handled; else keep both if different services
    if nrt and ("grok_next_reset" in functional or "zcode_next_reset" in functional):
        for r in nrt:
            if r["id"] not in {x[0] for x in out}:
                # already added when functional present
                pass

    # --- ShipX exact / near-exact dups ---
    shipx = _active(
        conn,
        "lower(object) LIKE '%shipx%' AND lower(predicate)='noted'",
    )
    # Group by first 160 normalized chars
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in shipx:
        key = re.sub(r"\s+", " ", (r["object"] or "").strip().lower())[:160]
        groups.setdefault(key, []).append(r)
    for key, members in groups.items():
        if len(members) <= 1:
            continue
        members.sort(key=lambda r: float(r["valid_from"] or 0), reverse=True)
        for r in members[1:]:
            out.append((r["id"], f"duplicate ShipX noted: {key[:50]}…"))

    # unique
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for bid, reason in out:
        if bid not in seen:
            seen.add(bid)
            uniq.append((bid, reason))
    return uniq


def apply_invalidations(conn: sqlite3.Connection, items: list[tuple[str, str]], *, apply: bool) -> int:
    now = time.time()
    n = 0
    for bid, _reason in items:
        if not apply:
            n += 1
            continue
        cur = conn.execute(
            """
            UPDATE beliefs SET valid_until=?, invalidated_at=?
            WHERE id=? AND valid_until IS NULL AND invalidated_at IS NULL
            """,
            (now, now, bid),
        )
        n += int(cur.rowcount or 0)
    if apply and n:
        conn.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    from kazma_core.paths import primary_memory_db

    db = args.db or primary_memory_db()
    if not Path(db).exists():
        print(f"DB not found: {db}")
        return 1
    print(f"DB: {db}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    conn = _connect(db)
    try:
        print("\n=== PROMOTE FUNCTIONAL ===")
        prom_lines, planned_fn = promote_functional(conn, apply=args.apply)
        for line in prom_lines:
            print(f"  {line}")

        items = plan_invalidations(conn, planned_functional=planned_fn)
        print(f"\n=== TO INVALIDATE ({len(items)}) ===")
        for bid, reason in items:
            row = conn.execute(
                "SELECT predicate, substr(object,1,90) AS o FROM beliefs WHERE id=?",
                (bid,),
            ).fetchone()
            if row:
                print(f"  {bid[:32]}  [{row['predicate']}] {row['o']!r}")
                print(f"      reason: {reason}")

        n = apply_invalidations(conn, items, apply=args.apply)
        print(f"\n{'Invalidated' if args.apply else 'Would invalidate'}: {n}")

        # Show resulting active reset-related rows
        print("\n=== ACTIVE RESET / FUNCTIONAL AFTER PLAN ===")
        rows = _active(
            conn,
            """
            lower(predicate) LIKE '%reset%'
            OR (lower(predicate)='noted' AND (
                 lower(object) LIKE '%next reset%' OR lower(object) LIKE '%weekly%reset%'
            ))
            """,
        )
        for r in rows:
            if (r["predicate"] or "") == "noted" and not _is_reset_schedule_blob(r["object"] or ""):
                continue
            # skip already planned invalidations in dry-run display
            if not args.apply and r["id"] in {i[0] for i in items}:
                continue
            print(
                f"  {r['predicate'][:22]:22} {(r['object'] or '')[:100]!r}"
            )

        if not args.apply:
            print("\nRe-run with --apply to commit.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
