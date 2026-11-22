"""The browser real-use suite (``scripts/memory_real_use.py``, the memory
plan's section 2.1 rule 3), without running it: it needs a real model and the
embedding model, so it is run by hand at every stage and its result kept in
``docs/audits/evidence/memory_real_use/``.

* Each language holds every kind of step the rule names: facts told, asked,
  asked in other words, changed, forgotten, never said. Negative control: a
  suite missing one is found.
* The private install's environment holds no Kazma or credential variable of
  this machine, and runs this checkout's packages.
* A step whose answer should be "I don't know" is judged as an abstention.
* A kept result holds no reply's text.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "audits" / "evidence" / "memory_real_use"


@pytest.fixture(scope="module")
def suite():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import memory_real_use
    finally:
        sys.path.pop(0)
    return memory_real_use


def test_each_language_holds_every_kind_of_step(suite, monkeypatch):
    assert suite.suite_covers_every_kind() == []
    assert {language.lang for language in suite.SUITE} == {"en", "ar"}
    # Negative control: a language without its "never said" step is found.
    cut = suite.Language("en", [s for s in suite.SUITE[0].steps if s.kind != "never_said"])
    monkeypatch.setattr(suite, "SUITE", (cut, suite.SUITE[1]))
    assert suite.suite_covers_every_kind() == ["en: never_said"]


def test_the_private_install_reads_nothing_of_this_machines_kazma(suite, monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-for-the-install")
    monkeypatch.setenv("KAZMA_DATABASE_URL", "postgresql://someone:pw@127.0.0.1/kazma")
    env = suite.server_env(tmp_path / "home", tmp_path / "data", 12345)
    assert "DEEPSEEK_API_KEY" not in env and "KAZMA_DATABASE_URL" not in env
    assert env["KAZMA_DATA_DIR"] == str(tmp_path / "data") and env["KAZMA_DB_BACKEND"] == "sqlite"
    assert env["PYTHONPATH"].split(";" if ";" in env["PYTHONPATH"] else ":")[0] == str(REPO / "kazma-core")


def test_an_answer_that_should_not_know_is_judged_as_an_abstention(suite):
    import longmemeval_bench as lme

    never = next(s for s in suite.SUITE[0].steps if s.kind == "never_said")
    prompt = lme.judge_prompt(suite.judge_item(never, 9), "I don't know your dog's name.")
    assert "unanswerable" in prompt
    asked = next(s for s in suite.SUITE[0].steps if s.kind == "asked")
    assert "unanswerable" not in lme.judge_prompt(suite.judge_item(asked, 3), "Lisbon")


def test_a_kept_result_holds_no_reply_text():
    allowed = {"made_on", "commit", "model", "reasoning", "what", "passed", "languages", "lang", "steps",
               "page_errors", "spent", "n", "kind", "action", "phase", "answered", "calls", "prompt_tokens",
               "completion_tokens", "approval_tools", "forgotten", "provider", "judge", "judge_spent",
               "prompt_cache_hit_tokens", "cached_tokens", "turn_failed"}
    for path in EVIDENCE.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        keys: set[str] = set()

        def walk(value, keys=keys):
            if isinstance(value, dict):
                keys.update(value)
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(data)
        assert keys <= allowed, (path.name, keys - allowed)


def test_another_provider_and_another_checkout(suite, monkeypatch, tmp_path):
    """Qwen's key stays out of the private install, which may run another
    checkout's code; a model at the provider's own address is saved as
    Settings saves it, then set up by the guide, which keeps the address."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-not-for-the-install")
    other = tmp_path / "before-the-change"
    env = suite.server_env(tmp_path / "home", tmp_path / "data", 12345, other)
    assert "DASHSCOPE_API_KEY" not in env
    assert env["PYTHONPATH"].split(";" if ";" in env["PYTHONPATH"] else ":")[0] == str(other / "kazma-core")

    class Browser:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        def api(self, method, path, data=None):
            self.calls.append((method, path, data))
            return {"ready": True}

    browser = Browser()
    suite.set_up_model(browser, provider="dashscope", model="qwen3.7-plus", key="k",
                       base_url="https://example.invalid/compatible-mode/v1")
    assert [(m, p) for m, p, _ in browser.calls] == [("POST", "/api/providers"), ("POST", "/api/setup/bootstrap")]
    assert browser.calls[0][2]["base_url"] == "https://example.invalid/compatible-mode/v1"
    assert browser.calls[1][2] == {"provider": "dashscope", "api_key": "", "model": "qwen3.7-plus"}
    # At the preset's own address: the guide alone, with the key.
    browser = Browser()
    suite.set_up_model(browser, provider="deepseek", model="deepseek-flash", key="k", base_url=None)
    assert browser.calls == [("POST", "/api/setup/bootstrap",
                              {"provider": "deepseek", "api_key": "k", "model": "deepseek-flash"})]


def test_a_turn_that_ended_in_kazmas_error_is_said_apart(suite):
    """Qwen's content inspection refused a harmless Arabic question (22 Nov):
    the turn ended in Kazma's own error, which is no memory miss."""
    from kazma_core.retry import friendly_llm_error

    error = friendly_llm_error(RuntimeError("LLM call failed (HTTP 400): data_inspection_failed"))
    assert suite.turn_failed(error)
    assert not suite.turn_failed("You live in Porto.") and not suite.turn_failed("")


def test_a_kept_store_is_the_memory_databases_and_never_the_vault(suite, tmp_path):
    """--keep-memory keeps what a failure is read from with no model call;
    the vault (the provider's key, saved as Settings saves it) stays behind."""
    import sqlite3

    data = tmp_path / "data"
    data.mkdir()
    for name in ("memory_state.db", "memory_ops.db", "vault.db", "settings.db"):
        conn = sqlite3.connect(str(data / name))
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (name,))
        conn.commit()
        conn.close()
    kept = suite.keep_memory_databases(data, tmp_path / "kept" / "ar")
    assert kept == ["memory_state.db", "memory_ops.db"]
    assert sorted(p.name for p in (tmp_path / "kept" / "ar").iterdir()) == ["memory_ops.db", "memory_state.db"]
    conn = sqlite3.connect(str(tmp_path / "kept" / "ar" / "memory_state.db"))
    assert conn.execute("SELECT x FROM t").fetchone() == ("memory_state.db",)
    conn.close()
