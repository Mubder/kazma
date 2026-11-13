"""Setup generates SearXNG's secret key; Kazma says when one runs without it.

``deploy/searxng/settings.yml`` and ``docker-compose.yml`` ship one placeholder
``secret_key``, and compose falls back to it when ``SEARXNG_SECRET`` is unset
(so a one-command start still works). The owner's decision (2026-11-11):
``kazma setup install`` keeps a generated ``SEARXNG_SECRET`` in the ``.env``
compose reads before it starts SearXNG, once, never over a value already
there; a SearXNG container of Kazma's running on the placeholder is a boot
WARNING and a failed security-report check. The old setup (no such step) is
the negative control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import pytest
import yaml

from kazma_core import container_services as cs
from kazma_core.security import searxng_secret as ss
from kazma_core.security import service_exposure as se

# The install's fake Windows machine and its fixture (test_install_modes).
from tests.test_install_modes import WINDOWS, _machine, _options, box  # noqa: F401 -- box is a fixture

ROOT = Path(__file__).resolve().parents[1]
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _ensure(env_path: Path) -> bool:
    """The install's step: SearXNG's row of the container services table."""
    return cs.ensure_secret(env_path, ss.SECRET_VAR, (ss.PLACEHOLDER,))


def _value(path: Path) -> str | None:
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == ss.SECRET_VAR:
            found = value.strip()
    return found


@pytest.fixture(autouse=True)
def _no_secret_in_environment(monkeypatch):
    monkeypatch.delenv(ss.SECRET_VAR, raising=False)


# -- the placeholder is the one the files ship ----------------------------------------


def test_the_placeholder_is_the_one_the_shipped_files_carry():
    settings = yaml.safe_load((ROOT / "deploy" / "searxng" / "settings.yml").read_text(encoding="utf-8"))
    assert settings["server"]["secret_key"] == ss.PLACEHOLDER
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    passed = compose["services"]["searxng"]["environment"]["SEARXNG_SECRET"]
    # compose still starts without setup: the fallback stays, and it is the placeholder.
    assert passed == "${SEARXNG_SECRET:-" + ss.PLACEHOLDER + "}"


# -- setup writes it once, restricted first, never over a value -----------------------


def test_setup_writes_a_generated_key_once_and_never_overwrites(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# SEARXNG_SECRET=\nKAZMA_PORT=9090\n", encoding="utf-8")
    seen: list[str] = []

    def restrict(path):
        seen.append(Path(path).read_text(encoding="utf-8"))
        return True

    monkeypatch.setattr("kazma_core.security.file_access.restrict_to_owner", restrict)
    assert _ensure(env) is True
    first = _value(env)
    assert first and HEX64.match(first)
    assert seen and ss.SECRET_VAR + "=" + first not in seen[0], "restricted before the key was written"
    assert "KAZMA_PORT=9090" in env.read_text(encoding="utf-8")

    assert _ensure(env) is False
    assert _value(env) == first


def test_a_new_env_file_is_made_and_restricted_before_the_key_is_in_it(tmp_path, monkeypatch):
    env = tmp_path / "fresh.env"
    seen: list[str] = []
    monkeypatch.setattr("kazma_core.security.file_access.restrict_to_owner",
                        lambda path: seen.append(Path(path).read_text(encoding="utf-8")) or True)
    assert _ensure(env) is True
    assert seen == [""] and HEX64.match(_value(env) or "")


def test_an_owner_value_is_kept_and_the_placeholder_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.security.file_access.restrict_to_owner", lambda path: True)
    own = tmp_path / "own.env"
    own.write_text("SEARXNG_SECRET=the-owners-own-choice\n", encoding="utf-8")
    assert _ensure(own) is False and _value(own) == "the-owners-own-choice"

    placeholder = tmp_path / "placeholder.env"
    placeholder.write_text(f"SEARXNG_SECRET={ss.PLACEHOLDER}\n", encoding="utf-8")
    assert _ensure(placeholder) is True
    assert _value(placeholder) != ss.PLACEHOLDER and HEX64.match(_value(placeholder) or "")

    in_shell = tmp_path / "shell.env"
    in_shell.write_text("", encoding="utf-8")
    monkeypatch.setenv(ss.SECRET_VAR, "set-in-the-shell-compose-runs-in")
    assert _ensure(in_shell) is False and _value(in_shell) is None


# -- through kazma setup install, on a fake machine -----------------------------------


def _full_install(box) -> int:
    from kazma_cli.install import install

    return install(_options(mode="full", yes=True), _machine(box))


def test_the_install_keeps_a_key_before_it_starts_searxng(box, monkeypatch):
    monkeypatch.setattr("kazma_core.security.file_access.restrict_to_owner", lambda path: True)
    env = box.home / ".env"
    assert _full_install(box) in (0, 1)
    assert box.searxng
    key = _value(env)
    assert key and HEX64.match(key)
    compose = next(a for a in box.ran if "compose" in a)
    # compose reads the .env the key is in.
    assert compose[compose.index("--env-file") + 1] == str(env)
    # A second run keeps it.
    _full_install(box)
    assert _value(env) == key


def test_negative_control_the_old_setup_leaves_searxng_on_the_placeholder(box, monkeypatch):
    from kazma_cli import install as inst

    real = inst._run_planned

    def old(item, machine, env_path, log):
        if item.step.action.startswith("service_secret:"):
            return True, ""  # the old plan had no such step
        return real(item, machine, env_path, log)

    monkeypatch.setattr(inst, "_run_planned", old)
    _full_install(box)
    assert box.searxng and _value(box.home / ".env") is None


def test_the_part_is_not_done_while_searxng_has_no_key(box, monkeypatch):
    """An install whose SearXNG answers but has no key (one set up before
    2026-11-11) is planned again, so the next run writes it and compose
    starts the container again with it."""
    from kazma_cli.install import _check_parts
    from kazma_core import install_components as ic

    env = box.home / ".env"
    env.write_text("KAZMA_SEARXNG_URL=http://127.0.0.1:8088\n", encoding="utf-8")
    box.searxng = True
    machine = _machine(box)
    found, _ = _check_parts([ic.by_id("private_search")], machine, WINDOWS)
    state = found["private_search"]
    assert state.ok is False and ss.SECRET_VAR in state.detail
    env.write_text(env.read_text(encoding="utf-8") + "SEARXNG_SECRET=" + "a" * 64 + "\n", encoding="utf-8")
    found, _ = _check_parts([ic.by_id("private_search")], machine, WINDOWS)
    assert found["private_search"].ok is True


# -- Kazma says it: boot WARNING and the security report ----------------------------------

ROWS = [{"Names": "kazma-searxng", "Image": "searxng/searxng:latest", "Ports": "127.0.0.1:8088->8080/tcp"},
        {"Names": "other-searxng", "Image": "searxng/searxng:latest", "Ports": "8080/tcp"}]


def _inspect(env: list[str], labels: dict | None = None, mounts: list | None = None) -> dict:
    return {"Config": {"Env": env, "Labels": labels or {}}, "Mounts": mounts or []}


def test_the_placeholder_is_read_from_the_container():
    assert ss.uses_placeholder(_inspect([f"SEARXNG_SECRET={ss.PLACEHOLDER}"])) is True
    assert ss.uses_placeholder(_inspect(["SEARXNG_SECRET=" + "b" * 64])) is False
    # No variable (a container from the compose before 2026-11-10): settings.yml decides.
    assert ss.uses_placeholder(_inspect(["PATH=/usr/bin"],
                                        labels={"com.docker.compose.project.working_dir": str(ROOT)})) is True
    assert ss.uses_placeholder(_inspect(["PATH=/usr/bin"])) is None


def test_the_settings_file_a_container_mounts_decides_without_the_variable(tmp_path):
    folder = tmp_path / "searxng"
    folder.mkdir()
    (folder / "settings.yml").write_text("server:\n  secret_key: \"" + "c" * 64 + "\"\n", encoding="utf-8")
    mounts = [{"Destination": "/etc/searxng", "Source": str(folder)}]
    assert ss.uses_placeholder(_inspect([], mounts=mounts)) is False


def test_boot_warns_once_and_the_report_fails_on_the_placeholder(monkeypatch, caplog):
    from kazma_core.security.hardening import SecurityHardeningRunner

    asked: list[str] = []
    monkeypatch.setattr(se, "_docker_rows", lambda: ROWS)
    monkeypatch.setattr(se, "_role_answer", lambda: None)
    monkeypatch.setattr(se, "_inspect_container",
                        lambda name: asked.append(name) or _inspect([f"SEARXNG_SECRET={ss.PLACEHOLDER}"]))
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda *a, **k: pytest.fail("the placeholder raises no ops alert"))
    with caplog.at_level(logging.WARNING, logger="kazma_core.security.service_exposure"):
        se.report_exposure()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "kazma-searxng" in warnings[0] and "placeholder" in warnings[0]
    assert ss.PLACEHOLDER not in warnings[0], "the key itself is never said"
    assert asked == ["kazma-searxng"], "only Kazma's own container is asked"

    check = asyncio.run(SecurityHardeningRunner().run_check("check_searxng_secret"))
    assert check.passed is False and "kazma setup install --with private_search" in check.recommendation


def test_a_generated_key_passes_and_says_nothing(monkeypatch, caplog):
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(se, "_docker_rows", lambda: ROWS)
    monkeypatch.setattr(se, "_role_answer", lambda: None)
    monkeypatch.setattr(se, "_inspect_container", lambda name: _inspect(["SEARXNG_SECRET=" + "d" * 64]))
    with caplog.at_level(logging.WARNING, logger="kazma_core.security.service_exposure"):
        se.report_exposure()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    check = asyncio.run(SecurityHardeningRunner().run_check("check_searxng_secret"))
    assert check.passed is True and "check_searxng_secret" in SecurityHardeningRunner.CHECKS


def test_docker_not_answering_is_said_not_passed(monkeypatch):
    monkeypatch.setattr(se, "_docker_rows", lambda: None)
    problems, unchecked = se.searxng_secret_problems()
    assert problems == [] and unchecked
    monkeypatch.setattr(se, "_docker_rows", lambda: ROWS)
    monkeypatch.setattr(se, "_inspect_container", lambda name: None)
    problems, unchecked = se.searxng_secret_problems()
    assert problems == [] and any("kazma-searxng" in u for u in unchecked)


def test_the_shipped_files_say_setup_generates_the_key():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("  searxng:", 1)[1].split("SEARXNG_SECRET:", 1)[0]
    assert "kazma setup install" in service
    assert "kazma setup install" in (ROOT / "deploy" / "searxng" / "settings.yml").read_text(encoding="utf-8")
    assert "kazma setup install" in (ROOT / ".env.example").read_text(encoding="utf-8").split(
        "# SEARXNG_SECRET=")[0].rsplit("# SearXNG", 1)[1]
