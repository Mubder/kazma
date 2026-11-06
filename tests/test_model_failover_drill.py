"""The failover chain is taken once a month (resilience plan F2).

The live install ran on one provider; a failover chain existed in code and
had never been taken. The drill forces the primary down for one test prompt
and checks the three things the owner depends on: the backup of another
vendor answers, the failover notice names it (held during the drill: nothing
is paged), and the primary answers again with the notice closed.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import pytest

from kazma_core import config_store
from kazma_core.llm_provider import LLMError, LLMResponse
from kazma_core.observability import failover_drill as drill
from kazma_core.observability import model_fallback

PRIMARY, BACKUP, SIBLING = "deepseek-flash", "claude-sonnet-backup", "deepseek-pro"


class _Client:
    def __init__(self, model, answer="OK", down=False):
        self.config = type("C", (), {"model": model})()
        self.answer, self.down, self.calls = answer, down, 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.down:
            raise LLMError(f"{self.config.model} is down", transient=True)
        return LLMResponse(content=self.answer, model=self.config.model)


class _Registry:
    PROVIDERS = {
        PRIMARY: {"name": "deepseek", "base_url": "https://api.deepseek.com/v1"},
        SIBLING: {"name": "deepseek-2", "base_url": "https://api.deepseek.com/beta"},
        BACKUP: {"name": "anthropic", "base_url": "https://api.anthropic.com"},
    }

    def __init__(self, **clients):
        self.clients = {PRIMARY: _Client(PRIMARY), BACKUP: _Client(BACKUP), SIBLING: _Client(SIBLING)}
        self.clients.update(clients)

    def get_active_profile(self):
        return {"model": PRIMARY}

    def find_provider_for_model(self, model):
        return self.PROVIDERS.get(model)

    def get_client(self, model=None):
        return self.clients[model]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No page leaves a test, and every test starts with no open failover."""
    import kazma_core.observability.ops_alerts as ops

    pages = []
    monkeypatch.setattr(ops, "alert", lambda key, title, detail, **kw: pages.append(key) or True)
    model_fallback.reset_fallback_state()
    yield pages
    model_fallback.reset_fallback_state()


def _drill(registry, chain=(BACKUP,)):
    return asyncio.run(drill.run_drill(registry=registry, chain=list(chain)))


def test_the_drill_passes_when_the_backup_answers_and_the_primary_comes_back(_quiet):
    registry = _Registry()
    result = _drill(registry)
    assert result.ok and result.step == "passed", result
    assert (result.primary, result.backup) == (PRIMARY, BACKUP)
    assert registry.clients[BACKUP].calls == 1
    assert registry.clients[PRIMARY].calls == 1, "the switch back asked the real primary once"
    assert _quiet == [], "a drill pages nobody"
    assert not model_fallback.failover_open(PRIMARY)


def test_negative_control_outside_a_drill_the_same_failover_pages(_quiet):
    """The held notices are what keep the drill quiet: the same report, made
    outside it, reaches the owner."""
    model_fallback.report_failover(model=PRIMARY, used_model=BACKUP, reason="drill")
    assert _quiet == [f"llm.fallback.failover:{PRIMARY}"]


def test_no_backup_configured_is_the_result():
    result = _drill(_Registry(), chain=())
    assert not result.ok and result.step == "not_configured"
    assert "agent.nonstop.failover.chain" in result.detail


def test_a_backup_of_the_same_vendor_is_no_backup():
    result = _drill(_Registry(), chain=(SIBLING,))
    assert not result.ok and result.step == "same_vendor"
    assert "api.deepseek.com" in result.detail


def test_a_backup_that_does_not_answer_fails_the_failover_step():
    result = _drill(_Registry(**{BACKUP: _Client(BACKUP, down=True)}))
    assert not result.ok and result.step == "failover"


def test_a_backup_that_answers_something_else_fails_the_failover_step():
    result = _drill(_Registry(**{BACKUP: _Client(BACKUP, answer="I cannot help with that.")}))
    assert result.step == "failover" and "not OK" in result.detail


def test_a_failover_with_no_notice_fails_the_notice_step(monkeypatch):
    monkeypatch.setattr(model_fallback, "report_failover", lambda **kw: None)
    result = _drill(_Registry())
    assert not result.ok and result.step == "notice"


def test_a_primary_still_down_fails_the_switch_back_step():
    result = _drill(_Registry(**{PRIMARY: _Client(PRIMARY, down=True)}))
    assert not result.ok and result.step == "switch_back"


def test_a_notice_that_stays_open_fails_the_switch_back_step(monkeypatch):
    monkeypatch.setattr(model_fallback, "report_model_served", lambda model: None)
    result = _drill(_Registry())
    assert result.step == "switch_back" and "stayed open" in result.detail


# ── the monthly sweep ─────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    yield settings
    settings.close()


def test_the_sweep_runs_monthly_keeps_the_result_and_reports_a_failure(store, _quiet, caplog):
    runs = []

    def run():
        runs.append(1)
        return drill.DrillResult(False, "not_configured", "No backup model is configured.", at=1000.0)

    with caplog.at_level("WARNING", logger="kazma_core.observability.failover_drill"):
        assert drill.drill_sweep(run=run, now=1000.0) == "not_configured"
    assert store.get(drill.RESULT_KEY)["step"] == "not_configured"
    assert "[failover-drill] FAIL:" in caplog.text
    assert _quiet == ["llm.failover_drill_failed"]
    assert drill.drill_sweep(run=run, now=1000.0 + drill.EVERY_S - 1) == "not due"
    assert drill.drill_sweep(run=run, now=1000.0 + drill.EVERY_S) == "not_configured"
    assert len(runs) == 2


def test_a_passing_drill_is_logged_for_the_weekly_report(store, _quiet, caplog):
    passed = drill.DrillResult(True, "passed", "fine", primary=PRIMARY, backup=BACKUP)
    with caplog.at_level("INFO", logger="kazma_core.observability.failover_drill"):
        drill.drill_sweep(run=lambda: passed, now=5.0)
    assert "[failover-drill] PASS:" in caplog.text and _quiet == []


def test_the_weekly_report_counts_the_drill():
    import re

    from kazma_core.observability.firing_ledger import FIRING_SIGNATURES

    (sig,) = [s for s in FIRING_SIGNATURES if s.mechanism == "backup-model drill"]
    assert re.search(sig.pattern, "[failover-drill] PASS: fine (3.1 s)")
    assert re.search(sig.pattern, "[failover-drill] FAIL: none (step not_configured)")


def test_the_drill_runs_in_a_process_of_its_own(tmp_path):
    """A real process on an empty install: no chain, one JSON line, exit 1.
    It runs from an empty folder, so no real .env is read."""
    import os

    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ)
    env.update(KAZMA_DATA_DIR=str(tmp_path / "data"), KAZMA_USER_HOME=str(tmp_path / "home"),
               KAZMA_DATABASE_URL="", KAZMA_DB_BACKEND="sqlite")
    env.pop("KAZMA_ENV_FILE", None)
    done = subprocess.run([sys.executable, "-m", "kazma_core.observability.failover_drill", "--json"],
                          capture_output=True, text=True, timeout=180, env=env, check=False, cwd=str(work))
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    assert lines, done.stderr[-2000:]
    assert done.returncode == 1
    assert json.loads(lines[-1])["step"] == "not_configured"
