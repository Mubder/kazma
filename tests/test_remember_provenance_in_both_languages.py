"""An Arabic "remember this" is the user's own fact, as an English one is (FA-228).

The 2026-11 functional audit, on live: "Please remember this for later: my
AUDIT-2611 test codeword is zq-..." was stored ``user_explicit`` (trust 1.0);
"تذكّر هذا من فضلك: رمز اختبار AUDIT-2611 الثاني هو qx-..." was stored
``agent_asserted`` (0.65), so any later model inference could replace it.
``provenance._holds`` judged the subject and predicate the model named the
fact with -- in English -- so an Arabic message held one word of six.

The value decides now (a fact's object, a note's text), and a hyphenated word
is held by its parts. Through the real memory tool: the same request in each
language, an English-named predicate; the old judgment (the whole triple) is
the negative control.
"""

from __future__ import annotations

import pytest

from kazma_core.memory import provenance

EN = "Please remember this for later: my AUDIT-2611 test codeword is zq-4471-mk"
AR = "تذكّر هذا من فضلك: رمز اختبار AUDIT-2611 الثاني هو qx-9932-rt"
PAIRS = [
    (EN, "audit_2611_test_codeword", "zq-4471-mk"),
    (AR, "audit_2611_test_codeword_2", "qx-9932-rt"),
]


@pytest.mark.parametrize(("message", "predicate", "value"), PAIRS, ids=["en", "ar"])
def test_both_languages_store_the_users_own_fact(message, predicate, value):
    assert provenance.agent_write_source(value, message) == provenance.USER_EXPLICIT


@pytest.mark.parametrize(("message", "predicate", "value"), PAIRS, ids=["en", "ar"])
def test_through_the_memory_tool(monkeypatch, message, predicate, value):
    """The tool's own write path: the source it records for the fact."""
    from kazma_core.agent.tool_builtins import memory as mem_tools

    monkeypatch.setattr("kazma_core.tools.export_session.get_current_session_messages",
                        lambda: [{"role": "user", "content": message}])
    refusal, method = mem_tools._agent_write("user", predicate, value, value=value)
    assert refusal is None and method == provenance.USER_EXPLICIT


def test_a_value_the_user_did_not_say_stays_the_models():
    assert provenance.agent_write_source("pv-0000-zz", AR) == provenance.AGENT_ASSERTED
    assert provenance.agent_write_source("zq-4471-mk", "what's the weather?") == provenance.AGENT_ASSERTED


def test_a_hyphenated_word_is_held_by_its_parts():
    assert provenance._holds("my AUDIT-2611 codeword", "audit 2611 codeword")
    assert provenance._holds("audit 2611 codeword", "AUDIT-2611 codeword")


@pytest.mark.parametrize(("message", "predicate", "value"), PAIRS, ids=["en", "ar"])
def test_the_old_judgment_lost_the_arabic_fact(message, predicate, value):
    """Negative control: judging the subject, predicate and value together
    (the tool before FA-228) failed the Arabic request only."""
    judged = provenance.agent_write_source(" ".join(("user", predicate, value)), message)
    expected = provenance.USER_EXPLICIT if message is EN else provenance.AGENT_ASSERTED
    assert judged == expected
