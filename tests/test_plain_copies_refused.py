"""Every plain copy that leaves the machine asks one rule first (decision N10).

Once an install enforces Spaces and holds a second account, its data holds
other people's spaces, and only restic's encrypted copy leaves the machine.
The universal backup's zip or rclone folder and the Postgres WAL archive's
segments and weekly base backups are plain copies: each path asks
``universal._plain_copy_refused`` before anything is sent.

And "is a plain copy configured" -- the Spaces switch-on check's
``encrypted_copy`` -- has one answer, ``universal.plain_copy_configured``,
which counts the WAL archive's copy too: it goes to the native provider
whatever ``backups.offsite.enabled`` says.

Controls: a WAL push with the refusal taken out sends a segment with two
accounts; the rule of before answers "no plain copy" for a provider set with
the universal copy off.

Gate: every function in product code that uploads to a provider or runs
rclone's copy asks ``_plain_copy_refused`` first, or is declared.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

ROOT = Path(__file__).resolve().parents[1]
SEGMENT = "000000010000000000000001"
_OFFSITE_KEYS = ("backups.offsite.provider", "backups.offsite.enabled", "backups.offsite.rclone_remote")


class _Provider:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def upload_file(self, local, remote):
        self.sent.append(remote)
        return {"ok": True}


@pytest.fixture
def archive(tmp_path) -> Path:
    folder = tmp_path / "wal"
    folder.mkdir()
    (folder / SEGMENT).write_text("x", encoding="utf-8")
    return folder


@pytest.fixture
def offsite():
    store = get_config_store()
    yield store
    for key in _OFFSITE_KEYS:
        store.delete(key)
    store.delete("backups.pg.wal_offsite_pushed")
    store.delete(identity.REGISTRY_KEY)


def _second_person(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    people.create_person("copy-person", "a-long-enough-password", by=OWNER_ACCOUNT_ID)


def test_one_account_the_wal_copy_goes_as_before(archive, tmp_path, offsite) -> None:
    from kazma_core.backup import wal_archive

    provider = _Provider()
    result = wal_archive._push_offsite(archive, root=tmp_path / "none", provider=provider, pushed=set())
    assert result["ok"] is True and provider.sent == [f"pgwal-{SEGMENT}"]


def test_with_a_second_person_the_wal_copy_is_refused(archive, tmp_path, offsite, monkeypatch) -> None:
    from kazma_core.backup import wal_archive

    _second_person(monkeypatch)
    provider = _Provider()
    result = wal_archive._push_offsite(archive, root=tmp_path / "none", provider=provider, pushed=set())
    assert result["refused"] == "plain_copy" and result["ok"] is False
    assert provider.sent == []


def test_control_the_wal_push_without_the_refusal_sends_a_segment(
    archive, tmp_path, offsite, monkeypatch
) -> None:
    from kazma_core.backup import universal, wal_archive

    _second_person(monkeypatch)
    monkeypatch.setattr(universal, "_plain_copy_refused", lambda: "")
    provider = _Provider()
    wal_archive._push_offsite(archive, root=tmp_path / "none", provider=provider, pushed=set())
    assert provider.sent == [f"pgwal-{SEGMENT}"]


@pytest.mark.parametrize("provider, enabled, remote, expected", [
    ("", None, "", False),
    ("webdav", None, "", True),
    ("webdav", False, "", True),     # the WAL archive's copy goes whatever `enabled` says
    ("", True, "gdrive:kazma", True),
    ("", False, "gdrive:kazma", False),
])
def test_one_answer_counts_the_wal_archives_copy(offsite, provider, enabled, remote, expected) -> None:
    from kazma_core.backup.universal import plain_copy_configured

    for key, value in zip(_OFFSITE_KEYS, (provider, enabled, remote)):
        if value is None or value == "":
            offsite.delete(key)
        else:
            offsite.set(key, value, category="backups")
    assert plain_copy_configured() is expected


def test_control_the_rule_of_before_missed_the_wal_copy(offsite) -> None:
    offsite.set("backups.offsite.provider", "webdav", category="backups")
    offsite.set("backups.offsite.enabled", False, category="backups")
    enabled = offsite.get("backups.offsite.enabled")
    before = bool(enabled) if enabled is not None else True
    assert before is False  # "no plain copy", while every segment went out


@pytest.mark.real_preconditions
def test_the_switch_on_check_names_a_provider_set_with_the_copy_off(offsite) -> None:
    from kazma_core.spaces import preconditions

    offsite.set("backups.offsite.provider", "webdav", category="backups")
    offsite.set("backups.offsite.enabled", False, category="backups")
    found = {f.id: f for f in preconditions.check()}
    assert found["encrypted_copy"].ok is False


# ── Gate: every plain upload asks the rule first ──────────────────────────

#: Functions (file, outermost function) that send files and need not ask.
DECLARED = {
    ("kazma-core/kazma_core/backup/cloud_sync.py", "*"):
        "the providers themselves: each upload is a caller's, and the callers ask",
}


def _plain_uploads(source: str) -> list[tuple[str, int, bool]]:
    """``(outermost function, line, asks)`` for every upload to a provider
    or rclone copy in *source*; *asks* when that function calls
    ``_plain_copy_refused``."""
    out: list[tuple[str, int, bool]] = []
    tree = ast.parse(source)
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        functions = [top] if not isinstance(top, ast.ClassDef) else [
            n for n in top.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function in functions:
            asks = any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", ""))
                       == "_plain_copy_refused" for n in ast.walk(function))
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                upload = isinstance(node.func, ast.Attribute) and node.func.attr == "upload_file"
                rclone = any(isinstance(arg, ast.List) and len(arg.elts) > 1
                             and all(isinstance(e, ast.Constant) for e in arg.elts[:2])
                             and [e.value for e in arg.elts[:2]] in (["rclone", "copy"], ["rclone", "sync"])
                             for arg in node.args)
                if upload or rclone:
                    out.append((function.name, node.lineno, asks))
    return out


def _problems(files: dict[str, str]) -> list[str]:
    problems = []
    for rel, source in files.items():
        if (rel, "*") in DECLARED:
            continue
        for function, line, asks in _plain_uploads(source):
            if not asks and (rel, function) not in DECLARED:
                problems.append(f"{rel}:{line} {function} sends a plain copy without asking the rule")
    return problems


def _product_files() -> dict[str, str]:
    files = {}
    for folder in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli"):
        for path in (ROOT / folder).rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "upload_file(" in text or '"rclone"' in text:
                files[path.relative_to(ROOT).as_posix()] = text
    return files


def test_every_plain_upload_asks_the_rule_first() -> None:
    files = _product_files()
    assert _problems(files) == []
    # The two paths this rule is for are found by the gate.
    found = {(rel, fn) for rel, text in files.items() for fn, _, _ in _plain_uploads(text)}
    assert ("kazma-core/kazma_core/backup/wal_archive.py", "_push_offsite") in found
    assert ("kazma-core/kazma_core/backup/universal.py", "_offsite_sync") in found


def test_gate_control_a_planted_upload_without_the_rule_fails() -> None:
    planted = {"kazma-core/kazma_core/backup/planted.py": (
        "import asyncio\n"
        "def push(provider, path):\n"
        "    return asyncio.run(provider.upload_file(path, path.name))\n"
        "def mirror(remote):\n"
        "    import subprocess\n"
        "    subprocess.run(['rclone', 'copy', 'x', remote])\n"
    )}
    problems = _problems(planted)
    assert len(problems) == 2, problems
