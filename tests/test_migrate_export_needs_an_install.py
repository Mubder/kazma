"""An export reads an installation, or refuses; verify refuses an empty bundle (FA-252).

The 2026-11 functional audit ran ``kazma migrate export`` from a scratch
folder: it made a new, empty ``kazma-data`` there (``paths.data_dir`` creates
the folder it is asked for), exported it, printed "Bundle written (0.0 MB)",
and ``kazma migrate verify`` called the 1 KB bundle valid. An owner moving an
install could have moved that bundle and deleted the source.

Each case runs the real exporter in a child process (``data_dir()`` comes
from the environment). The old exporter (no installation check) is the
negative control, and the bundle it writes is the one the new verify refuses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path



def _run(code: str, data_dir: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAZMA_")}
    env.update({"KAZMA_DB_BACKEND": "sqlite", "KAZMA_VAULT_KEY": "test-only-not-a-secret",
                "KAZMA_DATA_DIR": str(data_dir)})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=180)


_EXPORT = """
    import json
    from kazma_core.migration import exporter
    {patch}
    try:
        out = exporter.export_bundle({bundle!r}, include_assets=False)
        print(json.dumps({{"written": str(out)}}))
    except exporter.ExportRefused as exc:
        print(json.dumps({{"refused": str(exc)}}))
"""


def _export(tmp_path: Path, data_dir: Path, *, old: bool = False) -> dict:
    bundle = tmp_path / "bundle.zip"
    patch = "exporter._installation_problem = lambda d, b: None" if old else ""
    proc = _run(_EXPORT.format(bundle=str(bundle), patch=patch), data_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_an_export_from_a_folder_with_no_install_refuses_and_writes_nothing(tmp_path):
    data_dir = tmp_path / "scratch" / "kazma-data"
    answer = _export(tmp_path, data_dir)
    assert "refused" in answer
    assert str(data_dir) in answer["refused"]
    assert "Nothing was written" in answer["refused"]
    assert not data_dir.exists()  # it made no empty data folder either
    assert not (tmp_path / "bundle.zip").exists()


def test_an_empty_data_folder_is_refused_too(tmp_path):
    data_dir = tmp_path / "scratch" / "kazma-data"
    data_dir.mkdir(parents=True)
    assert "refused" in _export(tmp_path, data_dir)


def test_an_install_exports(tmp_path):
    data_dir = tmp_path / "install" / "kazma-data"
    data_dir.mkdir(parents=True)
    proc = _run(
        """
        import sqlite3
        from kazma_core.paths import data_dir
        c = sqlite3.connect(str(data_dir() / "agent_artifacts.db")); c.execute("CREATE TABLE t (v)"); c.commit(); c.close()
        """,
        data_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "written" in _export(tmp_path, data_dir)


def test_the_old_exporter_wrote_an_empty_bundle_and_verify_now_refuses_it(tmp_path):
    """Negative control: without the check the exporter writes the bundle
    the audit found; the new verify refuses that bundle."""
    data_dir = tmp_path / "scratch" / "kazma-data"
    answer = _export(tmp_path, data_dir, old=True)
    assert "written" in answer
    from kazma_core.migration.bundle import KazmaBundle

    report = KazmaBundle(Path(answer["written"])).verify()
    assert not report.ok
    assert any("no store of an installation" in e for e in report.errors), report.errors
