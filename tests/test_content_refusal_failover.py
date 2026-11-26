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
    {"code": "DataInspectionFailed", "message": "Input data may contain inappropriate content."},
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
                         (400, "invalid model"), (400, ""),
                         # A parameter of that name, and bodies that echo the person's words.
                         (400, '{"error": {"code": "invalid_value", "message": "Invalid value: \'content_filter\'"}}'),
                         (422, '{"detail": [{"msg": "bad", "input": "content exists risk"}]}'),
                         (400, '{"error": {"code": "bad_request", "message": "x", "param": "data_inspection_failed"}}'),
                         (400, '{"error": {"message": "' + "a" * 400 + ' content exists risk"}}')):
        assert not _is_content_refusal(status, text), text


def _at(host, name="", preset="", proxy=""):
    direct = f"https://{host}/v1" if host else ""
    return SimpleNamespace(config=SimpleNamespace(base_url=proxy or direct), _direct_base_url=direct,
                           provider_name=name, provider_preset=preset)


@pytest.mark.parametrize(("one", "two", "other"), [
    (_at("dashscope-intl.aliyuncs.com"), _at("api.deepseek.com"), True),
    (_at("api.deepseek.com"), _at("API.deepseek.com"), False),
    (_at("api.deepseek.com"), _at(""), False),
    (_at("api.deepseek.com"), _at("", "bedrock"), True),
    # Another region, plan or address of one vendor is not another vendor.
    (_at("dashscope-intl.aliyuncs.com"), _at("dashscope-us.aliyuncs.com"), False),
    (_at("dashscope-intl.aliyuncs.com"), _at("cn-hongkong.dashscope.aliyuncs.com"), False),
    (_at("dashscope-intl.aliyuncs.com"), _at("llm-1.ap-southeast-1.maas.aliyuncs.com"), False),
    (_at("dashscope-intl.aliyuncs.com"), _at("ap-southeast-1.maas.qwencloudapi.com"), False),
    (_at("api.openai.com"), _at("eu.api.openai.com"), False),
    (_at("api.moonshot.ai"), _at("api.moonshot.cn"), False),
    (_at("one.openai.azure.com"), _at("two.openai.azure.us"), False),
    (_at("gate.example", "Work", "dashscope"), _at("dashscope.aliyuncs.com"), False),
    (_at("gate.example", "Work", "dashscope"), _at("gate.example", "Work 2", "deepseek"), True),
    # Through the egress proxy every client has the proxy's host.
    (_at("api.openai.com", proxy="http://127.0.0.1:4000/v1"), _at("api.deepseek.com", proxy="http://127.0.0.1:4000/v1"), True),
])
def test_another_vendor_is_another_preset(one, two, other):
    assert other_vendor(one, two) is other


def test_a_built_client_knows_whose_it_is():
    from kazma_core.provider_adapters import build_client

    def built(name, url, preset=""):
        return build_client(name, LLMConfig(base_url=url, api_key="k", model="m"), {"name": name, "preset": preset})

    assert not other_vendor(built("Qwen EU", "https://x.example/v1", "dashscope"), built("dashscope", "https://y.example/v1"))
    assert other_vendor(built("Qwen EU", "https://x.example/v1", "dashscope"), built("z", "https://x.example/v1"))


def test_step_five_offers_another_vendor():
    from kazma_core.failover_backup import suggest_backups

    rows = [{"name": "Qwen", "preset": "dashscope", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"},
            {"name": "Qwen US", "preset": "dashscope", "base_url": "https://dashscope-us.aliyuncs.com/compatible-mode/v1"},
            {"name": "deepseek", "base_url": "https://api.deepseek.com/v1"}]

    class _Registry:
        def list_providers(self):
            return [{**r, "enabled": True, "api_key": "demo-key-1"} for r in rows]

        def get_provider(self, name):
            return next((r for r in self.list_providers() if r["name"] == name), None)

        def get_active_profile(self):
            return {"provider": "Qwen"}

        def resolve_provider_credentials(self, name):
            return "", "demo-key-1"

        def get_visible_models(self, name):
            return [name.lower() + "-model"]

        def find_provider_for_model(self, model):
            return next(r for r in self.list_providers() if model == r["name"].lower() + "-model")

    assert [b["provider"] for b in suggest_backups(_Registry())] == ["deepseek"]
    assert [b["provider"] for b in suggest_backups(_Registry(), beside="deepseek")] == ["Qwen"]


PICTURE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def test_the_line_follows_the_persons_own_last_message():
    from kazma_core.agent.content_refusal import already_told, person_text
    from kazma_core.agent.plan_fence import AUTO_CONTINUE_PROMPT

    asked = {"role": "user", "content": [{"type": "text", "text": "ما هذا؟"}, PICTURE]}
    assert person_text([asked]) == "ما هذا؟"
    assert refusal_line(person_text([asked]), "a", "b").startswith("> رفض")
    nudged = [asked, {"role": "assistant", "content": refusal_line("ما هذا؟", "a", "b") + "\n\nx"},
              {"role": "user", "content": AUTO_CONTINUE_PROMPT}]
    assert person_text(nudged) == "ما هذا؟" and already_told(nudged)
    assert not already_told([*nudged, {"role": "user", "content": "وهذا؟"}])  # another turn
    assert not already_told([asked])


def test_the_failure_says_what_was_done_in_the_messages_language():
    alone, tried = LLMError("x", kind="content_refused"), LLMError("x", kind="content_refused")
    tried.backup_tried = True
    both = friendly_llm_error(alone)
    assert "content filter" in both and "مرشّح المحتوى" in both and "set a backup model" in both
    english = friendly_llm_error(tried, user_text="what is it?")
    assert "مرشّح" not in english and "set a backup" not in english and "did not answer either" in english
    assert "The content filter" not in friendly_llm_error(tried, user_text="ما هذا السؤال؟")


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


async def test_a_backup_that_refuses_too_does_not_rest(quiet):
    """It is not down: the next outage asks it."""
    refusing = _Refusing("api.deepseek.com")
    frozen = SimpleNamespace(clients=(("other", refusing),), cooldown_seconds=300)
    with pytest.raises(LLMError) as raised:
        await rc.resilient_chat(_Refusing("dashscope-intl.aliyuncs.com"),
                                messages=[{"role": "user", "content": "x"}], frozen_failover=frozen)
    assert refusing.calls == 1 and rc._cooldowns == {} and raised.value.backup_tried
    wrong = _Wrong("api.deepseek.com")  # the control: one that failed rests
    with pytest.raises(LLMError):
        await rc.resilient_chat(_Refusing("dashscope-intl.aliyuncs.com"), messages=[{"role": "user", "content": "x"}],
                                frozen_failover=SimpleNamespace(clients=(("wrong", wrong),), cooldown_seconds=300))
    assert "wrong" in rc._cooldowns
    rc._cooldowns.clear()


# ── Every client's refusals ─────────────────────────────────────────────


@pytest.mark.parametrize("stream", [False, True])
async def test_anthropics_refusals(monkeypatch, stream):
    from kazma_core.anthropic_llm import AnthropicProvider

    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "Output blocked by content filtering policy"}}
    client = AnthropicProvider(LLMConfig(base_url="https://api.anthropic.com/v1", api_key="k", model="claude-x"))
    wired = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(400, json=body)),
                              base_url="https://api.anthropic.com/v1")

    async def _wired():
        return wired

    client._get_client = _wired
    with pytest.raises(LLMError) as raised:
        if stream:
            async for _delta in client.chat_stream([{"role": "user", "content": "x"}]):
                pass
        else:
            await client.chat([{"role": "user", "content": "x"}])
    assert is_content_refused(raised.value)
    with pytest.raises(LLMError) as raised:
        client._parse_response({"content": [], "stop_reason": "refusal", "usage": {}}, "claude-x", 0.0)
    assert is_content_refused(raised.value)
    kept = client._parse_response({"content": [{"type": "text", "text": "I will not."}], "stop_reason": "refusal", "usage": {}}, "c", 0.0)
    assert kept.content == "I will not."


async def test_bedrocks_refusals():
    from kazma_core.bedrock_llm import BedrockProvider

    class _Boto:
        def converse(self, **kw):
            exc = RuntimeError("ValidationException")
            exc.response = {"Error": {"Code": "ValidationException",
                                      "Message": "The model returned the following errors: Output blocked by content filtering policy"},
                            "ResponseMetadata": {"HTTPStatusCode": 400}}
            raise exc

    client = BedrockProvider(LLMConfig(model="m"), region="us-east-1")
    client._get_client = lambda: _Boto()
    with pytest.raises(LLMError) as raised:
        await client.chat([{"role": "user", "content": "x"}])
    assert is_content_refused(raised.value)
    with pytest.raises(LLMError) as raised:
        client._parse({"output": {"message": {"content": []}}, "stopReason": "content_filtered"}, "m", 0.0)
    assert is_content_refused(raised.value)


async def test_an_answer_kept_back_whole():
    client = _client(lambda req: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "content_filter"}], "usage": {}}))
    with pytest.raises(LLMError) as raised:
        await client.chat([{"role": "user", "content": "x"}])
    assert is_content_refused(raised.value)


#: What a function that meets a refused request reads: a status code in any
#: spelling (``!= 200``, ``400 <=``, ``.status``), ``is_error``, an HTTP or
#: boto error class, or the boto error's dict (the review of d3ae5b2c8, N5:
#: the gate matched three literal strings).
_MEETS_A_REFUSAL = ("status_code", ".status", "is_error", "is_client_error", "HTTPError", "HTTPStatusError",
                    "ClientError", "'Error'")


def _unasked(source: str) -> list[str]:
    """The functions of a client that raise ``LLMError`` where they meet a
    refused request and never ask ``content_refusal`` (nested functions with
    the one they are in)."""
    import ast

    out = []

    def walk(body, prefix=""):
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                text = ast.unparse(node)
                raises = any(isinstance(n, ast.Raise) and "LLMError" in ast.unparse(n) for n in ast.walk(node))
                if raises and any(mark in text for mark in _MEETS_A_REFUSAL) and "content_refusal(" not in text:
                    out.append(prefix + node.name)

    walk(ast.parse(source).body)
    return out


def test_every_client_asks_whether_a_filter_refused():
    """Reads: functions of every file that defines a client class, by how they
    meet a refused request (an HTTP status, a boto error). Does not read: a
    client that meets one any other way."""
    from pathlib import Path

    import kazma_core

    root = Path(kazma_core.__file__).parent
    files = [p for p in sorted(root.rglob("*_llm.py")) + [root / "llm_provider.py"]
             if "(LLMProvider)" in p.read_text(encoding="utf-8") or p.name == "llm_provider.py"]
    assert len(files) >= 5
    # Azure's, Google's and the retry helpers hand the call to the parent or re-raise what it classed.
    declared = {"LLMProvider._retry_refused_parameters", "LLMProvider._retry_direct_after_gateway_fail"}
    found = {p.name: [f for f in _unasked(p.read_text(encoding="utf-8")) if f not in declared] for p in files}
    assert {k: v for k, v in found.items() if v} == {}


@pytest.mark.parametrize("meets", [
    "if resp.status_code >= 400:", "if resp.status_code != 200:", "if 400 <= resp.status_code:",
    "if resp.is_error:", "if r.status >= 400:", "try:\n            pass\n        except httpx.HTTPError:",
    "try:\n            pass\n        except ClientError:",
])
def test_the_gate_sees_a_client_that_does_not_ask(meets):
    planted = f"class C(LLMProvider):\n    async def chat(self):\n        {meets}\n            raise LLMError('x')\n"
    assert _unasked(planted) == ["C.chat"]
    asked = planted.replace("raise LLMError('x')", "raise content_refusal('C', 400, b) or LLMError('x')")
    assert _unasked(asked) == []
    assert _unasked(planted.replace("LLMError('x')", "ValueError('x')")) == []


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


def test_a_backup_that_refuses_a_turn_too_does_not_rest_and_the_failure_says_so(monkeypatch, tmp_path):
    from kazma_core.agent import graph_supervisor, nonstop
    from kazma_core.model_registry import get_model_registry

    from tests.test_done_frame_names_the_answering_model import _turn

    backup = "backup-that-refuses"
    real = nonstop.get_nonstop_config

    def with_failover(*args, **kwargs):
        cfg = real(*args, **kwargs)
        cfg.failover.enabled, cfg.failover.chain, cfg.failover.cooldown_seconds = True, [backup], 300.0
        return cfg

    class _Refused(_Backup):
        async def chat(self, messages=None, tools=None, *args, **kwargs):
            raise LLMError("refused", transient=False, kind="content_refused")

    monkeypatch.setattr(nonstop, "get_nonstop_config", with_failover)
    monkeypatch.setattr(graph_supervisor, "_failover_cooldowns", {})
    monkeypatch.setitem(graph_supervisor._failover_clients, backup, _Refused("api.deepseek.com"))
    monkeypatch.setattr(graph_supervisor, "_failover_built_for", get_model_registry().generation)
    done, _row = _turn(monkeypatch, tmp_path, _Refused("dashscope-intl.aliyuncs.com"), "refusal-both")
    assert graph_supervisor._failover_cooldowns == {}
    assert "did not answer either" in done["content"] and "set a backup" not in done["content"]


# ── The review of d3ae5b2c8 (N5, N7) ────────────────────────────────────


def _routed(upstream):
    import json

    return json.dumps({"error": {"code": 400, "message": "Provider returned error",
                                 "metadata": {"raw": upstream, "provider_name": "Alibaba"}}})


@pytest.mark.parametrize("raw", [
    '{"error": {"code": "data_inspection_failed", "message": "Input data may contain inappropriate content."}}',
    {"error": {"code": "data_inspection_failed", "message": "x"}},
    "Input data may contain inappropriate content.",
])
def test_a_refusal_passed_on_by_a_router_is_classed(raw):
    """N7: OpenRouter's ``metadata.raw`` held Qwen's own refusal."""
    assert _is_content_refusal(400, _routed(raw))


@pytest.mark.parametrize("raw", ['{"error": {"code": "invalid_parameter", "message": "bad"}}', "", None, 7])
def test_another_error_passed_on_is_not(raw):
    assert not _is_content_refusal(400, _routed(raw))
    nested = _routed(_routed('{"error": {"code": "data_inspection_failed"}}'))
    assert not _is_content_refusal(400, nested)  # one level down only


async def test_a_refusal_after_a_refused_field_is_classed():
    """Found by the widened gate: a request asked again without a refused
    field and refused by the filter this time ended as an ordinary error."""
    answers = iter([
        httpx.Response(400, json={"error": {"message": "temperature is not supported by this model"}}),
        httpx.Response(400, json={"error": {"code": "data_inspection_failed", "message": "Input data may contain inappropriate content."}}),
    ])
    client = _client(lambda req: next(answers), host="refused-field.example")
    with pytest.raises(LLMError) as raised:
        await client.chat([{"role": "user", "content": "x"}], temperature=0.3)
    assert is_content_refused(raised.value)
