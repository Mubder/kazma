"""A write card must show the decision, not the document.

2026-09-17, live Telegram. Approving a `file_write` produced a card that was
3,200 characters of Arabic markdown rendered as ONE line of literal ``\\n``
escapes -- the JSON dump of the ``content`` arg -- ending in::

    ⚠️ 638 MORE CHARACTERS ARE NOT SHOWN.
    Open the web UI to see the rest before approving.

The show-everything rule is correct and was written for a reason: on
2026-08-30 a truncated ``cp a b && rm -rf c`` read as harmless, because the
dangerous half of a chained command is at the end. But that rule was written
for ``shell_exec`` and then applied to every tool. For a file write the
decision is *how many bytes, to which path*; the prose is not what is being
authorised, and telling the operator to go read 638 more characters of their
own document before they may save it is advice that helps nobody.

The same card carried a second defect: one hunk showed

    - الصدق المدمج: إن مات النموذج تقول ذلك صراحةً (⚠️) بدل تلفيق رد.
    + الصدق المدمج: إن مات النموذج تقول ذلك صراحةً (⚠️) بدل تلفيق رد.

identical to the eye. Something invisible differed, and there was no way to
see what.
"""

from __future__ import annotations

import json

import pytest
from kazma_gateway.agent_handler.hitl import (
    EXEC_TOOLS,
    _describe_noop,
    _format_args_for_approval,
    _format_patch_preview,
)

DOC = "# كاظمه — ما هي؟\n\n## تعريف سريع\n\n" + ("سطر عربي طويل جداً. " * 12 + "\n") * 60
PATH = "kazma-data/docs/kazma-what-is-ar.md"


# ── the card leads with the decision ───────────────────────────────────────


def test_a_write_card_states_path_and_size_before_anything_else():
    out = _format_args_for_approval("file_write", {"path": PATH, "content": DOC})
    head = out.splitlines()[0]

    assert PATH in head, "the path is the decision; it must not be buried"
    assert f"{len(DOC):,} characters" in out
    assert f"{len(DOC.splitlines()):,} lines" in out


def test_the_body_has_real_line_breaks_not_escapes():
    """The live card was one unbroken line of literal backslash-n."""
    out = _format_args_for_approval("file_write", {"path": PATH, "content": DOC})

    assert "\\n" not in out, "the document was JSON-escaped onto a single line"
    assert out.count("\n") > 10, "a multi-line document must render multi-line"
    assert "كاظمه" in out, "the excerpt must still be readable Arabic"


def test_a_long_document_no_longer_fills_the_card():
    out = _format_args_for_approval("file_write", {"path": PATH, "content": DOC})

    assert len(out) < 2000, f"card is {len(out)} chars; it was ~3,200 of prose"
    assert len(out) < len(DOC), "the card must be smaller than the document"


def test_it_says_how_much_it_is_not_showing():
    """Bounded, but never silently: the 2026-08-30 rule still applies."""
    out = _format_args_for_approval("file_write", {"path": PATH, "content": DOC})

    assert "more lines not shown" in out
    assert "MORE CHARACTERS ARE NOT SHOWN" not in out, (
        "'open the web UI to read your own prose' is not useful advice"
    )


def test_a_short_write_is_shown_whole_with_no_truncation_notice():
    body = "line one\nline two\nline three"
    out = _format_args_for_approval("file_write", {"path": "a.txt", "content": body})

    assert "line three" in out
    assert "not shown" not in out


def test_an_overlong_single_line_is_capped_but_marked():
    out = _format_args_for_approval(
        "file_write", {"path": "a.txt", "content": "x" * 5000}
    )

    assert len(out) < 1000
    assert "…" in out, "a clipped line must show that it was clipped"


def test_other_args_are_kept_because_they_are_decisions():
    out = _format_args_for_approval(
        "file_append", {"path": "a.txt", "content": "hi", "encoding": "latin-1"}
    )

    assert "encoding" in out and "latin-1" in out


def test_file_append_gets_the_same_treatment():
    out = _format_args_for_approval("file_append", {"path": PATH, "content": DOC})
    assert "\\n" not in out
    assert f"{len(DOC):,} characters" in out


# ── the shell_exec rule is untouched ───────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(EXEC_TOOLS))
def test_exec_tools_still_refuse_to_be_summarised(tool):
    """The regression this whole module exists to protect."""
    cmd = "cd /x && " + " && ".join(f"cp a{i} b{i}" for i in range(400))
    out = _format_args_for_approval(tool, {"command": cmd, "content": "decoy"})

    assert "MORE CHARACTERS ARE NOT SHOWN" in out
    assert "Do NOT approve this from chat" in out
    assert "characters," not in out.splitlines()[0], (
        "an exec tool must never be rendered as a content summary"
    )


def test_a_tool_with_no_content_arg_still_gets_json():
    out = _format_args_for_approval("send_file", {"file_path": "a.pdf", "caption": "c"})
    assert json.loads(out) == {"file_path": "a.pdf", "caption": "c"}


def test_a_non_string_content_falls_back_to_json():
    out = _format_args_for_approval("file_write", {"path": "a", "content": {"b": 1}})
    assert json.loads(out)["content"] == {"b": 1}


# ── a hunk that changes nothing says so ────────────────────────────────────


AR = "الصدق المدمج: إن مات النموذج تقول ذلك صراحةً (⚠️) بدل تلفيق رد."


@pytest.mark.parametrize(
    ("label", "old", "new", "expect"),
    [
        ("identical", AR, AR, "changes NOTHING"),
        ("bidi mark", AR, "‏" + AR, "invisible characters"),
        ("zero width", "ab", "a​b", "invisible characters"),
        ("nbsp for space", "a b", "a b", "invisible characters"),
        ("thin space", "a b", "a b", "invisible characters"),
        ("crlf", "a\r\nb", "a\nb", "line endings"),
        ("nfd", "é", "é", "normalisation"),
        ("trailing ws", "x  ", "x", "whitespace"),
    ],
)
def test_an_invisible_difference_is_named(label, old, new, expect):
    note = _describe_noop(old, new)
    assert note and expect in note, f"{label}: got {note!r}"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (AR, AR + " plus"),
        ("a b", "ab"),          # deleting a real space is a real change
        ("hello", "goodbye"),
        ("", "new file body"),
    ],
)
def test_a_real_change_is_not_called_a_noop(old, new):
    assert _describe_noop(old, new) is None


def test_the_patch_card_warns_before_the_hunk_not_after():
    out = _format_patch_preview(
        "file_apply_patch",
        {"path": PATH, "old_string": AR, "new_string": AR},
    )

    assert out is not None
    assert "changes NOTHING" in out
    warn_at = out.index("changes NOTHING")
    hunk_at = out.index(f"- {AR}")
    assert warn_at < hunk_at, (
        "printed after the diff it reads as a caption on something the "
        "operator has already tried and failed to read"
    )


def test_a_real_patch_carries_no_warning():
    out = _format_patch_preview(
        "file_apply_patch",
        {"path": PATH, "old_string": "before", "new_string": "after"},
    )

    assert out is not None
    assert "⚠️" not in out
    assert "- before" in out and "+ after" in out
