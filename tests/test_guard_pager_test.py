"""The guard's pager and its test page (the unified alerts plan, Stage 3).

Settings -> Alerts shows whether the guard's last page was taken and sends a
test page through the guard -- the path a page takes when Kazma is down. The
server writes ``guard.test_page`` beside the reload request
(``kazma_core.observability.guard_pager.request_test_page``); the running
guard sends one card through a notifier of its own and records the answer in
its state file (``PagerTest``), which the page reads back
(``pager_test_result``). Held here:

* every page's outcome is recorded in the state file (``pager``): taken or
  not, the card's first line, the chat -- never the bot's token, even inside
  an error that quotes the request's address;
* the request is answered once: sent, failed (with why), or expired when it
  is older than five minutes (never sent late); a request with a malformed id
  is dropped;
* the server asks only a guard that is running and answers test pages
  (``guard_features``); otherwise it says why;
* end to end with the REAL guard process: the request is taken and answered
  in the state file (under pytest the guard has no credentials, so the answer
  is "failed: not configured").

Each rule has a negative control. Nothing here reaches Telegram.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.observability import guard_pager

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
TOKEN = "123456:SECRET-bot-token-value"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_pager_test_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


class _Telegram:
    """urllib.request.urlopen standing in for Telegram: records, or refuses."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.sent: list[dict] = []

    def __call__(self, req, timeout=None):
        if self.refuse:
            # An error that quotes the address, token and all.
            raise urllib.error.HTTPError(req.full_url, 400, f"Bad Request for {req.full_url}", {}, io.BytesIO(b""))
        body = dict(x.split("=", 1) for x in req.data.decode().split("&"))
        self.sent.append(body)

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _Resp()


@pytest.fixture
def files(tmp_path, monkeypatch):
    """The guard's state and reload files, as both sides find them."""
    state = tmp_path / "guard.state.json"
    reload_file = tmp_path / "guard.reload"
    monkeypatch.setenv("KAZMA_GUARD_STATE", str(state))          # the guard's side
    monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(state))     # the server's side
    monkeypatch.setenv("KAZMA_GUARD_RELOAD_FILE", str(reload_file))
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "4242")
    return SimpleNamespace(state=state, request=tmp_path / "guard.test_page")


def _state(files) -> dict:
    return json.loads(files.state.read_text(encoding="utf-8"))


def _beating(features=("pager_test",), age: float = 1.0) -> dict:
    return {"heartbeat": time.time() - age, "guard_features": list(features)}


def _fake_guard() -> SimpleNamespace:
    said: list[tuple[str, dict]] = []
    return SimpleNamespace(log=lambda level, event, **kw: said.append((event, kw)), said=said,
                           notify=None, cwd=ROOT, server_python=None)


def _run_test(pager, timeout: float = 5.0) -> None:
    """Tick until the test's thread finished and its answer was taken."""
    end = time.time() + timeout
    pager.tick()
    while time.time() < end:
        thread = pager._thread
        if thread is not None and not thread.is_alive() and pager._result is None:
            return
        pager.tick()
        time.sleep(0.02)
    raise AssertionError("the test page never finished")


# -- the last page's outcome --------------------------------------------------------


def test_every_page_records_its_outcome_and_never_the_token(files, monkeypatch):
    tg = _Telegram()
    monkeypatch.setattr(guard.urllib.request, "urlopen", tg)
    g = guard.Guard.__new__(guard.Guard)
    g.log = lambda *a, **k: None
    g.notify = guard.Notifier(g.log)
    g.notify.on_outcome = g._note_page_outcome
    g._last_page_fp, g._last_page_at, g.page_cooldown_s = "", 0.0, 900
    assert g._page("warn", "Disk space low on C:", "12% free", kind="disk.low", force=True)
    pager = _state(files)["pager"]
    assert pager["ok"] is True and pager["chat"] == "4242" and pager["source"] == "env"
    assert pager["head"] == "\U0001f7e1 [Guard] Disk space low on C:"
    # Refused: recorded as not taken, the error kept -- without the token.
    tg.refuse = True
    g._page("error", "Kazma failed to start", "", kind="guard.startup_failed", force=True)
    pager = _state(files)["pager"]
    assert pager["ok"] is False and "HTTPError" in pager["error"]
    assert TOKEN not in json.dumps(_state(files))
    # Control: the error as Telegram's library words it does carry the token.
    with pytest.raises(urllib.error.HTTPError) as caught:
        tg(SimpleNamespace(full_url=f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=b""))
    assert TOKEN in str(caught.value)


def test_a_page_held_for_want_of_credentials_says_so(files, monkeypatch):
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
    notes: list[dict] = []
    n = guard.Notifier(lambda *a, **k: None)
    n.on_outcome = notes.append
    n.send(guard.format_operator_card("Guard", "warn", "Kazma stopped"))
    assert notes and notes[-1]["ok"] is False and notes[-1]["held"] == 1
    assert notes[-1]["error"] == "not configured"


# -- the test page ----------------------------------------------------------------------


def test_the_server_asks_and_the_guard_answers_sent(files, monkeypatch):
    tg = _Telegram()
    monkeypatch.setattr(guard.urllib.request, "urlopen", tg)
    files.state.write_text(json.dumps(_beating()), encoding="utf-8")
    asked = guard_pager.request_test_page()
    assert asked["ok"] and files.request.exists()
    assert guard_pager.pager_test_result(asked["id"])["state"] == "waiting"
    pager = guard.PagerTest(_fake_guard(), path=files.request)
    _run_test(pager)
    answer = guard_pager.pager_test_result(asked["id"], asked["requested_at"])
    assert answer["state"] == "sent" and answer["chat"] == "4242", answer
    assert len(tg.sent) == 1 and tg.sent[0]["chat_id"] == "4242"
    assert "Test+page" in tg.sent[0]["text"] or "Test page" in tg.sent[0]["text"]
    assert not files.request.exists(), "a request is taken once"
    # The same request is never answered twice (its file is gone).
    pager.tick()
    assert len(tg.sent) == 1


def test_a_failed_test_page_says_why_without_the_token(files, monkeypatch):
    monkeypatch.setattr(guard.urllib.request, "urlopen", _Telegram(refuse=True))
    files.state.write_text(json.dumps(_beating()), encoding="utf-8")
    asked = guard_pager.request_test_page()
    _run_test(guard.PagerTest(_fake_guard(), path=files.request))
    answer = guard_pager.pager_test_result(asked["id"])
    assert answer["state"] == "failed" and "HTTPError" in answer["error"]
    assert TOKEN not in json.dumps(_state(files))


def test_an_old_request_is_expired_never_sent_late(files, monkeypatch):
    tg = _Telegram()
    monkeypatch.setattr(guard.urllib.request, "urlopen", tg)
    files.request.write_text(json.dumps({"id": "abcdef0123456789", "ts": time.time() - 600}), encoding="utf-8")
    pager = guard.PagerTest(_fake_guard(), path=files.request)
    pager.tick()
    assert _state(files)["pager_test"]["state"] == "expired"
    assert tg.sent == []
    # Control: the same request made now is sent.
    files.request.write_text(json.dumps({"id": "abcdef0123456780", "ts": time.time()}), encoding="utf-8")
    _run_test(pager)
    assert _state(files)["pager_test"]["state"] == "sent" and len(tg.sent) == 1


def test_a_malformed_request_is_dropped(files, monkeypatch):
    tg = _Telegram()
    monkeypatch.setattr(guard.urllib.request, "urlopen", tg)
    files.request.write_text(json.dumps({"id": "../../x", "ts": time.time()}), encoding="utf-8")
    fake = _fake_guard()
    guard.PagerTest(fake, path=files.request).tick()
    assert tg.sent == [] and not files.request.exists()
    assert ("pager_test.bad_request", {}) in fake.said


def test_the_settings_the_test_proved_become_the_pagers(files, monkeypatch):
    """A chat changed in Settings: the guard's own notifier resolved its
    credentials once; a test that read them from settings ('vault') hands
    them over. One set in the guard's environment is never replaced."""
    monkeypatch.setattr(guard.urllib.request, "urlopen", _Telegram())
    fake = _fake_guard()
    fake.notify = SimpleNamespace(token="old", chat="111", _source="vault")

    def fresh():
        n = guard.Notifier(fake.log)
        n.chat, n._source = "999", "vault"
        return n

    request = {"id": "0123456789abcdef", "ts": time.time()}
    files.request.write_text(json.dumps(request), encoding="utf-8")
    _run_test(guard.PagerTest(fake, notifier_factory=fresh, path=files.request))
    assert (fake.notify.chat, fake.notify.token) == ("999", TOKEN)
    # Control: an environment-set pager keeps its own chat.
    fake.notify = SimpleNamespace(token="env-token", chat="222", _source="env")
    files.request.write_text(json.dumps({**request, "id": "0123456789abcdee"}), encoding="utf-8")
    _run_test(guard.PagerTest(fake, notifier_factory=fresh, path=files.request))
    assert fake.notify.chat == "222"


# -- what the server asks -------------------------------------------------------------------


@pytest.mark.parametrize(("state", "code"), [
    (None, "no_guard"),
    # Built when the test runs, not when it is collected: a heartbeat stamped
    # at collection is a minute old by the time a long run reaches it.
    (lambda: _beating(age=300), "guard_silent"),
    (lambda: _beating(features=("outside_watch",)), "guard_too_old"),
])
def test_the_server_asks_only_a_guard_that_can_answer(files, state, code):
    if state is not None:
        files.state.write_text(json.dumps(state()), encoding="utf-8")
    assert guard_pager.request_test_page() == {"ok": False, "code": code}
    assert not files.request.exists()


def test_an_unanswered_request_is_said(files):
    files.state.write_text(json.dumps(_beating()), encoding="utf-8")
    asked = guard_pager.request_test_page()
    later = asked["requested_at"] + guard_pager.ANSWER_WAIT_S + 1
    assert guard_pager.pager_test_result(asked["id"], asked["requested_at"], now=later)["state"] == "unanswered"
    # Control: within the wait it is still waiting.
    assert guard_pager.pager_test_result(asked["id"], asked["requested_at"])["state"] == "waiting"


def test_the_status_never_carries_the_token(files):
    files.state.write_text(json.dumps({**_beating(), "pager": {
        "at": time.time(), "ok": True, "head": "x", "source": "vault", "chat": "1",
        "token": TOKEN, "nested": {"token": TOKEN}}}), encoding="utf-8")
    status = guard_pager.pager_status()
    assert status["guard"] == "running" and status["tests"] is True
    assert TOKEN not in json.dumps(status)


# -- end to end: the real guard process -------------------------------------------------------


@pytest.mark.timeout(180)
def test_the_real_guard_answers_a_test_page(tmp_path, monkeypatch):
    from tests.test_guard_integration import GuardRun, _free_port

    port = _free_port()
    with GuardRun(tmp_path, port) as g:
        g.wait_for("child.ready", timeout=60)
        monkeypatch.setenv("KAZMA_GUARD_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("KAZMA_GUARD_RELOAD_FILE", str(tmp_path / "guard.reload"))
        asked = guard_pager.request_test_page()
        assert asked["ok"], asked
        end = time.time() + 60
        answer = {"state": "waiting"}
        while time.time() < end and answer["state"] in ("waiting", "sending"):
            time.sleep(0.5)
            answer = guard_pager.pager_test_result(asked["id"], asked["requested_at"])
        # No credentials under pytest: the guard says so, it does not hold it.
        assert answer["state"] == "failed", answer
        assert "not configured" in answer["error"]
        assert g.count("pager_test.requested") == 1
