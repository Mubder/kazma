"""Spaces S2-03: models per person -- which they may use, and their default.

Y (person A) may use only ``openrouter:model-b``; G (person B) has the
install's list (every model it offers); the install's default is
``deepseek:deepseek-flash``.

* **The picker** (``/api/models/options``) lists only Y's model, with it as
  Y's default; G and the admin see every model.
* **/model** cannot choose a model Y may not use: it is "not offered".
* **A job**: a real swarm worker of Y's with a model Y may not use calls
  nothing (the provider's ``chat`` refuses before any request); one with no
  model runs on Y's (``ModelRegistry.get_client``), never the install's.
* **The call** is the truth: every provider class's ``chat`` and
  ``chat_stream`` asks ``_refuse_a_model_not_allowed`` first (a source gate,
  a planted provider class as its control), so whatever chose the model, Y's
  call of it never leaves.

Control: the install's list in place of Y's (``person_models`` asked with
no rule for Y) offers and calls ``model-c`` for Y.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest
from kazma_core.model_registry import ModelRegistry
from kazma_core.spaces import identity, people, person_models, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_matrix import Matrix

REPO = Path(__file__).resolve().parents[1]
OFFERED = {"deepseek": ["deepseek-flash"], "openrouter": ["model-b", "model-c"]}
EVERY = sorted(f"{p}:{m}" for p, ms in OFFERED.items() for m in ms)
INSTALL_DEFAULT = "deepseek:deepseek-flash"


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


def _provider(ref: str, calls: list[str]):
    """A real client of the provider class *ref*'s provider speaks, with its
    request stubbed: *calls* records each model it was asked for."""
    from kazma_core.llm_provider import LLMConfig, LLMResponse
    from kazma_core.provider_adapters import build_client

    name, _, model = ref.partition(":")
    client = build_client(name, LLMConfig(base_url="https://models.invalid/v1", api_key="k", model=model))

    async def request(*_a: Any, **kwargs: Any) -> Any:
        calls.append(f"{name}:{kwargs.get('model') or model}")
        return LLMResponse(content="done", tool_calls=[], finish_reason="stop", model=model,
                           usage={"total_tokens": 2}, cost_usd=0.0)

    client._chat_inner = request
    return client


class _Registry(ModelRegistry):
    """The real ``ModelRegistry.get_client`` over two stand-in providers:
    ``built`` records every model a client was built for."""

    def __init__(self) -> None:  # noqa: D107 -- no stores: the rows are OFFERED
        self.built: list[str | None] = []
        self.calls: list[str] = []

    def list_providers(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in OFFERED]

    def find_provider_for_model(self, model_id: str) -> dict[str, Any] | None:
        return next(({"name": n} for n, ms in OFFERED.items() if model_id in ms), None)

    def get_active_profile(self) -> dict[str, str]:
        provider, _, model = INSTALL_DEFAULT.partition(":")
        return {"provider": provider, "model": model}

    def _build_client(self, model: str | None) -> tuple[Any, tuple[str, ...]]:
        self.built.append(model)
        ref = model or INSTALL_DEFAULT
        if ":" not in ref:
            ref = f"{self.find_provider_for_model(ref)['name']}:{ref}"
        return _provider(ref, self.calls), ("unusable",)

    def get_client_by_provider(self, provider_name: str, model: str | None = None) -> Any:
        return _provider(f"{provider_name}:{model or OFFERED[provider_name][0]}", self.calls)


def _listed() -> dict[str, Any]:
    return {"default": INSTALL_DEFAULT, "options": [
        {"ref": ref, "provider": ref.split(":")[0], "provider_label": ref.split(":")[0],
         "model": ref.split(":", 1)[1], "local": False} for ref in EVERY]}


@pytest.fixture
def y_with_one_model(monkeypatch):
    import kazma_core.model_registry as model_registry
    import kazma_core.models.options as options

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(scope, "_enforced", True)
    registry = _Registry()
    monkeypatch.setattr(model_registry, "get_model_registry", lambda: registry)
    monkeypatch.setattr(options, "model_options", lambda registry, details=True: _listed())
    with Matrix() as matrix:
        people.set_models(matrix.account_a, ["openrouter:model-b"], "", by=OWNER_ACCOUNT_ID)
        matrix.registry = registry
        yield matrix


def _install_list(monkeypatch) -> None:
    """The control: the install's list in place of the person's."""
    monkeypatch.setattr(person_models, "_models_here", lambda person=None: None)


# ── the picker ──────────────────────────────────────────────────────────


def _refs(listed: dict[str, Any]) -> list[str]:
    return [o["ref"] for o in listed["options"]]


def test_the_picker_lists_only_the_persons_models(y_with_one_model) -> None:
    m = y_with_one_model
    theirs = person_models.offered(_listed(), person=m.account_a)
    assert _refs(theirs) == ["openrouter:model-b"] and theirs["default"] == "openrouter:model-b"
    assert _refs(person_models.offered(_listed(), person=m.account_b)) == EVERY
    everyone = person_models.offered(_listed(), person=OWNER_ACCOUNT_ID)
    assert _refs(everyone) == EVERY and everyone["default"] == INSTALL_DEFAULT


def test_the_picker_route_answers_for_the_signed_in_person(y_with_one_model) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_ui import space_binding
    from kazma_ui.providers import create_providers_router

    m = y_with_one_model
    app = FastAPI()
    app.include_router(create_providers_router(get_config_store()))
    seen: dict[str, Any] = {}
    with pytest.MonkeyPatch.context() as patch, TestClient(app) as client:
        for account in (m.account_a, m.account_b):
            patch.setattr(space_binding, "account_of", lambda principal, a=account: a)
            seen[account] = client.get("/api/models/options").json()
    assert _refs(seen[m.account_a]) == ["openrouter:model-b"]
    assert _refs(seen[m.account_b]) == EVERY


def test_control_the_install_list_offers_every_model(y_with_one_model, monkeypatch) -> None:
    _install_list(monkeypatch)
    assert _refs(person_models.offered(_listed(), person=y_with_one_model.account_a)) == EVERY


# ── the chat ────────────────────────────────────────────────────────────


def test_the_chat_refuses_a_model_the_person_names_before_anything_runs(y_with_one_model) -> None:
    """The product's chat stream behind the real tenant middleware: Y names
    model-c; the answer says so, nothing is stored and no model is called."""
    from tests._chat_space_cases import world

    with world(y_with_one_model) as w:
        mine = w.chat("a", "model")
        before = len(w.stored("a", mine).messages)
        sent = w.call("a", "POST", "/api/chat/stream",
                      json={"session_id": mine.session_id, "message": "hello", "model": "openrouter:model-c"})
        assert sent.status_code == 200 and "is not one you may use" in sent.text, sent.text
        assert len(w.stored("a", mine).messages) == before, "the refused message was stored"
    assert y_with_one_model.registry.calls == []


# ── /model ──────────────────────────────────────────────────────────────


class _Manager:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def send(self, outbound: Any) -> None:
        self.said.append(str(outbound.text))


class _Store:
    async def get(self, _thread_id: str) -> dict[str, Any]:
        return {}


def _model_command(m: Matrix, text: str, monkeypatch) -> tuple[list[str], list[str]]:
    """``/model ...`` sent in Y's chat: what was said, and the models kept."""
    import kazma_core.chat_model_choice as chat_model_choice
    from kazma_gateway.agent_handler.commands import _try_model_command
    from kazma_gateway.gateway import IncomingMessage

    kept: list[str] = []
    monkeypatch.setattr(chat_model_choice, "set_chat_model",
                        lambda thread_id, ref, store=None: kept.append(ref))
    manager = _Manager()
    msg = IncomingMessage(platform="slack", sender_id="slack:u1", text=text,
                          context_metadata={"channel": "c1"})
    with m.as_a():
        asyncio.run(_try_model_command(msg, _Store(), manager, "t-model-command"))
    return manager.said, kept


def test_model_command_cannot_choose_a_model_the_person_may_not_use(y_with_one_model, monkeypatch) -> None:
    said, kept = _model_command(y_with_one_model, "/model openrouter:model-c", monkeypatch)
    assert kept == [] and "not offered" in said[-1], said
    said, kept = _model_command(y_with_one_model, "/model openrouter:model-b", monkeypatch)
    assert kept == ["openrouter:model-b"], said


def test_control_model_command_with_the_install_list(y_with_one_model, monkeypatch) -> None:
    _install_list(monkeypatch)
    _said, kept = _model_command(y_with_one_model, "/model openrouter:model-c", monkeypatch)
    assert kept == ["openrouter:model-c"]


# ── the call, and a job ─────────────────────────────────────────────────


def _ask(client: Any, model: str | None = None, *, stream: bool = False) -> str:
    async def run() -> str:
        if stream:
            async for _delta in client.chat_stream([{"role": "user", "content": "hi"}], model=model):
                pass
            return "streamed"
        return (await client.chat([{"role": "user", "content": "hi"}], model=model)).content

    return asyncio.run(run())


def test_the_call_refuses_a_model_the_person_may_not_use(y_with_one_model) -> None:
    from kazma_core.llm_provider import LLMError

    m = y_with_one_model
    calls: list[str] = []
    client = _provider("openrouter:model-c", calls)
    for stream in (False, True):
        with m.as_a(), pytest.raises(LLMError) as refused:
            _ask(client, stream=stream)
        assert refused.value.kind == "model_not_allowed" and not refused.value.transient
    assert calls == [], "a model Y may not use reached its provider"
    with m.as_a():
        assert _ask(client, "model-b") == "done"
    with m.as_b():
        assert _ask(client) == "done"
    assert _ask(client) == "done"  # Kazma's own work: no person, no rule
    assert calls == ["openrouter:model-b", "openrouter:model-c", "openrouter:model-c"]


def test_a_reference_the_caller_passes_on_is_judged_as_it_is(y_with_one_model) -> None:
    from kazma_core.llm_provider import LLMError

    calls: list[str] = []
    client = _provider("openrouter:model-b", calls)
    with y_with_one_model.as_a(), pytest.raises(LLMError):
        _ask(client, "openrouter:model-c")
    assert calls == []


def test_a_job_with_a_model_the_person_may_not_use_calls_nothing(y_with_one_model) -> None:
    """A real in-process swarm worker of Y's, set to model-c."""
    from kazma_core.swarm.worker import InProcessWorker

    m = y_with_one_model
    worker = InProcessWorker(name="probe", role="tester", provider="openrouter", model="model-c")
    with m.as_a():
        result = asyncio.run(worker.dispatch("sum the column"))
    assert m.registry.calls == [], "the job called a model Y may not use"
    assert result.get("status") != "success" and "not one this person may use" in str(result), result
    with m.as_b():
        asyncio.run(InProcessWorker(name="probe", role="tester", provider="openrouter",
                                    model="model-c").dispatch("sum the column"))
    assert m.registry.calls == ["openrouter:model-c"]


def test_work_that_names_no_model_runs_on_the_persons(y_with_one_model, monkeypatch) -> None:
    import kazma_core.models.selection as selection
    from kazma_core.swarm.worker import InProcessWorker

    m = y_with_one_model
    monkeypatch.setattr(selection, "select_provider_for_task", lambda registry, prompt: None)
    with m.as_a():
        asyncio.run(InProcessWorker(name="probe", role="tester").dispatch("sum the column"))
    assert m.registry.built[-1] == "openrouter:model-b" and m.registry.calls == ["openrouter:model-b"]
    with m.as_b():
        m.registry.get_client()
    assert m.registry.built[-1] is None, "G's work keeps the install's default"
    people.set_models(m.account_a, ["openrouter:model-b", "openrouter:model-c"], "openrouter:model-c",
                      by=OWNER_ACCOUNT_ID)
    with m.as_a():
        m.registry.get_client()
    assert m.registry.built[-1] == "openrouter:model-c", "Y's own default"


def test_control_the_install_list_calls_the_model(y_with_one_model, monkeypatch) -> None:
    _install_list(monkeypatch)
    calls: list[str] = []
    with y_with_one_model.as_a():
        assert _ask(_provider("openrouter:model-c", calls)) == "done"
    assert calls == ["openrouter:model-c"]


def test_without_spaces_nothing_is_read_or_refused(y_with_one_model, monkeypatch) -> None:
    m = y_with_one_model
    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr(identity, "read_registry", lambda: pytest.fail("a read without Spaces"))
    calls: list[str] = []
    with m.as_a():
        assert _ask(_provider("openrouter:model-c", calls)) == "done"
        m.registry.get_client()
    assert m.registry.built[-1] is None
    assert person_models.offered(_listed(), person=m.account_a) == _listed()


# ── every provider class asks ───────────────────────────────────────────

_ASK = "_refuse_a_model_not_allowed"


def _provider_classes(sources: dict[str, str]) -> list[str]:
    """``file:Class.method`` for each ``chat``/``chat_stream`` of a provider
    class (``LLMProvider`` and every class deriving from it) that neither
    asks the refusal first nor hands the call to ``super()``."""
    missing: list[str] = []
    for rel, text in sources.items():
        tree = ast.parse(text)
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            bases = {b.id for b in cls.bases if isinstance(b, ast.Name)}
            if cls.name != "LLMProvider" and "LLMProvider" not in bases:
                continue
            for fn in cls.body:
                if not isinstance(fn, ast.AsyncFunctionDef) or fn.name not in ("chat", "chat_stream"):
                    continue
                calls = {getattr(c.func, "attr", "") for c in ast.walk(fn) if isinstance(c, ast.Call)}
                delegates = any(
                    isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == fn.name and isinstance(c.func.value, ast.Call)
                    and getattr(c.func.value.func, "id", "") == "super"
                    for c in ast.walk(fn))
                if _ASK not in calls and not delegates:
                    missing.append(f"{rel}:{cls.name}.{fn.name}")
    return sorted(missing)


def _product_provider_sources() -> dict[str, str]:
    root = REPO / "kazma-core" / "kazma_core"
    found = {}
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "LLMProvider" in text and ("async def chat" in text):
            found[str(path.relative_to(REPO))] = text
    return found


def test_every_provider_class_asks_before_it_calls() -> None:
    sources = _product_provider_sources()
    assert any("anthropic_llm.py" in r for r in sources) and any("bedrock_llm.py" in r for r in sources)
    assert _provider_classes(sources) == []


def test_control_a_provider_class_that_does_not_ask_is_found() -> None:
    planted = {"planted.py": (
        "class LLMProvider:\n    pass\n"
        "class Planted(LLMProvider):\n"
        "    async def chat(self, messages, model=None):\n"
        "        return await self._send(messages, model)\n"
    )}
    assert _provider_classes(planted) == ["planted.py:Planted.chat"]


# ── People ──────────────────────────────────────────────────────────────


def test_people_lists_records_and_refuses(y_with_one_model) -> None:
    m = y_with_one_model
    listed = {p["id"]: p["models"] for p in people.list_people()}
    assert listed[m.account_a] == {"allowed": ["openrouter:model-b"], "default": "", "admin": False}
    assert listed[m.account_b]["allowed"] is None and listed[OWNER_ACCOUNT_ID]["admin"]
    assert identity.read_registry()["events"][-1]["kind"] == "models_set"
    for allowed, default, code in ((["openrouter:model-b"], "openrouter:model-c", "default_not_allowed"),
                                   ([], "", "no_models"), ("openrouter:model-b", "", "bad_models")):
        with pytest.raises(people.PeopleRefused) as refused:
            people.set_models(m.account_a, allowed, default, by=OWNER_ACCOUNT_ID)
        assert refused.value.code == code and refused.value.status == 400, (allowed, refused.value.code)
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_models(OWNER_ACCOUNT_ID, ["openrouter:model-b"], "", by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "admin_has_every_model"
    people.set_models(m.account_a, None, "", by=OWNER_ACCOUNT_ID)
    assert {p["id"]: p["models"] for p in people.list_people()}[m.account_a]["allowed"] is None


def test_the_route_sets_a_persons_models(y_with_one_model) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    m = y_with_one_model
    app = FastAPI()
    app.include_router(create_people_router())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal",
                      lambda request: {"source": "secret", "actor": "owner", "role": "admin"})
        with TestClient(app) as client:
            got = client.put(f"/api/people/{m.account_a}/models",
                             json={"allowed": ["openrouter:model-c"], "default": "openrouter:model-c"})
            bad = client.put(f"/api/people/{m.account_a}/models", json={"allowed": [], "default": ""})
    assert got.status_code == 200 and got.json()["default"] == "openrouter:model-c", got.text
    assert bad.status_code == 400 and bad.json()["error"] == "no_models", bad.text
