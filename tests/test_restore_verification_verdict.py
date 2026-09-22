"""Recovery evidence must distinguish missing verification from corruption."""

from __future__ import annotations

import pytest

from kazma_core.backup import restore_drill as rd


def test_magic_header_cannot_certify_archive_when_verifier_is_missing(tmp_path, monkeypatch):
    from kazma_core.migration import pg_bridge

    def unavailable():
        raise RuntimeError("PostgreSQL client tools not installed")

    monkeypatch.setattr(pg_bridge, "resolve_pg_restore", unavailable)
    dump = tmp_path / "invalid.dump"
    dump.write_bytes(b"PGDMPgarbage")
    result = rd.DrillResult(str(tmp_path))
    rd._check_pg_dump(dump, result)
    rd._check_pg_data_section(dump, result)
    assert result.verdict == "UNVERIFIED"
    assert not result.ok
    assert not result.failures
    assert {c["check"] for c in result.unverified} == {"postgres:toc", "postgres:data"}
    assert "1/3 checks passed" in result.summary()


@pytest.mark.parametrize(
    "checks,verdict,exit_code",
    [([True], "PASS", 0), ([None], "UNVERIFIED", 2),
     ([False], "FAIL", 1), ([None, False], "FAIL", 1)],
)
def test_cli_and_summary_follow_evidence(checks, verdict, exit_code, monkeypatch, capsys):
    result = rd.DrillResult("audit-fixture")
    for index, outcome in enumerate(checks):
        result.add(f"check:{index}", outcome, "controlled evidence")
    monkeypatch.setattr(rd, "run_drill", lambda backup: result)
    assert rd.main([]) == exit_code
    assert result.verdict == verdict
    assert f"{verdict}:" in capsys.readouterr().out


@pytest.mark.parametrize("outcome,severity,key", [
    (None, "warning", "backup.restore_drill_unverified"),
    (False, "critical", "backup.restore_drill_failed"),
])
def test_operator_alert_distinguishes_incomplete_from_failed(outcome, severity, key, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "kazma_core.observability.ops_alerts.alert",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    result = rd.DrillResult("audit-fixture")
    result.add("postgres:data", outcome, "required verification did not complete")
    rd._alert_failure(result)
    args, kwargs = sent.pop()
    assert args[0] == key
    assert kwargs["severity"] == severity
    assert "postgres:data" in args[2]


def test_offsite_without_readback_is_unverified(tmp_path, monkeypatch):
    import json

    (tmp_path / "manifest.json").write_text(
        json.dumps({"offsite": {"ok": True, "size": 100}}), encoding="utf-8",
    )
    monkeypatch.setattr("kazma_core.backup.cloud_sync.get_sync_provider", lambda: object())
    result = rd.DrillResult(str(tmp_path))
    rd._check_offsite_object(tmp_path, result)
    assert result.verdict == "UNVERIFIED"
    assert not result.ok
