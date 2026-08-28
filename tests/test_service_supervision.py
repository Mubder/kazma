"""Supervision layer: the health-gated guard and the per-platform installers.

Phase 1 of the resilience plan. The audit found no process supervisor of any
kind on the live host -- 198 scheduled tasks, none for Kazma -- and no service
definition shipped in the repo for any platform, despite a Dockerfile, k8s
manifests and a fly.toml being present.

These tests pin the two properties that make the supervision layer worth
having: it treats "alive but unhealthy" as death (which no OS supervisor
does), and it produces a working unit on every supported platform from any
platform.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SERVICE_DIR = Path(__file__).resolve().parents[1] / "scripts" / "service"


def _load(name: str):
    path = _SERVICE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_svc_{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load("kazma_guard")
installer = _load("install_service")


# ── the probe: what counts as alive ───────────────────────────────────


class _Resp:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode()

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, result):
    def fake(_req, timeout=None):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(guard.urllib.request, "urlopen", fake)


def test_ready_status_is_healthy(monkeypatch):
    _patch_urlopen(monkeypatch, _Resp(200, json.dumps({"status": "ready"})))
    ok, detail = guard.probe("http://x/health/ready", 1)
    assert ok is True and detail == "ready"


def test_degraded_status_is_unhealthy_and_names_the_failing_check(monkeypatch):
    """A 200 with failing checks is NOT alive.

    This is the case every OS supervisor misses: the process is up, the port
    answers, and the agent does nothing useful.
    """
    body = json.dumps({
        "status": "degraded",
        "checks": {"database": {"status": "failed"}, "config_store": {"status": "ok"}},
    })
    _patch_urlopen(monkeypatch, _Resp(200, body))
    ok, detail = guard.probe("http://x/health/ready", 1)
    assert ok is False
    assert "database" in detail


def test_non_200_is_unhealthy(monkeypatch):
    _patch_urlopen(monkeypatch, _Resp(503, "nope"))
    ok, detail = guard.probe("http://x/health/ready", 1)
    assert ok is False and "503" in detail


def test_unreachable_is_unhealthy_not_an_exception(monkeypatch):
    _patch_urlopen(monkeypatch, guard.urllib.error.URLError("connection refused"))
    ok, detail = guard.probe("http://x/health/ready", 1)
    assert ok is False and "unreachable" in detail


def test_probe_never_raises(monkeypatch):
    """A supervisor that dies on a probe error supervises nothing."""
    _patch_urlopen(monkeypatch, RuntimeError("boom"))
    ok, detail = guard.probe("http://x/health/ready", 1)
    assert ok is False and "boom" in detail


def test_unparseable_200_is_treated_as_alive(monkeypatch):
    """Don't kill a healthy server over a body we failed to parse."""
    _patch_urlopen(monkeypatch, _Resp(200, "<html>fine</html>"))
    ok, _ = guard.probe("http://x/health/ready", 1)
    assert ok is True


# ── restart policy ────────────────────────────────────────────────────


def test_backoff_grows_and_is_capped():
    g = guard.Guard.__new__(guard.Guard)
    seen = []
    for n in range(0, 12):
        g.restarts = n
        seen.append(guard.Guard._backoff(g))
    assert seen[0] < seen[3], "backoff must grow"
    assert max(seen) == guard.BACKOFF_LADDER_S[-1], "backoff must be capped"
    assert seen == sorted(seen), "backoff must be monotonic"


def test_crash_loop_detected_only_when_restarts_are_dense(monkeypatch):
    g = guard.Guard.__new__(guard.Guard)
    g.recent = guard.deque(maxlen=guard.CRASH_LOOP_COUNT)

    clock = {"t": 1000.0}
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock["t"])

    # Rapid restarts -> loop.
    for _ in range(guard.CRASH_LOOP_COUNT - 1):
        assert guard.Guard._crash_looping(g) is False
        clock["t"] += 1
    assert guard.Guard._crash_looping(g) is True

    # Restarts spread far apart -> not a loop.
    g.recent.clear()
    for _ in range(guard.CRASH_LOOP_COUNT):
        guard.Guard._crash_looping(g)
        clock["t"] += guard.CRASH_LOOP_WINDOW_S
    assert guard.Guard._crash_looping(g) is False


def test_crash_loop_cooldown_exceeds_the_window():
    """Cooling down for less than the detection window re-triggers instantly."""
    assert guard.CRASH_LOOP_COOLDOWN_S > guard.CRASH_LOOP_WINDOW_S


# ── notifier ──────────────────────────────────────────────────────────


def test_notifier_is_optional_and_silent_when_unconfigured(monkeypatch, tmp_path):
    for var in ("KAZMA_GUARD_TELEGRAM_TOKEN", "KAZMA_GUARD_TELEGRAM_CHAT",
                "SWARM_BOT_TOKEN", "SWARM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    log = guard.GuardLog(tmp_path / "g.log")
    n = guard.Notifier(log)
    assert n.configured is False
    n.send("hello")  # must not raise


def test_notifier_failure_does_not_propagate(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "c")
    monkeypatch.setattr(
        guard.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    assert n.configured is True
    n.send("hello")  # a failed alert must never take the supervisor down


def test_guard_log_is_separate_from_the_app_log():
    """If the guard logged into kazma.log it would go silent in exactly the
    failure it exists to report."""
    assert "guard" in guard._default_log_path().name


# ── installer: every platform, from any platform ──────────────────────

ALL_PLATFORMS = ["windows", "linux", "macos", "wsl", "docker"]


@pytest.mark.parametrize("target", ALL_PLATFORMS)
def test_every_platform_renders_a_unit(target, capsys):
    assert installer.do_print(target) == 0
    out = capsys.readouterr().out
    assert len(out.strip()) > 200, f"{target} produced no usable unit"


@pytest.mark.parametrize("target", ALL_PLATFORMS)
def test_units_are_pure_ascii(target, capsys):
    """A service installer must survive a cp1252 console.

    The first version of this tool used box-drawing characters and crashed
    with UnicodeEncodeError on the default Windows terminal -- on the exact
    platform it was most needed.
    """
    installer.do_print(target)
    out = capsys.readouterr().out
    bad = sorted({c for c in out if ord(c) > 127})
    assert not bad, f"{target} unit contains non-ascii: {bad}"


def test_systemd_unit_restarts_always_and_orders_after_postgres():
    unit = installer.systemd_unit()
    assert "Restart=always" in unit
    assert "RestartSec=" in unit
    assert "StartLimitBurst=" in unit, "needs a crash-loop guard"
    assert "postgresql.service" in unit, "Kazma's stores need Postgres first"


def test_launchd_plist_keeps_alive_and_throttles():
    plist = installer.launchd_plist()
    assert "<key>KeepAlive</key>" in plist
    assert "<key>SuccessfulExit</key>" in plist, "a clean stop must stay stopped"
    assert "ThrottleInterval" in plist, "needs a crash-loop guard"
    assert "RunAtLoad" in plist


def test_windows_task_restarts_and_runs_without_a_session():
    ps1 = installer.windows_task_ps1()
    assert "-RestartCount 3" in ps1
    assert "RestartInterval" in ps1
    assert "S4U" in ps1, "must run whether the user is logged on or not"
    assert "-AtStartup" in ps1, "must come back after a reboot"
    assert "MultipleInstances IgnoreNew" in ps1, "never run two Kazmas"


def test_every_platform_runs_the_same_guard():
    """One health contract, five supervisors. If a platform launches the
    server directly it loses health-gated restart and silently drifts."""
    for unit in (installer.systemd_unit(), installer.launchd_plist(),
                 installer.windows_task_ps1()):
        assert "kazma_guard.py" in unit


def test_docker_notes_call_out_the_missing_healthcheck():
    notes = installer.docker_notes()
    assert "healthcheck" in notes.lower()
    assert "/health/ready" in notes


def test_wsl_requires_both_layers():
    notes = installer.wsl_notes()
    assert "systemd=true" in notes
    assert "enable-linger" in notes


def test_platform_detection_returns_a_known_target():
    assert installer.detect_platform() in ("windows", "linux", "macos", "wsl", "unknown")


def test_userlevel_fallback_registers_without_elevation():
    """A failed install that leaves nothing behind is worse than a partial
    one that works until reboot.

    The elevated task needs S4U + Highest, which a normal user cannot
    register. The fallback must still give restart-on-failure and recovery
    at logon, and must not silently claim boot-start it cannot deliver.
    """
    limited = installer.windows_task_ps1(elevated=False)
    assert "-RunLevel Limited" in limited
    assert "S4U" not in limited, "S4U requires elevation"
    assert "-AtStartup" not in limited, "must not claim boot-start it lacks"
    assert "-AtLogOn" in limited
    assert "-RestartCount 3" in limited, "restart-on-failure still applies"
    assert "USER-LEVEL" in limited, "must say what it does not cover"


def test_elevated_task_is_still_the_default_and_the_documented_upgrade():
    elevated = installer.windows_task_ps1(elevated=True)
    assert "-AtStartup" in elevated and "S4U" in elevated
    # default arg must remain the real deployment
    assert installer.windows_task_ps1() == elevated


# ── credential resolution ─────────────────────────────────────────────


def _clear_notifier_env(monkeypatch):
    for var in ("KAZMA_GUARD_TELEGRAM_TOKEN", "KAZMA_GUARD_TELEGRAM_CHAT",
                "SWARM_BOT_TOKEN", "SWARM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)


def test_env_credentials_win_over_the_vault(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "env-token")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "env-chat")
    called = {"vault": False}

    def _boom(self):
        called["vault"] = True
        return ("vault-token", "vault-chat")

    monkeypatch.setattr(guard.Notifier, "_from_config_store", _boom)
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    assert n.token == "env-token" and n.chat == "env-chat"
    assert called["vault"] is False, "must not touch the vault when env is set"


def test_falls_back_to_the_vault(monkeypatch, tmp_path):
    _clear_notifier_env(monkeypatch)
    monkeypatch.setattr(
        guard.Notifier, "_from_config_store",
        lambda self: ("vault-token", "vault-chat"),
    )
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    assert n.configured is True
    assert n.token == "vault-token"


def test_broken_venv_loses_alerting_never_supervision(monkeypatch, tmp_path):
    """The config-store import is the guard's ONLY reach into the app.

    If kazma_core cannot be imported -- exactly the situation where the
    guard matters most -- resolution must degrade to "no alerts" rather
    than raise and take the supervisor down with it.
    """
    _clear_notifier_env(monkeypatch)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _explode(name, *a, **k):
        if name.startswith("kazma_core"):
            raise ImportError("simulated broken venv")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _explode)
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    assert n.configured is False
    n.send("should be a no-op")  # must not raise


def test_describe_never_leaks_the_token(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "SUPER-SECRET-TOKEN")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "12345")
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    desc = n.describe()
    assert "SUPER-SECRET-TOKEN" not in desc
    assert "12345" in desc, "the destination is useful; the credential is not"
