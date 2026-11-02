"""A migration carries settings, never standing approvals or sessions (FA-255).

The 2026-11 functional audit moved an install whose chat had an "Allow tool"
grant and a signed-in browser. On the target the importer ran
``import_yaml`` over the bundle's ``config.yaml``, flattening every stored
row into one row per leaf (``hitl_grant.<thread>.<tool>.expires_at``),
on top of the whole rows a Postgres dump had already restored. The grant
answered ``has_tool_grant`` although ``list_grants`` showed nothing, and a
revoke left it working (``get`` rebuilds a missing row from its children);
a signed-out session signed in again once its tombstone passed.

The bundle now carries the settings rows whole (``settings_rows.json``) and
the importer writes them by ``settings_restore``'s classes: settings and the
install's own credentials travel, runtime state and browser sessions do not.
The old step (``import_yaml`` of ``config.yaml``) is the negative control.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

THREAD = "migrate-grant-thread"


def _run(code: str, data_dir: Path, cwd: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAZMA_")}
    env.update({"KAZMA_DB_BACKEND": "sqlite", "KAZMA_VAULT_KEY": "test-only-not-a-secret",
                "KAZMA_DATA_DIR": str(data_dir), "KAZMA_SECRET": "s" * 48})
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


_SOURCE = """
    import json
    from kazma_core.config_store import get_config_store
    from kazma_core.safety import hitl_grants
    from kazma_core.security import web_sessions
    cs = get_config_store()
    cs.set("agent.language", "ar", category="agent")
    hitl_grants.grant_tool({thread!r}, "shell_exec", actor="owner", ttl_seconds=3600)
    sid = web_sessions.create_session(role="admin", actor="web")
    from kazma_core.migration.exporter import export_bundle
    out = export_bundle({bundle!r}, include_assets=False)
    print(json.dumps({{"sid": sid, "granted": hitl_grants.has_tool_grant({thread!r}, "shell_exec")}}))
"""

_TARGET = """
    import json, zipfile
    from kazma_core.config_store import get_config_store
    {old_step}
    from kazma_core.migration.importer import import_bundle
    r = import_bundle({bundle!r}, target_workspace_root={ws!r})
    {old_import}
    from kazma_core.safety import hitl_grants
    from kazma_core.security import web_sessions
    from kazma_core.settings_restore import RUNTIME, classify
    cs = get_config_store()
    granted_after_import = hitl_grants.has_tool_grant({thread!r}, "shell_exec")
    hitl_grants.clear_grants({thread!r}, actor="owner")
    web_sessions.revoke_session({sid!r})
    web_sessions._cache_evict(web_sessions._hash({sid!r}))
    keys = [k for values in cs.get_all().values() for k in values]
    print(json.dumps({{
        "ok": r.ok, "errors": r.errors,
        "language": cs.get("agent.language"),
        "granted_after_import": granted_after_import,
        "granted_after_revoke": hitl_grants.has_tool_grant({thread!r}, "shell_exec"),
        "session": web_sessions.get_session_payload({sid!r}) is not None,
        "runtime_keys": sorted(k for k in keys if classify(k) == RUNTIME and k.startswith(("hitl_grant", "path_grant"))),
        "session_keys": sorted(k for k in keys if k.startswith("web_session.")),
    }}))
"""


def _migrate(tmp_path: Path, *, old: bool = False) -> dict:
    src, dst = tmp_path / "src" / "kazma-data", tmp_path / "dst" / "kazma-data"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    bundle = tmp_path / "bundle.zip"
    source = _run(_SOURCE.format(thread=THREAD, bundle=str(bundle)), src, tmp_path)
    assert source["granted"]
    old_import = ""
    if old:
        # The old step 7: the bundle's config.yaml flattened into leaf rows.
        old_import = (
            "get_config_store().import_yaml(zipfile.ZipFile("
            f"{str(bundle)!r}).read('config.yaml').decode('utf-8'))"
        )
    return _run(_TARGET.format(bundle=str(bundle), ws=str(tmp_path / "ws"), thread=THREAD,
                               sid=source["sid"], old_step="", old_import=old_import), dst, tmp_path)


def test_a_migration_carries_the_settings_and_no_grant_or_session(tmp_path):
    out = _migrate(tmp_path)
    assert out["ok"], out["errors"]
    assert out["language"] == "ar"
    assert out["granted_after_import"] is False  # a standing approval stays behind
    assert out["granted_after_revoke"] is False
    assert out["session"] is False  # a browser's sign-in stays behind
    assert out["runtime_keys"] == []
    assert out["session_keys"] == []


def test_the_old_config_import_kept_a_revoked_grant_working(tmp_path):
    """Negative control: ``import_yaml`` of config.yaml left the grant in
    leaf rows, which a revoke did not reach, and the session's leaves too."""
    out = _migrate(tmp_path, old=True)
    assert out["granted_after_revoke"] is True
    assert any(k.startswith(f"hitl_grant.{THREAD}.shell_exec.") for k in out["runtime_keys"])
    assert out["session_keys"]


def test_a_bundle_with_a_postgres_dump_writes_no_settings_over_it(tmp_path, monkeypatch):
    """The dump restored the settings as whole rows; nothing is written over them."""
    from kazma_core.migration import importer

    written: list = []

    class _Store:
        def batch_set(self, items):
            written.extend(items)
            return len(items)

    class _Bundle:
        def has_file(self, name):
            return name == "data/postgres.dump"

    (tmp_path / "settings_rows.json").write_text(json.dumps([{"key": "agent.language", "value": "ar"}]),
                                                  encoding="utf-8")
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    importer._restore_settings(tmp_path, _Bundle(), importer.ImportReport(), lambda m: None)
    assert written == []


@pytest.mark.parametrize("key,carried", [
    ("agent.language", True),                      # a setting
    ("self_improvement.agent_evolution", True),    # learned
    ("account.owner", True),                       # the install's own identity
    ("hitl_grant.t.shell_exec", False),            # a standing approval
    ("path_grant.t.g1", False),                    # a folder grant
    ("long_task.t", False),                        # a chat's long task
    ("web_session.abc", False),                    # a browser's sign-in
    ("system.lifecycle.last_boot_epoch", False),   # this machine's boot stamp
])
def test_what_travels(key, carried):
    from kazma_core.migration.importer import _carried_in_migration

    assert _carried_in_migration(key) is carried
