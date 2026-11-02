"""The Knowledge citation footer is in the turn's language (2026-11, FA-229).

An answer that uses Knowledge Library text carries a footer naming the
library, verbatim. It was English always, so an Arabic answer ended in an
English line; and a failed ``knowledge_search`` handed the model the
exception's own text. The footer now follows the language of the user's
message (auto-inject) or of the search (the tool), and the failure is
``safe_error``'s code and reference.

The English footer on an Arabic message (the old behaviour) is the negative
control.
"""

from __future__ import annotations

import asyncio

from kazma_core.stores.knowledge_index import knowledge_footer

AR = "ما هي سياسة الإجازات في الشركة؟"
EN = "What is the leave policy?"


def test_an_arabic_message_gets_an_arabic_footer():
    footer = knowledge_footer(["hr-handbook"], AR)
    assert footer.startswith("📚 هذه المعلومات من مكتبة المعرفة") and '"hr-handbook"' in footer


def test_an_english_message_keeps_the_english_footer():
    assert knowledge_footer(["hr-handbook"], EN) == '📚 This data is from Knowledge "hr-handbook".'


def test_several_libraries_and_none():
    assert knowledge_footer(["a", "b"], AR) == '📚 هذه المعلومات من مكتبات المعرفة: "a"، "b".'
    assert knowledge_footer([], EN) == "📚 This data is from the Knowledge Library."


def test_negative_control_the_old_footer_was_english_under_arabic():
    old = '📚 This data is from Knowledge "hr-handbook".'
    assert knowledge_footer(["hr-handbook"], AR) != old


def test_a_failed_search_hands_the_model_no_exception_text(monkeypatch):
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.stores import knowledge_index

    def broken():
        raise RuntimeError("connection to postgresql://kazma:hunter2@db/kazma failed")

    monkeypatch.setattr(knowledge_index, "get_knowledge_index", broken)
    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    out = asyncio.run(registry.get_tool("knowledge_search").func(query=EN))
    assert out.startswith("Error: knowledge search failed")
    assert "hunter2" not in out and "(ref: " in out
