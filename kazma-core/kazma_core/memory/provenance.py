"""Who started a memory write, and what it may overrule (Memory audit M03).

A fact's ``extraction_method`` says where it came from, and the source-trust
gate (``belief_mutation``) uses it to decide whether a new statement may
replace a current one. Until 2026-10-13 the ladder was upside down:

- the agent's own tools (``memory_store``, ``memory_link_entities``) wrote
  ``user_explicit`` -- the user's gold label -- whatever the model was told
  to store, a fetched page or an email included, with no injection screen;
- the user's own words, read from their message, were ``llm_inferred``;
- so a fact the model stored blocked the user's later correction ("I live in
  Lisbon now": ``lower_trust_source``).

The ladder, from the top:

``user_explicit``
    The user's own channel: the Memory page, a chat command, or a memory tool
    called on a turn whose latest user message asks to remember and holds the
    words being stored (:func:`agent_write_source`).
``user_stated``
    The user's literal statement, read from their own message by the
    heuristic pass ("my name is", "I live in", "I work at").
``system_tool`` / ``agent_asserted`` / ``llm_inferred``
    A migration, an agent tool call the user did not ask for, the extractor's
    model pass.

A fact from the user (``user_explicit`` or ``user_stated``) is replaced only by
the user's own words (:func:`may_overrule`); the one exception is the model
pass correcting the heuristic reading of the SAME turn (both carry the turn's
time, AGENTS.md section 15H). An agent write that would replace a user fact is
refused, and the tool says so: the user corrects their own facts. Agent writes
are screened for injection first (:func:`screen_agent_text`).
"""

from __future__ import annotations

__all__ = [
    "AGENT_ASSERTED",
    "LLM_INFERRED",
    "SYSTEM_TOOL",
    "USER_EXPLICIT",
    "USER_SOURCES",
    "USER_STATED",
    "agent_write_source",
    "latest_user_message",
    "may_overrule",
    "screen_agent_text",
]

USER_EXPLICIT = "user_explicit"
USER_STATED = "user_stated"
SYSTEM_TOOL = "system_tool"
AGENT_ASSERTED = "agent_asserted"
LLM_INFERRED = "llm_inferred"

#: Facts the user said: only the user's own words replace them.
USER_SOURCES = frozenset({USER_EXPLICIT, USER_STATED})

#: The share of the stored text's content words the user's message must hold
#: for a tool write to count as the user's own (paraphrase is the model's).
_HELD_SHARE = 0.8


def may_overrule(existing: str | None, incoming: str, *, same_turn: bool = False) -> bool:
    """Whether a statement from *incoming* may replace a fact from *existing*."""
    if existing not in USER_SOURCES:
        return True
    if incoming in USER_SOURCES:
        return True
    # The model pass may still correct the heuristic reading of its own turn.
    return existing == USER_STATED and incoming == LLM_INFERRED and same_turn


def _holds(message: str, text: str) -> bool:
    """Whether *message* holds ``_HELD_SHARE`` of *text*'s content words. A
    hyphenated word counts as held when its parts are ("AUDIT-2611" in the
    message holds ``audit`` and ``2611``, and the reverse)."""
    from kazma_core.memory.query_terms import content_terms

    wanted = content_terms(text)
    if not wanted:
        return False
    terms = content_terms(message)
    present = set(terms)
    for term in terms:
        present.update(part for part in term.split("-") if len(part) >= 2)

    def held(word: str) -> bool:
        parts = [p for p in word.split("-") if p]
        return word in present or (len(parts) > 1 and all(p in present for p in parts))

    return sum(1 for w in wanted if held(w)) / len(wanted) >= _HELD_SHARE


def agent_write_source(text: str, latest_user_message: str | None) -> str:
    """``user_explicit`` when the user's latest message asks to remember and
    holds the VALUE being stored; else ``agent_asserted``.

    *text* is the value -- a fact's object, a note's text -- never the
    subject and predicate the model named it with (FA-228: the model names a
    fact in English, ``audit_2611_test_codeword``, so an Arabic "remember
    this" never held those words and the user's own fact was stored as the
    model's)."""
    from kazma_core.memory.remember_request import is_remember_request

    message = latest_user_message or ""
    if is_remember_request(message) and _holds(message, text):
        return USER_EXPLICIT
    return AGENT_ASSERTED


def screen_agent_text(*texts: str) -> str | None:
    """Why an agent memory write is refused, or None. The extractor's
    sanitizer screened its own facts; the tools screened nothing."""
    from kazma_core.safety.prompt_fence import filter_injection, is_override_delta

    for text in texts:
        if not (text or "").strip():
            continue
        if is_override_delta(text) or filter_injection(text) is None:
            return "it reads as an instruction to the assistant, not a fact"
    return None


def latest_user_message(messages: list[dict] | None) -> str:
    """The text of the newest user message of a turn's messages."""
    for message in reversed(messages or []):
        if (message or {}).get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return " ".join(
                    str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            return str(content or "")
    return ""
