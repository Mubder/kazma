"""Every length memory holds itself to, in one place.

**Facts** (Memory audit M36). The edit route capped a fact at 200/120/4000
characters while ``mutate_belief`` -- the one writer every other path uses
(the link route, the agent's tools, extraction) -- capped nothing, and
graph-group names were unbounded. A value past a limit is refused, never
cut: a cut value is a different fact.

**Memories of conversations are kept whole** (2026-11-19, the owner: "Keep
more, so find a way or a workaround to keep all text"). Every episode writer
cut what it stored -- 4,000 characters of the question and of the answer,
2,000 of the summary, 8,000 of a swarm result -- so a long message or answer
was remembered by its start only. Now:

* **Stored:** the whole text, up to :data:`EPISODE_TEXT_MAX` per field, a
  safety ceiling nothing a person sends or a model answers reaches.
* **Matched:** code that finds a turn's row by its text compares
  :func:`turn_key` -- the first :data:`LEGACY_TEXT_CUT` characters -- so a
  row written before keeps matching the whole text of the same turn. The
  forget ledger's key hashes the same part (:func:`question_key`).
* **Embedded:** a memory's meaning vector is made from its first
  :data:`EMBED_TEXT_CHARS` characters, as it always was (the question was
  never longer). Meaning search covers the start of a long memory; keyword
  search (the full-text index) covers all of it.
* **Shown:** a list of memories shows each one bounded (the recall block and
  the memory tools, :data:`RECALL_SHOW_QUESTION`... :data:`MEMORY_SHOW_MAX`),
  with how much more there is; ``memory_read`` reads one whole, a page of
  :data:`MEMORY_READ_PAGE` characters at a time.

No episode text is cut anywhere but here (``tests/test_memory_keeps_all_text.py``).
"""

from __future__ import annotations

import logging

__all__ = [
    "DEEP_PASS_ANSWER",
    "DEEP_PASS_PREVIOUS_ANSWER",
    "DEEP_PASS_PREVIOUS_QUESTION",
    "DEEP_PASS_QUESTION",
    "EMBED_TEXT_CHARS",
    "EPISODE_TEXT_MAX",
    "KEYWORD_HIT_CHARS",
    "LEGACY_BRIDGE_CUT",
    "LEGACY_SUMMARY_CUT",
    "LEGACY_TEXT_CUT",
    "MATCH_TEXT_CHARS",
    "MEMORY_READ_PAGE",
    "MEMORY_SHOW_ANSWER",
    "MEMORY_SHOW_MAX",
    "MEMORY_SHOW_QUESTION",
    "NAME_MAX",
    "OBJECT_MAX",
    "PREDICATE_MAX",
    "RECALL_SHOW_ANSWER",
    "RECALL_SHOW_ONE",
    "RECALL_SHOW_QUESTION",
    "SUBJECT_MAX",
    "embed_input",
    "episode_text",
    "question_key",
    "too_long",
    "turn_key",
]

logger = logging.getLogger(__name__)

SUBJECT_MAX = 200
PREDICATE_MAX = 120
OBJECT_MAX = 4000
#: An entity's or a graph group's display name.
NAME_MAX = 120

#: The most one field of a memory (a question, an answer, a summary) holds:
#: a safety ceiling, not a cut anyone meets. It is the web chat's request
#: body limit (``kazma_ui.body_limits.DEFAULT_BODY_LIMIT``, 2 MiB), so no
#: message the chat accepts is longer; Telegram, Discord, Slack and WhatsApp
#: messages are far shorter; a model's answer is bounded by its output limit
#: (tens of thousands of characters). What it bounds is the cost of one row:
#: the full-text index and recall's word coverage read every character
#: (measured 2026-11-19: 0.03 s of coverage per 140,000 characters).
EPISODE_TEXT_MAX = 2 * 1024 * 1024

#: What every episode writer kept until 2026-11-19: the first 4,000
#: characters of the question and of the answer (``dual_write``,
#: ``turn_reconcile``), 2,000 of the summary, and 8,000 of a swarm result
#: (``swarm_bridge``). A row written then holds at most this much; the
#: memory text fill (``memory/text_fill.py``) brings the rest back from the
#: chat where it still has it, and memory recovery (``rehydrate``) reproduces
#: what those writers stored.
LEGACY_TEXT_CUT = 4000
LEGACY_SUMMARY_CUT = 2000
LEGACY_BRIDGE_CUT = 8000

#: A memory's meaning vector is made from this much of its text: what it
#: always was (the question was cut at 4,000), so no vector changes and a
#: very long message costs the embedder no more than before.
EMBED_TEXT_CHARS = 4000

#: What recall compares a memory by (``episode_text.match_text``: de-duplication,
#: the graph walk's seeds): its first 400 characters, as always.
MATCH_TEXT_CHARS = 400
#: What a keyword (full-text) hit is compared by before recall reads its row:
#: the first 300 characters of its question, else its answer, as always.
KEYWORD_HIT_CHARS = 300

#: The per-turn recall block: five memories fit its budget, so each shows a
#: little of its question and of its answer (memory/episode_text.py).
RECALL_SHOW_QUESTION = 200
RECALL_SHOW_ANSWER = 300
RECALL_SHOW_ONE = 500

#: ``memory_search`` and the Memory page's probe: at most this much of one
#: memory, then how much more there is and how to read it.
MEMORY_SHOW_MAX = 1500
MEMORY_SHOW_QUESTION = 600
MEMORY_SHOW_ANSWER = 900

#: ``memory_read``: one page of a memory's text.
MEMORY_READ_PAGE = 8000

#: The model's fact pass (the deep pass, ``belief_extractor._llm_extract``)
#: reads the first 1,500 characters of a turn's question and 800 of its
#: answer: model input, bounded, unchanged by keeping memories whole (the
#: extraction benchmark's recordings are made with these). The end of a
#: longer message is read only by ``own_words.last_words``.
DEEP_PASS_QUESTION = 1500
DEEP_PASS_ANSWER = 800

#: The turn before, given to the deep pass to understand this one ("yes,
#: that one", "she moved too"; framework audit F2 step 2): its question and
#: its answer, bounded smaller (it is context, never a source of facts).
DEEP_PASS_PREVIOUS_QUESTION = 600
DEEP_PASS_PREVIOUS_ANSWER = 400


def episode_text(text: str | None, *, field: str = "text") -> str:
    """What a memory stores of *text*: all of it, up to the safety ceiling.

    Every episode writer stores through this (``dual_write.episode_row``,
    ``swarm_bridge``, the deferred queue payload, the V1 backfill). A text
    past the ceiling keeps its first :data:`EPISODE_TEXT_MAX` characters and
    says so in the log -- never silently.
    """
    value = text or ""
    if len(value) <= EPISODE_TEXT_MAX:
        return value
    logger.warning("[memory] a memory's %s of %d characters is past the %d-character ceiling; "
                   "its start is kept", field, len(value), EPISODE_TEXT_MAX)
    return value[:EPISODE_TEXT_MAX]


def turn_key(text: str | None) -> str:
    """What code that finds a turn's row by its text compares: the first
    :data:`LEGACY_TEXT_CUT` characters, stripped. A row written before
    2026-11-19 holds exactly that much of a long question, a row written
    since all of it, and both compare equal to the same turn."""
    return (text or "")[:LEGACY_TEXT_CUT].strip()


def question_key(text: str | None) -> str:
    """The text the forget ledger hashes for a turn's question
    (``forget.question_sha``): :func:`turn_key`, so a ledger row written
    before 2026-11-19 still names the same turn."""
    return turn_key(text)


def embed_input(text: str | None) -> str:
    """The part of a memory's text its meaning vector is made from."""
    return (text or "")[:EMBED_TEXT_CHARS]


def too_long(*, subject: str = "", predicate: str = "", obj: str = "", name: str = "") -> str | None:
    """The first field past its limit (``"subject"``, ...), or None."""
    for field, value, limit in (("subject", subject, SUBJECT_MAX), ("predicate", predicate, PREDICATE_MAX),
                                ("object", obj, OBJECT_MAX), ("name", name, NAME_MAX)):
        if len(value or "") > limit:
            return field
    return None
