"""Data paths must follow ``KAZMA_DATA_DIR``, not the process CWD.

Forty ``"kazma-data/..."`` literals lived across twenty modules while
``kazma_core.paths`` offered helpers that all resolve under ``data_dir()``. A
literal resolves against the *current working directory*, so the same logical
store was a different file depending on who opened it and from where: the
server, a cron job and a systemd unit with a different WorkingDirectory each
got their own, and the backup routine — which goes through ``data_dir()`` —
copied only one of them.

It bit for real before it was swept: ``KnowledgeStore`` and ``BookmarkStore``
hardcoded ``"kazma-data/settings.db"`` while ``ConfigStore`` resolved the SAME
filename through ``paths.settings_db()``.

A source grep for the literal lives in ``test_static_gates.py``. This file is
the behavioural half: the grep can be satisfied by a constant that is still
frozen at import, and only running it under a different ``KAZMA_DATA_DIR``
proves it actually moves.

Subprocess, not monkeypatch: several of these are module-level constants
evaluated at import, so the environment has to be set *before* the module is
first imported. A monkeypatch inside an already-loaded process would pass
while the shipped code stayed broken.

Import-time versus use-time — know which you have
-------------------------------------------------
Measured 2026-09-21, importing everything first and *then* changing
``KAZMA_DATA_DIR``:

* **Resolved at USE** (follows a late change): ``stores.knowledge._default_db``,
  ``stores.bookmarks._default_db`` — these are functions.
* **Frozen at IMPORT** (cannot): ``agent_runner.CHECKPOINT_DB``,
  ``checkpoint_retention.DEFAULT_DB``, ``time_travel.DEFAULT_DB_PATH``,
  ``swarm.task_store._DEFAULT_DB``, ``observability.llm_ledger._DEFAULT_DB``,
  ``swarm.semantic_cache._DEFAULT_DB``, ``tools.image_gen.IMAGE_DIR``,
  ``chat_attachments.ATTACHMENT_DIR``.

The frozen set is **fine for the bug this file is about**: a real deployment
has ``KAZMA_DATA_DIR`` in the environment before the process starts, so
reading it at import is correct, and that is what these tests verify. What
they cannot do is follow a change made afterwards.

That distinction is not academic. ``time_travel`` had to be fixed a second
time because a test monkeypatches the variable AFTER import and then builds a
SnapshotRecorder — its frozen default was also absolute, so it took the
``is_absolute() -> return unchanged`` branch in ``_resolve_db_path`` and
skipped the anchoring that function exists to do. The fix was to make the
DEFAULT ARGUMENTS ``None`` so resolution happens when the object is
constructed. If you add a test that sets ``KAZMA_DATA_DIR`` late and it fails
against one of the frozen names above, that is the same problem, and the same
shape of fix: resolve where it is used, not where it is declared.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

#: (import statement, expression) for each path that must follow the data dir.
PROBES: list[tuple[str, str]] = [
    ("from kazma_core.agent_runner import CHECKPOINT_DB", "CHECKPOINT_DB"),
    ("from kazma_core.checkpoint_retention import DEFAULT_DB", "DEFAULT_DB"),
    ("from kazma_core.time_travel import DEFAULT_DB_PATH", "DEFAULT_DB_PATH"),
    ("from kazma_core.swarm.task_store import _DEFAULT_DB", "_DEFAULT_DB"),
    ("from kazma_core.observability.llm_ledger import _DEFAULT_DB", "_DEFAULT_DB"),
    ("from kazma_core.swarm.semantic_cache import _DEFAULT_DB", "_DEFAULT_DB"),
    ("from kazma_core.tools.image_gen import IMAGE_DIR", "IMAGE_DIR"),
    ("from kazma_ui.chat_attachments import ATTACHMENT_DIR", "ATTACHMENT_DIR"),
    ("from kazma_core.stores.knowledge import _default_db", "_default_db()"),
    ("from kazma_core.stores.bookmarks import _default_db", "_default_db()"),
]


@pytest.mark.parametrize("stmt,expr", PROBES, ids=[e for _s, e in PROBES])
def test_path_follows_kazma_data_dir(stmt: str, expr: str, tmp_path) -> None:
    """Set KAZMA_DATA_DIR in a fresh process; the path must land under it."""
    env = dict(os.environ)
    env["KAZMA_DATA_DIR"] = str(tmp_path)
    env["KAZMA_DB_BACKEND"] = "sqlite"
    env.pop("KAZMA_DATABASE_URL", None)

    code = f"{stmt}\nimport json\nprint(json.dumps(str({expr})))"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    resolved = json.loads(proc.stdout.strip().splitlines()[-1])

    assert resolved.startswith(str(tmp_path)), (
        f"{expr} resolved to {resolved!r}, which is NOT under KAZMA_DATA_DIR "
        f"({tmp_path}). It is resolving against the process CWD, so a cron "
        "job, a service and the server each open a different file for the "
        "same store — and the backup, which uses data_dir(), copies one."
    )
