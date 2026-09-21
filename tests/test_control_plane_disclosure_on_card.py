"""An approval card must name Kazma's own stores when a command touches them.

File tools cannot write the control plane at all — `path_policy` rule 0 refuses
it before the allow ladder. `shell_exec` and `python_exec` do not go through
path policy: after approval they are host power, which THREAT_MODEL states
plainly and which this does not change.

What it changes is what the operator is looking at when they decide. The
difference between

    sqlite3 kazma-data/hitl_gates.db "update gates set state='approved'"

and any other `sqlite3` invocation is one filename in the middle of a long
line — and `hitl_gates.db` is the record of what that operator themselves
approved. A card that shows the command without naming what it touches is
asking for a decision while withholding the fact that decides it.

Deliberately a DISCLOSURE and not a block. A command-string check is
bypassable by a shell variable, an encoding, or a `python -c`, so refusing
here would claim a guarantee it cannot keep while breaking legitimate
read-only inspection. The human is the control; this hands the human the fact.
"""

from __future__ import annotations

import pytest

from kazma_core.workspace.path_policy import control_plane_db_names


def test_the_registry_is_in_the_name_list():
    names = control_plane_db_names()
    assert "hitl_gates.db" in names, (
        "the gate registry is the one store whose name MUST be disclosed — it "
        "is the record of what the operator approved"
    )


@pytest.mark.parametrize(
    "expected",
    ["vault.db", "rbac.db", "audit.db", "settings.db"],
)
def test_the_other_sensitive_stores_are_listed(expected):
    """Secrets, permissions, the audit trail, and configuration."""
    assert expected in control_plane_db_names()


def test_names_are_derived_not_hardcoded(monkeypatch):
    """A store added to `paths` must be covered without editing this list.

    The write refusal matches by suffix for the same reason. A disclosure that
    needs a human to remember it is one that goes stale the first time nobody
    does.
    """
    import kazma_core.paths as paths

    monkeypatch.setattr(paths, "audit_db", lambda: "/anywhere/renamed_audit.db")
    assert "renamed_audit.db" in control_plane_db_names(), (
        "the list is hardcoded rather than derived from kazma_core.paths"
    )


def test_a_broken_paths_helper_does_not_break_the_card(monkeypatch):
    """A disclosure that raises would take the approval card with it."""
    import kazma_core.paths as paths

    def _boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(paths, "vault_db_path", _boom)

    names = control_plane_db_names()          # must not raise
    assert "hitl_gates.db" in names, (
        "one unresolvable helper emptied the whole list; the registry must "
        "survive because it is not sourced from paths at all"
    )


def test_ordinary_paths_are_not_flagged():
    """The disclosure must stay rare enough to mean something."""
    names = control_plane_db_names()
    for innocuous in ("notes.txt", "app.db", "project.sqlite", "data.db"):
        assert innocuous not in names, (
            f"{innocuous} would trigger the warning on ordinary work, and a "
            "warning that fires on ordinary work is one people click past"
        )
