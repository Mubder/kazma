"""The guard and its commands probe the port Kazma actually binds.

On the drill VM (2026-11-07) Kazma bound 9191 (``KAZMA_PORT`` in .env) and
the guard was told with ``setx KAZMA_GUARD_HEALTH_URL``. ``--status`` probed
the built-in 9090 whatever was set and said "not answering" over a healthy
Kazma; the other commands did the same from any shell without the variable.

``health_url`` is the one answer: ``KAZMA_GUARD_HEALTH_URL``; for a command,
the URL the running guard recorded; else ``KAZMA_PORT`` as the server reads
it (the environment, else the install's .env); else 9090.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_health_url_test", _GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def clean(monkeypatch, guard):
    for name in ("KAZMA_GUARD_HEALTH_URL", "KAZMA_PORT", "KAZMA_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(guard, "_read_state", lambda: {})
    monkeypatch.setattr(guard, "_guard_alive", lambda: False)
    return monkeypatch


def _env(tmp_path: Path, text: str) -> list[Path]:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return [path]


def test_the_port_is_read_as_the_server_reads_it(guard, clean, tmp_path):
    assert guard.install_port(_env(tmp_path, "OPENAI_API_KEY=x\nKAZMA_PORT=9191\n")) == 9191
    assert guard.install_port(_env(tmp_path, 'export KAZMA_PORT="9292"  # drill\n')) == 9292
    assert guard.install_port(_env(tmp_path, "# KAZMA_PORT=9090\n")) is None
    assert guard.install_port(_env(tmp_path, "KAZMA_PORT=not-a-port\n")) is None
    clean.setenv("KAZMA_PORT", "9393")
    assert guard.install_port(_env(tmp_path, "KAZMA_PORT=9191\n")) == 9393, "the environment wins, as in serve.py"


def test_one_answer_in_its_order(guard, clean, tmp_path):
    files = _env(tmp_path, "KAZMA_PORT=9191\n")
    assert guard.health_url(env_files=[]) == guard.DEFAULT_HEALTH_URL
    assert guard.health_url(env_files=files) == "http://127.0.0.1:9191/health/ready"
    # A command beside a running guard probes what that guard probes.
    clean.setattr(guard, "_read_state", lambda: {"health_url": "http://127.0.0.1:9494/health/ready"})
    clean.setattr(guard, "_guard_alive", lambda: True)
    assert guard.health_url(of_running_guard=True, env_files=files) == "http://127.0.0.1:9494/health/ready"
    assert guard.health_url(env_files=files) == "http://127.0.0.1:9191/health/ready", "the guard itself never"
    clean.setenv("KAZMA_GUARD_HEALTH_URL", "http://127.0.0.1:9595/health/ready")
    assert guard.health_url(of_running_guard=True, env_files=files) == "http://127.0.0.1:9595/health/ready"


def test_status_probes_the_port_kazma_binds(guard, clean, tmp_path, capsys):
    """Negative control in words: the old --status probed 9090 whatever was set."""
    probed: list[str] = []

    def probe(url, timeout):
        probed.append(url)
        return guard.ProbeResult(True, "ready")

    clean.setattr(guard, "probe", probe)
    clean.setattr(guard, "_port_holder_pid", lambda port: None)
    clean.setattr(guard, "read_pause", lambda: None)
    clean.setenv("KAZMA_GUARD_HEALTH_URL", "http://127.0.0.1:9191/health/ready")
    guard._cmd_status()
    assert probed[0] == "http://127.0.0.1:9191/health/ready", probed
    # And with nothing set but the server's own port in .env.
    clean.delenv("KAZMA_GUARD_HEALTH_URL")
    files = _env(tmp_path, "KAZMA_PORT=9191\n")
    clean.setattr(guard, "_env_files", lambda: files)
    probed.clear()
    guard._cmd_status()
    assert probed[0] == "http://127.0.0.1:9191/health/ready", probed
    assert "healthy" in capsys.readouterr().out


def test_every_command_takes_the_url_from_the_one_function(guard):
    """No guard code reads the variable or the built-in URL on its own."""
    source = _GUARD.read_text(encoding="utf-8")
    assert source.count('os.environ.get("KAZMA_GUARD_HEALTH_URL"') == 1
    uses = [line for line in source.splitlines() if "DEFAULT_HEALTH_URL" in line]
    assert len(uses) == 2, uses  # its definition, and health_url's last answer
