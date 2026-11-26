"""Kazma works with Windows' file check (Smart App Control), and says so calmly.

Live 2026-11-26: Windows began refusing unsigned files on the owner's PC
(``WinError 4551``): Kazma's engine's docker.exe and psycopg's bundled client
library. ``kazma_core.windows_trust`` knows, quietly, which native parts
Windows allows, which ones Kazma replaced with a signed alternative, and
which feature waits for a signed version; ``kazma doctor``, ``kazma setup``,
the security report and the boot read it. The one alert
(``windows.feature_waits``) is raised only when no alternative kept a feature
working.

The owner's rule for every word (2026-11-26) is a gate here too: nothing
Kazma says about Windows' file check suggests turning a protection off or
reads as a flaw, in English or Arabic, read from the real source (the
words table, the guard's card, the fallbacks' log lines, the docs pages);
a planted sentence is the negative control.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core import docker_cli, libpq_reach, windows_policy, windows_trust
from kazma_core.windows_trust import PartState, TrustReport

REPO = Path(__file__).resolve().parents[1]

_ARABIC = re.compile(r"[\u0600-\u06FF]")


# ── Windows' own setting ─────────────────────────────────────────────────


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_winreg(value):
    def query(key, name):
        assert name == "VerifiedAndReputablePolicyState"
        if value is None:
            raise FileNotFoundError(2, "no value")
        return value, 4

    return SimpleNamespace(HKEY_LOCAL_MACHINE=object(), OpenKey=lambda *a: _Key(), QueryValueEx=query)


@pytest.mark.parametrize(("value", "state"), [(0, "off"), (1, "on"), (2, "evaluation"), (None, None), (7, None)])
def test_the_setting_is_read_from_windows_own_policy(monkeypatch, value, state):
    monkeypatch.setattr(windows_policy, "_on_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(value))
    assert windows_trust.smart_app_control() == state
    assert windows_policy.checks_files() is (state == "on")


def test_elsewhere_than_windows_nothing_is_read_or_probed(monkeypatch):
    monkeypatch.setattr(windows_trust, "_on_windows", lambda: False)
    monkeypatch.setitem(windows_trust._PROBES, "database_client", lambda: pytest.fail("probed"))
    report = windows_trust.check()
    assert report.smart_app_control is None and report.parts == ()
    assert windows_trust.lines(report) == []


# ── the database client library ──────────────────────────────────────────


@pytest.fixture
def libpq(tmp_path, monkeypatch):
    """psycopg's bundled pq and libpq, and an installed PostgreSQL's libpq."""
    pyd = tmp_path / "pq.cp314-win_amd64.pyd"
    dll = tmp_path / "libpq-abc.dll"
    pg = tmp_path / "PostgreSQL" / "18" / "bin"
    pg.mkdir(parents=True)
    (pg / "libpq.dll").write_bytes(b"")
    refused: set[str] = set()
    monkeypatch.setattr(libpq_reach, "bundled_files", lambda: [pyd, dll])
    monkeypatch.setattr(libpq_reach, "postgres_libpq_folders", lambda: [pg])
    monkeypatch.setattr(libpq_reach, "added_folder", lambda: None)
    monkeypatch.setattr(libpq_reach, "preferred", lambda: False)
    monkeypatch.setattr(libpq_reach, "load_error", lambda p: 4551 if p.name in refused else None)
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    return SimpleNamespace(refused=refused, pg=pg)


def test_an_allowed_client_library_is_allowed(libpq):
    assert windows_trust._database_client().status == windows_trust.ALLOWED


def test_a_refused_client_library_with_postgres_installed_uses_the_signed_one(libpq):
    libpq.refused.add("pq.cp314-win_amd64.pyd")
    state = windows_trust._database_client()
    assert state.status == windows_trust.ALTERNATIVE
    assert state.alternative == str(libpq.pg / "libpq.dll")
    assert state.files == ("pq.cp314-win_amd64.pyd",) and "4551" in state.detail


def test_a_refused_client_library_with_no_signed_one_waits(libpq):
    libpq.refused.update({"pq.cp314-win_amd64.pyd", "libpq.dll"})  # PostgreSQL's refused too
    state = windows_trust._database_client()
    assert state.status == windows_trust.WAITS and state.alternative == ""


def test_the_signed_library_chosen_up_front_is_said_as_preferred(libpq, monkeypatch):
    monkeypatch.setattr(libpq_reach, "preferred", lambda: True)
    monkeypatch.setattr(libpq_reach, "added_folder", lambda: libpq.pg)
    monkeypatch.setattr(libpq_reach, "load_error", lambda p: pytest.fail("nothing is loaded to say so"))
    state = windows_trust._database_client()
    assert state.status == windows_trust.PREFERRED and state.alternative == str(libpq.pg / "libpq.dll")


# ── Kazma's docker CLI ───────────────────────────────────────────────────


@pytest.fixture
def engine_cli(tmp_path, monkeypatch):
    own = tmp_path / "engine" / "bin" / "docker.exe"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"")
    monkeypatch.setattr(docker_cli, "kazma_engine_dir", lambda: tmp_path / "engine")
    monkeypatch.setattr(windows_trust, "_checks_files", lambda: False)
    return own


def test_a_refused_engine_cli_with_docker_desktops_uses_the_signed_one(engine_cli, monkeypatch):
    desktop = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    monkeypatch.setattr(docker_cli, "refused_by_os", lambda p: "WinError 4551: refused")
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: desktop)
    state = windows_trust._docker_cli()
    assert state.status == windows_trust.ALTERNATIVE and state.alternative == desktop


def test_a_refused_engine_cli_and_no_other_waits(engine_cli, monkeypatch):
    monkeypatch.setattr(docker_cli, "refused_by_os", lambda p: "WinError 4551: refused")
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: None)
    assert windows_trust._docker_cli().status == windows_trust.WAITS


def test_while_windows_checks_files_docker_desktops_signed_cli_is_preferred(engine_cli, monkeypatch):
    desktop = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    monkeypatch.setattr(windows_trust, "_checks_files", lambda: True)
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: desktop)
    monkeypatch.setattr(docker_cli, "refused_by_os", lambda p: pytest.fail("the unsigned CLI was started"))
    state = windows_trust._docker_cli()
    assert state.status == windows_trust.PREFERRED and state.alternative == desktop


def test_control_an_allowed_engine_cli_is_allowed(engine_cli, monkeypatch):
    monkeypatch.setattr(docker_cli, "refused_by_os", lambda p: None)
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: pytest.fail("no alternative needed"))
    assert windows_trust._docker_cli().status == windows_trust.ALLOWED


# ── the engine's shutdown service ────────────────────────────────────────


def _sc(state: str, code: int):
    text = (f"SERVICE_NAME: KazmaEngineShutdown\n        STATE              : 1  {state}\n"
            f"        WIN32_EXIT_CODE    : {code}  (0x{code:x})\n")
    return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=text.encode(), stderr=b"")


def test_a_shutdown_service_windows_refused_is_read_from_its_last_start():
    from kazma_core.container_engine_setup import shutdown_service_refusal

    assert shutdown_service_refusal(_sc("STOPPED", 4551)) == 4551
    assert shutdown_service_refusal(_sc("RUNNING", 0)) is None
    assert shutdown_service_refusal(_sc("STOPPED", 1067)) is None  # stopped for another reason


# ── what is said, and the one alert ──────────────────────────────────────


def _report(*parts: PartState, sac: str | None = "on") -> TrustReport:
    return TrustReport(sac, tuple(parts))


def test_every_phrase_is_in_english_and_arabic():
    for key, pair in windows_trust.WORDS.items():
        assert set(pair) == {"en", "ar"}, key
        assert pair["en"].strip() and _ARABIC.search(pair["ar"]), key
        assert re.findall(r"{\w+}", pair["en"]) == re.findall(r"{\w+}", pair["ar"]) or \
            sorted(re.findall(r"{\w+}", pair["en"])) == sorted(re.findall(r"{\w+}", pair["ar"])), key
    for part in windows_trust.PARTS:
        assert all(part.name) and all(part.feature), part.id
        assert _ARABIC.search(part.feature[1]), part.id


def test_smart_app_control_on_is_good_news_and_an_alternative_is_not_a_problem():
    report = _report(PartState("database_client", windows_trust.ALTERNATIVE, ("pq.pyd",), r"C:\PG\libpq.dll"))
    shown = windows_trust.lines(report)
    assert [s for s, _ in shown] == ["state", "alternative"]
    assert "Kazma works with it" in shown[0][1]
    assert "signed alternative" in shown[1][1] and "Everything works" in shown[1][1]
    ar = windows_trust.lines(report, "ar")
    assert all(_ARABIC.search(text) for _, text in ar)


def test_a_part_not_on_this_machine_is_not_mentioned():
    shown = windows_trust.lines(_report(PartState("compose", windows_trust.ABSENT), sac=None))
    assert shown == []


def test_setup_mentions_only_what_is_news():
    quiet = _report(PartState("database_client", windows_trust.ALLOWED), sac="off")
    assert windows_trust.notes(quiet) == []
    busy = _report(PartState("docker_cli", windows_trust.WAITS, ("docker.exe",)), sac="evaluation")
    assert len(windows_trust.notes(busy)) == 2


@pytest.fixture
def alerts(monkeypatch):
    sent: list[tuple] = []
    from kazma_core.observability import ops_alerts

    monkeypatch.setattr(ops_alerts, "alert", lambda *a, **k: sent.append((a, k)) or True)
    return sent


def test_a_feature_with_no_signed_alternative_raises_the_one_alert(monkeypatch, alerts, caplog):
    monkeypatch.setattr(windows_trust, "check", lambda: _report(
        PartState("docker_cli", windows_trust.WAITS, ("docker.exe",), detail="WinError 4551: refused"),
        PartState("database_client", windows_trust.ALTERNATIVE, ("pq.pyd",), r"C:\PG\libpq.dll")))
    caplog.set_level(logging.INFO, logger=windows_trust.__name__)
    windows_trust.report_at_boot()
    assert len(alerts) == 1
    (key, title, detail), kw = alerts[0]
    assert key == "windows.feature_waits" and kw["severity"] == "warn"
    assert "Running code and containers" in detail and "nothing is wrong with the PC" in detail
    assert "database" not in detail, "a part with an alternative is not in the alert"
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "Running code and containers" in warned[0].getMessage()


def test_control_alternatives_alone_raise_no_alert_and_say_one_info_line(monkeypatch, alerts, caplog):
    monkeypatch.setattr(windows_trust, "check", lambda: _report(
        PartState("database_client", windows_trust.ALTERNATIVE, ("pq.pyd",), r"C:\PG\libpq.dll")))
    caplog.set_level(logging.INFO, logger=windows_trust.__name__)
    windows_trust.report_at_boot()
    assert alerts == []
    said = [r for r in caplog.records if r.name == windows_trust.__name__]
    assert [r.levelno for r in said] == [logging.INFO]


def test_the_alert_is_catalogued_as_the_only_warning():
    from kazma_core.observability.alert_catalog import entry_for

    entry = entry_for("windows.feature_waits")
    assert entry is not None and entry.severity == "warn" and _ARABIC.search(entry.title_ar)
    reload_waits = entry_for("guard.reload_waits")
    assert reload_waits is not None and reload_waits.severity == "info"


# ── the surfaces ─────────────────────────────────────────────────────────


def test_doctor_marks_an_alternative_as_information_and_a_wait_as_a_warning(monkeypatch):
    from kazma_cli import doctor

    monkeypatch.setattr(windows_trust, "check", lambda: _report(
        PartState("database_client", windows_trust.ALTERNATIVE, ("pq.pyd",), r"C:\PG\libpq.dll"),
        PartState("docker_cli", windows_trust.WAITS, ("docker.exe",)),
        PartState("engine_shutdown", windows_trust.ALLOWED, ("x.exe",))))
    rows = doctor._windows_rows()
    assert [status for status, _ in rows] == [doctor.INFO, doctor.INFO, doctor.WARN, doctor.OK]
    monkeypatch.setattr(windows_trust, "check", lambda: _report(
        PartState("database_client", windows_trust.PREFERRED, ("pq.pyd",), r"C:\PG\libpq.dll")))
    assert [status for status, _ in doctor._windows_rows()] == [doctor.INFO, doctor.INFO]
    assert doctor.BAD not in {s for s, _ in rows}, "Windows' file check never fails the doctor"


def test_the_security_report_shows_it_as_information_never_a_failure(monkeypatch):
    import asyncio

    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(windows_trust, "check", lambda: _report(
        PartState("docker_cli", windows_trust.WAITS, ("docker.exe",))))
    result = asyncio.run(SecurityHardeningRunner(REPO).run_check("check_windows_file_check"))
    assert result.passed is True and result.severity == "info"
    assert "Kazma works with it" in result.message


def test_setup_install_notes_it_on_windows(monkeypatch):
    from kazma_cli import install

    said: list[str] = []
    machine = install.Machine(file_check=lambda: _report(
        PartState("database_client", windows_trust.ALTERNATIVE, ("pq.pyd",), r"C:\PG\libpq.dll")))
    install._say_file_check(SimpleNamespace(os="windows"), machine, lambda text, *a: said.append(text))
    assert len(said) == 2 and all(line.startswith("Note: ") for line in said)
    said.clear()
    install._say_file_check(SimpleNamespace(os="linux"), machine, lambda text, *a: said.append(text))
    assert said == []


# ── every binary Kazma installs itself is checked ────────────────────────


def _unprobed(downloads: dict) -> list[str]:
    known = set(windows_trust.DOWNLOAD_PARTS) | set(windows_trust.NOT_PROBED)
    return sorted(set(downloads) - known)


def test_every_binary_the_engine_setup_installs_is_a_part_or_declared():
    from kazma_core import container_engine_setup as ces

    assert _unprobed(ces.DOWNLOADS) == []
    ids = {part.id for part in windows_trust.PARTS}
    assert set(windows_trust.DOWNLOAD_PARTS.values()) <= ids
    assert {"database_client", "engine_shutdown"} <= ids  # bundled by psycopg; built by setup
    assert set(windows_trust._PROBES) == ids


def test_control_a_new_download_is_caught():
    from kazma_core import container_engine_setup as ces

    assert _unprobed({**ces.DOWNLOADS, "restic": object()}) == ["restic"]


# ── the words: never "turn it off", never a flaw (EN and AR) ─────────────

_NEVER_EN = ("turn off", "turn it off", "turning it off", "disable", "switch off", "allow it again",
             "security risk", "unsafe", "blocked", "smartscreen", "defender")
_NEVER_AR = ("تعطيل", "عطّل", "عطل ", "أوقف التحكم", "إيقاف التحكم", "غير آمن", "خطر", "محظور")


def _guard():
    spec = importlib.util.spec_from_file_location("_trust_guard", REPO / "scripts" / "service" / "kazma_guard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _log_strings(path: Path, functions: set[str] | None = None) -> list[str]:
    """Every string a logger call in *path* says (its literals joined), in
    the named functions only when given."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or (functions and fn.name not in functions):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
                text = "".join(c.value for a in node.args for c in ast.walk(a)
                               if isinstance(c, ast.Constant) and isinstance(c.value, str))
                out.append(text)
    return out


def _strings_in(path: Path, function: str) -> list[str]:
    """The string literals of *function* in *path*, joined per call (a card)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == function:
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    out.append("".join(c.value for a in call.args for c in ast.walk(a)
                                       if isinstance(c, ast.Constant) and isinstance(c.value, str)))
    assert out, f"{function} not found in {path.name}"
    return out


def _what_kazma_says() -> list[str]:
    said = [text for pair in windows_trust.WORDS.values() for text in pair.values()]
    said += [name for part in windows_trust.PARTS for name in (*part.name, *part.feature)]
    guard = _guard()
    for step in ("python", "kazma_core", "psycopg", "app", "docker", "timeout"):
        for detail in ("ImportError: DLL load failed: An Application Control policy has blocked this file.",
                       "OSError"):
            said += list(guard.preflight_words({"step": step, "detail": detail}))
    core = REPO / "kazma-core" / "kazma_core"
    said += _log_strings(core / "libpq_reach.py", {"reach_libpq"})
    said += _log_strings(core / "docker_cli.py", {"_usable"})
    said += _log_strings(core / "windows_trust.py")
    said += _strings_in(REPO / "scripts" / "service" / "kazma_guard.py", "_cli_waits")
    for page in _docs_pages():
        said.append(page.read_text(encoding="utf-8"))
    # The operator's detail quotes Windows itself ("has blocked this file").
    return [text.split("Detail:")[0] for text in said]


def _docs_pages() -> list[Path]:
    return sorted(REPO.glob("docs/docs/**/windows-smart-app-control*.md"))


def _offending(texts: list[str]) -> list[str]:
    return [text[:120] for text in texts
            if any(w in text.lower() for w in _NEVER_EN) or any(w in text for w in _NEVER_AR)]


def test_nothing_kazma_says_about_it_suggests_turning_a_protection_off():
    texts = _what_kazma_says()
    assert len(texts) > 40, "the sources were not read"
    assert _docs_pages(), "the docs pages (English and Arabic) are read too"
    assert _offending(texts) == []


def test_control_a_planted_sentence_is_found_in_either_language():
    assert _offending(["To use it, turn off Smart App Control."]) != []
    assert _offending(["عطّل التحكم الذكي في التطبيقات لاستخدامه."]) != []
    assert _offending(["Kazma uses the signed alternative."]) == []


# ── one definition of Windows' refusal codes (the audit's F6/F7) ─────────

def _product_sources(*patterns: str) -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", *patterns], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout.split()
    return {name: (REPO / name).read_text(encoding="utf-8", errors="replace") for name in listed
            if "/tests/" not in f"/{name}" and (REPO / name).is_file()}


def _copies_of_refusal_codes(sources: dict[str, str]) -> list[str]:
    """Where a product file spells Windows' refusal codes itself (1260, 4551)
    instead of reading ``windows_policy.REFUSAL_CODES``."""
    hits = []
    for name, text in sources.items():
        if name.endswith("kazma_core/windows_policy.py"):
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and type(node.value) is int and node.value in (1260, 4551):
                hits.append(f"{name}:{node.lineno}")
    return hits


def test_windows_refusal_codes_have_one_definition():
    sources = _product_sources("*.py")
    assert len(sources) > 500, "the sources were not read"
    assert _copies_of_refusal_codes(sources) == []
    assert windows_policy.REFUSAL_CODES == frozenset({5, 1260, 4551})
    assert libpq_reach.REFUSALS is windows_policy.REFUSAL_CODES
    assert docker_cli.refusal_codes() == windows_policy.REFUSAL_CODES


def test_control_a_second_copy_is_found():
    assert _copies_of_refusal_codes({"scripts/x.py": "R = frozenset({5, 1260, 4551})\n"}) == [
        "scripts/x.py:1", "scripts/x.py:1"]
    assert _copies_of_refusal_codes({"kazma-core/kazma_core/windows_policy.py": "R = {4551}\n"}) == []


def test_prepush_finds_docker_the_way_kazma_does(monkeypatch):
    """prepush kept a copy of the lookup (no winerror check, no
    KAZMA_DOCKER_BIN): it now loads docker_cli.py by path, as the guard does."""
    spec = importlib.util.spec_from_file_location("_trust_prepush", REPO / "scripts" / "prepush.py")
    prepush = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prepush
    spec.loader.exec_module(prepush)
    assert not hasattr(prepush, "_docker_candidates")
    module = prepush._docker_cli_module()
    assert Path(module.__file__).resolve() == (REPO / "kazma-core" / "kazma_core" / "docker_cli.py").resolve()
    monkeypatch.setattr(module, "find_docker_cli", lambda: r"C:\signed\docker.exe")
    monkeypatch.setattr(prepush, "_DOCKER", [])
    assert prepush.docker_exe() == r"C:\signed\docker.exe"
    monkeypatch.setattr(module, "find_docker_cli", lambda: None)
    monkeypatch.setattr(prepush, "_DOCKER", [])
    assert prepush.docker_exe() == "docker"


# ── Kazma never starts its own launchers (the audit's F8-F10) ────────────

def _launcher_names() -> list[str]:
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    return sorted(f"{name}.exe" for name in scripts)


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _launcher_uses(sources: dict[str, str], names: list[str]) -> list[str]:
    """Where Kazma's code or scripts would start a console-script launcher
    (made on the user's PC, never signed) instead of ``python -m``: a string
    a program runs, or a script line that is not a comment."""
    hits = []
    for name, text in sources.items():
        if name.endswith(".py"):
            tree = ast.parse(text)
            skip = _docstrings(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                        and any(exe in node.value.lower() for exe in names)):
                    hits.append(f"{name}:{node.lineno}")
        else:
            for number, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0] if name.endswith((".ps1", ".sh")) else line
                if not line.lstrip().startswith((";", "//", "REM", "rem", "::")) and \
                        any(exe in code.lower() for exe in names):
                    hits.append(f"{name}:{number}")
    return hits


def test_kazma_never_starts_its_own_launchers():
    names = _launcher_names()
    assert "kazma.exe" in names and len(names) >= 3
    sources = _product_sources("kazma-*/*.py", "kazma-*/**/*.py", "scripts/*.py", "scripts/**/*.py",
                               "*.ps1", "*.cmd", "scripts/**/*.iss", "scripts/**/*.ps1")
    assert any(n.endswith(".ps1") for n in sources) and any(n.endswith(".iss") for n in sources)
    assert _launcher_uses(sources, names) == []


def test_control_a_launcher_started_by_name_is_found():
    names = _launcher_names()
    planted = {
        "kazma-cli/kazma_cli/x.py": 'import subprocess\nsubprocess.run(["kazma.exe", "serve"])\n',
        "setup.ps1": "& (Join-Path $ScriptsDir 'kazma-tui.exe')\n# kazma.exe in a comment is fine\n",
    }
    assert _launcher_uses(planted, names) == ["kazma-cli/kazma_cli/x.py:2", "setup.ps1:1"]
