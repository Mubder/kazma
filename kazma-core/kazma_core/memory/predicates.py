"""One fact, one predicate name: names that are the same words (plan W6).

The model that extracts facts names predicates freely, so one fact comes back
under names like ``timezone`` / ``timezone_is`` or ``daily_tweet_cap`` /
``tweet_daily_cap``. Under two names a single-valued fact is never
superseded. Measured on the live install on 2026-09-27: eight subjects held
two conflicting current values that way (a daily cap of 16 and of 8, versions
0.10.0 and 0.11.0, two reset dates for one account) and seven held one value
twice.

Two names are the same when their words are: order, filler words ("is",
"of", "the" ...) and a plural "s" do not count. Deliberately nothing about
meaning: by embedding, ``grok_next_reset`` / ``grok_personal_next_reset`` --
two accounts -- score 0.94, above most true pairs, so any similarity
threshold merges different facts. Names that differ by a word (``slack_id`` /
``slack_user_id``) are for the extractor to avoid, by reusing the names in use
(:func:`predicate_vocabulary`, ``belief_extractor``).

- :func:`canonical_predicate` -- a new fact is written under the name its
  subject already uses for the same words and type (``mutate_belief``), so
  the same value is a no-op, a new value supersedes, a set appends.
- :func:`merge_same_word_predicates` -- facts already stored under two such
  names (reconsolidation): one current value, the user's word first.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "canonical_predicate",
    "merge_same_word_predicates",
    "predicate_vocabulary",
]

#: Words that change nothing about what a predicate means.
_FILLER = frozenset({"is", "are", "the", "a", "an", "of"})

#: Single-valued predicate types: one current value per (subject, predicate).
_SINGLE_VALUED = frozenset({"functional", "state"})


def _predicate_key(predicate: str | None) -> frozenset[str]:
    """The words of a predicate name: order, filler and a plural 's' dropped."""
    words: set[str] = set()
    for word in (predicate or "").strip().lower().replace(" ", "_").split("_"):
        if not word or word in _FILLER:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.add(word)
    return frozenset(words)


def canonical_predicate(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    subject: str,
    predicate: str,
    predicate_type: str,
) -> str:
    """The name *subject* already uses for *predicate*'s words and type, else
    *predicate*. A name the subject already has is kept as it is; of several,
    the most used (then the shortest, then the first by name)."""
    key = _predicate_key(predicate)
    if not key or not subject:
        return predicate
    best: tuple[int, int, str] | None = None
    for name, ptype, count in conn.execute(
        "SELECT predicate, predicate_type, COUNT(*) FROM beliefs "
        "WHERE tenant_id = ? AND subject = ? GROUP BY predicate, predicate_type",
        (tenant_id or "default", subject),
    ).fetchall():
        if name == predicate:
            return predicate
        if ptype != predicate_type or not name or _predicate_key(name) != key:
            continue
        rank = (-int(count), len(name), name)
        if best is None or rank < best:
            best = rank
    return best[2] if best else predicate


def predicate_vocabulary(conn: sqlite3.Connection, *, tenant_id: str, limit: int = 60) -> list[str]:
    """The tenant's most used predicate names among current facts, for the
    extractor to reuse instead of inventing another name for the same thing."""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT predicate, COUNT(*) AS n FROM beliefs WHERE tenant_id = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL "
            "GROUP BY predicate ORDER BY n DESC, predicate LIMIT ?",
            (tenant_id or "default", int(limit)),
        ).fetchall()
        if row[0]
    ]


def merge_same_word_predicates(
    conn: sqlite3.Connection, *, tenant_id: str, max_merges: int = 200
) -> list[str]:
    """Retire current facts that repeat or contradict one fact stored under
    another name with the same words and type. Returns the retired ids.

    A single-valued fact keeps its latest statement -- when every value came
    from the same kind of source. Where the user's own words and an inference
    disagree, both stay: keeping the user's would retire a newer value the
    extractor only labelled inferred (live: an admin reset date of Sept 1 the
    user stated in August against Sept 29 from a September turn), and keeping
    the newer one would let an inference overrule the user (the trust gate).
    A set keeps each value once: the user's statement if there is one, else
    the first. Retiring is ``invalidate_belief``: the row stays as history,
    and the mirror, graph and entity counts follow.
    """
    from kazma_core.memory.hygiene import invalidate_belief

    groups: dict[tuple[str, str, frozenset[str]], list[dict[str, Any]]] = {}
    for bid, subject, predicate, ptype, obj, valid_from, method in conn.execute(
        "SELECT id, subject, predicate, predicate_type, object, valid_from, extraction_method "
        "FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL",
        (tenant_id or "default",),
    ).fetchall():
        key = _predicate_key(predicate)
        if key:
            groups.setdefault((subject, ptype, key), []).append({
                "id": bid, "predicate": predicate, "value": (obj or "").strip().lower(),
                "when": float(valid_from or 0), "user": method == "user_explicit",
            })

    retire: list[dict[str, Any]] = []
    for (_subject, ptype, _key), members in groups.items():
        if len({m["predicate"] for m in members}) < 2:
            continue  # one name: the exact-duplicate merge's, not this one's
        if ptype in _SINGLE_VALUED:
            if len({m["user"] for m in members}) > 1:
                continue  # the user's words against an inference: both stay
            keep = max(members, key=lambda m: m["when"])
            retire.extend(m for m in members if m is not keep)
        else:
            seen: set[str] = set()
            for m in sorted(members, key=lambda m: (not m["user"], m["when"])):
                if m["value"] in seen:
                    retire.append(m)
                else:
                    seen.add(m["value"])

    retired: list[str] = []
    for m in retire[: max(0, int(max_merges))]:
        if invalidate_belief(m["id"], conn=conn, tenant_id=tenant_id).get("ok"):
            retired.append(str(m["id"]))
    if retired:
        logger.info(
            "[memory] %d fact(s) retired that repeated or contradicted one fact "
            "under another name (tenant %s)", len(retired), tenant_id,
        )
    return retired
