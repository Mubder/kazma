"""A model's thinking text is never part of its answer, whole or streamed, for
any provider (``kazma_core.reasoning_text``)."""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import httpx
import pytest
from kazma_core import reasoning_text
from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.reasoning_text import ThinkFilter, split_reasoning, text_of_parts

ANSWER = "The meeting is on Thursday at ten, in the small room."

CASES = {
    "a think block first": ("<think>\nlet me see\n</think>\n\n" + ANSWER, ANSWER),
    "on one line": ("<think>Okay, let me see.</think>\n\n" + ANSWER, ANSWER),
    "two of them": ("<think>\na</think>\n<think>b</think>\n" + ANSWER, ANSWER),
    "thinking, upper case": (" <THINKING>\na</THINKING> " + ANSWER, ANSWER),
    "never closed": ("<think>\nstill going when the limit came", ""),
    "the Qwen reply": (ANSWER + "\n</think>\n\n" + ANSWER, ANSWER),
    "the Qwen reply, on one line": (ANSWER + "</think>" + ANSWER, ANSWER),
    "a lone tag on its line": ("First I add them.\n</think>\n\n" + ANSWER, ANSWER),
    "a tag at a line's end": ("First I add them. Done.</think>\n\n" + ANSWER, ANSWER),
    "a tag after the answer": (ANSWER + "\n</think>\n", ANSWER),
    "Arabic": ("<think>\nأفكر</think>\nالاجتماع يوم الخميس", "الاجتماع يوم الخميس"),
    "Arabic, a tag at a line's end": ("أجمعهما أولًا؟</think>\n\nالاجتماع يوم الخميس", "الاجتماع يوم الخميس"),
}

KEPT = {
    "no tag": ANSWER,
    "a word": "I think it is on Thursday.",
    "the tag named in prose": "A model writes </think> when it is done, and then the answer.",
    "the tag in a code span": "It ends with `</think>` then the answer.\n`</think>`\nSo it goes.",
    "the tag in a fence": "```\n<think>\nx\n</think>\ny\n```\nThat is the format.",
    "an opening tag later": "Use <think> to start it.",
    "less than": "a < b and b <c",
    # The review of 470f10299 (M4): each was cut.
    "an answer about the tag, first word": "<think> is an opening tag. DeepSeek R1 and Qwen use it.",
    "the same, with its closing tag": ("<think> is the tag that opens it. They close it with </think>, and what "
                                       "follows is the answer."),
    "the same, at a line's end": "<think> is the tag that opens it, and it closes with </think>\nThen comes the answer.",
    "an example with both tags": "A Qwen reply looks like this:\n\n<think>\nX\n</think>\n\nThe answer is X.",
    "an indented example": "It looks like this:\n\n    X\n    </think>\n\nThen the answer.",
    "a tab": "It looks like this:\n\n\t</think>\n\nThen the answer.",
    "a tilde fence": "~~~\nX\n</think>\ny\n~~~\nThat is it.",
    "a tilde fence holding backticks": "~~~\n```\n</think>\n\ny\n~~~\nThat is it.",
    "introduced by a colon": "The closing tag is:\n</think>\n\nThe answer comes after it.",
    "a whole reply on one line": "<think>reasoning</think>answer is the format.",
    "only a block on one line": "<think>reasoning</think>",
    "unclosed, on a line": "<think>reasoning goes here, then its closing tag.",
}


def _streamed(text: str, cuts: list[int]) -> str:
    """What a stream of *text* cut at *cuts* shows."""
    f, shown, last = ThinkFilter(), [], 0
    for cut in [*cuts, len(text)]:
        shown.append(f.feed(text[last:cut]))
        last = cut
    return "".join(shown) + f.finish()


def _retracted(text: str, cuts: list[int]) -> str:
    f, last = ThinkFilter(), 0
    for cut in [*cuts, len(text)]:
        f.feed(text[last:cut])
        last = cut
    f.finish()
    return f.retracted


@pytest.mark.parametrize("name", CASES)
def test_reasoning_is_taken_out(name):
    text, answer = CASES[name]
    got, reasoning = split_reasoning(text)
    assert got.strip() == answer and reasoning or name == "a tag after the answer"
    assert got.strip() == answer


@pytest.mark.parametrize("name", KEPT)
def test_an_answer_that_names_the_tag_is_not_cut(name):
    assert split_reasoning(KEPT[name]) == (KEPT[name], "")


@pytest.mark.parametrize("name", [*CASES, *KEPT])
def test_a_stream_shows_no_tag_and_nothing_twice_however_it_is_cut(name):
    text, answer = CASES.get(name) or (KEPT[name], KEPT[name])
    rng = random.Random(7)
    ways = [list(range(1, len(text))), []] + [
        sorted(rng.sample(range(1, len(text)), k=min(5, len(text) - 1))) for _ in range(40)]
    for cuts in ways:
        shown = _streamed(text, cuts)
        if name in KEPT:
            assert shown == text
            continue
        assert "think" not in shown.lower()
        # What streamed is the answer, or what stood before a stray tag (which
        # the final response then replaces) -- never both.
        before = text.split("</think>")[0].strip()
        assert shown.strip() in (answer, before)
        if shown.strip() not in (answer, ""):
            # Thinking with no opening tag: it streamed, and is taken back.
            assert _retracted(text, cuts).strip() == before


def test_a_cut_answer_that_was_still_thinking_on_its_first_line():
    text = "<think>Okay, let me see what"
    assert split_reasoning(text, cut=True) == ("", "Okay, let me see what")
    assert split_reasoning(text) == (text, "")
    spaced = "<think> is a tag that some models"  # prose, cut or not
    assert split_reasoning(spaced, cut=True) == (spaced, "")
    for cut, shown in ((True, ""), (False, text)):
        f = ThinkFilter()
        assert f.feed(text) + f.finish(cut=cut) == shown


def test_parts_of_a_list_that_are_reasoning_are_not_text():
    parts = [{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]}, {"type": "reasoning", "text": "hm"},
             {"type": "text", "text": ANSWER}]
    assert text_of_parts(parts) == ANSWER


# ── Through the client ──────────────────────────────────────────────────


def _client(handler) -> LLMProvider:
    client = LLMProvider(LLMConfig(base_url="https://api.example.com/v1", api_key="k", model="m"))
    wired = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.example.com/v1")

    async def _wired():
        return wired

    client._get_client = _wired
    return client


def _sse(pieces: list[dict]) -> str:
    frames = [json.dumps({"choices": [{"index": 0, "delta": d}]}) for d in pieces]
    frames.append(json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    return "".join(f"data: {f}\n\n" for f in frames) + "data: [DONE]\n\n"


def _old_stream(pieces: list[dict]) -> str:
    """What a stream showed until 2026-11-25: every content delta (the control)."""
    return "".join(str(d.get("content") or "") for d in pieces)


#: A recorded Qwen-shaped stream: its reasoning field, then the answer, a
#: stray tag, and the answer again.
QWEN = [{"reasoning_content": "The user asks when."}, *({"content": w + " "} for w in ANSWER.split()),
        {"content": "</think>"}, {"content": "\n\n"}, *({"content": w + " "} for w in ANSWER.split())]


async def test_the_recorded_qwen_stream(monkeypatch):
    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    client = _client(lambda req: httpx.Response(200, text=_sse(QWEN), headers={"content-type": "text/event-stream"}))
    shown, final = "", None
    async for delta in client.chat_stream([{"role": "user", "content": "when?"}]):
        shown += delta.content or ""
        final = delta.response or final
    assert final is not None and final.content.strip() == ANSWER
    assert "</think>" not in shown and shown.count("Thursday") == 1
    old = _old_stream(QWEN)
    assert "</think>" in old and old.count("Thursday") == 2


@pytest.mark.parametrize("message", [
    {"content": "<think>\nhm</think>" + ANSWER}, {"content": ANSWER, "reasoning_content": "hm"},
    {"content": ANSWER, "reasoning": "hm"},
    {"content": [{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]}, {"type": "text", "text": ANSWER}]},
])
async def test_a_blocking_answer(message):
    client = _client(lambda req: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", **message}, "finish_reason": "stop"}], "usage": {}}))
    answer = await client.chat([{"role": "user", "content": "when?"}])
    assert answer.content == ANSWER and answer.reasoning_chars > 0 or isinstance(message["content"], list)
    assert answer.content == ANSWER


def test_anthropic_and_bedrock_too():
    from kazma_core.anthropic_llm import AnthropicProvider
    from kazma_core.bedrock_llm import BedrockProvider

    text = "<think>\nhm</think>\n" + ANSWER
    anthropic = AnthropicProvider(LLMConfig(api_key="k", model="claude-x"))
    got = anthropic._parse_response(
        {"content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": text}], "usage": {}}, "claude-x", 0.0)
    assert got.content == ANSWER
    bedrock = BedrockProvider(LLMConfig(model="m"), region="us-east-1")
    assert bedrock._parse({"output": {"message": {"content": [{"text": text}]}}}, "m", 0.0).content == ANSWER


# ── The class: every client's text goes through the rule ────────────────
#
# Read: every file under kazma_core, nested ones too. A StreamDelta's text,
# positional or named, is a whole parsed response's, or a name that is only
# ever given ThinkFilter's answers in its file. Every text a response is given
# -- LLMResponse(...), a ``replace(..., content=...)`` of one, a ``x.content =``
# or ``+=`` after it was made -- is a constant or a name every binding of which
# in its function is split_reasoning's answer (the review of d3ae5b2c8, N5). Not
# read: a client that names the classes otherwise, builds them through a
# helper, or sets the text through ``setattr``.


def _delta_text(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == "content"), None)


def _names_given(tree: ast.AST, name: str) -> list[ast.expr]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            out.append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and getattr(node.target, "id", "") == name:
            out.append(node.value)
    return out


def _filtered(value: ast.expr | None) -> bool:
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr in ("feed", "finish")


def _raw_text(source: str) -> list[str]:
    """What streams or returns a provider's text as it came."""
    tree = ast.parse(source)
    scope: dict[ast.AST, ast.AST] = {}  # node -> the function it is in (or the module)

    def mark(node: ast.AST, inside: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            scope[child] = inside
            mark(child, child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else inside)

    mark(tree, tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        made = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if made == "StreamDelta":
            text = _delta_text(node)
            if text is None or ast.unparse(text) in ("resp.content", "response.content"):
                continue
            given = _names_given(tree, text.id) if isinstance(text, ast.Name) else []
            if not given or not all(_filtered(v) for v in given):
                out.append(ast.unparse(node))
        elif made in ("LLMResponse", "replace"):
            text = next((k.value for k in node.keywords if k.arg == "content"),
                        node.args[0] if node.args and made == "LLMResponse" else None)
            if text is not None and not _answer_of_split(scope.get(node, tree), text):
                out.append(ast.unparse(node)[:80])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == "content" for t in targets) and (
                isinstance(node, ast.AugAssign) or not _answer_of_split(scope.get(node, tree), node.value)
            ):
                out.append(ast.unparse(node)[:80])
    return out


def _answer_of_split(tree: ast.AST, text: ast.expr | None) -> bool:
    """A constant, or a name every binding of which in the function it is
    used in is split_reasoning's answer."""
    if text is None or isinstance(text, ast.Constant):
        return True
    return isinstance(text, ast.Name) and _split_into(tree, text.id)


def _split_into(tree: ast.AST, name: str) -> bool:
    """Every value *name* is given in the file is split_reasoning's answer:
    any other binding of it (a plain assignment, ``+=``, a loop or ``with``
    target, ``:=``) counts against it."""
    given = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and target.elts and getattr(target.elts[0], "id", "") == name:
                    given.append(getattr(getattr(node.value, "func", None), "id", "") == "split_reasoning")
                elif any(getattr(t, "id", "") == name for t in ast.walk(target)):
                    given.append(False)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr, ast.For, ast.AsyncFor, ast.comprehension)):
            if any(getattr(t, "id", "") == name for t in ast.walk(node.target)):
                given.append(False)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            if any(getattr(t, "id", "") == name for t in ast.walk(node.optional_vars)):
                given.append(False)
    return bool(given) and all(given)


#: Files that make an LLMResponse of text no provider sent.
NOT_A_PROVIDERS_TEXT: dict[str, str] = {}


def test_no_client_gives_a_providers_text_as_it_came():
    root = Path(reasoning_text.__file__).parent
    texts = {str(p.relative_to(root)).replace("\\", "/"): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))}
    clients = {name: text for name, text in texts.items()
               if ("StreamDelta(" in text or "LLMResponse(" in text) and name != "llm_stream.py"}
    assert {"llm_provider.py", "anthropic_llm.py", "bedrock_llm.py"} <= set(clients)
    raw = {name: found for name, text in clients.items() if name not in NOT_A_PROVIDERS_TEXT and (found := _raw_text(text))}
    assert raw == {}


@pytest.mark.parametrize("planted", [
    "yield StreamDelta(content=piece)", "yield StreamDelta(piece)", "yield llm_stream.StreamDelta(content=piece)",
    "shown = piece\nyield StreamDelta(content=shown)",
    "shown = f.feed(piece)\nshown += piece\nyield StreamDelta(content=shown)",
    "answer = LLMResponse(content=''.join(parts))", "content = ''.join(parts)\nanswer = LLMResponse(content=content)",
    "answer = LLMResponse(text)",
    # The review of d3ae5b2c8, N5: the name given another text too, a copy
    # with another text, and the text set after the response was made.
    "content, h = split_reasoning(t)\ncontent = ''.join(parts)\nanswer = LLMResponse(content=content)",
    "content, h = split_reasoning(t)\nfor content in parts: pass\nanswer = LLMResponse(content=content)",
    "answer = dataclasses.replace(resp, content=''.join(parts))",
    "content, h = split_reasoning(t)\nresp = LLMResponse(content=content)\nresp.content = raw",
    "resp.content += piece",
])
def test_the_gate_sees_a_providers_text(planted):
    assert _raw_text(planted)
    assert not _raw_text("shown = f.feed(piece)\nyield StreamDelta(content=shown)\nyield StreamDelta(response=r)")
    assert not _raw_text("content, hidden = split_reasoning(text)\nanswer = LLMResponse(content=content)")
    assert not _raw_text("content, hidden = split_reasoning(text)\nanswer = replace(resp, content=content)\n"
                         "answer.content = ''")


async def test_thinking_with_no_opening_tag_is_not_kept_as_the_turns_words(monkeypatch):
    """It streams (nothing says yet what it is); at its closing tag what was
    streamed is taken back, and the stream's consumer is told."""
    import asyncio

    from kazma_core import llm_stream

    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    thinking = "First the date, then the room."
    pieces = [{"content": w + " "} for w in thinking.split()] + [{"content": "\n</think>\n\n"}, {"content": ANSWER}]
    client = _client(lambda req: httpx.Response(200, text=_sse(pieces), headers={"content-type": "text/event-stream"}))
    queue: asyncio.Queue = asyncio.Queue()
    llm_stream.register_delta_queue("retract-thread", queue)
    # emit_token_delta and the retraction find the thread the same way.
    monkeypatch.setattr(llm_stream, "_resolve_stream_thread", lambda tid: "retract-thread")
    monkeypatch.setattr("kazma_core.safety.hitl.get_current_thread_id", lambda: "retract-thread")
    try:
        answer = await llm_stream.invoke_llm_chat(client, [{"role": "user", "content": "when?"}])
    finally:
        llm_stream.unregister_delta_queue("retract-thread")
    events = [queue.get_nowait() for _ in range(queue.qsize())]
    streamed = "".join(e["data"]["chunk"]["content"] for e in events if e["event"] == "on_chat_model_stream")
    [back] = [e["data"]["text"] for e in events if e["event"] == "on_chat_model_retract"]
    assert answer.content == ANSWER and streamed.strip() == thinking and streamed.endswith(back) and back.strip() == thinking


@pytest.mark.parametrize("retracts", [True, False])
def test_a_turn_keeps_no_thinking_of_a_model_with_no_opening_tag(monkeypatch, tmp_path, retracts):
    """Through the real graph and streamer: the saved row and the final frame
    hold the answer alone. A client that takes nothing back is the control."""
    from kazma_core.llm_provider import LLMResponse
    from kazma_core.llm_stream import StreamDelta

    from tests.test_done_frame_names_the_answering_model import _turn

    thinking = "First the date, then the room."

    class _Thinks:
        async def chat(self, *args, **kwargs):
            raise AssertionError("it streams")

        async def chat_stream(self, *args, **kwargs):
            yield StreamDelta(content=thinking)
            if retracts:
                yield StreamDelta(retract=thinking)
            yield StreamDelta(response=LLMResponse(content=ANSWER, finish_reason="stop", model="m",
                                                   usage={"total_tokens": 3}))

    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    done, row = _turn(monkeypatch, tmp_path, _Thinks(), f"retract-{retracts}")
    assert done["content"] == ANSWER and row["content"] == ANSWER
    assert ("First the date" in json.dumps(row)) is not retracts


# ── The review of d3ae5b2c8, N6: a retraction and a full queue ──────────


def test_a_retraction_names_only_what_reached_the_page():
    from kazma_core.llm_stream import _queued_tail

    pieces = [("a", True), ("bc", False), ("de", True)]
    assert _queued_tail(pieces, 4) == "de" and pieces == [("a", True)]
    pieces = [("xab", True), ("c", True)]
    assert _queued_tail(pieces, 3) == "abc" and pieces == [("x", True)]
    assert _queued_tail([("ab", False)], 2) == ""


@pytest.mark.parametrize("size", [3, 5, 40])
async def test_a_full_queue_does_not_keep_the_thinking(monkeypatch, size):
    """A token dropped from a full queue made the retraction miss (it took
    back an exact end), and a retraction itself was dropped when the queue was
    full: the narration kept the thinking."""
    import asyncio

    from kazma_core import llm_stream
    from kazma_core.llm_provider import LLMResponse
    from kazma_core.llm_stream import StreamDelta, invoke_llm_chat

    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    thinking = ["First ", "the ", "date, ", "then ", "the ", "room. "]

    class _Client:
        async def chat_stream(self, *a, **kw):
            for piece in thinking:
                yield StreamDelta(content=piece)
            yield StreamDelta(retract="".join(thinking))
            yield StreamDelta(response=LLMResponse(content=ANSWER))

    queue: asyncio.Queue = asyncio.Queue(maxsize=size)
    llm_stream.register_delta_queue("n6-thread", queue)
    try:
        await _run(invoke_llm_chat, _Client())
    finally:
        llm_stream._delta_queues.pop("n6-thread", None)
    painted = ""
    while not queue.empty():
        event = queue.get_nowait()
        if event["event"] == "on_chat_model_stream":
            painted += event["data"]["chunk"]["content"]
        elif event["event"] == "on_chat_model_retract":
            painted = painted.removesuffix(event["data"]["text"])
    assert painted == ""


async def _run(invoke, client):
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id("n6-thread")
    try:
        return await invoke(client, [{"role": "user", "content": "x"}])
    finally:
        reset_current_thread_id(token)
