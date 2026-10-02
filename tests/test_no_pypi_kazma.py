"""Nothing installs Kazma, or tells anyone to install it, by name from PyPI.

No Kazma package is published on PyPI: ``kazma``, ``kazma-core``, ``kazma-ui``,
``kazma-cli``, ``kazma-gateway``, ``kazma-skills``, ``kazma-tui`` and
``kazma-memory`` all answered 404 there on 2026-09-30, so each is anyone's name
to register. Kazma ships as a git checkout and as GitHub release wheels. Found
that day:

* ``kazma update`` on a wheel install asked PyPI for the newest version and
  then had pip upgrade ``kazma`` by name -- whoever registered the name would
  have run code on every such update;
* the Settings update check read PyPI's ``kazma`` (its running version was a
  hard-coded "0.5.0", and when PyPI failed it said the install was current);
* twelve hints -- error messages, a tool result the model relays, the swarm
  page, the CLI, eight docs pages -- told the reader to install ``kazma[web]``,
  ``kazma[durable]`` or ``kazma-core[swarm]`` (an extra that never existed) by
  name.

The gate reads every tracked product, script and doc file for an install
command naming a Kazma distribution, and every Python file's AST for an install
argv built from the package name; negative controls feed it each old form. The
behavioural half runs the updater and the Settings check against a fake GitHub.

Since 2026-10-01 the project holds these names on PyPI with reservations
(``scripts/pypi_reserve.py``, uploaded by ``.github/workflows/pypi-reserve.yml``):
version 0.0.1 of each, with no code, only a page pointing at the GitHub
releases. Kazma still installs only from GitHub, so every rule above holds: an
install by name from PyPI would get a reservation, not Kazma. The reservation
list and ``KAZMA_DISTS`` are held equal below, so a new package name is
reserved with the rest.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import re
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]

KAZMA_DISTS = frozenset({
    "kazma", "kazma-core", "kazma-ui", "kazma-cli", "kazma-gateway",
    "kazma-skills", "kazma-tui", "kazma-memory",
})

# History, working notes, and the tests that quote the old forms.
_EXCLUDED_PREFIXES = ("tests/", "docs/audits/", "docs/plans/")
_EXCLUDED_FILES = frozenset({"CHANGELOG.md"})
_TEXT_SUFFIXES = frozenset({
    ".py", ".js", ".mjs", ".html", ".md", ".mdx", ".yaml", ".yml", ".toml",
    ".txt", ".ps1", ".sh", ".cfg", ".ini", ".example", ".j2", ".bat", ".cmd",
})
_TEXT_NAMES = frozenset({"Dockerfile", "Makefile", "Procfile"})

_INSTALL_VERB = re.compile(
    r"\b(?:(?:python3?|py)\s+-m\s+pip|pip3?|uv\s+pip|pipx)\s+install\b"
    r"|\b(?:uv|poetry)\s+add\b"
)
# A command ends at a shell separator or a markdown code span's end.
_SEGMENT_END = re.compile(r"[;|`]|&&")
# Flags whose next token is a value (a path, an index, an interpreter),
# never a requirement.
_VALUE_FLAGS = frozenset({
    "-e", "--editable", "-r", "--requirement", "-c", "--constraint",
    "-f", "--find-links", "-i", "--index-url", "--extra-index-url",
    "--python", "-p", "-t", "--target", "--prefix", "--root", "--platform",
    "--python-version", "--implementation", "--abi", "--src", "--cache-dir",
})
_PYPI_URL = re.compile(r"pypi\.org/(?:pypi|project|simple)/kazma(?:[-_][a-z]+)?\b", re.I)
_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _dist_name(token: str, *, fold_case: bool) -> str:
    """``'kazma[web]'`` / ``kazma==0.11`` / ``kazma_core`` -> the name pip would fetch.

    Empty for a path (``kazma-core/``, ``./dist/kazma-...whl``, ``.[web]``).
    """
    token = token.strip("'\"`()<>,:.")
    match = _REQUIREMENT_NAME.match(token)
    if not match or token[match.end():match.end() + 1] in ("/", "\\"):
        return ""
    name = re.sub(r"[._]+", "-", match.group(1))
    return name.lower() if fold_case else name


def bare_installs(text: str) -> list[str]:
    """Each install command in *text* that names a Kazma distribution."""
    hits: list[str] = []
    for line in text.splitlines():
        for url in _PYPI_URL.finditer(line):
            hits.append(url.group(0))
        for verb in _INSTALL_VERB.finditer(line):
            rest = line[verb.end():]
            end = _SEGMENT_END.search(rest)
            tokens = (rest[: end.start()] if end else rest).split()
            skip = False
            for i, token in enumerate(tokens):
                bare = token.strip("'\"`")
                if skip:
                    skip = False
                    continue
                if bare.split("=", 1)[0] in _VALUE_FLAGS:
                    skip = "=" not in bare
                    continue
                if bare.startswith("-") or bare == "@":
                    continue
                if i + 1 < len(tokens) and tokens[i + 1] == "@":
                    continue  # ``name @ URL``: a direct reference, not the index
                # Case kept: prose ("... in the Kazma folder") names the
                # product, a command names the lower-case distribution.
                if _dist_name(bare, fold_case=False) in KAZMA_DISTS:
                    hits.append(line.strip())
                    break
    return hits


def install_argv_names(tree: ast.AST) -> list[int]:
    """Lines where an install argv (``["install", ..., PACKAGE_NAME]``) names Kazma."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        consts = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if not consts & {"install", "add"}:
            continue
        for elt in node.elts:
            names = [elt] if isinstance(elt, ast.Name) else []
            if isinstance(elt, ast.JoinedStr):
                names = [v.value for v in elt.values if isinstance(v, ast.FormattedValue)]
            if any(isinstance(n, ast.Name) and n.id == "PACKAGE_NAME" for n in names) or (
                isinstance(elt, ast.Constant)
                and isinstance(elt.value, str)
                and _dist_name(elt.value, fold_case=True) in KAZMA_DISTS
            ):
                lines.append(node.lineno)
                break
    return lines


def _tracked_text_files() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    out = []
    for rel in files:
        path = Path(rel)
        if rel in _EXCLUDED_FILES or rel.startswith(_EXCLUDED_PREFIXES):
            continue
        if path.suffix not in _TEXT_SUFFIXES and path.name not in _TEXT_NAMES and not path.name.startswith(".env"):
            continue
        if (REPO / rel).is_file():
            out.append(rel)
    return out


def test_no_file_installs_kazma_by_name():
    files = _tracked_text_files()
    # The gate reads what it claims: every site the 2026-09-30 sweep fixed.
    for known in (
        "kazma-cli/kazma_cli/update.py",
        "kazma-cli/kazma_cli/main.py",
        "kazma-core/kazma_core/settings_manager.py",
        "kazma-core/kazma_core/tools/read_url.py",
        "kazma-ui/kazma_ui/swarm_panel/routes_general.py",
        "docs/docs/guide/knowledge-library.md",
        "docs/SUPPLY_CHAIN.md",
    ):
        assert known in files, known
    problems = []
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        problems += [f"{rel}: {hit}" for hit in bare_installs(text)]
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            problems += [f"{rel}:{n}: install argv names Kazma" for n in install_argv_names(tree)]
    assert not problems, (
        "No Kazma package is published on PyPI, so every Kazma name there is "
        "anyone's to register. Install an extra from the Kazma folder "
        "(kazma_core.install_hint.extra_install_hint) and a release wheel from "
        "GitHub (kazma_cli.update.do_pip_update):\n  " + "\n  ".join(problems)
    )


@pytest.mark.parametrize("old", [
    """print("Install with: pip install 'kazma[ui]' or pip install jinja2 python-multipart")""",
    '''"Install with: pip install kazma-core[swarm] "''',
    "Install: <code>pip install kazma-core[swarm]</code>",
    """"(`pip install 'kazma[web]'` + browsers).\"""",
    'return "playwright package not installed (pip install kazma[web])"',
    "``pip install 'kazma[durable]'``. Kill-switch ``KAZMA_TEMPORAL=0``.",
    'resp = httpx.get("https://pypi.org/pypi/kazma/json", timeout=5.0)',
    "PYPI_URL = \"https://pypi.org/pypi/kazma/json\"",
    "pip install --upgrade kazma",
    'python -m pip install -U "kazma>=0.11"',
    "uv add kazma",
    "pipx install kazma_cli",
])
def test_the_gate_catches_every_old_form(old):
    """Negative control (§28): each form the sweep removed is caught."""
    assert bare_installs(old), old


@pytest.mark.parametrize("fine", [
    'pip install -e ".[rag,dev,tui]"',
    'uv pip install --python /venv/bin/python -e ".[rag]"',
    'install: "pip install -e kazma-tui/ -e kazma-core/"',
    'hint = "pip install neo4j in Kazma venv"',
    "pip install sigstore",
    'Settings -> Packages, or pip install -e ".[web]" in the Kazma folder',
    "pip install ./dist/kazma-0.11.0-py3-none-any.whl",
    "never install `kazma` (or `kazma[...]`) by name from PyPI",
])
def test_the_gate_passes_local_installs(fine):
    assert not bare_installs(fine), fine


def test_the_argv_gate_catches_the_old_updater():
    """Negative control: the pip call ``do_pip_update`` made until 2026-09-30."""
    old = '''
def do_pip_update() -> bool:
    result = _run_pip(["install", "--upgrade", PACKAGE_NAME], timeout=_INSTALL_TIMEOUT)
    other = [sys.executable, "-m", "pip", "install", f"{PACKAGE_NAME}[rag]"]
    third = ("install", "kazma-core")
'''
    assert len(install_argv_names(ast.parse(old))) == 3
    fine = '''
cmd = [sys.executable, "-m", "pip", "install", "-e", f".[{extra}]"]
show = _run_pip(["show", PACKAGE_NAME])
wheel = _run_pip(["install", "--upgrade", spec])
'''
    assert install_argv_names(ast.parse(fine)) == []


def _reservations() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pypi_reserve", REPO / "scripts" / "pypi_reserve.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_reservations_hold_every_kazma_name():
    """The names held on PyPI are this gate's Kazma names: a package name
    added to one list and not the other is a name left for anyone."""
    assert set(_reservations().NAMES) == KAZMA_DISTS


def test_each_name_publishes_under_its_own_environment():
    """PyPI keeps one pending-publisher configuration (owner, repository,
    workflow, environment) pending for one new project at a time: live
    2026-10-01 it took `kazma` and `kazma-cli` and refused every later name.
    So each name uploads under its own environment, and the guide says so."""
    import yaml

    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "pypi-reserve.yml").read_text(encoding="utf-8")
    )
    assert workflow["jobs"]["publish"]["environment"] == "pypi-${{ matrix.name }}"
    assert workflow["jobs"]["publish"]["permissions"] == {"id-token": "write"}
    guide = (REPO / "docs" / "SUPPLY_CHAIN.md").read_text(encoding="utf-8")
    assert "`pypi-kazma`" in guide and "`pypi-kazma-cli`" in guide


def test_a_reservation_installs_nothing_and_points_at_the_releases(tmp_path):
    reserve = _reservations()
    for name in reserve.NAMES:
        project = reserve.write_project(name, tmp_path / name)
        meta = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
        readme = (project / "README.md").read_text(encoding="utf-8")
        assert meta["project"]["name"] == name
        assert meta["project"]["version"] == "0.0.1", "below every real Kazma version"
        assert "dependencies" not in meta["project"]
        assert meta["tool"]["hatch"]["build"]["targets"]["wheel"]["bypass-selection"] is True
        assert set(p.name for p in project.iterdir()) == {"README.md", "pyproject.toml"}
        assert "https://github.com/Mubder/kazma/releases" in readme
        assert not _INSTALL_VERB.search(readme), "a reservation never says how to install by name"
    with pytest.raises(ValueError):
        reserve.write_project("kazma-extra", tmp_path / "extra")


def test_both_update_checks_read_one_release():
    """The CLI keeps its own copy (it must run while kazma_core may not import)."""
    from kazma_cli import update
    from kazma_core import settings_manager

    assert update._RELEASE_API == settings_manager._RELEASE_API
    assert update._RELEASE_API == "https://api.github.com/repos/Mubder/kazma/releases/latest"


# ── a fake GitHub ───────────────────────────────────────────────────────────

_WHEEL = "kazma-0.12.0-py3-none-any.whl"
_DOWNLOAD = "https://github.com/Mubder/kazma/releases/download/v0.12.0/"


class _Resp:
    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"") -> None:
        self.status_code = status
        self._body = body
        self.content = content
        self.text = body if isinstance(body, str) else ""

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not JSON")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self):
        for i in range(0, len(self.content), 7):
            yield self.content[i:i + 7]

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeGitHub:
    """Answers the release API, SHA256SUMS and the wheel; records every URL."""

    def __init__(self, wheel: bytes = b"wheel-bytes", *, listed: str | None = None,
                 digest: str | None = None, url: str | None = None,
                 with_wheel: bool = True, with_sums: bool = True, status: int = 200) -> None:
        self.wheel = wheel
        good = hashlib.sha256(wheel).hexdigest()
        self.listed = good if listed is None else listed
        self.digest = good if digest is None else digest
        self.url = url or _DOWNLOAD + _WHEEL
        self.with_wheel = with_wheel
        self.with_sums = with_sums
        self.status = status
        self.seen: list[str] = []

    def release(self) -> dict[str, Any]:
        assets: list[dict[str, Any]] = []
        if self.with_wheel:
            assets.append({"name": _WHEEL, "browser_download_url": self.url,
                           "digest": f"sha256:{self.digest}" if self.digest else None})
            assets.append({"name": _WHEEL + ".sigstore.json",
                           "browser_download_url": _DOWNLOAD + _WHEEL + ".sigstore.json"})
        if self.with_sums:
            assets.append({"name": "SHA256SUMS", "browser_download_url": _DOWNLOAD + "SHA256SUMS"})
        return {"tag_name": "v0.12.0", "assets": assets}

    def get(self, url: str, **_kw: Any) -> _Resp:
        self.seen.append(url)
        if url.endswith(("/releases/latest", "/releases/tags/v0.12.0")):
            return _Resp(self.status, self.release()) if self.status == 200 else _Resp(self.status)
        if url == _DOWNLOAD + "SHA256SUMS":
            line = f"{self.listed}  {_WHEEL}\n" if self.listed else ""
            return _Resp(200, line + f"{'0' * 64}  kazma-0.12.0.tar.gz\n")
        return _Resp(404)

    def stream(self, method: str, url: str, **_kw: Any) -> _Resp:
        self.seen.append(url)
        return _Resp(200, content=self.wheel) if url == self.url else _Resp(404)

    # httpx.Client(...) -> this object, as a context manager
    def __call__(self, *_a: Any, **_kw: Any) -> FakeGitHub:
        return self

    def __enter__(self) -> FakeGitHub:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def github(monkeypatch):
    import httpx

    def install(fake: FakeGitHub) -> FakeGitHub:
        monkeypatch.setattr(httpx, "Client", fake)
        return fake

    return install


def _never_pypi(fake: FakeGitHub) -> None:
    assert fake.seen, "nothing was asked"
    assert not [u for u in fake.seen if "pypi" in u], fake.seen


# ── kazma update: the newest release, and its wheel ─────────────────────────


def test_the_latest_release_names_its_wheel_and_hash(github):
    from kazma_cli.update import get_latest_release

    fake = github(FakeGitHub())
    rel = get_latest_release()
    assert rel is not None
    assert (rel.version, rel.wheel_name, rel.wheel_url) == ("0.12.0", _WHEEL, _DOWNLOAD + _WHEEL)
    assert rel.wheel_sha256 == hashlib.sha256(b"wheel-bytes").hexdigest()
    assert rel.problem == ""
    assert rel.page == "https://github.com/Mubder/kazma/releases/tag/v0.12.0"
    _never_pypi(fake)


@pytest.mark.parametrize("fake_kwargs, problem", [
    ({"digest": "1" * 64}, "disagree"),
    ({"listed": ""}, "does not list"),
    ({"with_sums": False}, "does not list"),
    ({"url": "https://evil.example/kazma-0.12.0-py3-none-any.whl"}, "not served from"),
    ({"with_wheel": False}, "no wheel"),
])
def test_a_release_it_cannot_verify_says_why(github, fake_kwargs, problem):
    from kazma_cli.update import get_latest_release

    github(FakeGitHub(**fake_kwargs))
    rel = get_latest_release()
    assert rel is not None and problem in rel.problem
    assert rel.wheel_sha256 == ""


def test_the_hash_stands_without_githubs_digest(github):
    """Releases from before GitHub reported digests: SHA256SUMS alone."""
    from kazma_cli.update import get_latest_release

    github(FakeGitHub(digest=""))
    rel = get_latest_release()
    assert rel is not None and rel.problem == "" and rel.wheel_sha256


def test_no_answer_is_none(github):
    from kazma_cli.update import get_latest_release

    github(FakeGitHub(status=403))
    assert get_latest_release() is None


def _pip_recorder(monkeypatch):
    from kazma_cli import update

    calls: list[list[str]] = []

    def fake_pip(args: list[str], timeout: float = 0) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        spec = args[-1]
        wheel = Path(spec.split("[", 1)[0])
        # Installed from the downloaded file, while it exists, with the bytes
        # the release published.
        assert wheel.is_file() and wheel.name == _WHEEL
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == hashlib.sha256(b"wheel-bytes").hexdigest()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(update, "_run_pip", fake_pip)
    return calls


def test_update_installs_the_verified_wheel_with_its_extras(github, monkeypatch):
    from kazma_cli.update import do_pip_update, get_latest_release

    fake = github(FakeGitHub())
    calls = _pip_recorder(monkeypatch)
    assert do_pip_update(get_latest_release(), ["rag", "postgres"]) is True
    assert len(calls) == 1
    args = calls[0]
    assert args[:2] == ["install", "--upgrade"]
    assert args[2].endswith(f"{_WHEEL}[rag,postgres]")
    assert _dist_name(args[2], fold_case=True) not in KAZMA_DISTS  # a path, not a name
    _never_pypi(fake)


def test_a_wheel_that_does_not_match_is_not_installed(github, monkeypatch):
    from kazma_cli.update import do_pip_update, get_latest_release

    fake = github(FakeGitHub())
    rel = get_latest_release()
    fake.wheel = b"something else"  # swapped between the listing and the download
    calls = _pip_recorder(monkeypatch)
    assert do_pip_update(rel) is False
    assert calls == []


def test_a_release_with_a_problem_downloads_nothing(github, monkeypatch):
    from kazma_cli.update import do_pip_update, get_latest_release

    fake = github(FakeGitHub(digest="1" * 64))
    rel = get_latest_release()
    fake.seen.clear()
    calls = _pip_recorder(monkeypatch)
    assert do_pip_update(rel) is False
    assert calls == [] and fake.seen == []


def test_an_oversized_download_is_refused(github, monkeypatch):
    from kazma_cli import update

    github(FakeGitHub())
    monkeypatch.setattr(update, "_MAX_WHEEL_BYTES", 4)
    calls = _pip_recorder(monkeypatch)
    assert update.do_pip_update(update.get_latest_release()) is False
    assert calls == []


def test_update_run_hands_the_release_to_the_installer(github, monkeypatch):
    """``kazma update --yes`` on a wheel install: the release, never a name."""
    from kazma_cli import update

    github(FakeGitHub())
    monkeypatch.setattr(update, "_find_git_root", lambda: None)
    monkeypatch.setattr(update, "detect_install_type", lambda: "pip")
    monkeypatch.setattr(update, "get_current_version", lambda: "0.11.0")
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: ["rag"])
    seen: list[tuple[Any, Any]] = []
    monkeypatch.setattr(update, "do_pip_update", lambda rel, extras=(), reinstall=False: seen.append((rel, extras)) or True)
    update.run(["--yes"])
    assert len(seen) == 1
    assert seen[0][0].wheel_name == _WHEEL and seen[0][1] == ["rag"]


# ── repairing a wheel install: --reinstall and --force ──────────────────────


def test_a_reinstall_puts_the_same_version_back(github, monkeypatch):
    """pip calls an installed version satisfied; forced for Kazma alone, then its deps."""
    from kazma_cli.update import do_pip_update, get_latest_release

    github(FakeGitHub())
    calls = _pip_recorder(monkeypatch)
    assert do_pip_update(get_latest_release(), ["rag"], reinstall=True) is True
    assert [c[:-1] for c in calls] == [["install", "--force-reinstall", "--no-deps"], ["install"]]
    assert calls[0][-1].endswith(_WHEEL) and calls[1][-1].endswith(f"{_WHEEL}[rag]")


def test_reinstall_on_a_wheel_install_uses_its_own_release(github, monkeypatch):
    """It used to take the current folder for the project (an editable install of it)."""
    from kazma_cli import update

    fake = github(FakeGitHub())
    monkeypatch.setattr(update, "_find_git_root", lambda: None)
    monkeypatch.setattr(update, "detect_install_type", lambda: "pip")
    monkeypatch.setattr(update, "get_current_version", lambda: "0.12.0")
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: [])
    monkeypatch.setattr(update, "_reinstall_local", lambda cwd: pytest.fail(f"editable install of {cwd}"))
    # Never this machine's server: a live install answering on 9090 would refuse.
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    seen: list[tuple[Any, Any, Any]] = []
    monkeypatch.setattr(
        update, "do_pip_update", lambda rel, extras=(), reinstall=False: seen.append((rel, extras, reinstall)) or True,
    )
    update.run(["--reinstall", "--yes"])
    assert len(seen) == 1 and seen[0][0].version == "0.12.0" and seen[0][2] is True
    assert any(u.endswith("/releases/tags/v0.12.0") for u in fake.seen)
    _never_pypi(fake)


def test_force_on_the_latest_version_reinstalls(github, monkeypatch):
    from kazma_cli import update

    github(FakeGitHub())
    monkeypatch.setattr(update, "_find_git_root", lambda: None)
    monkeypatch.setattr(update, "detect_install_type", lambda: "pip")
    monkeypatch.setattr(update, "get_current_version", lambda: "0.12.0")
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: [])
    seen: list[bool] = []
    monkeypatch.setattr(update, "do_pip_update", lambda rel, extras=(), reinstall=False: seen.append(reinstall) or True)
    update.run(["--force", "--yes"])
    assert seen == [True]
    monkeypatch.setattr(update, "get_current_version", lambda: "0.11.0")
    update.run(["--yes"])  # a newer release: an ordinary upgrade
    assert seen == [True, False]


# ── Settings: "Check for updates" ───────────────────────────────────────────


@pytest.fixture
def settings_github(monkeypatch):
    import httpx

    seen: list[str] = []

    def install(answer: Any) -> list[str]:
        def fake_get(url: str, **_kw: Any) -> _Resp:
            seen.append(url)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(httpx, "get", fake_get)
        return seen

    return install


def _manager():
    from kazma_core.settings_manager import SettingsManager

    return SettingsManager.__new__(SettingsManager)  # the check reads no settings


def test_settings_reports_a_newer_release(settings_github):
    from kazma_core.version import get_version

    seen = settings_github(_Resp(200, {"tag_name": "v99.0.0"}))
    info = _manager().check_updates()
    assert info["update_available"] is True
    assert info["latest_version"] == "99.0.0"
    assert info["current_version"] == get_version()
    assert info["release_url"] == "https://github.com/Mubder/kazma/releases/tag/v99.0.0"
    assert seen == ["https://api.github.com/repos/Mubder/kazma/releases/latest"]


def test_settings_current_when_the_release_is_not_newer(settings_github):
    from kazma_core.version import get_base_version

    settings_github(_Resp(200, {"tag_name": f"v{get_base_version()}"}))
    info = _manager().check_updates()
    assert info["update_available"] is False and "error" not in info


@pytest.mark.parametrize("answer, words", [
    (_Resp(403), "GitHub answered 403"),
    (_Resp(200, {"assets": []}), "no release"),
    (_Resp(200, "<html>"), "no release"),
])
def test_settings_says_when_it_could_not_check(settings_github, answer, words):
    """It used to say "Running the latest version" whenever the check failed."""
    settings_github(answer)
    info = _manager().check_updates()
    assert info["update_available"] is False and words in info["error"]


def test_settings_says_when_github_is_unreachable(settings_github):
    import httpx

    settings_github(httpx.ConnectError("offline"))
    info = _manager().check_updates()
    assert info["error"] == "GitHub could not be reached (ConnectError)"


def test_release_versions_compare_by_number():
    from kazma_core.settings_manager import _release_key

    assert _release_key("0.11.0+g1a2b3c4") == _release_key("0.11") == (0, 11)
    assert _release_key("0.12.0") > _release_key("0.11.9")
    assert _release_key("1.0.0rc1") == _release_key("1.0.0")


# ── the swarm page: an import error, not a package ──────────────────────────


def test_a_swarm_that_does_not_import_is_logged_once(monkeypatch, caplog):
    import sys

    from kazma_ui.services import SWARM_CORE_MISSING, SwarmService

    monkeypatch.setitem(sys.modules, "kazma_core.swarm", None)
    svc = SwarmService()
    with caplog.at_level(logging.WARNING, logger="kazma_ui.services"):
        assert svc.has_swarm_core() is False
        assert svc.has_swarm_core() is False  # the page polls it
    warnings = [r for r in caplog.records if r.name == "kazma_ui.services" and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and warnings[0].exc_info
    assert "pip" not in SWARM_CORE_MISSING and "import error" in SWARM_CORE_MISSING


def test_extra_hints_install_from_the_kazma_folder():
    from kazma_core.install_hint import extra_install_hint

    hint = extra_install_hint("web")
    assert '-e ".[web]"' in hint and "Settings" in hint
    assert not bare_installs(hint)
