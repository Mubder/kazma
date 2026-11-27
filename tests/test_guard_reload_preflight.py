"""A reload never swaps a working server for one that cannot start here.

Live 2026-11-26, 11:30 UTC: Windows' Application Control (Smart App Control)
had begun refusing unsigned DLLs half an hour earlier. The running server
had loaded psycopg's client library before that; the guard's reload stopped
it, the new process could not load the library, and Kazma started read-only
for thirty minutes with its database answering. The guard now runs a
preflight first -- a child process with the server's interpreter, in its
folder, importing what the new code loads and starting the docker CLI it
picks -- and keeps the running server when it fails: the update waits.

The failure is planted the way Windows' refusal reads: a ``psycopg`` in the
child's folder whose import raises "DLL load failed while importing pq: An
Application Control policy has blocked this file." Each behaviour has its
negative control: the same check with nothing planted switches servers.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_SERVICE = Path(__file__).resolve().parents[1] / "scripts" / "service"

#: How Windows' refusal reads inside an import error (live, 2026-11-26).
_REFUSAL = "DLL load failed while importing pq: An Application Control policy has blocked this file."
#: Words that would make Windows' protection read as a fault or ask a user
#: to weaken it (the owner's rule, 2026-11-26).
_NEVER = ("turn off", "turn it off", "disable", "switch off", "allow it again", "security risk",
          "unsafe", "blocked")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_preflight_{name}", _SERVICE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load("kazma_guard")


def _plant_refused_psycopg(folder: Path) -> None:
    """A psycopg whose import fails the way Windows' refusal does."""
    pkg = folder / "psycopg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(f"raise ImportError({_REFUSAL!r})\n", encoding="utf-8")


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __call__(self, level: str, event: str, **fields: object) -> None:
        self.events.append((level, event, fields))

    def names(self) -> list[str]:
        return [e for _, e, _ in self.events]


class _Child:
    pid = 4243
    returncode = None

    def poll(self):
        return self.returncode


class _Notify:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def _guard_for(folder: Path) -> guard.Guard:
    g = guard.Guard.__new__(guard.Guard)
    g.log = _Log()
    g._stop = False
    g.proc = _Child()
    g.health_url = "http://127.0.0.1:9/health/ready"
    g.cwd = folder
    g.server_python = sys.executable
    g.notify = _Notify()
    g.spawned_at = time.time() - 60
    return g


@pytest.fixture
def stops(monkeypatch) -> list[int]:
    stopped: list[int] = []

    def stop_child(proc, log, **kw):
        stopped.append(proc.pid)
        return True

    monkeypatch.setattr(guard, "stop_child", stop_child)
    return stopped


# ── the check itself: a real child with the server's python ───────────────


def test_the_check_passes_for_code_that_starts_here(tmp_path):
    result = guard.run_reload_preflight(sys.executable, tmp_path)
    assert result["ok"] is True, result
    assert result["steps"]["kazma_core"] == "ok" and result["steps"]["app"] == "ok"
    assert result["steps"]["psycopg"] in {"c", "binary", "python", "not installed"}
    assert result["steps"]["docker"] in {"started (wsl)", "started (cli)", "none found"}


def test_the_check_fails_where_windows_refuses_a_library_the_new_code_loads(tmp_path):
    _plant_refused_psycopg(tmp_path)
    result = guard.run_reload_preflight(sys.executable, tmp_path)
    assert result["ok"] is False and result["step"] == "psycopg"
    assert "Application Control" in result["detail"]
    assert guard.preflight_refused_by_windows(result)


def test_an_interpreter_that_cannot_start_fails_the_check(tmp_path):
    result = guard.run_reload_preflight(str(tmp_path / "no-python.exe"), tmp_path)
    assert result["ok"] is False and result["step"] == "python"


def test_a_check_that_does_not_finish_fails_and_keeps_the_heartbeat(tmp_path, monkeypatch):
    """A hung child is killed at the bound; the guard's tick runs meanwhile."""
    monkeypatch.setattr(guard, "_RELOAD_PREFLIGHT", "import time\ntime.sleep(60)\n")
    ticks: list[float] = []
    result = guard.run_reload_preflight(sys.executable, tmp_path, timeout_s=2.5,
                                        tick=lambda: ticks.append(time.monotonic()))
    assert result["ok"] is False and result["step"] == "timeout"
    assert len(ticks) >= 2, "the heartbeat must keep going while the check runs"


# ── the guard: a failed check keeps the running server ────────────────────


def test_a_reload_whose_new_code_cannot_start_keeps_the_running_server(tmp_path, stops):
    _plant_refused_psycopg(tmp_path)
    g = _guard_for(tmp_path)
    requested_at = guard.request_reload()
    assert g._take_reload_request() is False
    assert stops == [], "the working server was stopped for code that cannot start here"
    state = guard._read_state()
    assert state["reload_ack"] == requested_at and state["reload_action"] == "kept_running"
    assert state["reload_preflight"]["step"] == "psycopg"
    assert state["reload_preflight"]["windows_check"] is True
    assert "guard.reload_preflight_failed" in g.log.names()
    assert not g._stopped_for_reload
    # One calm card: the update waits, Kazma keeps running.
    assert len(g.notify.sent) == 1
    card = g.notify.sent[0]
    assert "An update waits; Kazma keeps running" in card
    assert "Nothing is wrong with the PC" in card and "signed version" in card
    words = card.split("Detail:")[0].lower()  # the operator's detail quotes Windows itself
    assert not [w for w in _NEVER if w in words], card


def test_control_the_same_reload_with_code_that_starts_switches_servers(tmp_path, stops):
    g = _guard_for(tmp_path)
    requested_at = guard.request_reload()
    assert g._take_reload_request() is True
    assert stops == [_Child.pid]
    state = guard._read_state()
    assert state["reload_ack"] == requested_at and state["reload_action"] == "restarting"
    assert state.get("reload_preflight") is None
    assert "guard.reload_preflight_ok" in g.log.names()
    assert g.notify.sent == []


def test_the_same_reason_is_paged_once(tmp_path, stops):
    _plant_refused_psycopg(tmp_path)
    g = _guard_for(tmp_path)
    for _ in range(2):
        guard.request_reload()
        assert g._take_reload_request() is False
    assert len(g.notify.sent) == 1
    assert [e for e in g.log.names() if e == "guard.reload_preflight_failed"] == [
        "guard.reload_preflight_failed"] * 2


def test_skip_check_switches_without_the_check(tmp_path, stops):
    _plant_refused_psycopg(tmp_path)
    g = _guard_for(tmp_path)
    guard.request_reload(check=False)
    assert g._take_reload_request() is True
    assert stops == [_Child.pid]
    assert "guard.reload_preflight_skipped" in g.log.names()


def test_a_reload_during_boot_keeps_the_booting_server_too(tmp_path, stops, monkeypatch):
    _plant_refused_psycopg(tmp_path)
    g = _guard_for(tmp_path)
    g.spawned_at = time.time() - 5
    guard.request_reload()
    answers = iter([(False, "starting"), (True, "ready")])
    monkeypatch.setattr(guard, "probe", lambda *a: next(answers))
    monkeypatch.setattr(guard, "server_started_at", lambda *a: time.time())
    assert g._wait_ready(g.spawned_at) is True
    assert stops == []


# ── --reload says it calmly and exits with its own code ───────────────────


def test_reload_says_the_update_waits(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "_guard_alive", lambda: True)
    monkeypatch.setattr(guard, "_live_commit", lambda *a: "abc")
    monkeypatch.setattr(guard, "clear_pause", lambda: False)
    real_request = guard.request_reload

    def request_and_answer(**kw):
        ts = real_request(**kw)
        guard._update_state(reload_ack=ts, reload_action="kept_running", reload_preflight={
            "step": "psycopg", "detail": f"ImportError: {_REFUSAL}", "windows_check": True})
        return ts

    monkeypatch.setattr(guard, "request_reload", request_and_answer)
    monkeypatch.setattr(guard, "_wait_for_new_boot",
                        lambda *a, **k: pytest.fail("no new server is waited for: none was started"))
    assert guard._cmd_reload() == guard.RELOAD_WAITS_EXIT
    out = capsys.readouterr().out
    assert "An update waits; Kazma keeps running." in out
    assert "the database client library" in out and "Nothing is wrong with the PC" in out
    assert not [w for w in _NEVER if w in out.split("Detail:")[0].lower()], out


def test_control_reload_waits_for_the_new_server_when_the_guard_restarts(monkeypatch):
    monkeypatch.setattr(guard, "_guard_alive", lambda: True)
    monkeypatch.setattr(guard, "_live_commit", lambda *a: "abc")
    monkeypatch.setattr(guard, "clear_pause", lambda: False)
    real_request = guard.request_reload

    def request_and_answer(**kw):
        ts = real_request(**kw)
        guard._update_state(reload_ack=ts, reload_action="restarting")
        return ts

    monkeypatch.setattr(guard, "request_reload", request_and_answer)
    waited: list[float] = []
    monkeypatch.setattr(guard, "_wait_for_new_boot", lambda h, ts, *a, **k: waited.append(ts) or 0)
    assert guard._cmd_reload() == 0
    assert len(waited) == 1


def test_the_guard_says_it_has_the_check():
    """--status calls a guard without the feature older than its file."""
    assert "reload_preflight" in guard.GUARD_FEATURES


def test_the_card_words_name_each_step_calmly():
    for step in ("python", "kazma_core", "psycopg", "app", "docker", "timeout", "unknown"):
        for detail in (_REFUSAL, "ModuleNotFoundError: No module named 'x'"):
            title, text = guard.preflight_words({"step": step, "detail": detail})
            assert title == "An update waits; Kazma keeps running"
            assert "Kazma keeps running the version it had" in text
            assert not [w for w in _NEVER if w in text.split("Detail:")[0].lower()], text


def test_status_says_the_update_waits(tmp_path, stops):
    _plant_refused_psycopg(tmp_path)
    g = _guard_for(tmp_path)
    guard.request_reload()
    g._take_reload_request()
    lines = guard._update_status_lines(guard._read_state())
    assert lines and lines[0].startswith("update      : waits since")
    assert "Kazma keeps running the version it had" in lines[0]


def test_control_status_says_nothing_after_an_update_went_through(tmp_path, stops):
    g = _guard_for(tmp_path)
    guard.request_reload()
    g._take_reload_request()
    assert guard._update_status_lines(guard._read_state()) == []


def test_the_state_file_is_json_the_cli_reads(tmp_path, stops):
    _plant_refused_psycopg(tmp_path)
    g = _guard_for(tmp_path)
    guard.request_reload()
    g._take_reload_request()
    raw = json.loads(guard._state_path().read_text(encoding="utf-8"))
    assert set(raw["reload_preflight"]) == {"at", "step", "detail", "windows_check"}


# ── the detail is masked before anyone reads it (review L7, AGENTS §26 F) ──

def _secrets() -> tuple[str, str]:
    """A URL password and a token, built from pieces (no file holds one)."""
    return "pw" + "Zq81" * 4, "gh" + "p_" + "A1b2" * 9


def _plant_failing_app_with_secrets(folder: Path) -> tuple[str, str]:
    password, token = _secrets()
    pkg = folder / "psycopg"
    pkg.mkdir(parents=True, exist_ok=True)
    words = f"cannot reach postgresql://kazma:{password}@db:5432/kazma with {token}"
    (pkg / "__init__.py").write_text(f"raise RuntimeError({words!r})\n", encoding="utf-8")
    return password, token


def test_a_secret_in_the_failure_reaches_no_log_state_or_card(tmp_path, stops):
    password, token = _plant_failing_app_with_secrets(tmp_path)
    g = _guard_for(tmp_path)
    guard.request_reload()
    assert g._take_reload_request() is False
    record = guard._read_state()["reload_preflight"]
    assert record["step"] == "psycopg" and record["windows_check"] is False
    said = [json.dumps(record), json.dumps(g.log.events, default=str), *g.notify.sent,
            guard._state_path().read_text(encoding="utf-8")]
    for text in said:
        assert password[:12] not in text and token[:12] not in text, text
    assert "RuntimeError" in record["detail"] and "postgresql://" in record["detail"]


def test_the_guard_masks_what_the_child_could_not(tmp_path, monkeypatch):
    _, token = _secrets()
    raw = {"ok": False, "steps": {}, "step": "kazma_core", "detail": f"ImportError: bad {token}"}
    monkeypatch.setattr(guard, "_preflight_child", lambda *a, **k: dict(raw))
    result = guard.run_reload_preflight(sys.executable, tmp_path)
    assert token[:12] not in result["detail"] and "ImportError: bad" in result["detail"]
    assert result["windows_check"] is False
    # The control: what the child said held the token.
    assert token in raw["detail"]


def test_without_the_masker_the_detail_is_withheld_and_windows_still_decides(tmp_path, monkeypatch):
    _, token = _secrets()
    monkeypatch.setattr(guard, "_TOKEN_SHAPES_MODULE", None)
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)  # token_shapes.py is not there
    raw = {"ok": False, "steps": {}, "step": "psycopg", "detail": f"ImportError: {_REFUSAL} {token}"}
    monkeypatch.setattr(guard, "_preflight_child", lambda *a, **k: dict(raw))
    result = guard.run_reload_preflight(sys.executable, tmp_path)
    assert result["detail"] == guard.DETAIL_WITHHELD
    assert result["windows_check"] is True and guard.preflight_refused_by_windows(result)
    title, words = guard.preflight_words(result)
    assert "Windows checked" in words and token[:12] not in words
