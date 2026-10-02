"""The security report asks the questions the product asks, through one answer each.

A report with its own copy of a rule measures the copy: the old skills
check looked for a manifest name the loader never reads. Each reader the
report uses is the one the product path uses; these tests pin that.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

# ── built-in skills: the report and the loader resolve a skill one way ──


def _native_skill(root: Path, name: str, manifest: str, tools: str | None) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "skill_manifest.yaml").write_text(manifest, encoding="utf-8")
    if tools is not None:
        (folder / "tools.py").write_text(tools, encoding="utf-8")
        (folder / "__init__.py").write_text("", encoding="utf-8")


def test_a_skill_problem_is_what_the_loader_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from kazma_skills import native_loader

    pkg = tmp_path / "pkgroot" / "kazma_skills" / "native"
    _native_skill(pkg, "good_skill", "name: good\ntools:\n  hello:\n    description: says hello\n",
                  "def hello():\n    return 'hi'\n")
    _native_skill(pkg, "half_skill", "name: half\ntools:\n  present: {}\n  missing: {}\n",
                  "def present():\n    return 1\n")
    _native_skill(pkg, "prompt_skill", "name: prompt\ndescription: instructions only\n", None)
    _native_skill(pkg, "bad_yaml", "- just\n- a list\n", None)
    (pkg / "_private").mkdir()

    import kazma_skills.native as native_pkg

    monkeypatch.setattr(native_pkg, "__path__", [str(pkg), *native_pkg.__path__])
    for name in ("good_skill", "half_skill"):
        sys.modules.pop(f"kazma_skills.native.{name}", None)
        sys.modules.pop(f"kazma_skills.native.{name}.tools", None)

    registered: list[str] = []

    class Registry:
        _tools: dict[str, object] = {}

        def register_function(self, *, name: str, **_: object) -> None:
            registered.append(name)

    loader = native_loader.NativeSkillLoader(Registry())
    monkeypatch.setattr(loader, "native_dir", pkg)

    assert [d.name for d in loader.skill_dirs()] == ["bad_yaml", "good_skill", "half_skill", "prompt_skill"]
    problems = loader.problems()
    assert sorted(problems) == ["bad_yaml", "half_skill"]
    assert problems["half_skill"] == ["tool 'missing' has no function in its tools module"]
    assert "not a mapping" in problems["bad_yaml"][0]

    loader.register_all()
    assert sorted(registered) == ["hello", "present"]  # what the report calls a problem is what is skipped


# ── agent skills: the report and activation decide integrity one way ────


def test_activation_asks_the_same_integrity_question(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from kazma_core.agent_skills import catalog
    from kazma_core.agent_skills.discovery import AgentSkill
    from kazma_core.agent_skills.integrity import VerifyResult
    from kazma_core.agent_skills.parser import ParsedSkill

    skill = AgentSkill(
        name="notes", description="d", location=tmp_path / "SKILL.md", scope="user",
        parsed=ParsedSkill(name="notes", description="d", body="Do things."),
    )
    monkeypatch.setattr(catalog, "activation_integrity",
                        lambda s, warn=True: VerifyResult(ok=False, reason="checksum mismatch"))
    assert "Refusing to load the skill body" in catalog.format_skill_activation(skill)


def test_the_report_does_not_log_each_unsigned_skill(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from kazma_core.agent_skills.catalog import activation_integrity
    from kazma_core.agent_skills.discovery import discover_skills

    folder = tmp_path / ".agents" / "skills" / "notes-helper"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: notes-helper\ndescription: d\n---\nbody\n", encoding="utf-8")
    skill = discover_skills(project_root=tmp_path)["notes-helper"]

    with caplog.at_level(logging.WARNING, logger="kazma_core.agent_skills.integrity"):
        assert activation_integrity(skill, warn=False).signed is False
    assert not [r for r in caplog.records if "no integrity checksum" in r.getMessage()]
    with caplog.at_level(logging.WARNING, logger="kazma_core.agent_skills.integrity"):
        activation_integrity(skill)  # activation still says so
    assert [r for r in caplog.records if "no integrity checksum" in r.getMessage()]


def test_skill_files_lists_what_discovery_reads(tmp_path: Path) -> None:
    from kazma_core.agent_skills.discovery import discover_skills, skill_files

    for name, text in (("ok-one", "---\nname: ok-one\ndescription: d\n---\n"), ("broken", "no frontmatter")):
        folder = tmp_path / ".agents" / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(text, encoding="utf-8")

    listed = {p.parent.name for scope, p in skill_files(project_root=tmp_path) if scope == "project"}
    assert listed == {"ok-one", "broken"}
    assert "ok-one" in discover_skills(project_root=tmp_path)
    assert "broken" not in discover_skills(project_root=tmp_path)


# ── MCP: the report and the vault move ask one question ────────────────


def test_the_report_lists_what_the_move_empties(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core import mcp_servers_store as store

    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text(
        "mcp:\n  servers:\n  - name: notes\n    command: [npx, notes]\n    env:\n      NOTES_TOKEN: abc123def456ghi789\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "_cs_get", lambda: [])
    assert store.servers_with_plaintext_secrets(yaml_path=yaml_path) == ["notes"]

    monkeypatch.setattr("kazma_core.config_store._try_get_vault", lambda: None)
    assert store.move_plaintext_secrets(yaml_path=yaml_path) == 0  # no vault: nothing moves...
    assert store.servers_with_plaintext_secrets(yaml_path=yaml_path) == ["notes"]  # ...and the report says so


@pytest.mark.parametrize(("value", "on"), [(None, True), ("1", True), ("0", False), ("off", False), ("False", False)])
def test_the_scope_guard_switch_has_one_reading(monkeypatch: pytest.MonkeyPatch, value: str | None, on: bool) -> None:
    from kazma_core.mcp.manager import mcp_scope_guard_enabled

    if value is None:
        monkeypatch.delenv("KAZMA_MCP_SCOPE_GUARD", raising=False)
    else:
        monkeypatch.setenv("KAZMA_MCP_SCOPE_GUARD", value)
    assert mcp_scope_guard_enabled() is on


def test_the_router_reads_the_switch_through_that_function() -> None:
    import inspect

    from kazma_core.mcp import manager

    source = inspect.getsource(manager.AsyncMCPManager._route_workspace_scope)
    assert "mcp_scope_guard_enabled()" in source
    # The switch is read in one place in the product (its messages may name it).
    reads = [
        path for path in Path(manager.__file__).resolve().parents[1].rglob("*.py")
        if 'environ.get("KAZMA_MCP_SCOPE_GUARD"' in path.read_text(encoding="utf-8")
    ]
    assert [p.name for p in reads] == ["manager.py"]
    assert inspect.getsource(manager).count('environ.get("KAZMA_MCP_SCOPE_GUARD"') == 1


# ── approvals ───────────────────────────────────────────────────────────


def test_only_answered_gates_count_as_recorded_decisions(tmp_path: Path) -> None:
    from kazma_core.safety import hitl_gates

    hitl_gates.set_db_path_for_tests(str(tmp_path / "gates.db"))
    try:
        assert hitl_gates.recorded_decision_count() == 0
        hitl_gates.register_gate(hitl_gates.GateRow(gate_id="a", thread_id="t", tool="x", created_at=1.0))
        hitl_gates.register_gate(hitl_gates.GateRow(gate_id="b", thread_id="t", tool="y", created_at=2.0))
        assert hitl_gates.recorded_decision_count() == 0  # asked, not answered
        hitl_gates.claim_gate("a", "denied", "owner")
        assert hitl_gates.recorded_decision_count() == 1
    finally:
        hitl_gates.set_db_path_for_tests(None)
