"""serve.py — the launcher kazma_guard runs — takes its bind host from .env.

Regression for the 2026-09-22 audit: it read KAZMA_HOST before any .env was
loaded, so a KAZMA_HOST line there was ignored while `kazma serve`,
`kazma-web` and docs/docs/ops/wsl-fixed-access.md all treated .env as
authoritative. Runs the real script in a subprocess with uvicorn stubbed, so
no server starts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_RUNNER = """
import runpy, sys, types
sys.modules["uvicorn"] = types.SimpleNamespace(
    run=lambda **kw: print("BIND", kw["host"], kw["port"])
)
runpy.run_path(sys.argv[1], run_name="__main__")
"""


def test_serve_binds_the_host_named_in_dotenv(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "KAZMA_HOST=localhost\nKAZMA_SECRET=test-secret-for-serve-env\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"KAZMA_HOST", "KAZMA_SECRET", "KAZMA_ENV_FILE", "KAZMA_TRUSTED_PROXIES"}
    }
    env.update({"KAZMA_USER_HOME": str(home), "KAZMA_DB_BACKEND": "sqlite"})
    result = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(REPO_ROOT / "serve.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "BIND localhost 9090" in result.stdout, result.stdout[-2000:]
    # The .env secret was seen before the "generated a secret" fallback ran.
    assert "Generated KAZMA_SECRET" not in result.stdout
