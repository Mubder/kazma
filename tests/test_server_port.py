"""Kazma's port: one answer everywhere, chosen at install, changed with one command.

The owner's request (2026-11-13): 9090 by default, and whoever installs Kazma
may choose another port. Until then the port was written out as 9090 in some
twenty places that read the shell's ``KAZMA_PORT`` alone, so a port kept in
the install's ``.env`` reached the server and not a ``kazma`` command run from
another shell, and the guard probed its first port until it was restarted.

* ``server_port.server_port`` is the one answer, and the guard's
  standard-library copy (``install_port``) agrees with it on every layout;
* ``port_problem`` refuses a port Kazma cannot have (each reason, with the
  shape of Windows' ``netsh`` answer);
* the install asks once, keeps the answer, takes the next free port with
  ``-y`` when 9090 is taken, refuses a bad ``--port``, and moves an install
  (its guard reloads) without asking again;
* ``kazma setup port`` shows, checks and changes it, reloads through the
  guard, and names what still points at the old port;
* the guard reads the port again before each start (``_follow_port``), and a
  reload follows a moved port;
* no product code writes 9090 except where the default is defined.

Each with its negative control.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core import server_port as sp

# The install's fake Windows machine and its fixture (test_install_modes).
from tests.test_install_modes import box  # noqa: F401 -- a fixture

ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / "scripts" / "service" / "kazma_guard.py"


def _load_guard():
    name = "kazma_guard_for_port_tests"
    spec = importlib.util.spec_from_file_location(name, GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard(monkeypatch):
    module = _load_guard()
    yield module
    sys.modules.pop("kazma_guard_for_port_tests", None)


@pytest.fixture(autouse=True)
def _no_port_in_the_shell(monkeypatch):
    for name in ("KAZMA_PORT", "KAZMA_ENV_FILE", "KAZMA_GUARD_HEALTH_URL", "KAZMA_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)


# -- one answer -------------------------------------------------------------------------------


def test_the_default_is_9090_in_both_copies(guard):
    assert sp.DEFAULT_PORT == guard.DEFAULT_PORT == 9090
    assert guard.DEFAULT_HEALTH_URL == f"http://127.0.0.1:{sp.DEFAULT_PORT}/health/ready"
    assert sp.BROKER_RANGE == (guard.BROKER_PORT_LOW, guard.BROKER_PORT_HIGH)


@pytest.mark.parametrize("files, shell, expected", [
    ({}, None, None),
    ({"install": "KAZMA_PORT=9191\n"}, None, 9191),
    ({"home": "KAZMA_PORT=9292\n", "install": "KAZMA_PORT=9191\n"}, None, 9191),  # the install wins over home
    ({"home": "KAZMA_PORT=9292\n"}, None, 9292),
    ({"install": "KAZMA_PORT=9191\n", "named": "KAZMA_PORT=9393\n"}, None, 9393),  # KAZMA_ENV_FILE wins
    ({"install": "KAZMA_PORT=9191\nKAZMA_PORT=9494\n"}, None, 9494),  # the last line wins
    ({"install": "export KAZMA_PORT='9595' # mine\n"}, None, 9595),
    ({"install": "# KAZMA_PORT=9191\n"}, None, None),
    ({"install": "KAZMA_PORT=not-a-port\n"}, None, None),
    ({"install": "KAZMA_PORT=9191\n"}, "9696", 9696),  # the shell wins
])
def test_the_server_and_the_guard_read_the_same_port(tmp_path, monkeypatch, guard, files, shell, expected):
    home, install, named = tmp_path / "home", tmp_path / "install", tmp_path / "named.env"
    home.mkdir()
    install.mkdir()
    places = {"home": home / ".env", "install": install / ".env", "named": named}
    for where, text in files.items():
        places[where].write_text(text, encoding="utf-8")
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    if "named" in files:
        monkeypatch.setenv("KAZMA_ENV_FILE", str(named))
    if shell:
        monkeypatch.setenv("KAZMA_PORT", shell)
    ladder = [places["home"], places["install"]] + ([named] if "named" in files else [])
    monkeypatch.setattr(guard, "REPO_ROOT", install)
    assert guard._env_files() == ladder
    assert sp.configured_port(ladder) == expected
    assert guard.install_port() == expected
    assert sp.server_port(ladder) == (expected or 9090)


def test_the_ladder_server_port_reads_is_the_server_s_own(monkeypatch, tmp_path):
    """``server_port`` reads ``env_file_ladder(include_cwd=False)``: the
    install's own files, never the launch folder's."""
    from kazma_core import env_files

    elsewhere = tmp_path / "a-cloned-repo"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("KAZMA_PORT=9999\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    assert elsewhere / ".env" not in sp._env_files()
    assert sp._env_files() == env_files.env_file_ladder(include_cwd=False)


# -- whether a port can be Kazma's ---------------------------------------------------------------

NETSH = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      5357        5357
     50000       50059     *
     20181       20780

* - Administered port exclusions.
"""


def test_windows_excluded_ranges_are_read_from_netsh(monkeypatch):
    monkeypatch.setattr(sp.sys, "platform", "win32")
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw.get("stdin")))
        return subprocess.CompletedProcess(argv, 0, stdout=NETSH, stderr="")

    assert sp._excluded_port_ranges(run) == [(5357, 5357), (50000, 50059), (20181, 20780)]
    assert seen[0][0][:3] == ["netsh", "interface", "ipv4"] and seen[0][1] == subprocess.DEVNULL
    monkeypatch.setattr(sp.sys, "platform", "linux")
    assert sp._excluded_port_ranges(run) == [] and len(seen) == 1


@pytest.mark.parametrize("port, words", [
    ("abc", "not a port number"),
    (0, "not a port"),
    (70000, "not a port"),
    (80, "below 1024"),
    (5432, "Postgres"),
    (8088, "SearXNG"),
    (7687, "Neo4j"),
    (2376, "container engine"),
    (24000, "document worker"),
    (50010, "Windows keeps"),
])
def test_each_reason_a_port_cannot_be_kazma_s(port, words):
    why = sp.port_problem(port, ranges=[(50000, 50059)], in_use=lambda n, h: None)
    assert why and words in why, why


def test_a_free_port_passes_and_a_taken_one_is_said():
    assert sp.port_problem(9191, ranges=[], in_use=lambda n, h: None) is None
    assert "listens" in sp.port_problem(9191, ranges=[],
                                        in_use=lambda n, h: "another program already listens on it")
    # The port this install's Kazma holds is its own, not taken.
    assert sp.port_problem(9090, current=9090, ranges=[], in_use=lambda n, h: "taken") is None


def test_a_real_listener_is_found():
    with __import__("socket").socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        if port < 1024 or sp.BROKER_RANGE[0] <= port <= sp.BROKER_RANGE[1]:
            pytest.skip("the system gave a port the rules refuse anyway")
        assert "listens" in (sp.port_problem(port, ranges=[]) or "")
    assert sp.port_problem(port, ranges=[]) is None


def test_first_free_port_skips_every_problem():
    taken = {9091, 9092}
    assert sp.first_free_port(9091, problem=lambda p: "taken" if p in taken else None) == 9093
    with pytest.raises(RuntimeError):
        sp.first_free_port(9091, problem=lambda p: "taken", tries=3)


# -- the install ---------------------------------------------------------------------------------


def _install(box, **kw):
    from kazma_cli.install import install
    from tests.test_install_modes import _machine, _options

    answers = kw.pop("answers", None)
    machine = _machine(box, answers=answers or [], interactive=answers is not None)
    return install(_options(**kw), machine), machine


def _env(box) -> str:
    path = box.home / ".env"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_the_first_install_asks_once_and_keeps_the_answer(box):
    box.busy_ports = {9191}
    code, machine = _install(box, answers=["", "9191", "80", "9292", ""])
    assert code == 0, machine.asked
    assert machine.asked.count("Port for Kazma's page [9090]: ") == 3
    assert "KAZMA_PORT=9292" in _env(box)
    assert box.opened == ["http://127.0.0.1:9292/setup"]
    # A second run never asks again and keeps it.
    code, again = _install(box, answers=[])
    assert code == 0 and again.asked == [] and "KAZMA_PORT=9292" in _env(box)


def test_yes_takes_9090_or_the_next_free_port_and_says_so(box, capsys):
    box.busy_ports = {9090, 9091}
    code, _ = _install(box, yes=True)
    assert code == 0
    assert "KAZMA_PORT=9092" in _env(box)
    assert "Port 9090 cannot be Kazma's" in capsys.readouterr().out


def test_a_bad_port_option_is_refused_before_anything(box, capsys):
    code, _ = _install(box, yes=True, port=8088)
    assert code == 2 and not box.ran and "SearXNG" in capsys.readouterr().out
    assert "KAZMA_PORT" not in _env(box)


def test_a_port_option_moves_an_installed_kazma_and_reloads_it(box, capsys):
    code, _ = _install(box, yes=True)
    assert code == 0 and "KAZMA_PORT=9090" in _env(box)
    box.ran.clear()
    code, _ = _install(box, yes=True, port=9393)
    assert code == 0
    assert "KAZMA_PORT=9393" in _env(box) and "KAZMA_PORT=9090" not in _env(box)
    reloads = [argv for argv in box.ran if "--reload" in argv]
    assert len(reloads) == 1 and "--when-idle" in reloads[0]
    assert "Bookmarks of http://127.0.0.1:9090" in capsys.readouterr().out


@pytest.mark.parametrize("shape", ["guard_state", "answering"])
def test_an_install_from_before_the_record_keeps_9090(box, capsys, shape):
    """An install made before setup recorded anything (its guard's state
    file, or a Kazma answering on 9090) holds 9090: the run must not take it
    for "9090 is taken" and move it."""
    if shape == "guard_state":
        (box.root / ".kazma").mkdir(parents=True, exist_ok=True)
        (box.root / ".kazma" / "guard.state.json").write_text("{}", encoding="utf-8")
    else:
        box.kazma_up = True
    box.busy_ports = {9090}
    code, machine = _install(box, answers=["", ""])
    assert "Port for Kazma's page [9090]: " not in machine.asked
    assert "KAZMA_PORT" not in _env(box)
    said = capsys.readouterr().out
    assert ("--port N" in said) == (shape == "answering")


def test_negative_control_without_the_earlier_install_check_it_moves(box, monkeypatch):
    from kazma_cli import install as inst

    real = inst.choose_for_install
    monkeypatch.setattr(inst, "choose_for_install",
                        lambda *a, **k: real(*a, **{**k, "installed_before": False}))
    box.kazma_up = True
    box.busy_ports = {9090}
    _install(box, yes=True)
    assert "KAZMA_PORT=9091" in _env(box), "the old rule moves an install it should have kept"


def test_negative_control_without_the_port_step_the_choice_is_lost(box, monkeypatch):
    from kazma_cli import install as inst

    monkeypatch.setattr(inst, "_keep_port", lambda *a, **k: False)
    _install(box, answers=["", "9191", ""])
    assert "KAZMA_PORT" not in _env(box)


# -- kazma setup port ------------------------------------------------------------------------------


def _port_options(port=None, check=False, no_reload=False):
    return argparse.Namespace(port=port, check=check, no_reload=no_reload)


def _run_port(tmp_path, options, *, busy=(), guard_runs=False, reload_code=0, configs=()):
    from kazma_cli import port_setup

    env_path = tmp_path / ".env"
    root = tmp_path / "install"
    (root / "scripts" / "service").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "service" / "kazma_guard.py").write_text("", encoding="utf-8")
    said: list[str] = []
    reloads: list[list[str]] = []

    def problem(port, current=None):
        return sp.port_problem(port, current=current, ranges=[],
                               in_use=lambda n, h: "another program already listens on it" if n in busy else None)

    def reload(argv):
        reloads.append(argv)
        return reload_code

    code = port_setup.run(options, env_path=env_path, root=root, reload=reload, say=said.append,
                          problem=problem, configs=list(configs), guard_runs=lambda r: guard_runs)
    return code, said, reloads, env_path


def test_setup_port_shows_checks_and_changes(tmp_path, monkeypatch):
    code, said, _, _ = _run_port(tmp_path, _port_options())
    assert code == 0 and "port 9090, from the default" in said[0]
    code, said, _, env = _run_port(tmp_path, _port_options(8088, check=True))
    assert code == 2 and "SearXNG" in said[0] and not env.exists()
    code, said, _, env = _run_port(tmp_path, _port_options(9191, check=True))
    assert code == 0 and not env.exists()
    cloudflared = tmp_path / "config.yml"
    cloudflared.write_text("ingress:\n  - service: http://localhost:9090\n", encoding="utf-8")
    code, said, reloads, env = _run_port(tmp_path, _port_options(9191), guard_runs=True, configs=[cloudflared])
    assert code == 0 and "KAZMA_PORT=9191" in env.read_text(encoding="utf-8")
    assert len(reloads) == 1 and reloads[0][-2:] == ["--reload", "--when-idle"]
    assert any(str(cloudflared) in line for line in said)
    code, said, reloads, _ = _run_port(tmp_path, _port_options(9191), guard_runs=True)
    assert code == 0 and not reloads and "already uses port 9191" in said[-1]


def test_setup_port_on_the_default_writes_it_down_and_reloads_nothing(tmp_path):
    code, said, reloads, env = _run_port(tmp_path, _port_options(9090), guard_runs=True)
    assert code == 0 and not reloads and "KAZMA_PORT=9090" in env.read_text(encoding="utf-8")


def test_setup_port_without_a_guard_waits_for_the_next_start(tmp_path):
    code, said, reloads, env = _run_port(tmp_path, _port_options(9191), guard_runs=False)
    assert code == 0 and not reloads and "next start" in said[-1]


def test_setup_port_refuses_when_the_shell_sets_another(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_PORT", "9595")
    code, said, _, env = _run_port(tmp_path, _port_options(9191))
    assert code == 2 and "shell's environment" in said[0] and not env.exists()


def test_setup_port_is_a_kazma_setup_command(tmp_path, monkeypatch):
    from kazma_cli import port_setup, setup

    got = []
    monkeypatch.setattr(port_setup, "run", lambda options: got.append(options) or 0)
    assert setup.run(["port", "9191", "--check"]) == 0
    assert got[0].port == 9191 and got[0].check


# -- the guard follows the port -------------------------------------------------------------------


def test_the_guard_reads_the_port_again_before_each_start(guard, tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("KAZMA_USER_HOME", str(tmp_path / "home"))
    states: list[dict] = []
    monkeypatch.setattr(guard, "_update_state", lambda **f: states.append(f))
    logged: list[str] = []
    fake = type("G", (), {})()
    fake.health_url = guard.health_url()
    fake.log = lambda level, event, **kw: logged.append(event)
    assert fake.health_url.endswith(":9090/health/ready")
    (tmp_path / ".env").write_text("KAZMA_PORT=9191\n", encoding="utf-8")
    guard.Guard._follow_port(fake)
    assert fake.health_url == "http://127.0.0.1:9191/health/ready"
    assert states == [{"health_url": fake.health_url}] and logged == ["guard.port_changed"]
    guard.Guard._follow_port(fake)
    assert len(states) == 1, "an unchanged port writes nothing"


def test_the_guard_follows_the_port_in_its_spawn_loop():
    source = GUARD_PATH.read_text(encoding="utf-8")
    loop = source.split("clear_stale_port(self.health_url, self.log)", 1)[0]
    assert loop.rstrip().endswith("self._follow_port()") or "self._follow_port()" in loop[-600:]


def test_a_reload_follows_the_port_the_guard_probes(guard, monkeypatch):
    monkeypatch.setattr(guard, "_read_state", lambda: {"health_url": "http://127.0.0.1:9191/health/ready"})
    monkeypatch.setattr(guard, "_guard_alive", lambda: True)
    assert guard._recorded_health("http://127.0.0.1:9090/health/ready").endswith(":9191/health/ready")
    monkeypatch.setattr(guard, "_guard_alive", lambda: False)
    assert guard._recorded_health("http://127.0.0.1:9090/health/ready").endswith(":9090/health/ready")


def test_the_broker_never_takes_kazma_s_port(guard, monkeypatch):
    monkeypatch.setenv("KAZMA_PORT", "24000")
    monkeypatch.setattr(guard, "_port_is_free", lambda port: True)
    monkeypatch.delenv("KAZMA_GUARD_BROKER_PORT", raising=False)
    assert guard._choose_broker_port(24000) != 24000


# -- no 9090 written out in product code --------------------------------------------------------------

#: Where the default may be written: its two definitions (the guard is
#: standard-library only and keeps a copy, held equal above). Docstrings and
#: comments may name it; a string or number in code may not.
_ALLOWED = {
    "kazma-core/kazma_core/server_port.py",
    "scripts/service/kazma_guard.py",  # DEFAULT_PORT and its docstrings
    # The retired ui.port's old shipped value: history, never today's default.
    "kazma-core/kazma_core/config_defaults.py",
}
_LITERAL = re.compile(r"(?<![\w.])9090(?![\w])")


def _value_tokens():
    import tokenize

    kinds = {tokenize.NUMBER, tokenize.STRING}
    middle = getattr(tokenize, "FSTRING_MIDDLE", None)  # Python 3.12: an f-string's text
    if middle is not None:
        kinds.add(middle)
    return kinds


_VALUE_TOKENS = _value_tokens()


def _code_lines(path: Path):
    import ast
    import io
    import tokenize

    text = path.read_text(encoding="utf-8")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return
    docstrings = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            docstrings.add(body[0].value.lineno)
    for tok in tokens:
        if tok.type in _VALUE_TOKENS and _LITERAL.search(tok.string):
            if tok.type == tokenize.STRING and tok.start[0] in docstrings:
                continue
            yield tok.start[0], tok.string


def _offenders(files) -> list[str]:
    found = []
    for rel in files:
        path = ROOT / rel
        if rel.replace("\\", "/") in _ALLOWED or not path.is_file():
            continue
        for line, text in _code_lines(path):
            found.append(f"{rel}:{line}: {text[:80]}")
    return found


def _product_python() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL).stdout.split()
    keep = ("kazma-core/", "kazma-cli/", "kazma-ui/", "kazma-gateway/", "kazma-skills/", "kazma-tui/", "serve.py",
            "scripts/service/")
    return [f for f in out if f.startswith(keep) and "/tests/" not in f]


def test_no_product_code_writes_9090_out():
    offenders = _offenders(_product_python())
    assert not offenders, "ask kazma_core.server_port instead:\n" + "\n".join(offenders)


def test_negative_control_a_planted_9090_is_found(tmp_path, monkeypatch):
    planted = ROOT / "kazma-core" / "kazma_core" / "_planted_port_probe.py"
    planted.write_text('URL = "http://127.0.0.1:9090/health"\n', encoding="utf-8")
    try:
        assert _offenders(["kazma-core/kazma_core/_planted_port_probe.py"])
    finally:
        planted.unlink()
