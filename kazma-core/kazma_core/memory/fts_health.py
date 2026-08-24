"""M-10 helper: detect FTS/base row-count drift and heal it.

Partial desync (some rows indexed, others not) never triggers the
schema-ensure rebuild (which only fires when FTS is exactly empty). A cheap
COUNT comparison closes silent recall MISSES.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["fts_drift_check"]


def _fts_indexed_count(conn: sqlite3.Connection, fts: str) -> int:
    """Number of rows actually in the FTS index.

    ``SELECT COUNT(*) FROM <fts>`` on an FTS5 ``content=`` table follows the
    content table, so it cannot see a partial desync. ``<fts>_docsize`` is
    the shadow table that tracks indexed rowids.
    """
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {fts}_docsize").fetchone()[0])
    except Exception:
        return int(conn.execute(f"SELECT COUNT(*) FROM {fts}").fetchone()[0])

# Hardcoded pairs only — never interpolate untrusted table names.
_FTS_PAIRS: tuple[tuple[str, str], ...] = (
    ("beliefs", "beliefs_fts"),
    ("episodes", "episodes_fts"),
    ("entities", "entities_fts"),
)


def fts_drift_check(conn: sqlite3.Connection, *, auto_heal: bool = True) -> dict[str, Any]:
    """Compare base vs FTS row counts for beliefs/episodes/entities.

    Returns per-table stats; when ``auto_heal`` and a delta exists, runs the
    standard ``INSERT INTO <fts>(<fts>) VALUES('rebuild')`` (full reindex —
    bounded by table size, run from the 6h macro-sleep sweep).
    """
    out: dict[str, Any] = {"tables": {}, "healed": [], "drift": False}
    for base, fts in _FTS_PAIRS:
        try:
            b = int(conn.execute(f"SELECT COUNT(*) FROM {base}").fetchone()[0])
            f = _fts_indexed_count(conn, fts)
            entry: dict[str, Any] = {"base": b, "fts": f, "delta": b - f}
            if entry["delta"] != 0:
                out["drift"] = True
            if entry["delta"] != 0 and auto_heal and b > 0:
                conn.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
                f2 = _fts_indexed_count(conn, fts)
                entry["healed_to"] = f2
                if f2 != b:
                    entry["error"] = "rebuild did not converge"
                else:
                    out["healed"].append(base)
            out["tables"][base] = entry
        except Exception as e:
            out["tables"][base] = {"error": str(e)[:200]}
    if out["healed"]:
        try:
            conn.commit()
        except Exception:
            logger.debug("[fts_health] commit after rebuild skipped", exc_info=True)
        logger.info("[fts_health] rebuilt FTS for %s", out["healed"])
    elif out["drift"]:
        logger.warning("[fts_health] FTS drift (heal off or empty base): %s", out["tables"])
    return out
