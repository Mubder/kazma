"""Token store for platform callback data with size constraints (e.g. Telegram 64 bytes).

Translates long callback strings into short tokens (e.g. ``cb:<token>``) backed by
an in-memory LRU cache and persistent SQLite storage with a 24-hour TTL.
"""

from __future__ import annotations

import collections
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from kazma_core.paths import data_dir

logger = logging.getLogger(__name__)

__all__ = [
    "decode_callback_data",
    "encode_callback_data",
]

_TTL_SECONDS = 86400  # 24 hours
_MAX_LRU_ENTRIES = 4096

_lock = threading.Lock()
_lru_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
_db_initialized = False


def _get_db_path() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "callbacks.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS callbacks (
            token TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_callbacks_expires ON callbacks(expires_at)"
    )
    conn.commit()


def _get_connection() -> sqlite3.Connection:
    global _db_initialized
    conn = sqlite3.connect(str(_get_db_path()), timeout=5.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if not _db_initialized:
        _ensure_schema(conn)
        _db_initialized = True
    return conn


def encode_callback_data(data: str, max_bytes: int = 64) -> str:
    """Encode *data* into a short token if its UTF-8 length exceeds *max_bytes*.

    Returns the original string if within size bounds, or ``cb:<token>``.
    """
    if not data or not isinstance(data, str):
        return data or ""

    encoded_bytes = data.encode("utf-8")
    if len(encoded_bytes) <= max_bytes:
        return data

    token = secrets.token_hex(8)  # 16 chars -> 'cb:' + 16 = 19 bytes
    short_code = f"cb:{token}"
    now = time.time()
    expires_at = now + _TTL_SECONDS

    with _lock:
        _lru_cache[token] = data
        if len(_lru_cache) > _MAX_LRU_ENTRIES:
            _lru_cache.popitem(last=False)

        try:
            conn = _get_connection()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO callbacks (token, data, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (token, data, now, expires_at),
                )
                # Opportunistic cleanup: delete expired records occasionally
                conn.execute("DELETE FROM callbacks WHERE expires_at < ?", (now,))
        except Exception:
            logger.debug("[callback_store] Failed to persist callback token %s", token, exc_info=True)

    return short_code


def decode_callback_data(data: str) -> str:
    """Decode *data*, expanding ``cb:<token>`` into its original payload if present."""
    if not data or not isinstance(data, str) or not data.startswith("cb:"):
        return data

    token = data[3:]
    with _lock:
        if token in _lru_cache:
            _lru_cache.move_to_end(token)
            return _lru_cache[token]

        try:
            conn = _get_connection()
            row = conn.execute(
                "SELECT data, expires_at FROM callbacks WHERE token = ?",
                (token,),
            ).fetchone()
            if row:
                val, expires = row
                if expires > time.time():
                    _lru_cache[token] = val
                    if len(_lru_cache) > _MAX_LRU_ENTRIES:
                        _lru_cache.popitem(last=False)
                    return val
        except Exception:
            logger.debug("[callback_store] Failed to retrieve callback token %s", token, exc_info=True)

    return data
