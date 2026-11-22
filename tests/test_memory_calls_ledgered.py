"""Memory's own model calls are in the per-call ledger, with the cache's share.

The memory plan's findings 6 and 9 (22 Nov 2026). Memory's own model calls
-- the deep pass, the weekly summaries and roll-ups, the lesson refiner, the
V1 backfill -- were in no ledger: no spend report, product measure or
person's daily limit counted them. They go through
``llm_ledger.ledgered_chat`` now: one call, and its row naming whose call it
was (the bound scope). And no row counted the prompt tokens the provider
served from its cache: every answered row reads its usage with
``token_usage.token_counts`` (DeepSeek's ``prompt_cache_hit_tokens``,
OpenAI's and Qwen's ``prompt_tokens_details.cached_tokens``, Anthropic's
``cache_read_input_tokens``) into the ``cached_tokens`` column, and every
sum gives the prompt tokens and their cached share beside each other.

From the source:

* every model call in ``kazma_core/memory/`` and ``skills/self_improvement``
  goes through ``ledgered_chat``, or is declared with why;
* every ledger row of an answered call hands its usage to the ledger
  (``usage=``); an error row has none to hand.

Through the real code: the deep pass (an English and an Arabic turn), the
weekly summaries' chat and the lesson refiner each write their row, with
prompt, completion and cached tokens and the scope's space and person; a
deep pass whose call fails writes an error row. The controls: today's
calls (the client's ``chat`` itself) write no row; a row recorded as the
supervisor recorded it (``usage.get("prompt_tokens")``) counts no cached
token and, for Anthropic's shape, no prompt token; a planted unledgered
memory call and a planted row with no usage are found.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.llm_provider import LLMError, LLMResponse
from kazma_core.observability import llm_ledger
from kazma_core.token_usage import token_counts

from tests.test_model_calls_checked import NOT_A_MODEL, model_call_sites

ROOT = Path(__file__).resolve().parents[1]

#: Where memory's own model calls live.
_MEMORY_PLACES = ("kazma-core/kazma_core/memory/", "kazma-core/kazma_core/skills/self_improvement.py")

#: Memory model calls that are not ledgered, (file, function) -> (sites, why).
NOT_LEDGERED: dict[tuple[str, str], tuple[int, str]] = {
    ("kazma-core/kazma_core/memory/entity_resolution.py", "_tier3_llm_disambiguate"): (
        1, "tier 3 is off by default, and its client.chat( here is a coroutine never awaited: no call "
           "reaches a provider (said to the main session, 22 Nov)"),
    ("kazma-core/kazma_core/memory/benchmark.py", "_CapturingClient.chat"): (
        1, "the benchmark's recorder, which wraps the client it records: not the product's calls"),
}

#: The ledger's row writers: a call of one of these records a row.
_ROW_WRITERS = frozenset({"record_llm_call", "arecord_llm_call"})
#: resilient_chat's own writer (``_record``), which forwards to arecord_llm_call.
_RESILIENT = "kazma-core/kazma_core/agent/resilient_chat.py"


def _listed(*patterns: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout.split()
    return [f for f in out if "/tests/" not in f and "_tests/" not in f]


def _memory_sites(file: str, source: str) -> list[Any]:
    return [s for s in model_call_sites(file, source)
            if (s.file, s.function) not in NOT_A_MODEL]


def unledgered(file: str, source: str) -> dict[tuple[str, str], list[Any]]:
    out: dict[tuple[str, str], list[Any]] = {}
    for site in _memory_sites(file, source):
        if not site.text.startswith("ledgered_chat("):
            out.setdefault((site.file, site.function), []).append(site)
    return out


def _memory_files() -> list[str]:
    return [f for f in _listed("kazma-core/kazma_core/*.py", "kazma-core/kazma_core/**/*.py")
            if f.startswith(_MEMORY_PLACES)]


def test_every_memory_model_call_is_ledgered() -> None:
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for file in _memory_files():
        found = unledgered(file, (ROOT / file).read_text(encoding="utf-8"))
        for key, sites in found.items():
            seen.add(key)
            if key not in NOT_LEDGERED:
                problems.extend(f"{s.file}:{s.line} {s.function}: {s.text}" for s in sites)
            elif len(sites) != NOT_LEDGERED[key][0]:
                problems.append(f"{key}: {len(sites)} unledgered call(s), declared {NOT_LEDGERED[key][0]}")
    assert not problems, (
        "A memory model call outside the per-call ledger (finding 6). Call "
        "llm_ledger.ledgered_chat(client, messages, work=...) instead of the client's chat, "
        "or declare it here with why:\n  " + "\n  ".join(problems))
    stale = sorted(set(NOT_LEDGERED) - seen)
    assert not stale, f"declared but holds no unledgered call: {stale}"


def test_the_memory_calls_are_found_ledgered() -> None:
    ledgered = set()
    for file in _memory_files():
        for site in _memory_sites(file, (ROOT / file).read_text(encoding="utf-8")):
            if site.text.startswith("ledgered_chat("):
                ledgered.add((file.rsplit("/", 1)[1], site.function))
    assert {("belief_extractor.py", "_llm_extract"),
            ("topic_summaries.py", "_default_chat._chat"),
            ("self_improvement.py", "analyze_and_apply_chat_turn"),
            ("self_improvement.py", "SelfImprovementSkill._analyze_success"),
            ("self_improvement.py", "SelfImprovementSkill._analyze_failure"),
            ("backfill_v2.py", "_llm_extract_beliefs_from_memories")} <= ledgered


_PLANTED_MEMORY = '''
async def summarize_more(client, messages):
    return await client.chat(messages)
'''


def test_control_a_planted_unledgered_memory_call_is_found() -> None:
    file = "kazma-core/kazma_core/memory/planted.py"
    assert unledgered(file, _PLANTED_MEMORY)
    fixed = _PLANTED_MEMORY.replace("await client.chat(messages)",
                                    "await ledgered_chat(client, messages, work='planted')")
    assert not unledgered(file, fixed)


# ── every answered row reads its usage (finding 9) ──────────────────────


def _row_calls(file: str, source: str) -> list[ast.Call]:
    names = set(_ROW_WRITERS) | ({"_record"} if file == _RESILIENT else set())
    calls = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if name in names:
            calls.append(node)
    return calls


def rows_without_usage(file: str, source: str) -> list[str]:
    """The row writes in *source* that hand no usage over: an answered call's
    row that would count no cached token. An error row (``status="error"``)
    has no usage; a forwarder (``**fields`` only) passes its caller's on."""
    out = []
    for call in _row_calls(file, source):
        words = {k.arg: k.value for k in call.keywords if k.arg}
        if not words:  # a forwarder: record_llm_call(**fields)
            continue
        status = words.get("status")
        if isinstance(status, ast.Constant) and status.value == "error":
            continue
        if "usage" not in words:
            out.append(f"{file}:{call.lineno} {ast.unparse(call.func)}(...)")
    return out


def test_every_answered_ledger_row_reads_its_usage() -> None:
    problems: list[str] = []
    for file in _listed("kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"):
        source = (ROOT / file).read_text(encoding="utf-8")
        if not any(w in source for w in (*_ROW_WRITERS, "_record(")):
            continue
        problems.extend(rows_without_usage(file, source))
    assert not problems, (
        "A ledger row of an answered call that hands no usage over: it counts "
        "no cached token (finding 9). Pass usage=response.usage:\n  " + "\n  ".join(problems))


def test_control_a_planted_row_with_no_usage_is_found() -> None:
    planted = '''
async def answer(client, response):
    await arecord_llm_call(model="m", prompt_tokens=response.usage.get("prompt_tokens", 0))
    await arecord_llm_call(model="m", status="error", error_kind="x")
'''
    assert rows_without_usage("kazma-core/kazma_core/agent/planted.py", planted) == [
        "kazma-core/kazma_core/agent/planted.py:3 arecord_llm_call(...)"]


# ── the shapes ───────────────────────────────────────────────────────────

_SHAPES = {
    "qwen": {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940,
             "prompt_tokens_details": {"cached_tokens": 512}},
    "deepseek": {"prompt_tokens": 900, "completion_tokens": 40, "prompt_cache_hit_tokens": 512,
                 "prompt_cache_miss_tokens": 388},
    "anthropic": {"input_tokens": 388, "output_tokens": 40, "total_tokens": 428,
                  "cache_read_input_tokens": 512},
    "responses": {"input_tokens": 900, "output_tokens": 40, "input_tokens_details": {"cached_tokens": 512}},
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_every_shape_gives_the_prompt_and_its_cached_share(shape) -> None:
    counts = token_counts(_SHAPES[shape])
    assert (counts.prompt, counts.completion, counts.cached) == (900, 40, 512)


def test_odd_usages_count_nothing_and_never_more_cached_than_prompt() -> None:
    assert tuple(token_counts(None)) == (0, 0, 0)
    assert tuple(token_counts({"prompt_tokens": True, "completion_tokens": "7"})) == (0, 0, 0)
    assert tuple(token_counts({"prompt_tokens": -3})) == (0, 0, 0)
    assert token_counts({"prompt_tokens": 10, "prompt_cache_hit_tokens": 50}).cached == 10


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    yield llm_ledger
    conn.close()


def _rows(ledger) -> list[dict[str, Any]]:
    return list(reversed(ledger.query_recent(50)))


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_a_row_and_its_sums_carry_the_cached_share(ledger, shape) -> None:
    ledger.record_llm_call(thread_id="t", turn_id="u", model="m", usage=_SHAPES[shape])
    [row] = _rows(ledger)
    assert (row["prompt_tokens"], row["completion_tokens"], row["cached_tokens"]) == (900, 40, 512)
    usage = ledger.turn_usage("u")
    assert (usage["prompt_tokens"], usage["cached_tokens"], usage["tokens"]) == (900, 512, 940)


@pytest.mark.parametrize("shape", ["anthropic", "deepseek"])
def test_control_a_row_recorded_as_the_supervisor_recorded_it(ledger, shape) -> None:
    """Today's rows: ``prompt_tokens=usage.get("prompt_tokens")``, no usage
    handed over. No cached token, and Anthropic's prompt counted as none."""
    usage = _SHAPES[shape]
    ledger.record_llm_call(thread_id="t", turn_id="u", model="m",
                           prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                           completion_tokens=int(usage.get("completion_tokens", 0) or 0))
    [row] = _rows(ledger)
    assert row["cached_tokens"] == 0
    assert row["prompt_tokens"] == (0 if shape == "anthropic" else 900)


def test_a_ledger_from_before_the_column_gains_it(tmp_path, monkeypatch) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
                "thread_id TEXT DEFAULT '', iteration INTEGER DEFAULT 0, provider TEXT DEFAULT '', "
                "model TEXT DEFAULT '', prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, "
                "total_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0, duration_ms REAL DEFAULT 0.0, "
                "status TEXT DEFAULT 'ok', error_kind TEXT DEFAULT '', failover_from TEXT DEFAULT '')")
    old.execute("INSERT INTO llm_calls (ts, thread_id, prompt_tokens, completion_tokens, total_tokens) "
                "VALUES ('2026-11-01T00:00:00+00:00', 'old', 10, 5, 15)")
    old.commit()
    old.close()
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(path))
    try:
        assert llm_ledger.thread_usage("old")["cached_tokens"] == 0
        llm_ledger.record_llm_call(thread_id="old", usage=_SHAPES["qwen"])
        assert llm_ledger.thread_usage("old")["cached_tokens"] == 512
    finally:
        conn.close()


# ── through the real code ────────────────────────────────────────────────


class _Client:
    """A provider client: answers with a Qwen-shaped usage, or raises."""

    def __init__(self, content: str, *, fail: BaseException | None = None) -> None:
        self.content, self.fail, self.calls = content, fail, 0

    async def chat(self, messages: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return LLMResponse(content=self.content, model="qwen3.7-plus", usage=dict(_SHAPES["qwen"]),
                           cost_usd=0.0, cost_known=False)


class _Registry:
    def __init__(self, client: Any) -> None:
        self.client, self.active_model = client, "qwen3.7-plus"

    def get_client(self, *args: Any, **kwargs: Any) -> Any:
        return self.client


@pytest.fixture
def model(monkeypatch):
    """The active model, scripted (no provider, no key)."""
    import kazma_core.model_registry as model_registry

    def use(client: Any) -> Any:
        monkeypatch.setattr(model_registry, "get_model_registry", lambda *a, **k: _Registry(client))
        return client

    return use


def _old_call(monkeypatch) -> None:
    """Today's calls, as they were: the client's chat itself, no row."""

    async def raw(client: Any, messages: Any, *, work: str, **kwargs: Any) -> Any:
        return await client.chat(messages, **kwargs)

    monkeypatch.setattr(llm_ledger, "ledgered_chat", raw)
    from kazma_core.skills import self_improvement

    monkeypatch.setattr(self_improvement, "ledgered_chat", raw)


_TURNS = {
    "en": ("My sister Noura works as a dentist.", "Noted: Noura is a dentist."),
    "ar": ("أختي نورة تشتغل طبيبة أسنان.", "تمام، نورة طبيبة أسنان."),
}


def _deep_pass(language: str) -> tuple[list[dict[str, Any]] | None, bool]:
    from kazma_core.memory.belief_extractor import _llm_extract
    from kazma_core.spaces.scope import bind_scope

    async def run() -> Any:
        with bind_scope("space-a", "account-a"):
            return await _llm_extract(*_TURNS[language])

    return asyncio.run(run())


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_deep_pass_writes_its_row(ledger, model, language) -> None:
    client = model(_Client('{"beliefs": []}'))
    beliefs, unavailable = _deep_pass(language)
    assert (beliefs, unavailable) == ([], False) and client.calls == 1
    [row] = _rows(ledger)
    assert row["thread_id"] == "memory:deep_pass" and row["status"] == "ok"
    assert (row["model"], row["prompt_tokens"], row["completion_tokens"], row["cached_tokens"]) == (
        "qwen3.7-plus", 900, 40, 512)
    assert (row["space"], row["asker"], row["priced"]) == ("space-a", "account-a", 0)


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_the_deep_pass_as_it_was_writes_no_row(ledger, model, monkeypatch, language) -> None:
    client = model(_Client('{"beliefs": []}'))
    _old_call(monkeypatch)
    assert _deep_pass(language) == ([], False) and client.calls == 1
    assert _rows(ledger) == []


def test_a_deep_pass_whose_call_fails_writes_an_error_row(ledger, model) -> None:
    model(_Client("", fail=LLMError("provider down", transient=True)))
    assert _deep_pass("en") == (None, True)
    [row] = _rows(ledger)
    assert (row["status"], row["error_kind"], row["prompt_tokens"]) == ("error", "LLMError", 0)


def test_the_weekly_summaries_chat_writes_its_row(ledger, model, monkeypatch) -> None:
    from kazma_core.memory.topic_summaries import _default_chat

    client = model(_Client('{"skip": true}'))
    chat, name = _default_chat()
    assert name == "qwen3.7-plus"
    reply = asyncio.run(chat([{"role": "user", "content": "summarize"}]))
    assert reply.content == '{"skip": true}' and client.calls == 1
    [row] = _rows(ledger)
    assert (row["thread_id"], row["cached_tokens"]) == ("memory:summaries", 512)
    # Control: the chat handed over as it was (the client's own) writes none.
    _old_call(monkeypatch)
    chat, _ = _default_chat()
    asyncio.run(chat([{"role": "user", "content": "summarize"}]))
    assert len(_rows(ledger)) == 1


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_lesson_refiner_writes_its_row(ledger, monkeypatch, language) -> None:
    from kazma_core.skills import self_improvement
    from kazma_core.skills.lesson_signal import LessonSignal

    client = _Client("NOTHING")
    monkeypatch.setattr(self_improvement, "self_improvement_enabled", lambda: True)
    monkeypatch.setattr(self_improvement, "_llm_for_delta", lambda: client)
    monkeypatch.setattr(self_improvement, "_held_lessons", lambda agent_id, space: [])
    monkeypatch.setattr(self_improvement.get_self_improvement(), "_enabled", True)
    message = {"en": "From now on, keep your answers short.", "ar": "من الحين ورايح، اختصر ردودك."}[language]

    def refine() -> dict[str, Any]:
        return asyncio.run(self_improvement.analyze_and_apply_chat_turn(
            user_message=message, success=True, signal=LessonSignal("feedback", message)))

    assert refine()["action"] == "skip" and client.calls == 1
    [row] = _rows(ledger)
    assert (row["thread_id"], row["cached_tokens"]) == ("memory:lessons", 512)
    _old_call(monkeypatch)
    refine()
    assert client.calls == 2 and len(_rows(ledger)) == 1
