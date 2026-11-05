"""Run the provider UI's data layer and check what it decides.

Everything else about `providers.js` is checked by reading its source, which
proves a string is present and nothing about what it does. The one rule worth
executing is the one the whole capability schema exists for: an unverified
capability must not render as "no". `null` and `false` mean different things,
and a badge that shows both as a cross puts a measurement's authority behind a
guess.

Skipped where node is unavailable. The source-level checks in
`test_provider_conformance.py` still run everywhere.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui" / "kazma_ui" / "static" / "js" / "providers.js"
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _run(script: str) -> object:
    """Load providers.js into a bare global and evaluate *script*."""
    harness = (
        "globalThis.window = globalThis;\n"
        f"const src = require('fs').readFileSync({json.dumps(str(_JS))}, 'utf8');\n"
        "eval(src);\n"
        "const M = globalThis.ProvidersManager;\n"
        f"console.log(JSON.stringify((() => {{ {script} }})()));\n"
    )
    # node writes UTF-8 whatever the console code page; decode it as such
    # (text=True alone read Arabic labels as cp1252 on Windows).
    out = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _badges(supports: dict) -> dict:
    provider = {"capabilities": {"api_style": "openai", "supports": supports}}
    rows = _run(
        f"return M.capabilityBadges({json.dumps(provider)});"
    )
    return {row["key"]: row["state"] for row in rows}


class TestUnverifiedIsNotNo:
    def test_null_renders_as_unknown(self):
        states = _badges({"tools": None, "streaming": None, "json_mode": None, "vision": None})
        assert states["tools"] == "unknown"
        assert states["streaming"] == "unknown"

    def test_false_renders_as_no(self):
        states = _badges({"tools": False, "streaming": None, "json_mode": None, "vision": None})
        assert states["tools"] == "no"

    def test_true_renders_as_yes(self):
        states = _badges({"tools": True, "streaming": None, "json_mode": None, "vision": None})
        assert states["tools"] == "yes"

    def test_a_provider_with_no_capabilities_gets_no_badges(self):
        """Better an empty row than four badges claiming four unknowns about a
        provider the backend said nothing about."""
        assert _run("return M.capabilityBadges({});") == []


class TestCardState:
    def test_a_fresh_test_result_wins_over_stored_health(self):
        assert _run(
            "return M.cardState({health: 'healthy'}, {success: false, reachable: true, chat_ok: false});"
        ) == "chat_failing"

    def test_stored_health_carries_the_state_across_a_reload(self):
        assert _run("return M.cardState({health: 'chat_failing'}, null);") == "chat_failing"
        assert _run("return M.cardState({health: 'healthy'}, null);") == "working"
        assert _run("return M.cardState({health: 'down'}, null);") == "unreachable"

    def test_never_tested_is_its_own_state(self):
        """Not 'unreachable'. Nobody has looked, and saying otherwise invents a
        failure."""
        assert _run("return M.cardState({}, null);") == "untested"


class TestSummaryAndLabels:
    """Live 2026-09-28: the summary counted a switched-off Ollama as "Chat
    failing" and a switched-off ASR endpoint as "Unreachable", and the row
    pills stayed English on the Arabic page."""

    def test_disabled_providers_are_not_counted(self):
        counts = _run(
            "return M.stateCounts(["
            "{enabled: true, health: 'healthy'},"
            "{enabled: false, health: 'chat_failing'},"
            "{enabled: false, health: 'down'},"
            "{enabled: true}]);"
        )
        assert counts == {"working": 1, "chat_failing": 0, "unreachable": 0, "untested": 1}

    def test_a_provider_without_the_flag_counts(self):
        """Negative control: only an explicit enabled=false is left out."""
        assert _run("return M.stateCounts([{health: 'down'}]);")["unreachable"] == 1

    def test_labels_follow_the_page_language(self):
        label = _run(
            "window.t = (k) => ({'settings.state_working': 'يعمل'})[k] || k;"
            "return M.stateLabel('working');"
        )
        assert label == "يعمل"

    def test_labels_fall_back_to_english(self):
        assert _run("return M.stateLabel('chat_failing');") == "Chat failing"
        assert _run("return M.stateLabel('models_ok_chat_failing');") == "models ok · chat failing"
        assert _run("return M.stateLabel('no-such-state');") == "Not tested"


class TestStoredChecks:
    """Live 2026-11-05: the Providers list said "Chat failing" for Z.AI while
    its detail panel said both checks were "not run yet" and showed no alert:
    the panel read only a result from this page visit, the row the stored
    health the last check wrote."""

    def _states(self, result: str, health: str) -> dict:
        rows = _run(f"return M.checksFor({result}, {health});")
        return {row["key"]: (row["state"], row["detail"]) for row in rows}

    def test_stored_chat_failing_shows_the_failed_chat_check(self):
        states = self._states("null", "'chat_failing'")
        assert states == {"models": ("ok", "last check"), "chat": ("fail", "last check")}

    def test_stored_healthy_shows_both_checks_passed(self):
        assert self._states("null", "'healthy'") == {
            "models": ("ok", "last check"), "chat": ("ok", "last check"),
        }

    def test_stored_down_fails_the_model_list_and_leaves_chat_unknown(self):
        for health in ("'down'", "'degraded'"):
            assert self._states("null", health) == {
                "models": ("fail", "last check"), "chat": ("idle", ""),
            }

    def test_never_checked_stays_not_run(self):
        assert self._states("null", "undefined") == {
            "models": ("idle", ""), "chat": ("idle", ""),
        }

    def test_a_fresh_result_wins_over_stored_health(self):
        states = self._states(
            "{success: true, reachable: true, latency_ms: 12, chat_ms: 30}", "'chat_failing'"
        )
        assert states["models"][0] == "ok" and states["chat"][0] == "ok"
        assert "last check" not in states["chat"][1]

    def test_the_old_call_without_health_is_the_negative_control(self):
        """What the hub passed before: the result only. Stored chat_failing then
        reads as two checks never run, the panel the owner saw."""
        assert self._states("null", "undefined") == {
            "models": ("idle", ""), "chat": ("idle", ""),
        }
        assert _run("return M.cardState({health: 'chat_failing'}, null);") == "chat_failing"

    def test_the_stored_alert_line_says_it_is_the_last_checks(self):
        assert _run("return M.storedDetail({health: 'chat_failing'});").startswith(
            "Saved from the last check"
        )
        assert _run("return M.storedDetail({});") == ""

    def test_the_hub_hands_the_panel_the_stored_health(self):
        hub = (_JS.parent / "settings_hub.js").read_text(encoding="utf-8")
        assert "ProvidersManager.checksFor(p && p._test, p && p.health)" in hub
        assert "ProvidersManager.storedDetail(p)" in hub
        template = (
            _JS.parent.parent.parent / "templates" / "settings.html"
        ).read_text(encoding="utf-8")
        assert "x-show=\"providerState(selectedHubProvider()) !== 'untested'\"" in template
        assert 'x-show="selectedHubProvider()._test"' not in template
