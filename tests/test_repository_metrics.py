"""Fresh metrics use the official static collector, not old files or test imports."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from kazma_core.tools import repository_metrics as metrics


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[1] / "scripts/generate_metrics.py", root / "scripts/generate_metrics.py")
    (root / "kazma-core").mkdir()
    (root / "kazma-core/app.py").write_text("def current():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_example.py").write_text("raise AssertionError('must never import tests')\ndef test_one(): pass\n", encoding="utf-8")
    (root / "METRICS.md").write_text("Outdated: 999999 files and tests", encoding="utf-8")
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture"], check=True, capture_output=True)
    monkeypatch.setattr(metrics, "_source_root", lambda: root)
    return root


async def test_fresh_static_counts_ignore_stale_snapshot_and_do_not_import_tests(source):
    first = json.loads(await metrics.repository_metrics())
    assert first["ok"] and first["python"]["files"] == 3
    assert first["test_functions"] == 1
    assert first["collected_tests"] is None
    assert first["tracked_changes"] is False
    assert first["upstream"] is None
    assert first["installation_history_commits"] == 1
    assert "Never substitute test_functions" in first["claim_rules"]
    (source / "kazma-core/app.py").write_text("def current():\n    return 1\n\ndef another():\n    return 2\n", encoding="utf-8")
    second = json.loads(await metrics.repository_metrics())
    assert second["python"]["total"] == first["python"]["total"] + 3
    assert second["tracked_changes"] is True
    assert second["commit"] == first["commit"]
    assert (source / "METRICS.md").read_text(encoding="utf-8") == "Outdated: 999999 files and tests"


async def test_installation_merges_are_not_reported_as_upstream_commits(source):
    subprocess.run(["git", "-C", str(source), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "installation only"], check=True, capture_output=True)
    result = json.loads(await metrics.repository_metrics())
    assert result["upstream"]["commits"] == 1
    assert result["installation_history_commits"] == 2


@pytest.mark.parametrize("damage", [None, "source", "fixture", "invalid", "partial"])
async def test_collected_cases_require_matching_receipt_without_importing_tests(source, damage):
    spec = importlib.util.spec_from_file_location("fixture_generator", source / "scripts/generate_metrics.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    fixture = source / "tests/example.txt"
    fixture.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    receipt = generator.collection_receipt({"tests": {"collected": 13}, "commit": {"sha": "fixture"}}, generator.collection_fingerprint())
    target = source / generator.COLLECTION_RECEIPT
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(receipt), encoding="utf-8")
    if damage == "source":
        (source / "kazma-core/app.py").write_text("changed", encoding="utf-8")
    elif damage == "fixture":
        fixture.write_text("changed", encoding="utf-8")
    elif damage == "invalid":
        target.write_text("broken json", encoding="utf-8")
    elif damage == "partial":
        receipt["collected_tests"] = 0
        target.write_text(json.dumps(receipt), encoding="utf-8")
    else:
        # Checkout line endings and merge-only history must not invalidate the receipt.
        file = source / "kazma-core/app.py"
        file.write_bytes(file.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "merge history"], check=True, capture_output=True)
    result = json.loads(await metrics.repository_metrics())
    assert result["ok"]
    assert result["collected_tests"] == (13 if damage is None else None)
    assert (result["collection_receipt_status"] == "verified_inputs") == (damage is None)


async def test_changed_source_during_measurement_refuses_claims(source, monkeypatch):
    original = metrics._stamp
    calls = 0

    def stamp(root):
        nonlocal calls
        calls += 1
        measured = original(root)
        return measured if calls == 1 else ("different-head", measured[1])

    monkeypatch.setattr(metrics, "_stamp", stamp)
    result = json.loads(await metrics.repository_metrics())
    assert not result["ok"]
    assert "changed during measurement" in result["error"]
    assert "python" not in result


async def test_wheel_without_source_never_quotes_cached_file(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "_source_root", lambda: tmp_path)
    (tmp_path / "METRICS.md").write_text("old count", encoding="utf-8")
    assert not json.loads(await metrics.repository_metrics())["ok"]


def test_metrics_tool_is_registered_and_read_only():
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import get_tool_tier

    names = {tool["name"] for tool in LocalToolRegistry().list_tools()}
    assert "repository_metrics" in names
    assert get_tool_tier("repository_metrics") == "read"
