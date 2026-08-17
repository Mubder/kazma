"""settings.js is a composer; tab mixins must assemble a complete factory."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_JS = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "static" / "js"
_TPL = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"

_FILES = [
    "settings_core.js",
    "settings_hub.js",
    "settings_agent.js",
    "settings_integrations.js",
    "settings_ops.js",
    "settings.js",
]


def _compose_factory() -> dict:
    files = [str((_JS / name).resolve()) for name in _FILES]
    script = r"""
const fs = require('fs');
const vm = require('vm');
const files = JSON.parse(process.argv[1]);
const ctx = { console, setTimeout, fetch: async () => null };
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
for (const f of files) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx);
if (typeof ctx.settingsApp !== 'function') process.exit(2);
const app = ctx.settingsApp();
const need = ['init', '_fetch', '_loadSecondarySettings', 'onTabChange', 'loadHubProviders',
  'saveAgent', 'loadMcpServers', 'saveVoiceSettings', 'archiveBackup', 'restartServer'];
const missing = need.filter((k) => typeof app[k] !== 'function');
process.stdout.write(JSON.stringify({
  tab: app.tab,
  missing,
  keys: Object.keys(app).length,
}));
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(files)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_settings_factory_composes_all_mixins() -> None:
    info = _compose_factory()
    assert info["tab"] == "providers_connectors"
    assert info["missing"] == []
    assert info["keys"] > 80


def test_settings_html_loads_mixins_before_composer() -> None:
    html = _TPL.read_text(encoding="utf-8")
    order = [
        "settings_core.js",
        "settings_hub.js",
        "settings_agent.js",
        "settings_integrations.js",
        "settings_ops.js",
        "settings.js",
    ]
    idxs = [html.index(name) for name in order]
    assert idxs == sorted(idxs)
    assert "window.settingsApp" in (_JS / "settings.js").read_text(encoding="utf-8")


def test_settings_js_is_no_longer_a_god_file() -> None:
    text = (_JS / "settings.js").read_text(encoding="utf-8")
    assert text.count("\n") < 40
    assert "KazmaSettingsMixins" in text
    assert "self.loading = false" in text or "loading = false" in (
        _JS / "settings.js"
    ).read_text(encoding="utf-8")


def test_settings_init_does_not_await_voice_before_clearing_loading() -> None:
    core = (_JS / "settings_core.js").read_text(encoding="utf-8")
    # First paint must not wait on STT/TTS provider lists.
    init = core.split("async init()")[1].split("async restartServer")[0]
    assert "loadVoiceModels" not in init
    assert "_loadSecondarySettings" in init
    assert "self.loading = false" in init
