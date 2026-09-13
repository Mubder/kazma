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
    out = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
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
