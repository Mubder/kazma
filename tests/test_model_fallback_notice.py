"""No model fallback is silent.

Until 2026-09-25 a substituted provider or a failover model was one WARNING
line in kazma.log. The operator had configured DeepSeek and every boot built
the agent on Z.AI for nine days; the log said so and nothing else did.

Now each fallback reaches the chat platforms (``ops_alerts``, throttled per
condition) and the web banner (``AlertDispatcher.post_banner``), clears when
the configured model serves again, and a substitution that was announced says
it is resolved. See :mod:`kazma_core.observability.model_fallback`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.observability import model_fallback
from kazma_core.observability.alerts import AlertDispatcher

REPO_ROOT = Path(__file__).resolve().parents[1]
DEEPSEEK_URL = "https://api.deepseek.com/v1"
ZAI_URL = "https://api.z.ai/api/paas/v4"


@pytest.fixture
def pages(monkeypatch):
    sent: list[dict] = []
    state = {"deliver": True}

    def _alert(key, title, detail="", *, severity="warn", cooldown_s=None):
        sent.append({"key": key, "title": title, "detail": detail, "severity": severity})
        return state["deliver"]

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", _alert)
    sent_state = SimpleNamespace(sent=sent, state=state)
    return sent_state


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """DeepSeek configured without a key; Z.AI has one. No vault (plaintext)."""
    monkeypatch.setattr("kazma_core.config_store._try_get_vault", lambda: None)
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import ModelRegistry

    store = ConfigStore(
        db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"),
    )
    reg = ModelRegistry(store)
    reg.upsert_provider({
        "name": "deepseek", "base_url": DEEPSEEK_URL, "api_key": "",
        "models": ["deepseek-flash"], "enabled": True,
    })
    reg.upsert_provider({
        "name": "zai", "base_url": ZAI_URL, "api_key": "zai-key",
        "models": ["glm-5.3-flash"], "enabled": True,
    })
    reg.set_active_provider("deepseek", base_url=DEEPSEEK_URL, model="deepseek-flash")
    yield reg
    store.close()


def _banners() -> list[str]:
    return [a.subsystem for a in AlertDispatcher.get_recent_alerts()]


# -- the substitution ------------------------------------------------------


def test_a_substitution_is_announced_in_chat_and_on_the_banner(registry, pages):
    client = registry.get_client()

    assert client.config.base_url == ZAI_URL
    assert [p["key"] for p in pages.sent] == ["llm.fallback.substitution:deepseek"]
    page = pages.sent[0]
    assert "deepseek has no usable API key" in page["title"]
    assert "zai/glm-5.3-flash" in page["detail"]
    assert "deepseek/deepseek-flash" in page["detail"]
    assert "Settings -> Providers" in page["detail"]
    assert _banners() == ["llm-fallback:deepseek"]
    (banner,) = AlertDispatcher.get_recent_alerts()
    assert banner.callback_id == "link:/settings?tab=providers_connectors"
    assert banner.button_text == "Open Providers"


def test_a_banner_link_must_be_a_local_page():
    for bad in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
        payload = AlertDispatcher.post_banner(
            subsystem="t", title="t", reason="r", link=bad, link_text="Go",
        )
        assert (payload.callback_id, payload.button_text) == ("", ""), bad
    good = AlertDispatcher.post_banner(subsystem="t", title="t", reason="r", link="/settings")
    assert (good.callback_id, good.button_text) == ("link:/settings", "Open")
    assert _banners() == ["t"], "one banner per subsystem, the latest"


def test_a_lasting_fallback_is_said_once_not_per_call(registry, pages):
    for _ in range(6):
        registry.get_client()

    assert len(pages.sent) == 1
    assert _banners() == ["llm-fallback:deepseek"]
    (episode,) = model_fallback._active_fallbacks()
    assert episode["count"] == 6


def test_fixing_the_key_clears_the_banner_and_says_so(registry, pages):
    registry.get_client()
    registry.upsert_provider({"name": "deepseek", "api_key": "sk-deepseek"})

    assert registry.get_client().config.base_url == DEEPSEEK_URL
    assert _banners() == []
    assert model_fallback._active_fallbacks() == []
    resolved = pages.sent[-1]
    assert resolved["key"] == "llm.fallback.substitution:deepseek.resolved"
    assert resolved["severity"] == "info"
    assert "usable key again" in resolved["title"]


def test_resolution_is_only_said_after_the_fallback_was(registry, pages):
    """Ops alerts off (alert() returns False): no 'resolved' out of nowhere."""
    pages.state["deliver"] = False
    registry.get_client()
    registry.upsert_provider({"name": "deepseek", "api_key": "sk-deepseek"})
    registry.get_client()

    assert [p["key"] for p in pages.sent] == ["llm.fallback.substitution:deepseek"]
    assert _banners() == [], "the banner still clears"


def test_a_diagnostic_asking_is_not_a_fallback(registry, pages):
    from kazma_core.diagnostic_scope import read_only_diagnostic

    with read_only_diagnostic("doctor"):
        assert registry.get_client().config.base_url == ZAI_URL

    assert pages.sent == []
    assert _banners() == []
    assert model_fallback._active_fallbacks() == []


def test_a_healthy_build_costs_nothing(registry, pages):
    registry.upsert_provider({"name": "deepseek", "api_key": "sk-deepseek"})
    for _ in range(3):
        registry.get_client()
    assert pages.sent == []
    assert _banners() == []


# -- failover ----------------------------------------------------------------


class _Resp:
    def __init__(self, model: str) -> None:
        self.model = model
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.cost_usd = 0.0
        self.content = "ok"
        self.tool_calls = []
        self.finish_reason = "stop"


class _Down:
    config = SimpleNamespace(model="primary-model")

    async def chat(self, *a, **k):
        from kazma_core.llm_provider import LLMError

        raise LLMError("upstream 503", transient=True, kind="overloaded")


class _Up:
    def __init__(self, model: str) -> None:
        self.config = SimpleNamespace(model=model)

    async def chat(self, *a, **k):
        return _Resp(self.config.model)


@pytest.fixture
def failover_chain(monkeypatch):
    ns = SimpleNamespace(
        ledger_enabled=False,
        failover=SimpleNamespace(enabled=True, chain=["backup-model"], cooldown_seconds=60),
    )
    monkeypatch.setattr("kazma_core.agent.nonstop.get_nonstop_config", lambda: ns)
    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: SimpleNamespace(get_client=lambda model=None: _Up(model or "backup-model")),
    )
    from kazma_core.agent import graph_supervisor, resilient_chat

    for cache in (resilient_chat._clients, resilient_chat._cooldowns,
                  graph_supervisor._failover_clients, graph_supervisor._failover_cooldowns):
        cache.clear()
    yield ns
    for cache in (resilient_chat._clients, resilient_chat._cooldowns,
                  graph_supervisor._failover_clients, graph_supervisor._failover_cooldowns):
        cache.clear()


async def test_a_worker_failover_is_announced_and_clears(failover_chain, pages):
    from kazma_core.agent.resilient_chat import resilient_chat

    resp = await resilient_chat(
        _Down(), messages=[{"role": "user", "content": "hi"}],
        max_attempts=1, backoff_base=0.0,
    )
    assert resp.model == "backup-model"
    assert [p["key"] for p in pages.sent] == ["llm.fallback.failover:primary-model"]
    assert "backup-model answered" in pages.sent[0]["title"]
    assert "overloaded" in pages.sent[0]["detail"]
    assert _banners() == ["llm-failover:primary-model"]

    await resilient_chat(
        _Up("primary-model"), messages=[{"role": "user", "content": "hi"}],
        max_attempts=1, backoff_base=0.0,
    )
    assert _banners() == []
    assert len(pages.sent) == 1, "a failover ending is not paged"


async def test_a_supervisor_failover_is_announced(failover_chain, pages, monkeypatch):
    """The chat path: supervisor_node's own failover chain."""
    from kazma_core.agent.graph_supervisor import supervisor_node
    from kazma_core.agent.state import initial_supervisor_state

    monkeypatch.setattr(
        "kazma_core.retry.load_retry_config",
        lambda: {"max_attempts": 1, "min_wait": 0, "max_wait": 0},
    )

    class _Halt:
        def should_halt(self):
            return False

        def record_cost(self, *a, **k):
            pass

        def record_user_interaction(self, *a, **k):
            pass

    state = initial_supervisor_state()
    state["messages"] = [{"role": "user", "content": "hello"}]
    await supervisor_node(
        state,
        llm=_Down(),
        system_prompt="you are a test",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_Halt(),
        authority=SimpleNamespace(counter=SimpleNamespace(should_compact=lambda m: False)),
        tracer=SimpleNamespace(trace_llm_call=lambda *a, **k: None),
    )
    keys = [p["key"] for p in pages.sent]
    assert keys and keys[0].startswith("llm.fallback.failover:")
    assert "backup-model answered" in pages.sent[0]["title"]


# -- the class gate: every place that swaps the model reports it -----------

#: Matched against whole string constants, so a message split over adjacent
#: literals (the registry's is) still matches: the parser joins them.
_SWAP_SIGNATURE = re.compile(
    r"no usable API key; using|failover to '|answered after primary failure"
)
#: Cheap per-line prefilter for the file text, looser on purpose.
_MAYBE_SWAP = re.compile(r"no usable API key|failover to|answered after primary failure")
_REPORTS = {"report_substitution", "report_failover"}


def _scan(source: str, rel: str) -> tuple[int, list[str]]:
    """(swap log lines found, those whose enclosing class/function never reports)."""
    tree = ast.parse(source)
    found = 0
    out: list[str] = []
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        swaps = [
            node.lineno for node in ast.walk(top)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and _SWAP_SIGNATURE.search(node.value)
        ]
        if not swaps:
            continue
        found += len(swaps)
        calls = {
            (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
            for n in ast.walk(top) if isinstance(n, ast.Call)
        }
        if not calls & _REPORTS:
            out += [f"{rel}:{line}" for line in swaps]
    return found, out


def _unreported_swaps(source: str, rel: str) -> list[str]:
    return _scan(source, rel)[1]


def _product_files() -> list[Path]:
    return [
        p for p in REPO_ROOT.glob("kazma-*/kazma_*/**/*.py")
        if "_tests" not in p.parts[1] and "tests" not in p.parts
    ]


def test_every_model_swap_is_reported():
    offenders: list[str] = []
    seen = 0
    for path in _product_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _MAYBE_SWAP.search(text):
            continue
        found, unreported = _scan(text, path.relative_to(REPO_ROOT).as_posix())
        seen += found
        offenders += unreported
    # registry substitution + supervisor failover + resilient_chat failover
    assert seen >= 3, "the gate lost sight of the swap sites it exists for"
    assert not offenders, (
        "These log that a different model answered and tell nobody else. Call "
        "kazma_core.observability.model_fallback.report_substitution / "
        "report_failover where the swap happens:\n  " + "\n  ".join(offenders)
    )


def test_the_swap_gate_catches_a_silent_fallback():
    """Negative control (§28): the pre-2026-09-25 shape is flagged."""
    silent = (
        "def pick(self):\n"
        "    logger.warning('Profile provider=%s has no usable API key; using %s', a, b)\n"
        "    return b\n"
    )
    told = silent.replace("    return b\n", "    report_substitution(provider=a)\n    return b\n")
    assert _unreported_swaps(silent, "x.py") == ["x.py:2"]
    assert _unreported_swaps(told, "x.py") == []
