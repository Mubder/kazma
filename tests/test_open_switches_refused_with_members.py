"""Once a second account exists, no switch opens the install (Spaces S4-03).

With people on an install who do not all trust each other, sign-in off
(``KAZMA_AUTH_DISABLED``, ``KAZMA_DEV_WS_BYPASS``,
``KAZMA_LOOPBACK_AUTOLOGIN``, ``KAZMA_TRUST_LAN``), demo mode
(``KAZMA_DEMO_MODE``), the chat apps' open switch
(``KAZMA_GATEWAY_OPEN_TO_EVERYONE``) or the open season take-over
(``KAZMA_SESSION_OPEN_TAKEOVER``) hands every one of them -- and anyone who
reaches the install -- what the owner's sign-in guards. The boot refuses them (``boot_guard.open_switches_refusal``, asked by
the builder once the settings store answers): the process exits before
anything serves, saying which switch and how to turn it off. One list of the
switches (``boot_guard.OPEN_SWITCHES``, read through
``boot_guard.open_switches_on``, which the cloud profile reads too).

Each switch through the real ``create_app``, on a loopback bind (the bind
check, ``factory_exposure_refusal``, passes there: this test is about the
accounts). An install with one account starts as before; the chat apps'
open switch with the strict allowlist on starts (the sender rule ignores it
then); an identity store that cannot answer refuses.

Negative control: the boot guard without the rule, which starts.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from kazma_core.security import boot_guard
from kazma_core.spaces import identity

LOOPBACK = ["uvicorn", "kazma_ui.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]


@pytest.fixture
def switches_off(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (*boot_guard.OPEN_SWITCHES, "KAZMA_GATEWAY_STRICT_ALLOWLIST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", LOOPBACK)
    return monkeypatch


@pytest.fixture
def two_people() -> Iterator[None]:
    """Two accounts beside the owner's (the matrix's people)."""
    from tests._space_matrix import Matrix

    with Matrix():
        yield


def _boot() -> object:
    from kazma_ui.app import create_app

    return create_app()


@pytest.mark.parametrize("switch", boot_guard.OPEN_SWITCHES)
def test_a_switch_that_opens_the_install_is_refused_once_a_second_account_exists(
    switches_off: pytest.MonkeyPatch, two_people: None, switch: str, capsys: pytest.CaptureFixture[str]
) -> None:
    switches_off.setenv(switch, "1")
    try:
        _boot()
    except SystemExit as stop:
        assert stop.code == 1
    else:
        raise AssertionError(f"Kazma started with {switch} on and a second account")
    said = capsys.readouterr().err
    # It names the switch, the accounts it counted, and how to turn it off.
    assert switch in said and ".env" in said, said
    assert "matrix-person-a" in said and "matrix-person-b" in said, said


@pytest.mark.parametrize("switch", boot_guard.OPEN_SWITCHES)
def test_one_account_starts_as_before(switches_off: pytest.MonkeyPatch, switch: str) -> None:
    switches_off.setenv(switch, "1")
    assert boot_guard.open_switches_refusal() == ""
    assert _boot() is not None


def test_no_switch_reads_nothing(switches_off: pytest.MonkeyPatch, two_people: None) -> None:
    def never(*_a, **_k):
        raise AssertionError("the accounts were read with no switch on")

    switches_off.setattr(identity, "list_accounts", never)
    assert boot_guard.open_switches_refusal() == ""


def test_the_strict_allowlist_overrides_the_chat_apps_open_switch(
    switches_off: pytest.MonkeyPatch, two_people: None
) -> None:
    switches_off.setenv(boot_guard.CHAT_APPS_OPEN_SWITCH, "1")
    switches_off.setenv("KAZMA_GATEWAY_STRICT_ALLOWLIST", "1")
    assert boot_guard.open_switches_on() == []
    assert boot_guard.open_switches_refusal() == ""


def test_an_identity_store_that_cannot_answer_refuses(switches_off: pytest.MonkeyPatch) -> None:
    switches_off.setenv("KAZMA_DEMO_MODE", "1")

    def away():
        raise identity.IdentityUnavailable("the settings store is away")

    switches_off.setattr(identity, "list_accounts", away)
    said = boot_guard.open_switches_refusal()
    assert "KAZMA_DEMO_MODE" in said and "could not be read" in said, said


def test_a_switch_is_read_by_the_one_spelling(switches_off: pytest.MonkeyPatch, two_people: None) -> None:
    """``boot_guard.env_flag``: 1/true/yes/on, nothing else."""
    switches_off.setenv("KAZMA_AUTH_DISABLED", "no")
    assert boot_guard.open_switches_refusal() == ""
    switches_off.setenv("KAZMA_AUTH_DISABLED", "Yes")
    assert "KAZMA_AUTH_DISABLED" in boot_guard.open_switches_refusal()


@pytest.mark.parametrize("switch", boot_guard.OPEN_SWITCHES)
def test_negative_control_the_boot_guard_without_the_rule_starts(
    switches_off: pytest.MonkeyPatch, two_people: None, switch: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Planted: the boot of before, no rule. Kazma starts with the switch on
    and a second account: the check above fails on its own assertion."""
    switches_off.setattr(boot_guard, "open_switches_refusal", lambda: "")
    with pytest.raises(AssertionError, match="Kazma started with"):
        test_a_switch_that_opens_the_install_is_refused_once_a_second_account_exists(
            switches_off, None, switch, capsys)


# ── One list: the cloud profile (S4-08) refuses exactly these switches ─────
#: The two lists as each builder shipped them before the merge: S4-08's
#: (main, 9a2fa6b13) without ``KAZMA_TRUST_LAN`` and with no strict override,
#: S4-03's without the open take-over.
_CLOUD_LIST_BEFORE = ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_DEV_WS_BYPASS",
                      "KAZMA_LOOPBACK_AUTOLOGIN", "KAZMA_GATEWAY_OPEN_TO_EVERYONE",
                      "KAZMA_SESSION_OPEN_TAKEOVER")


def _cloud_refuses(switch: str) -> bool:
    from kazma_core.security import cloud_profile

    ok, detail = cloud_profile._sign_in()
    return not ok and f"{switch} is on" in detail


@pytest.mark.parametrize("switch", boot_guard.OPEN_SWITCHES)
def test_the_cloud_profile_and_the_boot_refuse_the_same_switches(
    switches_off: pytest.MonkeyPatch, two_people: None, switch: str
) -> None:
    switches_off.delenv("KAZMA_SECRET", raising=False)
    switches_off.setenv(switch, "1")
    assert switch in boot_guard.open_switches_refusal(), f"the boot let {switch} through"
    assert _cloud_refuses(switch), f"the cloud profile let {switch} through"


def test_the_strict_allowlist_overrides_the_chat_apps_switch_for_both(
    switches_off: pytest.MonkeyPatch, two_people: None
) -> None:
    switches_off.delenv("KAZMA_SECRET", raising=False)
    switches_off.setenv(boot_guard.CHAT_APPS_OPEN_SWITCH, "1")
    switches_off.setenv("KAZMA_GATEWAY_STRICT_ALLOWLIST", "1")
    assert boot_guard.open_switches_refusal() == ""
    assert not _cloud_refuses(boot_guard.CHAT_APPS_OPEN_SWITCH), (
        "the cloud profile refused a chat apps' open switch the strict allowlist overrides")


def test_the_session_take_over_switch_is_read_as_every_switch_is(switches_off: pytest.MonkeyPatch) -> None:
    from kazma_core.sessions import directory

    switches_off.setenv("KAZMA_SESSION_OPEN_TAKEOVER", "Yes")
    assert directory._open_takeover_enabled() and "KAZMA_SESSION_OPEN_TAKEOVER" in boot_guard.open_switches_on()
    switches_off.setenv("KAZMA_SESSION_OPEN_TAKEOVER", "no")
    assert not directory._open_takeover_enabled() and boot_guard.open_switches_on() == []


def test_negative_control_two_lists_disagree(switches_off: pytest.MonkeyPatch, two_people: None) -> None:
    """Planted: the cloud profile reading S4-08's own list, as before the
    merge. ``KAZMA_TRUST_LAN`` passes it while the boot refuses it."""
    from kazma_core.security import cloud_profile

    switches_off.setattr(cloud_profile, "open_switches_on",
                         lambda: [n for n in _CLOUD_LIST_BEFORE if boot_guard.env_flag(n)])
    with pytest.raises(AssertionError, match="the cloud profile let KAZMA_TRUST_LAN through"):
        test_the_cloud_profile_and_the_boot_refuse_the_same_switches(switches_off, None, "KAZMA_TRUST_LAN")


def test_a_switch_added_to_the_one_list_reaches_both_refusals(
    switches_off: pytest.MonkeyPatch, two_people: None
) -> None:
    """Planted: a new switch on ``OPEN_SWITCHES``. The boot with a second
    account and the cloud profile both refuse it, with no change elsewhere."""
    switches_off.delenv("KAZMA_SECRET", raising=False)
    switches_off.setattr(boot_guard, "OPEN_SWITCHES", (*boot_guard.OPEN_SWITCHES, "KAZMA_PLANTED_OPEN"))
    switches_off.setenv("KAZMA_PLANTED_OPEN", "1")
    assert "KAZMA_PLANTED_OPEN" in boot_guard.open_switches_refusal()
    assert _cloud_refuses("KAZMA_PLANTED_OPEN")


def test_every_open_switch_is_read_as_a_security_switch() -> None:
    """Each switch of the one list is read through ``env_flag``
    (``SECURITY_SWITCHES``, held by ``tests/test_security_switch_spelling.py``)."""
    assert set(boot_guard.OPEN_SWITCHES) <= boot_guard.SECURITY_SWITCHES, (
        set(boot_guard.OPEN_SWITCHES) - boot_guard.SECURITY_SWITCHES)
