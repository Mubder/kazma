"""Two model calls' narrations stay two paragraphs, live and after an
approval (resilience plan I).

Live, separate narration paragraphs were joined into one: the live stream
added a blank line between model calls only when the text before ended in
no whitespace (one newline then joins in Markdown), and the resumed leg
after an approval added nothing. Both paths take the break from
``_paragraph_break``, and the stored turn keeps it.
"""

from __future__ import annotations

import ast
import inspect

from kazma_ui.sse_chat import _streaming
from kazma_ui.sse_chat._streaming import _paragraph_break
from kazma_ui.turn_document import parts_from_stream


def test_the_break_makes_a_blank_line_whatever_the_text_ends_with():
    assert _paragraph_break("") == ""
    assert _paragraph_break("Checking the files.") == "\n\n"
    assert _paragraph_break("Checking the files.\n") == "\n"
    assert _paragraph_break("Checking the files.\n\n") == ""
    # Negative control: the old rule left one newline -- one paragraph in Markdown.
    old = "Checking the files.\n" + ("" if "Checking the files.\n".endswith(("\n", " ", "\t")) else "\n\n")
    assert old + "Found 3." == "Checking the files.\nFound 3."


def test_the_stored_turn_keeps_the_paragraphs():
    acc = "Checking the files.\n"
    acc += _paragraph_break(acc) + "Found 3 files."
    text = [p["text"] for p in parts_from_stream(streamed=acc, final="") if p["type"] == "text"][0]
    assert "Checking the files.\n\nFound 3 files." in text


def test_both_paths_take_the_break_from_one_place():
    source = inspect.getsource(_streaming)
    calls = [n for n in ast.walk(ast.parse(source))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_paragraph_break"]
    assert len(calls) >= 3, "the live path (check and separator) and the resume leg"
    assert 'not content_acc.endswith(("\n", " ", "\t"))' not in source
