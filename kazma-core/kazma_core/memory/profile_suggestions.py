"""Lines Kazma suggests for "About me" (the memory framework audit's F13,
decision 10).

"About me" is the user's own text (``memory.profile``), shown to the model on
every call; nothing Kazma infers goes there. What Kazma can do is offer a
line built from a fact the user stated themself -- "You said you live in
Porto. Add to About me?" -- and add it only when the user says yes. So:

* a suggestion is made only from a current fact of the user's own words
  (``provenance.USER_SOURCES``: ``user_stated``, ``user_explicit``), never
  from Kazma's reading of a turn (``llm_inferred``) or anything a tool or
  another member said;
* in a shared space only from the asker's own entry (``speakers.entry_id``),
  and it goes into the asker's own About me there (Spaces S1-11, N31);
* nothing joins About me without the user's yes (:func:`add_suggestion`),
  and a line already there, or one the user dismissed, is not offered again;
* the line is written in the language the user said it in (the turn the fact
  came from; else the value's own script), from a short table of sentences.

A decision is kept by the fact's id (``memory_profile_suggestions``): no value
is stored, so forgetting the fact leaves nothing to read. A fact the user
changes is a new fact, and a new suggestion. Everything here is computed when
asked, with no model call.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

__all__ = ["SUGGESTION_LIMIT", "add_suggestion", "dismiss_suggestion", "list_suggestions"]

logger = logging.getLogger(__name__)

#: At most this many lines are offered at once.
SUGGESTION_LIMIT = 8
#: A value longer than this is no profile line.
_VALUE_MAX = 120

#: Kazma's fact names a line is made for, in English and Arabic. ``favorite_``
#: names take the category from the name (English) or leave it out (Arabic).
_LINES: dict[str, tuple[str, str]] = {
    "name_is": ("My name is {v}.", "اسمي {v}."),
    "preferred_name": ("Call me {v}.", "ناديني {v}."),
    "lives_in": ("I live in {v}.", "أسكن في {v}."),
    "works_at": ("I work at {v}.", "أشتغل في {v}."),
    "current_role": ("I work as {v}.", "وظيفتي {v}."),
    "prefers": ("I prefer {v}.", "أفضّل {v}."),
    "allergic_to": ("I am allergic to {v}.", "عندي حساسية من {v}."),
}
_FAVORITE = ("My favourite {c} is {v}.", "{v} هو المفضل عندي.")


def _line_for(predicate: str, value: str, lang: str) -> str | None:
    i = 1 if lang == "ar" else 0
    if predicate in _LINES:
        return _LINES[predicate][i].format(v=value)
    if predicate.startswith("favorite_") and len(predicate) > len("favorite_"):
        category = predicate[len("favorite_"):].replace("_", " ")
        return _FAVORITE[i].format(c=category, v=value)
    return None


def _is_arabic(text: str) -> bool:
    from kazma_core.documents.arabic import is_rtl_dominant

    return bool(text) and is_rtl_dominant(text)


def _language(conn: sqlite3.Connection, tenant: str, row: sqlite3.Row) -> str:
    """The language the user said the fact in: the turn it came from, else
    the value's own script."""
    said = ""
    if row["source_session"] and row["source_turn"] is not None:
        found = conn.execute(
            "SELECT user_text FROM episodes WHERE tenant_id = ? AND session_id = ? AND turn_number = ? "
            "AND user_text IS NOT NULL AND user_text != '' LIMIT 1",
            (tenant, row["source_session"], row["source_turn"]),
        ).fetchone()
        said = str(found[0]) if found else ""
    return "ar" if _is_arabic(said or str(row["object"] or "")) else "en"


def _folded(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return " " + " ".join(fold_for_search(text or "").lower().split()) + " "


def _says_it_already(about: str, value: str) -> bool:
    """About me already holds the value, as whole words."""
    v = _folded(value).strip()
    return bool(v) and f" {v} " in _folded(about.replace(".", " ").replace(",", " ").replace("،", " "))


def _whose(tenant: str, account: str | None) -> tuple[str, str] | None:
    """``(subject, account key)`` of the asker's facts: ``user`` in a
    personal space, the asker's own entry in a shared one (None for no
    asker there)."""
    from kazma_core.memory.speakers import USER, entry_id, room

    if room(tenant).shared:
        if not account:
            return None
        return entry_id(account), account
    return USER, ""


def _decided(conn: sqlite3.Connection, tenant: str, key: str) -> set[str]:
    return {str(r[0]) for r in conn.execute(
        "SELECT belief_id FROM memory_profile_suggestions WHERE tenant_id = ? AND account = ?", (tenant, key))}


def _user_sources() -> frozenset[str]:
    """The sources that are the user's own words (``provenance.USER_SOURCES``)."""
    from kazma_core.memory.provenance import USER_SOURCES

    return frozenset(USER_SOURCES)


def _candidates(conn: sqlite3.Connection, tenant: str, subject: str) -> list[sqlite3.Row]:
    sources = sorted(_user_sources())
    return conn.execute(
        "SELECT id, predicate, object, valid_from, source_session, source_turn FROM beliefs "
        f"WHERE tenant_id = ? AND subject = ? AND valid_until IS NULL AND object != '' "
        f"AND extraction_method IN ({','.join('?' for _ in sources)}) "
        "ORDER BY valid_from DESC",
        (tenant, subject, *sources),
    ).fetchall()


def _suggestion(conn: sqlite3.Connection, tenant: str, row: sqlite3.Row, about: str) -> dict[str, Any] | None:
    value = " ".join(str(row["object"] or "").split())
    if not value or len(value) > _VALUE_MAX or _says_it_already(about, value):
        return None
    lang = _language(conn, tenant, row)
    line = _line_for(str(row["predicate"] or ""), value, lang)
    if line is None:
        return None
    return {"id": str(row["id"]), "predicate": str(row["predicate"]), "value": value,
            "line": line, "lang": lang, "said_at": float(row["valid_from"] or 0)}


def list_suggestions(conn: sqlite3.Connection, *, tenant_id: str, account: str | None = None,
                     limit: int = SUGGESTION_LIMIT) -> list[dict[str, Any]]:
    """The lines Kazma offers the asker for their About me, newest fact
    first: each ``{"id", "predicate", "value", "line", "lang", "said_at"}``.
    Blocking (SQLite)."""
    from kazma_core.memory.profile import get_about

    tenant = tenant_id
    whose = _whose(tenant, account)
    if whose is None:
        return []
    subject, key = whose
    about = get_about(tenant, conn=conn, account=account or None)["about"]
    decided = _decided(conn, tenant, key)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _candidates(conn, tenant, subject):
        if str(row["id"]) in decided:
            continue
        found = _suggestion(conn, tenant, row, about)
        if found is None or found["line"] in seen:
            continue
        seen.add(found["line"])
        out.append(found)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _record(conn: sqlite3.Connection, tenant: str, key: str, belief_id: str, decision: str) -> None:
    conn.execute(
        "INSERT INTO memory_profile_suggestions (tenant_id, account, belief_id, decision, decided_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, account, belief_id) DO UPDATE SET "
        "decision = excluded.decision, decided_at = excluded.decided_at",
        (tenant, key, belief_id, decision, time.time()),
    )


def _offered(conn: sqlite3.Connection, tenant: str, account: str | None, belief_id: str) -> dict[str, Any] | None:
    """The suggestion for *belief_id*, if it is still one the asker is
    offered (current, their own words, theirs, not decided, not in About me)."""
    return next((s for s in list_suggestions(conn, tenant_id=tenant, account=account, limit=10_000)
                 if s["id"] == belief_id), None)


def add_suggestion(conn: sqlite3.Connection, *, tenant_id: str, account: str | None,
                   belief_id: str) -> dict[str, Any]:
    """The user's yes: the line joins the end of their About me. Refused with
    a code the page words: ``not_found`` (no such suggestion for this asker
    now), ``about_too_long`` (About me would pass its limit; nothing
    changes), ``no_account``. Blocking."""
    from kazma_core.memory.profile import get_about, set_about

    tenant = tenant_id
    whose = _whose(tenant, account)
    if whose is None:
        return {"ok": False, "error": "no_account", "code": "no_account"}
    found = _offered(conn, tenant, account, belief_id)
    if found is None:
        return {"ok": False, "error": "not_found", "code": "not_found"}
    about = get_about(tenant, conn=conn, account=account or None)["about"].rstrip()
    out = set_about(tenant, (about + "\n" + found["line"]) if about else found["line"], conn=conn,
                    account=account or None)
    if not out.get("ok"):
        return out
    _record(conn, tenant, whose[1], belief_id, "added")
    conn.commit()
    logger.info("[profile] a suggested line joined About me (%s)", found["predicate"])
    return {"ok": True, "about": out.get("about", ""), "line": found["line"]}


def dismiss_suggestion(conn: sqlite3.Connection, *, tenant_id: str, account: str | None,
                       belief_id: str) -> dict[str, Any]:
    """The user's no: the line is not offered again for this fact. Blocking."""
    tenant = tenant_id
    whose = _whose(tenant, account)
    if whose is None:
        return {"ok": False, "error": "no_account", "code": "no_account"}
    if _offered(conn, tenant, account, belief_id) is None:
        return {"ok": False, "error": "not_found", "code": "not_found"}
    _record(conn, tenant, whose[1], belief_id, "dismissed")
    conn.commit()
    return {"ok": True}
