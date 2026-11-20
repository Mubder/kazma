"""A missing local restic repository is made, once, and said (2026-11-20).

Nothing in Kazma made the local repository (``<data dir>/backups/restic``).
An install restored from a migration bundle -- which carries the restic
passphrase and never the backups -- had a passphrase and no repository, so
``configured_repos`` counted the local repository and every copy into it
failed "repository does not exist": the universal backup's local snapshot,
and the Postgres dump's copy, which gave up as a failed memory task
(``native_pg_offsite``) shown under Running work (live 2026-11-19).

Now ``restic_repo.backup`` makes the install's own local repository when it
is missing, raises one alert saying a new one was made, and never makes a
remote one (a missing remote may be a wrong address).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from kazma_core.backup import restic_repo as rr

ROOT = Path(__file__).resolve().parents[1]
_PW = "test-passphrase-not-a-real-secret"

needs_restic = pytest.mark.skipif(not rr.restic_available(), reason="restic is not installed")


@pytest.fixture
def local(tmp_path, monkeypatch):
    """The install's local repository path, empty: a passphrase and no repository."""
    monkeypatch.setattr(rr, "_data_dir", lambda: tmp_path / "data")
    path = tmp_path / "data" / "backups" / "restic"
    return str(path)


@pytest.fixture
def alerts(monkeypatch):
    raised: list[str] = []

    def fake_alert(key, *_a, **_k):
        raised.append(key)

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", fake_alert)
    return raised


@pytest.fixture
def payload(tmp_path):
    folder = tmp_path / "payload"
    folder.mkdir()
    (folder / "dump.txt").write_text("pg dump stand-in", encoding="utf-8")
    return str(folder)


@needs_restic
def test_a_missing_local_repository_is_made_and_said_once(local, alerts, payload):
    first = rr.backup(local, _PW, [payload])
    assert first.ok, first.error
    assert (Path(local) / "config").is_file()
    assert alerts == ["backup.restic_local_made"]
    second = rr.backup(local, _PW, [payload])
    assert second.ok, second.error
    assert alerts == ["backup.restic_local_made"], "said once: the second copy found the repository"


@needs_restic
def test_a_missing_remote_repository_is_never_made(local, alerts, payload, tmp_path):
    elsewhere = str(tmp_path / "not-the-local-repository")
    res = rr.backup(elsewhere, _PW, [payload])
    assert not res.ok
    assert not (Path(elsewhere) / "config").exists()
    assert alerts == []


@needs_restic
def test_control_without_the_step_the_copy_fails_as_on_live(local, alerts, payload, monkeypatch):
    """The live failure, with the new step taken out."""
    monkeypatch.setattr(rr, "_local_repo_made", lambda *_a, **_k: None)
    res = rr.backup(local, _PW, [payload])
    assert not res.ok
    assert "does not exist" in (res.error or "")


def test_the_local_repository_is_recognised_by_its_place(local):
    assert rr._is_local_repo(local)
    assert not rr._is_local_repo("s3:https://example.invalid/bucket/restic")
    assert not rr._is_local_repo("rclone:remote:restic")
    assert not rr._is_local_repo("")


def test_every_restic_backup_goes_through_the_one_function():
    """Gate: the restic ``backup`` command is built only in
    ``restic_repo.backup``, so every copy -- the universal backup's and the
    Postgres dump's -- gets the missing local repository made."""
    found = []
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-cli/kazma_cli"):
        for path in (ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                        and node.elts[0].value == "backup" and len(node.elts) > 1 \
                        and isinstance(node.elts[1], ast.Constant) and node.elts[1].value == "--json":
                    found.append(path.relative_to(ROOT).as_posix())
    assert found == ["kazma-core/kazma_core/backup/restic_repo.py"], found


def test_the_gate_finds_a_planted_restic_backup(tmp_path):
    planted = 'args = ["backup", "--json", "--tag", "kazma"]\n'
    tree = ast.parse(planted)
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.List) and n.elts
            and getattr(n.elts[0], "value", None) == "backup"
            and getattr(n.elts[1], "value", None) == "--json"]
    assert hits


# ── what the alert says: restic's sentence, never its JSON ────────────────

# restic writes its error as ONE JSON line (the message's line breaks are
# JSON escapes), as the live alert carried it.
_LIVE = json.dumps({
    "message_type": "exit_error", "code": 10,
    "message": ("Fatal: repository does not exist: unable to open config file: GetFileAttributesEx "
                "C:/Users/x/kazma/kazma-data/backups/restic/config: The system cannot find the path "
                "specified." + chr(10) + "Is there a repository at the following location?" + chr(10)
                + "C:/Users/x/kazma/kazma-data/backups/restic"),
}, separators=(",", ":"))


def test_a_restic_json_error_reads_as_its_sentence():
    said = rr._meaningful_error(_LIVE, "")
    assert said.startswith("Fatal: repository does not exist")
    assert "message_type" not in said and "{" not in said
    nested = '{"message_type":"error","error":{"message":"pack 1a2b is damaged"},"during":"archival"}'
    assert rr._meaningful_error(nested, "") == "pack 1a2b is damaged"


def test_other_lines_are_kept_as_they_are():
    assert rr._meaningful_error("Fatal: wrong password or no key found", "") == \
        "Fatal: wrong password or no key found"
    assert rr._meaningful_error("{not json at all", "") == "{not json at all"


def test_control_the_raw_json_reached_the_alert(monkeypatch):
    """What the owner's phone showed on 2026-11-20."""
    monkeypatch.setattr(rr, "_restic_sentence", lambda line: line)
    assert rr._meaningful_error(_LIVE, "").startswith('{"message_type":"exit_error"')


# ── which copy of a Postgres dump failed ──────────────────────────────────


def _pg_alerts(monkeypatch, failed):
    from kazma_core.memory import worker_bootstrap

    raised = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda key, title, detail="", **kw: raised.append((key, kw.get("severity"))))
    worker_bootstrap._alert_pg_offsite(failed)
    return raised


def test_a_failed_local_copy_is_said_as_local(monkeypatch):
    """Live 2026-11-20: "Postgres offsite copy failed -- Destinations still
    missing this dump: local", while the offsite copy had succeeded."""
    assert _pg_alerts(monkeypatch, ["local"]) == [("backup.pg_local_copy", "warn")]


def test_a_failed_offsite_copy_stays_critical(monkeypatch):
    assert _pg_alerts(monkeypatch, ["remote"]) == [("backup.pg_offsite", "critical")]
    assert _pg_alerts(monkeypatch, ["local", "remote"]) == [("backup.pg_offsite", "critical")]
