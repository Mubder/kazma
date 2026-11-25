"""A message one vendor's content filter refuses is answered by a backup of
another vendor, and the answer says so (``agent.content_refusal``)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from kazma_core.agent import resilient_chat as rc
from kazma_core.agent.content_refusal import is_content_refused, other_vendor, refusal_line
from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider, LLMResponse, _is_content_refusal
from kazma_core.retry import friendly_llm_error

QWEN_REFUSAL = {"error": {"code": "data_inspection_failed", "type": "data_inspection_failed", "message":
                          "<400> InternalError.Algo.DataInspectionFailed: Input text data may contain inappropriate content."}}

REFUSALS = [
    QWEN_REFUSAL,
    {"error": {"code": "content_filter", "message": "The response was filtered", "innererror": {"code": "ResponsibleAIPolicyViolation"}}},
    {"error": {"message": "Content Exists Risk", "type": "invalid_request_error"}},
    {"error": {"code": "content_policy_violation", "message": "Your function call was rejected"}},
]


def _client(handler, host="dashscope-intl.aliyuncs.com") -> LLMProvider:
    base = f"https://{host}/compatible-mode/v1"
    client = LLMProvider(LLMConfig(base_url=base, api_key="k", model="qwen-plus"))
    wired = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base)

    async def _wired():
        return wired

    client._get_client = _wired
    return client


@pytest.mark.parametrize("body", REFUSALS)
@pytest.mark.parametrize("stream", [False, True])
async def test_a_refusal_is_named_and_asked_once(body, stream, monkeypatch):
    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    asked = []

    def answer(req):
        asked.append(req)
        return httpx.Response(400, json=body)

    client = _client(answer)
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    with pytest.raises(LLMError) as raised:
        if stream:
            async for _delta in client.chat_stream([{"role": "user", "content": "x"}], tools=tools):
                pass
        else:
            await client.chat([{"role": "user", "content": "x"}], tools=tools)
    assert raised.value.kind == "content_refused" and not raised.value.transient
    assert len(asked) == 1, "asked again: a filter refuses it again"
    assert "content filter" in friendly_llm_error(raised.value, model="qwen-plus")


def test_only_a_filters_own_words_are_a_refusal():
    for status, text in ((400, "maximum context length"), (400, "tools: maximum number of items is 128"),
                         (401, "data_inspection_failed"), (429, "content_filter"), (500, "data_inspection_failed"),
                         (400, "invalid model"), (400, "")):
        assert not _is_content_refusal(status, text), text


def test_another_vendor_is_another_host():
    def at(host, name=""):
        return SimpleNamespace(config=SimpleNamespace(base_url=f"https://{host}/v1" if host else ""), provider_name=name)

    assert other_vendor(at("dashscope-intl.aliyuncs.com"), at("api.deepseek.com"))
    assert not other_vendor(at("api.deepseek.com"), at("API.deepseek.com"))
    assert not other_vendor(at("api.deepseek.com"), at(""))
    assert other_vendor(at("api.deepseek.com"), at("", "bedrock"))


def test_the_line_is_in_the_messages_language():
    assert refusal_line("what is this?", "qwen-plus", "deepseek-chat").startswith("> The content filter")
    assert refusal_line("ما هذا السؤال؟", "qwen-plus", "deepseek-chat").startswith("> رفض")
    assert "`deepseek-chat`" in refusal_line("ما هذا؟", "qwen-plus", "deepseek-chat")


# ── Work outside a chat turn (resilient_chat) ───────────────────────────


class _Backup:
    def __init__(self, host):
        self.config = SimpleNamespace(base_url=f"https://{host}/v1", model="b")
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        return LLMResponse(content="answered", model="b")


class _Refusing(_Backup):
    async def chat(self, messages, **kw):
        self.calls += 1
        raise LLMError("the filter refused", transient=False, kind="content_refused")


class _Wrong(_Refusing):
    async def chat(self, messages, **kw):
        self.calls += 1
        raise LLMError("invalid model", transient=False)


@pytest.fixture
def quiet(monkeypatch):
    async def nothing(**kw):
        return None

    alerts = []
    monkeypatch.setattr(rc, "_record", nothing)
    monkeypatch.setattr("kazma_core.observability.model_fallback.report_failover", lambda **kw: alerts.append(kw))
    rc._cooldowns.clear()
    return alerts


async def test_a_refused_call_goes_to_the_other_vendor_at_once(quiet):
    same, other = _Backup("dashscope-us.aliyuncs.com"), _Backup("api.deepseek.com")
    same.config.base_url = "https://dashscope-intl.aliyuncs.com/v1"
    primary = _Refusing("dashscope-intl.aliyuncs.com")
    frozen = SimpleNamespace(clients=(("same", same), ("other", other)), cooldown_seconds=0)
    answer = await rc.resilient_chat(primary, messages=[{"role": "user", "content": "x"}], frozen_failover=frozen)
    assert answer.content == "answered" and (primary.calls, same.calls, other.calls) == (1, 0, 1)
    assert quiet == [], "a filter's refusal is no failure to alert on"


async def test_another_permanent_error_still_fails_fast(quiet):
    """The control: a 4xx that is no refusal is asked nowhere else."""
    other = _Backup("api.deepseek.com")
    frozen = SimpleNamespace(clients=(("other", other),), cooldown_seconds=0)
    with pytest.raises(LLMError):
        await rc.resilient_chat(_Wrong("x.example"), messages=[{"role": "user", "content": "x"}], frozen_failover=frozen)
    assert other.calls == 0


async def test_with_no_other_vendor_the_refusal_is_the_answer(quiet):
    same = _Backup("dashscope-intl.aliyuncs.com")
    frozen = SimpleNamespace(clients=(("same", same),), cooldown_seconds=0)
    with pytest.raises(LLMError) as raised:
        await rc.resilient_chat(_Refusing("dashscope-intl.aliyuncs.com"),
                                messages=[{"role": "user", "content": "x"}], frozen_failover=frozen)
    assert is_content_refused(raised.value) and same.calls == 0


# ── A chat turn, through the real graph and streamer ────────────────────


@pytest.mark.parametrize("kind", ["content_refused", ""])
def test_a_turn_is_answered_by_the_other_vendor_and_says_why(monkeypatch, tmp_path, kind):
    from kazma_core.agent import graph_supervisor, nonstop
    from kazma_core.model_registry import get_model_registry

    from tests.test_done_frame_names_the_answering_model import _Answers, _turn

    backup = "backup-model-refusal"
    real = nonstop.get_nonstop_config

    def with_failover(*args, **kwargs):
        cfg = real(*args, **kwargs)
        cfg.failover.enabled, cfg.failover.chain, cfg.failover.cooldown_seconds = True, ["same-vendor", backup], 0.0
        return cfg

    class _Refused(_Backup):
        async def chat(self, messages=None, tools=None, *args, **kwargs):
            raise LLMError("refused", transient=False, kind=kind)

    class _Other(_Answers):
        config = SimpleNamespace(base_url="https://api.deepseek.com/v1", model=backup)

    same = _Backup("dashscope-intl.aliyuncs.com")
    monkeypatch.setattr(nonstop, "get_nonstop_config", with_failover)
    monkeypatch.setitem(graph_supervisor._failover_clients, backup, _Other(backup))
    monkeypatch.setitem(graph_supervisor._failover_clients, "same-vendor", same)
    monkeypatch.setattr(graph_supervisor, "_failover_built_for", get_model_registry().generation)
    done, row = _turn(monkeypatch, tmp_path, _Refused("dashscope-intl.aliyuncs.com"), f"refusal-{kind or 'plain'}")
    assert same.calls == 0
    if kind:
        assert done["content"].startswith("> The content filter") and done["content"].endswith(f"Answered by {backup}.")
        assert done["model"] == backup and row["content"] == done["content"]
    else:  # the control: any other permanent refusal is not asked elsewhere
        assert "Answered by" not in done["content"] and done["content"].startswith("⚠️")
