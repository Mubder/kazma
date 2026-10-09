"""Code delimiters in prose must not shift subsequent Telegram styling."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_gateway.adapters.telegram_send import chunk_message, send_chunks_with_retry
from kazma_gateway.agent_handler.graph import _prepare_tg_outbound
from kazma_gateway.gateway import IncomingMessage
from kazma_gateway.telegram_format import md_to_tg_html, tg_quote


@pytest.mark.parametrize("prefix", ["Fence markers: ", "علامات السياج: "])
def test_unmatched_run_does_not_steal_inline_opener(prefix):
    text = prefix + "``` or ~~~; `_protected` then `_join_prose_paragraphs`."
    expected = prefix + "``` or ~~~; <code>_protected</code> then <code>_join_prose_paragraphs</code>."
    assert md_to_tg_html(text) == expected
    # The original single-backtick matcher consumes the last tick of ``` and
    # the opener of `_protected`, so this is a real old-behavior control.
    old = re.sub(r"`([^`\n]+)`", lambda m: f"<code>{m[1]}</code>", text)
    assert old != expected and "<code>_protected</code>" not in old


@pytest.mark.parametrize(("source", "expected"), [
    ("Use ``a`b`` then `c_d`.", "Use <code>a`b</code> then <code>c_d</code>."),
    ("Use ```a``b``` and `c_d`.", "Use <code>a``b</code> and <code>c_d</code>."),
    ("Literal ``` and `` then `c_d`.", "Literal ``` and `` then <code>c_d</code>."),
    ("Use `` ` `` for ticks.", "Use <code> ` </code> for ticks."),
    ("Before `a\nb` after.", "Before `a\nb` after."),
    ("Unclosed ```\n`c_d`", "Unclosed ```\n<code>c_d</code>"),
    ("`<tag> & **text** _x_`", "<code>&lt;tag&gt; &amp; **text** _x_</code>"),
    ("Before ``` literal ``` after.", "Before <code> literal </code> after."),
])
def test_matching_inline_runs(source, expected):
    assert md_to_tg_html(source) == expected


@pytest.mark.parametrize("marker", ["```", "````", "~~~", "~~~~"])
def test_line_fences_preserve_code_whitespace_and_do_not_style_body(marker):
    body = '  print("<hello>")  \n\n**literal** _literal_ `literal`\n'
    source = f"Before\n{marker}python\n{body}{marker}\nAfter `a_b`"
    expected_body = body.replace("<hello>", "&lt;hello&gt;")
    assert md_to_tg_html(source) == f"Before\n<pre>{expected_body}</pre>\nAfter <code>a_b</code>"


def test_longer_closer_and_shorter_inner_fence():
    source = "````text\n```\n`inside`\n`````\n`outside_name`"
    assert md_to_tg_html(source) == "<pre>```\n`inside`\n</pre>\n<code>outside_name</code>"


def test_tilde_block_protects_backtick_fence_inside_it():
    source = "~~~\n```python\n  a_b\n```\n~~~"
    assert md_to_tg_html(source) == "<pre>```python\n  a_b\n```\n</pre>"


def test_tilde_opener_cannot_shrink_to_match_short_closer():
    source = "~~~~\ncode\n~~~"
    assert md_to_tg_html(source) == source


def test_crlf_fence_body_keeps_its_line_endings():
    assert md_to_tg_html("```python\r\n  a_b\r\n```\r\nend") == "<pre>  a_b\r\n</pre>\nend"


def test_fence_must_close_on_its_own_line_with_same_character():
    source = "```python\n`a_b`\n``` prose\n~~~\n`c_d`"
    rendered = md_to_tg_html(source)
    assert "<pre>" not in rendered
    assert "<code>a_b</code>" in rendered and "<code>c_d</code>" in rendered


def test_swarm_quote_uses_same_conversion_and_keeps_other_styles():
    source = "# Title\n**bold** *italic* [link](https://example.com)\n``` and `a_b`"
    assert tg_quote(source) == (
        '<blockquote><b>Title</b>\n<b>bold</b> <i>italic</i> '
        '<a href="https://example.com">link</a>\n``` and <code>a_b</code></blockquote>'
    )


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
def test_conversion_is_telegram_only_and_context_is_not_mutated(platform):
    source = "``` and `a_b`"
    ctx = {"thread_id": "fixture"}
    out, meta = _prepare_tg_outbound(IncomingMessage(platform, "fixture", "test"), source, ctx)
    if platform == "telegram":
        assert out == "``` and <code>a_b</code>" and meta["parse_mode"] == "HTML"
    else:
        assert out == source and meta is ctx
    assert ctx == {"thread_id": "fixture"}


async def test_gateway_html_reaches_sender_without_plaintext_fallback():
    source = "السياج ``` ثم `_protected` و `_join_prose_paragraphs`."
    out, meta = _prepare_tg_outbound(IncomingMessage("telegram", "fixture", "test"), source, {})
    response = MagicMock(status_code=200)
    response.json.return_value = {"ok": True}
    http = MagicMock(post=AsyncMock(return_value=response))
    assert await send_chunks_with_retry(
        http=http, chat_id=1, chunks=chunk_message(out, parse_mode=meta["parse_mode"]),
        parse_mode=meta["parse_mode"], reply_markup=None, rate_acquire=AsyncMock(),
    )
    http.post.assert_awaited_once()
    payload = http.post.call_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == "السياج ``` ثم <code>_protected</code> و <code>_join_prose_paragraphs</code>."
