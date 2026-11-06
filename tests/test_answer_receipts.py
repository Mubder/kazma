"""A turn's closing answer matches what its tools did (resilience plan D3).

The final answer is the model's own account of its work. A report "saved as
kuwait_history.pdf" after a refused generate_pdf read like a finished job.
At the terminal boundary the answer is checked against the running turn's
tool receipts: a claimed file no tool wrote gets a "Not done" line in the
answer's language, and files written but not named get one line built from
the receipts.
"""

from __future__ import annotations

import asyncio
import json

from kazma_core.agent.answer_receipts import check_answer_receipts


def _turn(answer, calls=(), *, user="Write the report as a PDF."):
    """A turn: the user's request, the model's calls and their results, the answer.
    *calls*: (name, args, result content, is_error)."""
    messages = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"},
                {"role": "user", "content": user}]
    results = {}
    for i, (name, args, content, is_error) in enumerate(calls):
        cid = f"c{i}"
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
        messages.append({"role": "tool", "tool_call_id": cid, "name": name, "content": content})
        results[cid] = {"name": name, "content": content, "is_error": is_error}
    messages.append({"role": "assistant", "content": answer})
    return messages, {"tool_results": results}


def _final(messages, state):
    return check_answer_receipts(messages, state)[-1]["content"]


WROTE_MD = ("file_write", {"path": "reports/kuwait.md", "content": "# Kuwait"}, "Wrote 1 lines, 8 bytes to reports/kuwait.md", False)
PDF_REFUSED = ("generate_pdf", {"title": "Kuwait history", "markdown_path": "reports/kuwait.md"},
               "Error: the document service is unavailable; nothing was written.", True)


def test_a_claimed_file_no_tool_wrote_gets_a_correction():
    messages, state = _turn("I saved the report as kuwait_history.pdf.", [WROTE_MD, PDF_REFUSED])
    text = _final(messages, state)
    assert text.startswith("I saved the report as kuwait_history.pdf.")
    assert "Not done: no tool wrote or sent kuwait_history.pdf in this turn." in text


def test_negative_control_without_the_check_the_false_claim_stands():
    messages, state = _turn("I saved the report as kuwait_history.pdf.", [WROTE_MD, PDF_REFUSED])
    assert messages[-1]["content"] == "I saved the report as kuwait_history.pdf."


def test_a_true_claim_gets_nothing():
    messages, state = _turn("I wrote the draft to reports/kuwait.md.", [WROTE_MD])
    assert _final(messages, state) == "I wrote the draft to reports/kuwait.md."


def test_a_generated_document_named_in_its_result_counts():
    made = ("generate_pdf", {"title": "Kuwait history"},
            "✅ PDF saved: C:/data/documents/Kuwait_history.pdf (3 pages)", False)
    messages, state = _turn("Done: I generated Kuwait_history.pdf for you.", [made])
    assert _final(messages, state) == "Done: I generated Kuwait_history.pdf for you."


def test_an_arabic_claim_is_corrected_in_arabic():
    messages, state = _turn("حفظت التقرير في الملف kuwait_history.pdf.", [WROTE_MD, PDF_REFUSED],
                            user="اكتب التقرير بصيغة PDF")
    text = _final(messages, state)
    assert "لم يتم: لم تكتب أي أداة الملف kuwait_history.pdf" in text


def test_an_arabic_sentence_naming_the_file_is_still_read():
    # "الملف" holds the letters of "لم" (not): the negation is a whole word only.
    messages, state = _turn("تم حفظ الملف kuwait_history.pdf بنجاح.", [PDF_REFUSED], user="احفظه")
    assert "لم يتم:" in _final(messages, state)


def test_a_negated_offered_or_earlier_file_is_no_claim():
    for answer in (
        "I could not save kuwait_history.pdf: the document service is down.",
        "I can save it as kuwait_history.pdf if you want.",
        "Earlier I saved kuwait_history.pdf for you.",
        "لم أتمكن من حفظ الملف kuwait_history.pdf.",
        "سأحفظه باسم kuwait_history.pdf بعد موافقتك.",
    ):
        messages, state = _turn(answer, [PDF_REFUSED])
        assert _final(messages, state) == answer, answer


def test_files_written_but_not_named_are_listed_from_the_receipts():
    messages, state = _turn("The report is ready.", [WROTE_MD])
    assert _final(messages, state) == "The report is ready.\n\nFiles written or sent this turn: kuwait.md."


def test_a_failed_turn_a_json_answer_and_one_paragraph_are_left_alone():
    messages, state = _turn("I saved kuwait_history.pdf.", [PDF_REFUSED])
    assert check_answer_receipts(messages, {**state, "turn_failed": True}) == messages
    messages, state = _turn('{"saved": "kuwait_history.pdf"}', [PDF_REFUSED])
    assert check_answer_receipts(messages, state) == messages
    messages, state = _turn("I saved kuwait_history.pdf.", [PDF_REFUSED],
                            user="Answer in one paragraph: what did you do?")
    assert check_answer_receipts(messages, state) == messages


def test_an_earlier_turns_write_is_not_this_turns():
    messages, state = _turn("I saved kuwait.md.", [])
    # The same file written in an EARLIER turn is not a receipt of this one.
    messages[1:1] = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "old", "type": "function", "function": {
            "name": "file_write", "arguments": json.dumps({"path": "kuwait.md", "content": "x"})}}]},
        {"role": "tool", "tool_call_id": "old", "name": "file_write", "content": "Wrote 1 lines"},
    ]
    assert "Not done" in _final(messages, state)


def test_the_respond_node_applies_it():
    """Through the real terminal boundary."""
    from kazma_core.agent.graph_respond import respond_node

    messages, state = _turn("I saved the report as kuwait_history.pdf.", [WROTE_MD, PDF_REFUSED])
    out = asyncio.run(respond_node({**state, "messages": messages, "iteration": 1, "max_iterations": 15,
                                    "thread_id": "t-receipts"}))
    assert "Not done: no tool wrote or sent kuwait_history.pdf" in out["messages"][-1]["content"]
