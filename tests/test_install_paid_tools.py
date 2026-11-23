"""A turn on a person's own key keeps off APIs the install pays for outside
the model client (Spaces S2-04, the review of de6874f3f).

A turn let in past its person's daily limit runs on their own model keys
(``daily_limits.own_keys_only``), and ``LLMProvider._person_call`` refuses
a model call on the install's key. A tool that pays another API with the
install's key was never asked there: past her limit, on her own DeepSeek
key, "draw me 20 posters" went to the install's image API. Each such path
is kept off (``spaces/install_paid.py``):

* ``generate_image``: a paid backend asked by name is refused before its
  card (the graph's preflight, in the person's language) and at the call;
  ``auto`` resolves to the keyless backend. Control: outside such a turn,
  ``auto`` picks the paid backend whose key is set.
* the acquisition policy's remote providers (Tavily, Jina, Firecrawl, the
  proxy) and E2B answer no. Control: outside such a turn they answer yes.
* **Gate**: every tool-reachable module that reads an API key from the
  environment is declared in ``PAID_KEY_READERS`` with how an own-key turn
  keeps off what it pays; a planted module is the control, and a declared
  module that reads no key any more fails as stale.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.spaces import daily_limits, install_paid
from kazma_core.spaces.install_paid import INSTALL_PAID_TOOLS, PAID_KEY_READERS, own_key_refusal

REPO = Path(__file__).resolve().parents[1]

#: Where a tool's code lives: the tools, the agent's built-in tools, the
#: skills, and what they reach for web, code, speech and X.
ROOTS = (
    "kazma-core/kazma_core/tools", "kazma-core/kazma_core/agent/tool_builtins", "kazma-skills/kazma_skills",
    "kazma-core/kazma_core/web_acquire", "kazma-core/kazma_core/sandbox", "kazma-core/kazma_core/voice",
    "kazma-core/kazma_core/x_api", "kazma-core/kazma_core/proxy", "kazma-core/kazma_core/documents",
    "kazma-core/kazma_core/memory/embedder.py",
)
_KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]*(API_KEY|_KEY|_TOKEN|_SECRET)$")
#: Kazma's own secrets, never a paid API's.
_KAZMA_OWN = frozenset({"KAZMA_SECRET", "KAZMA_VAULT_KEY"})
#: A provider's key read from the settings store by a module of its own.
_PROVIDER_KEY_READERS = ("_get_provider_api_key_from_db",)


def _reads_a_key(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        name = None
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _PROVIDER_KEY_READERS:
                return True
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and (
                    (isinstance(fn, ast.Attribute) and (fn.attr == "getenv" or (
                        fn.attr == "get" and getattr(fn.value, "attr", "") == "environ")))
                    or (isinstance(fn, ast.Name) and fn.id == "getenv")):
                name = node.args[0].value
        elif (isinstance(node, ast.Subscript) and getattr(node.value, "attr", "") == "environ"
              and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
            name = node.slice.value
        if name and _KEY_NAME.match(name) and name not in _KAZMA_OWN:
            return True
    return False


def key_readers(sources: dict[str, str]) -> set[str]:
    return {rel for rel, text in sources.items() if _reads_a_key(text)}


def _sources() -> dict[str, str]:
    found: dict[str, str] = {}
    for root in ROOTS:
        path = REPO / root
        for file in [path] if path.is_file() else sorted(path.rglob("*.py")):
            found[file.relative_to(REPO).as_posix()] = file.read_text(encoding="utf-8")
    return found


def test_every_module_that_reads_a_key_is_declared() -> None:
    readers = key_readers(_sources())
    assert readers - set(PAID_KEY_READERS) == set(), (
        "A module a tool reaches reads an API key: declare it in install_paid.PAID_KEY_READERS with how a "
        "turn on the person's own key keeps off what it pays")
    assert set(PAID_KEY_READERS) - readers == set(), "declared, but reads no key any more"


def test_control_a_planted_tool_that_reads_a_paid_key_is_found() -> None:
    planted = {"kazma-core/kazma_core/tools/planted.py": (
        "import os, httpx\n"
        "async def planted_tool(prompt):\n"
        "    key = os.environ.get('PLANTED_API_KEY')\n"
        "    return httpx.post('https://api.planted.invalid', headers={'Authorization': key})\n")}
    assert key_readers(planted) == {"kazma-core/kazma_core/tools/planted.py"}
    assert key_readers({"x.py": "import os\nos.environ.get('KAZMA_SECRET')\n"}) == set()


def test_every_install_paid_tool_is_asked_before_its_card() -> None:
    from kazma_core.safety.call_preflight import PREFLIGHT_TOOLS

    assert set(INSTALL_PAID_TOOLS) <= PREFLIGHT_TOOLS


# ── in a turn on the person's own key ───────────────────────────────────


def _own_key_turn(work: Callable[[], Awaitable[Any]]) -> Any:
    """*work* run in a turn let in on the person's own key (the rule bound
    as the graph's nodes bind it)."""
    async def run() -> Any:
        daily_limits.hold_to_own_keys({"own_keys_only": True})
        return await work()

    return asyncio.run(run())


@pytest.fixture
def paid_image_keys(monkeypatch):
    for name in ("OPENAI_API_KEY", "STABILITY_API_KEY", "FAL_KEY"):
        monkeypatch.setenv(name, f"install-{name.lower()}")
    monkeypatch.delenv("KAZMA_IMAGE_PROVIDER", raising=False)


@pytest.mark.parametrize("provider", ["dall-e", "stability", "flux"])
def test_a_paid_image_backend_is_refused_before_its_card(paid_image_keys, provider) -> None:
    from kazma_core.agent.graph_tool_worker import _refuse_unrunnable_calls

    call = {"id": "c1", "name": "generate_image", "arguments": {"prompt": "a poster", "provider": provider}}
    kept, refused = _own_key_turn(lambda: _refuse_unrunnable_calls([call], user_text="draw me a poster"))
    assert kept == [] and len(refused) == 1
    assert "daily limit" in refused[0]["content"] and "no approval was asked" in refused[0]["content"]
    arabic = _own_key_turn(lambda: _refuse_unrunnable_calls([call], user_text="ارسم لي ملصقًا"))[1]
    assert "بلغت حدك اليومي" in arabic[0]["content"]


def test_control_outside_such_a_turn_the_call_goes_on_to_its_card(paid_image_keys) -> None:
    from kazma_core.agent.graph_tool_worker import _refuse_unrunnable_calls

    call = {"id": "c1", "name": "generate_image", "arguments": {"prompt": "a poster", "provider": "dall-e"}}
    kept, refused = asyncio.run(_refuse_unrunnable_calls([call], user_text="draw me a poster"))
    assert kept == [call] and refused == []


def _no_paid_backend(monkeypatch) -> list[str]:
    from kazma_core.tools.image_backends import dall_e, flux, pollinations, stability

    made: list[str] = []
    for module, cls in ((dall_e, "DallEBackend"), (stability, "StabilityBackend"), (flux, "FluxBackend")):
        async def paid(self, *_a: Any, **_k: Any) -> bytes:
            pytest.fail(f"{type(self).__name__} was called with the install's key")

        monkeypatch.setattr(getattr(module, cls), "generate", paid)

    async def keyless(self, *_a: Any, **_k: Any) -> bytes:
        made.append("pollinations")
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    monkeypatch.setattr(pollinations.PollinationsBackend, "generate", keyless)
    return made


def test_at_the_call_a_paid_backend_is_refused_and_auto_is_the_keyless_one(paid_image_keys, monkeypatch) -> None:
    from kazma_core.tools.image_gen import generate_image

    made = _no_paid_backend(monkeypatch)
    with pytest.raises(RefusedBeforeEffect, match="daily limit"):
        _own_key_turn(lambda: generate_image("a poster", provider="dall-e"))
    _own_key_turn(lambda: generate_image("a poster", provider="auto"))
    assert made == ["pollinations"]


def test_control_outside_such_a_turn_auto_picks_the_paid_backend(paid_image_keys) -> None:
    from kazma_core.tools.image_backends.router import detect_available_provider, resolve_provider

    assert detect_available_provider() == "dall-e" and resolve_provider("auto") == "dall-e"
    assert _own_key_turn(lambda: asyncio.sleep(0, resolve_provider("auto"))) == "pollinations"


def test_the_refusal_is_none_outside_such_a_turn_and_for_the_keyless_backend(paid_image_keys) -> None:
    assert own_key_refusal("generate_image", {"provider": "dall-e"}) is None
    assert _own_key_turn(lambda: asyncio.sleep(0, own_key_refusal("generate_image", {"provider": "pollinations"}))) is None
    both = _own_key_turn(lambda: asyncio.sleep(0, own_key_refusal("generate_image", {"provider": "flux"})))
    assert "daily limit" in both and "بلغت حدك اليومي" in both  # no message to read: both languages


def test_remote_acquisition_providers_answer_no(monkeypatch) -> None:
    from kazma_core.web_acquire.policy import AcquisitionPolicy

    policy = AcquisitionPolicy(mode="local_first_with_remote_fallback",
                               providers=frozenset({"tavily", "jina", "firecrawl", "proxy"}))
    remote = ("tavily", "jina", "firecrawl", "proxy")
    assert all(policy.permits(p) for p in remote)  # control: outside such a turn
    assert _own_key_turn(lambda: asyncio.sleep(0, [policy.permits(p) for p in remote])) == [False] * 4


def test_e2b_answers_no(monkeypatch) -> None:
    from kazma_core.safety import deployment_policy
    from kazma_core.sandbox import e2b

    monkeypatch.setenv("E2B_API_KEY", "install-e2b-key")
    monkeypatch.delenv("KAZMA_E2B", raising=False)
    monkeypatch.setattr(deployment_policy, "container_required", lambda: False)
    assert e2b.e2b_enabled() is True  # control: outside such a turn
    assert _own_key_turn(lambda: asyncio.sleep(0, e2b.e2b_enabled())) is False


def test_every_install_paid_tool_has_a_refusal_case() -> None:
    """A new row of INSTALL_PAID_TOOLS needs its own paid path judged
    (``install_paid._paid_path``) and a case here."""
    assert set(INSTALL_PAID_TOOLS) == {"generate_image"}
    assert install_paid._paid_path("generate_image", {"provider": "dall-e"}) is True
