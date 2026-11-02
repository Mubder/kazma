"""Every number and switch kazma.yaml ships is checked, and a restore
refuses keys this build does not read (2026-11, FA-251).

``validate_setting`` covered about ten keys; any other took any value on
every path (the single and batch saves, a restore, ``config_save``, the
TUI). ``agent.max_iterations`` stored -7 and 999999 and the turn silently
used 5 or 100; ``agent.language`` stored "xx" and the prompt fell back to
English while Settings showed "xx". A backup's ``audit2611.key`` was
written. Now:

- ``agent.max_iterations`` takes the runtime's range, ``agent.language``
  the languages Kazma speaks;
- every number and switch ``kazma.yaml`` ships takes its type, read from
  the file (a form's "30" is 30, "yes" a switch on);
- a restore lists a key no rule names, the file does not ship and the
  install does not hold under ``refused``.

The rules without the type rule (``_rules`` alone) are the negative control.
"""

from __future__ import annotations

import pytest

from kazma_core.settings_validation import (
    AGENT_LANGUAGES,
    SettingRejected,
    _rules,
    shipped_values,
    validate_setting,
)


@pytest.mark.parametrize("value", [-7, 999999, "lots", True])
def test_max_iterations_outside_its_range_is_refused(value):
    with pytest.raises(SettingRejected):
        validate_setting("agent.max_iterations", value)


def test_max_iterations_from_a_form_is_a_number():
    assert validate_setting("agent.max_iterations", "30") == (30, "agent")


@pytest.mark.parametrize("value", ["xx", "", "fr"])
def test_an_unknown_language_is_refused(value):
    with pytest.raises(SettingRejected):
        validate_setting("agent.language", value)


def test_the_languages_are_the_interfaces():
    from kazma_ui.i18n import SUPPORTED_LANGUAGES

    assert set(SUPPORTED_LANGUAGES) == AGENT_LANGUAGES


def _typed_keys() -> dict[str, type]:
    return {key: type(value) for key, value in shipped_values().items()
            if isinstance(value, (bool, int, float))}


def test_every_shipped_number_and_switch_has_a_rule():
    typed = _typed_keys()
    assert len(typed) >= 40, typed  # the reading of kazma.yaml is not blind
    unchecked = []
    for key, kind in typed.items():
        wrong = "not a number" if kind is not bool else "maybe"
        try:
            validate_setting(key, wrong)
        except SettingRejected:
            continue
        unchecked.append(key)
    assert not unchecked, f"these take any value: {unchecked}"


def test_negative_control_without_the_type_rule_a_number_takes_text(monkeypatch):
    from kazma_core import settings_validation

    typed = [key for key, kind in _typed_keys().items() if kind is int and key not in _rules()]
    assert typed
    monkeypatch.setattr(settings_validation, "_shipped_type_rule", lambda name: None)
    assert validate_setting(typed[0], "not a number") == ("not a number", None)


@pytest.mark.parametrize("key, value, expected", [
    ("memory.enabled", "yes", True), ("memory.enabled", "off", False), ("memory.enabled", 1, True),
])
def test_a_switch_from_a_form_is_a_switch(key, value, expected):
    if key not in shipped_values():
        pytest.skip(f"{key} is not shipped")
    assert validate_setting(key, value)[0] is expected


def test_a_restore_refuses_a_key_this_build_does_not_read(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.settings_restore import restore

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        backup = "agent:\n  language: en\naudit2611:\n  key: 1\n"
        refused = restore(backup, store, dry_run=True)["plan"]["refused"]
        assert refused.get("audit2611.key") == "not a setting this build reads"
        assert "agent.language" not in refused
    finally:
        store.close()
