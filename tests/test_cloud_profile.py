"""The cloud profile (Spaces S4-08): the checks a public install must pass at
boot, each missing piece refusing or warning as declared.

* With ``KAZMA_CLOUD_PROFILE=1`` and every piece in place, Kazma starts.
* Each piece missing on its own: a ``refuse`` piece stops the boot and is the
  only one named; a ``warn`` piece lets it start and is said at WARNING.
  ``BREAKS`` holds one way to break every check, and a check with none
  fails the gate.
* Through the real ``create_app``: the environment's checks refuse before
  the app is built (before it waits for its database); the settings' refuse
  in the builder once the store answers. Controls: the profile off starts
  with every piece missing; with every piece in place the builder goes on.
* A check that cannot be made is a missing piece; the profile off reads
  nothing.
* ``kazma doctor`` shows the profile: on, a missing refuse piece fails it;
  off, every check is shown and counts for nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.safety import deployment_policy
from kazma_core.security import cloud_profile
from kazma_core.security.cloud_profile import CHECKS, PROFILE_SWITCH, REFUSE, WARN

SECRET = "s" * 32
_ENV = {
    PROFILE_SWITCH: "1",
    "KAZMA_PUBLIC_URL": "https://kazma.example.org",
    "KAZMA_TRUSTED_PROXIES": "127.0.0.1",
    "KAZMA_HOST": "127.0.0.1",
    "KAZMA_PRODUCTION": "1",
    "KAZMA_SECRET": SECRET,
}
_OFF = ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_DEV_WS_BYPASS", "KAZMA_LOOPBACK_AUTOLOGIN")
_SETTINGS = {deployment_policy.CONTAINER_REQUIRED_KEY: True,
             "backups.restic.remote": "s3:https://s3.example.invalid/kazma"}


@pytest.fixture
def ready(monkeypatch):
    """A public install with every piece in place."""
    import kazma_core.docker_cli as docker_cli

    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    for name in _OFF:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: "docker")
    monkeypatch.setattr(docker_cli, "engine_label", lambda: "Docker")
    store = get_config_store()
    for key, value in _SETTINGS.items():
        store.set(key, value)
    yield
    for key in _SETTINGS:
        store.delete(key)


def _no_docker(monkeypatch) -> None:
    import kazma_core.docker_cli as docker_cli

    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: None)


#: One way to break each check: (how, what the detail says).
BREAKS: dict[str, list[tuple[Callable[[Any], None], str]]] = {
    "tls": [
        (lambda mp: mp.delenv("KAZMA_PUBLIC_URL"), "not set"),
        (lambda mp: mp.setenv("KAZMA_PUBLIC_URL", "http://kazma.example.org"), "http"),
        (lambda mp: mp.setenv("KAZMA_PUBLIC_URL", "https://127.0.0.1"), "this machine"),
    ],
    "proxy": [
        (lambda mp: mp.delenv("KAZMA_TRUSTED_PROXIES"), "not set"),
        (lambda mp: mp.setenv("KAZMA_TRUSTED_PROXIES", "*"), "never trusts"),
    ],
    "sign_in": [
        *[(lambda mp, n=name: mp.setenv(n, "1"), f"{name} is on") for name in _OFF],
        (lambda mp: mp.setenv("KAZMA_SECRET", "short"), "shorter than"),
    ],
    "code_in_containers": [
        (lambda mp: get_config_store().set(deployment_policy.CONTAINER_REQUIRED_KEY, False), "is off"),
    ],
    "bind": [(lambda mp: mp.setenv("KAZMA_HOST", "0.0.0.0"), "without the proxy")],  # noqa: S104 -- the case under test
    "production": [(lambda mp: mp.delenv("KAZMA_PRODUCTION"), "is off")],
    "container_engine": [(_no_docker, "no docker CLI")],
    "backups": [(lambda mp: get_config_store().delete("backups.restic.remote"), "stays on this machine")],
}

_CASES = [(key, how, said) for key, ways in BREAKS.items() for how, said in ways]
_SEVERITY = {c.key: c.severity for c in CHECKS}
_SETTINGS_CHECK = {c.key: c.settings for c in CHECKS}


def test_every_check_has_a_way_to_break_it_and_the_plans_pieces_are_there() -> None:
    assert set(BREAKS) == {c.key for c in CHECKS}
    assert {"tls", "proxy", "sign_in", "code_in_containers", "backups"} <= set(BREAKS)
    assert all(c.severity in (REFUSE, WARN) and c.fix for c in CHECKS)


def test_control_a_check_with_no_way_to_break_it_fails_the_gate(monkeypatch) -> None:
    extra = cloud_profile._Check("planted", "A planted piece", REFUSE, False, lambda: (True, ""), "fix")
    monkeypatch.setattr(cloud_profile, "CHECKS", (*CHECKS, extra))
    assert set(BREAKS) != {c.key for c in cloud_profile.CHECKS}


def test_every_piece_in_place_starts(ready, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=cloud_profile.__name__)
    assert all(f.ok for f in cloud_profile.findings()), cloud_profile.findings()
    assert cloud_profile.boot_refusal(settings=False) == ""
    assert cloud_profile.boot_refusal(settings=True) == ""
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(("key", "how", "said"), _CASES, ids=[f"{k}-{i}" for i, (k, _h, _s) in enumerate(_CASES)])
def test_each_missing_piece_refuses_or_warns_as_declared(ready, monkeypatch, caplog, key, how, said) -> None:
    caplog.set_level(logging.WARNING, logger=cloud_profile.__name__)
    how(monkeypatch)
    failed = [f for f in cloud_profile.findings() if not f.ok]
    assert [f.key for f in failed] == [key], failed
    assert said in failed[0].detail and failed[0].fix
    stage = _SETTINGS_CHECK[key]
    refusal = cloud_profile.boot_refusal(settings=stage)
    other = cloud_profile.boot_refusal(settings=not stage)
    assert other == ""
    if _SEVERITY[key] == REFUSE:
        title = next(c.title for c in CHECKS if c.key == key)
        assert title in refusal and failed[0].fix in refusal
        assert sum(c.title in refusal for c in CHECKS) == 1
    else:
        assert refusal == ""
        warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(said in m for m in warned), warned


def test_a_check_that_cannot_be_made_is_a_missing_piece(ready, monkeypatch) -> None:
    def away() -> bool:
        raise RuntimeError("the settings store is away")

    monkeypatch.setattr(deployment_policy, "container_required", away)
    refusal = cloud_profile.boot_refusal(settings=True)
    assert "could not be checked (RuntimeError)" in refusal


def test_the_profile_off_reads_nothing(monkeypatch) -> None:
    monkeypatch.delenv(PROFILE_SWITCH, raising=False)

    def read(*_a: Any, **_k: Any) -> Any:
        pytest.fail("a check ran with the profile off")

    monkeypatch.setattr(cloud_profile, "findings", read)
    assert cloud_profile.boot_refusal(settings=False) == ""
    assert cloud_profile.boot_refusal(settings=True) == ""


# ── through the real create_app ─────────────────────────────────────────


class _Built(Exception):
    """The boot went past the stage under test."""


def test_create_app_refuses_before_building_when_the_environment_is_missing(ready, monkeypatch, capsys) -> None:
    from kazma_ui import app as app_mod

    monkeypatch.delenv("KAZMA_TRUSTED_PROXIES")
    monkeypatch.setattr(app_mod.KazmaAppBuilder, "build", lambda self: pytest.fail("the app was built"))
    with pytest.raises(SystemExit) as stopped:
        app_mod.create_app()
    assert stopped.value.code == 1
    assert "A reverse proxy is declared" in capsys.readouterr().err


def test_control_the_profile_off_builds_with_every_piece_missing(monkeypatch) -> None:
    from kazma_ui import app as app_mod

    for name in (PROFILE_SWITCH, "KAZMA_PUBLIC_URL", "KAZMA_TRUSTED_PROXIES", "KAZMA_PRODUCTION"):
        monkeypatch.delenv(name, raising=False)

    def built(self: Any) -> Any:
        raise _Built

    monkeypatch.setattr(app_mod.KazmaAppBuilder, "build", built)
    with pytest.raises(_Built):
        app_mod.create_app()


def _stop_after_the_settings_stage(monkeypatch) -> None:
    """The builder's next step after the settings stage raises _Built."""
    import kazma_core.agent as agent

    def reached(*_a: Any, **_k: Any) -> Any:
        raise _Built

    monkeypatch.setattr(agent, "load_config", reached)


def test_the_builder_refuses_once_the_settings_store_answers(ready, monkeypatch, capsys) -> None:
    from kazma_ui import app as app_mod

    get_config_store().set(deployment_policy.CONTAINER_REQUIRED_KEY, False)
    _stop_after_the_settings_stage(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        app_mod.create_app()
    assert stopped.value.code == 1
    assert "Code runs only in containers" in capsys.readouterr().err


def test_control_every_piece_in_place_the_builder_goes_on(ready, monkeypatch) -> None:
    from kazma_ui import app as app_mod

    _stop_after_the_settings_stage(monkeypatch)
    with pytest.raises(_Built):
        app_mod.create_app()


# ── kazma doctor ────────────────────────────────────────────────────────


def _doctor(monkeypatch, capsys) -> tuple[int, str]:
    from kazma_cli import doctor

    monkeypatch.setattr(doctor, "collect", lambda: [])
    monkeypatch.setattr(doctor, "_system_rows", lambda: [])
    monkeypatch.setattr(doctor, "_database_service_rows", lambda: [])
    code = doctor.run([])
    return code, capsys.readouterr().out


def test_doctor_shows_the_profile_and_fails_a_missing_refuse_piece(ready, monkeypatch, capsys) -> None:
    code, out = _doctor(monkeypatch, capsys)
    assert code == 0 and "The cloud profile" in out and "the cloud profile is on" in out
    assert all(c.title in out for c in CHECKS)
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "http://kazma.example.org")
    code, out = _doctor(monkeypatch, capsys)
    assert code == 1 and "[FAIL] The public address is https: no (Kazma refuses to start)" in out


def test_doctor_with_the_profile_off_shows_every_check_and_counts_none(monkeypatch, capsys) -> None:
    for name in (PROFILE_SWITCH, "KAZMA_PUBLIC_URL", "KAZMA_TRUSTED_PROXIES"):
        monkeypatch.delenv(name, raising=False)
    code, out = _doctor(monkeypatch, capsys)
    assert code == 0 and "the cloud profile is off" in out
    assert "[info] The public address is https: no" in out and "[FAIL]" not in out
