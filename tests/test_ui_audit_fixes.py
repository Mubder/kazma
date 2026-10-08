"""Behavioral browser-module checks and named native controls in both languages."""

from __future__ import annotations

import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from kazma_ui.app import create_app
from kazma_ui.i18n import TRANSLATIONS

ROOT = Path(__file__).resolve().parents[1]


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_ui_modules_protect_drafts_and_show_status_freshness():
    run = subprocess.run(
        ["node", str(ROOT / "tests/js/test_ui_audit_fixes.js")],
        capture_output=True,
        text=True,
        encoding="utf8",
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_rendered_command_catalog_is_safe_and_inside_soft_navigation_content():
    response = TestClient(create_app()).get("/chat")
    assert response.status_code == 200
    text = response.text
    match = re.search(r'<script id="kazma-command-catalog" type="application/json">(.*?)</script>', text, re.S)
    assert match is not None
    assert text.index('id="main-content"') < match.start() < text.index("</main>")
    names = {row["name"] for row in json.loads(match[1])}
    assert {"x", "model", "documents", "kb", "ide", "compact", "replay", "fork", "mission"} <= names


def test_named_controls_and_associated_labels():
    templates = ROOT / "kazma-ui/kazma_ui/templates"
    ide = (templates / "ide.html").read_text(encoding="utf8")
    assert re.search(r'<button[^>]*class="ide-tree-row"', ide)
    assert "aria-label=\"{{ t('common.send') }}\"" in ide
    assert "aria-label=\"{{ t('skills.toggle_named', name=skill.name) }}\"" in (templates / "skills.html").read_text(
        encoding="utf8"
    )
    swarm = (
        (templates / "swarm.html")
        .read_text(encoding="utf8")
        .split('<form id="dispatch-form"', 1)[1]
        .split("</form>", 1)[0]
    )
    parser = Controls()
    parser.feed("<form " + swarm)
    labels = {attrs.get("for") for tag, attrs in parser.tags if tag == "label"}
    for tag, attrs in parser.tags:
        if tag in ("input", "textarea", "select") and attrs.get("id"):
            assert attrs["id"] in labels, attrs["id"]
    documents = (templates / "documents.html").read_text(encoding="utf8")
    assert '<button type="button" class="btn btn-secondary" @click="$refs.file.click()"' in documents
    for key in (
        "documents.choose_file",
        "skills.toggle_named",
        "ide.parent_directory",
        "common.send",
        "agents.stale_status",
        "settings.connector_status_missing_token",
    ):
        assert all(TRANSLATIONS[key].get(lang) for lang in ("en", "ar"))


def test_shipped_native_capability_descriptions_cover_both_languages():
    import yaml
    from kazma_core.agent.tool_registry import LocalToolRegistry

    names = {tool["name"] for tool in LocalToolRegistry(include_builtins=True).list_tools()}
    for path in (ROOT / "kazma-skills/kazma_skills/native").rglob("skill_manifest.yaml"):
        manifest = yaml.safe_load(path.read_text(encoding="utf8"))
        key = "skill.desc." + manifest["name"]
        assert all(TRANSLATIONS.get(key, {}).get(lang) for lang in ("en", "ar")), key
        names.update((manifest.get("tools") or {}).keys())
    missing = [
        name
        for name in sorted(names)
        if not all(TRANSLATIONS.get("tool.desc." + name, {}).get(lang) for lang in ("en", "ar"))
    ]
    assert not missing, missing
