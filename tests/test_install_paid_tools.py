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

from tests.test_crawl_auto_resume import journal, library  # noqa: F401 -- fixtures the crawl cases use

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


def _env_read_argument(node: ast.AST) -> ast.AST | None:
    """The name argument of an environment read (``os.getenv(x)``,
    ``os.environ.get(x)``, ``os.environ[x]``), or None."""
    if isinstance(node, ast.Call) and node.args:
        fn = node.func
        if ((isinstance(fn, ast.Attribute) and (fn.attr == "getenv" or (
                fn.attr == "get" and getattr(fn.value, "attr", "") == "environ")))
                or (isinstance(fn, ast.Name) and fn.id == "getenv")):
            return node.args[0]
    if isinstance(node, ast.Subscript) and getattr(node.value, "attr", "") == "environ":
        return node.slice
    return None


def _key_names(tree: ast.AST) -> set[str]:
    """The string constants of the module's tables (a tuple, list, set or
    mapping literal; ``__all__`` is no table) shaped like an API key's
    name."""
    exports = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)}
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)) or id(node) in exports:
            continue
        items = [*node.keys, *node.values] if isinstance(node, ast.Dict) else node.elts
        found |= {item.value for item in items if isinstance(item, ast.Constant)
                  and isinstance(item.value, str) and _KEY_NAME.match(item.value)
                  and item.value not in _KAZMA_OWN}
    return found


def _reads_a_key(source: str) -> bool:
    """A module reads an API key: by its literal name, or from a table -- a
    read whose name is not a literal, in a module that holds a key's name
    (``documents/extract_salvage.py`` reads ``os.environ.get(key_name)`` from
    its providers' table; the review of 3faf46175)."""
    tree = ast.parse(source)
    tabled = bool(_key_names(tree))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _PROVIDER_KEY_READERS):
            return True
        argument = _env_read_argument(node)
        if argument is None:
            continue
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            if _KEY_NAME.match(argument.value) and argument.value not in _KAZMA_OWN:
                return True
        elif tabled:
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


def test_control_a_planted_table_read_is_found() -> None:
    """A key read from a table, as ``extract_salvage`` reads its providers'
    keys: the name is not a literal at the read. The first gate saw only
    literal names, so that module passed it."""
    planted = {"kazma-core/kazma_core/documents/planted.py": (
        "import os\n"
        "PROVIDERS = (('parser', 'PLANTED_PARSE_API_KEY'),)\n"
        "def parse(path):\n"
        "    for provider, key_name in PROVIDERS:\n"
        "        key = os.environ.get(key_name, '')\n")}
    assert key_readers(planted) == {"kazma-core/kazma_core/documents/planted.py"}
    # A read from a variable in a module that names no key is not a key read.
    assert key_readers({"y.py": "import os\ndef f(name):\n    return os.environ.get(name)\n"}) == set()
    assert "kazma-core/kazma_core/documents/extract_salvage.py" in key_readers(_sources())


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


# ── the review of 3faf46175 ─────────────────────────────────────────────


def _shipped_paid_path(name: str, arguments: dict) -> bool:
    """The rule as 3faf46175 shipped it: the backend the call RESOLVES to."""
    from kazma_core.tools.image_backends.router import PAID_BACKENDS, resolve_provider

    return resolve_provider(arguments.get("provider")) in PAID_BACKENDS


@pytest.fixture
def install_default_dall_e(paid_image_keys, monkeypatch):
    monkeypatch.setenv("KAZMA_IMAGE_PROVIDER", "dall-e")


def test_an_install_default_paid_backend_draws_with_the_keyless_one(install_default_dall_e, monkeypatch) -> None:
    from kazma_core.agent.graph_tool_worker import _refuse_unrunnable_calls
    from kazma_core.tools.image_gen import generate_image

    made = _no_paid_backend(monkeypatch)
    for arguments in ({"prompt": "a poster"}, {"prompt": "a poster", "provider": "auto"}):
        call = {"id": "c1", "name": "generate_image", "arguments": arguments}
        kept, refused = _own_key_turn(lambda c=call: _refuse_unrunnable_calls([c], user_text="draw me a poster"))
        assert kept == [call] and refused == []
    _own_key_turn(lambda: generate_image("a poster"))
    assert made == ["pollinations"]
    # The paid backend the call names is still refused.
    with pytest.raises(RefusedBeforeEffect, match="daily limit"):
        _own_key_turn(lambda: generate_image("a poster", provider="dall-e"))


def test_control_the_shipped_rule_refused_every_image_of_such_a_turn(install_default_dall_e) -> None:
    assert _own_key_turn(lambda: asyncio.sleep(0, _shipped_paid_path("generate_image", {}))) is True
    assert install_paid._paid_path("generate_image", {}) is False


# A document job's remote parse, for a person past their daily limit.


@pytest.fixture
def past_her_limit(monkeypatch):
    """Spaces enforced, Reem past her daily limit by the ledger's count."""
    from kazma_core.observability import llm_ledger
    from kazma_core.spaces import scope

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(daily_limits, "_ledger_on", lambda: True)
    spent = {"reem": 10**12}
    monkeypatch.setattr(llm_ledger, "usage_of", lambda person, since: {
        "tokens": spent.get(person, 0), "cost": 0.0, "unpriced": 0})
    return spent


def _remote_parse_job(tmp_path, monkeypatch, actor):
    from kazma_core.documents import extract_salvage as salvage

    from tests.test_document_egress_hardening import _enable_remote, _job, _svc
    from tests.test_extract_salvage import _ir

    _enable_remote(monkeypatch, "LLAMAPARSE_API_KEY")
    uploaded: list[str] = []
    monkeypatch.setattr(salvage, "_llamaparse", lambda *a: uploaded.append("llamaparse") or "## Remote\n\ntext")
    svc = _svc(tmp_path / "store")
    job = _job(svc, "space-r", actor)
    path = tmp_path / "x.pdf"
    path.write_bytes(b"inert bytes")
    seen: list[Any] = []
    monkeypatch.setattr(svc, "_parse_to_manifest_impl",
                        lambda *a, **k: seen.append(salvage.try_remote_parse(path, _ir("", 0))))
    try:
        svc._parse_to_manifest(job, force_ocr=False)
        egress = [e for e in svc.audit.list_events(tenant_id="space-r")["events"] if e["event_type"] == "egress"]
    finally:
        svc.close()
    return uploaded, seen, egress


def test_a_document_job_past_its_persons_limit_parses_locally(tmp_path, monkeypatch, past_her_limit) -> None:
    uploaded, seen, egress = _remote_parse_job(tmp_path, monkeypatch, "reem")
    assert uploaded == [] and seen == [None] and egress == []  # nothing left the machine


def test_control_under_the_limit_the_remote_parser_reads_it(tmp_path, monkeypatch, past_her_limit) -> None:
    past_her_limit["reem"] = 0
    uploaded, seen, egress = _remote_parse_job(tmp_path, monkeypatch, "reem")
    assert uploaded == ["llamaparse"] and seen[0] is not None
    assert {e["action"] for e in egress} == {"remote_parse_attempt", "remote_parse_success"}


def test_control_without_the_refusal_her_document_went_to_the_paid_parser(tmp_path, monkeypatch,
                                                                         past_her_limit) -> None:
    from kazma_core.documents import ingestion

    # The stage as it was: no limit read before the remote parse.
    monkeypatch.setattr(daily_limits, "call_refusal", lambda asker=None: None)
    assert ingestion is not None
    uploaded, _seen, _egress = _remote_parse_job(tmp_path, monkeypatch, "reem")
    assert uploaded == ["llamaparse"]


# A crawl started in an own-key turn, resumed after a restart.

#: The binding the turn uses, kept from before a control replaces the resume's.
_REAL_OWN_KEYS_SCOPE = daily_limits.own_keys_scope


@pytest.mark.asyncio
@pytest.mark.usefixtures("journal", "library")
async def test_a_crawl_started_in_an_own_key_turn_resumes_under_the_rule(monkeypatch) -> None:
    seen = await _crawl_cut_off_and_resumed(monkeypatch, started_in_rule=True)
    assert seen and all(seen), seen


@pytest.mark.asyncio
@pytest.mark.usefixtures("journal", "library")
async def test_control_a_crawl_started_outside_the_rule_resumes_without_it(monkeypatch) -> None:
    seen = await _crawl_cut_off_and_resumed(monkeypatch, started_in_rule=False)
    assert seen and not any(seen), seen


@pytest.mark.asyncio
@pytest.mark.usefixtures("journal", "library")
async def test_control_the_resume_as_it_was_lost_the_rule(monkeypatch) -> None:
    import contextlib

    monkeypatch.setattr(daily_limits, "own_keys_scope", lambda on: contextlib.nullcontext())
    seen = await _crawl_cut_off_and_resumed(monkeypatch, started_in_rule=True)
    assert seen and not any(seen), seen


async def _crawl_cut_off_and_resumed(monkeypatch, *, started_in_rule: bool) -> list[bool]:
    """Start a crawl (in an own-key turn or not), cut it off by a restart,
    resume it the product's way; returns, per page fetched after the resume,
    whether the own-key rule held there."""
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.web_acquire import crawl_jobs
    from kazma_ui import kb_api

    from tests.test_crawl_auto_resume import SEED, _settled

    with _REAL_OWN_KEYS_SCOPE(started_in_rule):  # the turn the crawl starts in
        job, *_ = await ki._prepare_ingest_site("docs", SEED, job_id="j-own")
    await crawl_jobs.call(job.update, lambda row: row.update(kind="site"))
    assert bool(crawl_jobs.inspect_jobs("j-own").get("own_keys_only")) is started_in_rule
    with monkeypatch.context() as m:
        m.setattr(crawl_jobs, "_owner_gone", lambda row, now: True)
        crawl_jobs.recover()
    held: list[bool] = []
    fetch = ki._web_fetch_text

    async def fetch_and_look(url, **kwargs):
        held.append(daily_limits.own_keys_only())
        return await fetch(url, **kwargs)

    monkeypatch.setattr(ki, "_web_fetch_text", fetch_and_look)
    kb_api.create_kb_router()
    assert await kb_api.resume_interrupted_crawls() == ["j-own"]
    await _settled("j-own")
    return held
