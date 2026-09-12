"""A RAM warning is not a missing dependency.

On 2026-09-12 the operator got a Telegram alert -- "RAM usage at 91%" -- with a
button labelled "Resolve Subsystem Issue" and the text "Click below to trigger
the remote installation safely". Clicking it ran, against the live install:

    uv add system-init

There is no package called `system-init`. The name came from
`f"{subsystem.lower()}-init"` in `trigger_system_alert`, a label for a
subsystem, passed down a pipeline that assumed every alert is a
missing-dependency alert. `uv add` failed, then `uv pip install` failed, and
nothing was installed -- because nobody has registered that name on PyPI.

That is not a security control. `system-init`, `disk-init`, `network-init` are
unregistered names an alert card will ask an admin to install on demand, and
`uv add` writes the result into pyproject.toml as a permanent dependency.
Being admin-gated does not save it: the admin is not choosing a package, they
are clicking "resolve" on a memory warning.

Three layers, tested here:
  1. the alert stops inventing package names;
  2. the card stops offering a button when there is nothing to install;
  3. the installer refuses anything not on ALLOWED_PACKAGES -- the chokepoint,
     and the one that holds even if a future alert invents a name again.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.observability.alerts import AlertDispatcher
from kazma_core.system.installer import ALLOWED_PACKAGES


@pytest.fixture
def captured(monkeypatch):
    """Capture what would be sent to the platform adapters."""
    sent: list[dict] = []

    async def _deliver(self, alert):
        sent.append(alert.to_dict())

    from kazma_core.observability.alerts import BusAlertChannel

    monkeypatch.setattr(BusAlertChannel, "deliver", _deliver)
    AlertDispatcher._last_dispatch.clear()
    return sent


# ── 1. the alert must not invent a package name ─────────────────────────────


def test_a_ram_alert_offers_no_install_button(captured):
    """The exact alert from the incident."""
    asyncio.run(
        AlertDispatcher.trigger_system_alert(
            subsystem="System",
            status="DEGRADED",
            message="RAM usage at 91% — approaching capacity",
            severity="WARNING",
        )
    )
    alert = next(a for a in captured if a["subsystem"] == "System")
    assert alert["callback_id"] == "", (
        f"a RAM warning produced installable callback {alert['callback_id']!r}"
    )
    assert alert["button_text"] == ""


@pytest.mark.parametrize(
    "subsystem", ["System", "Disk", "Network", "Memory", "Scheduler"]
)
def test_no_subsystem_name_becomes_a_package_name(captured, subsystem):
    """`f"{subsystem.lower()}-init"` for anything unrecognised is how this
    happened. Any subsystem added later must not reintroduce it."""
    asyncio.run(
        AlertDispatcher.trigger_system_alert(
            subsystem=subsystem, status="DEGRADED", message="something is wrong"
        )
    )
    alert = next(a for a in captured if a["subsystem"] == subsystem)
    cb = alert["callback_id"]
    assert not cb.endswith("-init"), f"{cb!r} is a label being passed off as a package"
    assert cb == "" or cb in ALLOWED_PACKAGES


def test_a_genuinely_missing_dependency_still_offers_the_button(captured):
    """The counterweight. This feature exists for a real case -- the ML extras
    are genuinely absent and genuinely installable -- and removing the button
    everywhere would be a worse bug than the one being fixed."""
    asyncio.run(
        AlertDispatcher.trigger_system_alert(
            subsystem="Memory",
            status="DEGRADED",
            message="sentence-transformers is not installed; recall is degraded",
        )
    )
    alert = next(a for a in captured if a["subsystem"] == "Memory")
    assert alert["callback_id"] == "sentence-transformers"
    assert alert["callback_id"] in ALLOWED_PACKAGES
    assert alert["button_text"]


# ── 2. the installer refuses anything it was not told about ─────────────────


@pytest.fixture
def spawned(monkeypatch):
    """Record what would actually be executed, without executing it."""
    calls: list[str] = []
    from kazma_core.system import runtime_manager as rm

    def _spawn(coro, name=""):
        calls.append(name)
        coro.close()  # never run the installer in a test

    monkeypatch.setattr(rm, "spawn_background", _spawn)
    monkeypatch.setattr(rm, "get_config_store", lambda: _NullStore())
    rm._active_promotions.clear()
    return calls


class _NullStore:
    def set(self, *a, **k):
        pass


@pytest.mark.parametrize(
    "package", ["system-init", "disk-init", "memory-init", "network-init"]
)
def test_promotion_refuses_an_invented_package(spawned, package):
    """The chokepoint. Even if an alert invents a name again, nothing runs."""
    from kazma_core.system.runtime_manager import trigger_package_promotion

    asyncio.run(trigger_package_promotion(package))
    assert spawned == [], f"{package!r} reached the installer"


@pytest.mark.parametrize("package", ["requests", "../../etc/passwd", "evil; rm -rf /", ""])
def test_promotion_refuses_anything_off_the_allowlist(spawned, package):
    """Not just the -init names: an allowlist, not a denylist. `requests` is a
    real package and still must not be installable from an alert card."""
    from kazma_core.system.runtime_manager import trigger_package_promotion

    asyncio.run(trigger_package_promotion(package))
    assert spawned == []


def test_promotion_still_runs_for_a_known_dependency(spawned):
    """The negative control for the guard itself. A guard that refuses
    everything would silently break the ML-extras install this feature exists
    for, and nobody would notice until they needed it."""
    from kazma_core.system.runtime_manager import trigger_package_promotion

    asyncio.run(trigger_package_promotion("sentence-transformers"))
    assert spawned == ["promote:sentence-transformers"]


def test_both_install_paths_share_one_allowlist():
    """`asynchronous_install_package` checked ALLOWED_PACKAGES from the start;
    `trigger_package_promotion` did not, and it is the one the alert buttons
    call. Two installers with two different rules is how the gap appeared."""
    import inspect

    from kazma_core.system import installer, runtime_manager

    for mod, fn in (
        (runtime_manager, "trigger_package_promotion"),
        (installer, "asynchronous_install_package"),
    ):
        src = inspect.getsource(getattr(mod, fn))
        assert "ALLOWED_PACKAGES" in src, f"{fn} does not consult the allowlist"


def test_the_allowlist_holds_no_init_names():
    """If one ever gets added, the layers above stop helping."""
    assert not [p for p in ALLOWED_PACKAGES if p.endswith("-init")]
