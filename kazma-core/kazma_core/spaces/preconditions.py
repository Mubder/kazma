"""What must hold before a second person is let in (Spaces S1-33).

An install with one account behaves as it did before Spaces. The moment a
second account exists, every gap between "one operator" and "several people"
is somebody's data. So Spaces is switched on, and a second account made, only
when every precondition here holds; a missing one refuses and is named.

* :func:`check` runs every precondition and says what each found.
  ``kazma spaces migrate --check`` prints it. It reads only.
* :func:`missing` is the ones that do not hold.
* :func:`require` refuses with them (``people.create_person`` asks it for
  every new account; :func:`switch_on` asks it before the switch is stored).
* :func:`switch_on` is the one way the product turns Spaces on.
* :func:`plan` and :func:`apply` are the move's own steps -- the ones Kazma
  can make itself (one value per secret, a key per space, the owner's API
  token). The rest are named with what closes them.

The preconditions (:data:`PRECONDITIONS`):

``unbound_work``
    Work that binds no space refuses wherever it would read a space's data.
    Asked two ways: each function that answers "which space" is called with
    nothing bound, as an enforcing install would call it
    (``scope.as_if_enforced``), and must refuse; and the fallbacks to the
    owner's space still written out in the code are counted
    (:data:`TENANT_FALLBACKS_LEFT`, the S1-02 ratchet).
``vault_copies``
    No owner holds a secret in two rows that disagree
    (``SecretVault.copies_that_disagree``).
``daily_limits``, ``feature_grants``
    A daily limit per person (S2-02) and features per person (S2-11) are
    built: no friend uses the owner's model keys without a limit and a grant.
``encrypted_copy``
    No plain copy of the backup is set to leave the machine (decision N10).
``owner_token``
    The deploy check signs in with an owner API token, not the shared secret
    (decision N15).
``space_keys``
    Every space but the owner's has a key, and none of its secrets is still
    under the install's vault key or names a key that is lost (S1-26).
``earlier_mode`` (the switch only)
    The older multi-user mode left no accounts whose threads would need the
    move of plan 7.10, which is not built.

A check that cannot be made is a precondition that does not hold: unknown is
never read as passed. Every function here reads settings or the vault: call
it off the event loop.
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib.util
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, NoSpaceBound

__all__ = [
    "OWNER_TOKEN_FILE",
    "PRECONDITIONS",
    "TENANT_FALLBACKS_LEFT",
    "Finding",
    "PreconditionsMissing",
    "apply",
    "check",
    "missing",
    "notes",
    "owner_token_path",
    "plan",
    "require",
    "switch_on",
]

logger = logging.getLogger(__name__)

#: Fallbacks to the owner's space still written out in product code: the
#: number ``tests/test_no_space_no_data.py`` counts (S1-02's ratchet, held
#: equal to this). It only goes down; at zero the precondition's second half
#: holds.
TENANT_FALLBACKS_LEFT = 205

#: The owner's API token for the deploy check, in Kazma's home
#: (``<install>/.kazma``): one of Kazma's secret files.
OWNER_TOKEN_FILE = "owner_api.token"
_OWNER_TOKEN_NAME = "deploy check (owner)"

#: Where each move step records itself (runtime state, never a setting).
_JOURNAL_KEY = "system.spaces.migrate"


@dataclass(frozen=True)
class Finding:
    """What one precondition found. ``detail`` never holds a secret."""

    id: str
    title: str
    ok: bool
    detail: str
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "ok": self.ok, "detail": self.detail, "fix": self.fix}


class PreconditionsMissing(RuntimeError):
    """A second account, or the switch, was refused: these are missing."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = list(findings)
        super().__init__(
            "Not yet: " + "; ".join(f"{f.title}: {f.detail}" for f in self.findings)
        )


# ── unbound_work ────────────────────────────────────────────────────────


def _probe_scope() -> str:
    from kazma_core.spaces.scope import require_space_id

    return require_space_id()


def _probe_tenant() -> str:
    from kazma_core.tenant_isolation import require_tenant_id

    return require_tenant_id()


def _probe_tools() -> str:
    from kazma_core.safety.hitl import get_current_tenant_id

    return get_current_tenant_id()


def _probe_state() -> str:
    from kazma_core.spaces.scope import space_of_state

    return space_of_state({})


def _probe_accounts() -> str:
    from kazma_core.spaces.connected_accounts import connected_space

    return connected_space()


def _probe_x() -> str:
    from kazma_core.x_api.ownership import x_tenant_id

    return x_tenant_id()


def _probe_lessons() -> str:
    from kazma_core.skills.self_improvement import soul_space

    return soul_space()


#: Each function that answers "which space is this work in". With nothing
#: bound, on an enforcing install, every one refuses (``NoSpaceBound``).
PROBES: tuple[tuple[str, Callable[[], str]], ...] = (
    ("the scope (spaces.scope.require_space_id)", _probe_scope),
    ("every store's key (tenant_isolation.require_tenant_id)", _probe_tenant),
    ("the agent's tools (safety.hitl.get_current_tenant_id)", _probe_tools),
    ("a turn's state (spaces.scope.space_of_state)", _probe_state),
    ("connected accounts (connected_accounts.connected_space)", _probe_accounts),
    ("the X account (x_api.ownership.x_tenant_id)", _probe_x),
    ("lessons (self_improvement.soul_space)", _probe_lessons),
)


def _answers_with_nothing_bound(probe: Callable[[], str]) -> str | None:
    """What *probe* answers on an enforcing install with nothing bound, or
    None when it refuses. Run in an empty context: no scope, no tenant, no
    install work, whatever the caller has bound."""
    from kazma_core.spaces.scope import as_if_enforced

    def _ask() -> str | None:
        with as_if_enforced():
            try:
                return str(probe())
            except NoSpaceBound:
                return None

    return contextvars.Context().run(_ask)


def _unbound_work() -> tuple[bool, str, str]:
    answered = [
        f"{what} answers {got!r}" for what, probe in PROBES
        if (got := _answers_with_nothing_bound(probe)) is not None
    ]
    parts = list(answered)
    if TENANT_FALLBACKS_LEFT:
        parts.append(
            f"{TENANT_FALLBACKS_LEFT} places in the code still fall back to the owner's "
            "space when no space is named (the S1-02 ratchet)"
        )
    if not parts:
        return True, "work with no space bound is refused everywhere it is asked", ""
    return (False, "; ".join(parts),
            "This is Kazma's own code (plan item S1-02): it closes with a Kazma update, not a setting.")


# ── vault_copies ────────────────────────────────────────────────────────


def _vault() -> Any:
    from kazma_core.security.vault import get_vault

    return get_vault()


def _whose(entry: dict[str, Any]) -> str:
    if entry["whose"] == "install":
        return "the install's"
    return "the owner's space's" if entry["whose"] == OWNER_SPACE_ID else f"space {entry['whose']}'s"


def _vault_copies() -> tuple[bool, str, str]:
    vault = _vault()
    if vault is None:
        return True, "the vault is off: no secret is stored", ""
    found = vault.copies_that_disagree()
    if not found:
        return True, "every secret has one value for its owner", ""
    names = ", ".join(f"{e['name']} ({_whose(e)}, {len(e['copies'])} copies)" for e in found)
    return (False, f"{len(found)} secret(s) are held in copies that disagree: {names}",
            "kazma spaces migrate gives each its newest copy's value (the install's mail names: the global copy's).")


# ── daily_limits, feature_grants ────────────────────────────────────────

#: The module each stage-2 item brought forward answers from, once built.
_BUILT_BY = {
    "daily_limits": ("kazma_core.spaces.daily_limits", "S2-02"),
    "feature_grants": ("kazma_core.spaces.features", "S2-11"),
}


def _module_answer(module: str) -> tuple[bool, str] | None:
    """What the module that builds a per-person rule says of it, or None
    when the module is not there. A module present is not the rule in
    force: it must name ``precondition() -> (holds, why)``, its own check
    that the rule applies to every person, and that check must hold."""
    try:
        if importlib.util.find_spec(module) is None:
            return None
    except (ImportError, ValueError):
        return None
    loaded = importlib.import_module(module)
    check = getattr(loaded, "precondition", None)
    if not callable(check):
        return False, f"{module} names no precondition() of its own"
    holds, why = check()
    return bool(holds), str(why or "")


def _built(which: str, what: str) -> tuple[bool, str, str]:
    module, item = _BUILT_BY[which]
    answer = _module_answer(module)
    if answer is None:
        return (False, f"{what} is not built yet (plan item {item})",
                f"It closes with the Kazma update that builds {item}.")
    holds, why = answer
    if holds:
        return True, f"{what} holds ({item}): {why}" if why else f"{what} holds ({item})", ""
    return (False, f"{what} does not hold yet ({item}): {why}" if why else f"{what} does not hold yet ({item})",
            f"See {item}'s own setting.")


def _daily_limits() -> tuple[bool, str, str]:
    return _built("daily_limits", "a daily spending limit per person")


def _feature_grants() -> tuple[bool, str, str]:
    return _built("feature_grants", "features per person")


# ── encrypted_copy ──────────────────────────────────────────────────────


def _encrypted_copy() -> tuple[bool, str, str]:
    from kazma_core.backup.universal import plain_copy_configured

    if not plain_copy_configured():
        return True, "no plain copy of the backup is set to leave the machine", ""
    return (False, "a plain copy of the backup is set to leave the machine (a provider's zip or rclone's folder)",
            "Settings -> Backup: use restic's encrypted copy and turn the plain copy off.")


# ── owner_token ─────────────────────────────────────────────────────────


def owner_token_path() -> Path:
    """``<install>/.kazma/owner_api.token``."""
    from kazma_core.paths import user_home

    return user_home() / OWNER_TOKEN_FILE


def _store() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _owner_token_state() -> str:
    """``ok``, ``no_file``, ``unknown`` (the file's token is not a stored
    admin token) or ``expired``."""
    from kazma_core.settings_manager import stored_api_tokens

    path = owner_token_path()
    if not path.is_file():
        return "no_file"
    held = hashlib.sha256(path.read_text(encoding="utf-8").strip().encode()).hexdigest()
    for entry in stored_api_tokens(_store()):
        if str(entry.get("token_hash") or "") != held or str(entry.get("role") or "") != "admin":
            continue
        try:
            made = datetime.fromisoformat(str(entry.get("created_at") or "").replace("Z", "+00:00"))
            if made.tzinfo is None:
                made = made.replace(tzinfo=UTC)
            days = int(entry.get("expires_days") or 0)
        except (TypeError, ValueError):
            return "expired"
        return "expired" if days and (datetime.now(UTC) - made).days > days else "ok"
    return "unknown"


def _owner_token() -> tuple[bool, str, str]:
    state = _owner_token_state()
    if state == "ok":
        return True, "the deploy check has an owner API token", ""
    said = {
        "no_file": "the deploy check has no owner API token: it signs in with the shared secret",
        "unknown": "the deploy check's token is not one of this install's admin API tokens",
        "expired": "the deploy check's owner API token has expired",
    }[state]
    return False, said, "kazma spaces migrate makes the token and keeps it in Kazma's home, readable by you only."


# ── space_keys ──────────────────────────────────────────────────────────


def _space_keys() -> tuple[bool, str, str]:
    from kazma_core.spaces import space_keys

    found = space_keys.report()
    if not found["vault"]:
        return True, "the vault is off: no secret is stored", ""
    parts: list[str] = []
    if found["no_key"]:
        parts.append(f"{len(found['no_key'])} space(s) have no key: {', '.join(found['no_key'])}")
    unsealed = {s: n for s, n in found["unsealed"].items() if n}
    if unsealed:
        parts.append("secrets still under the install's vault key: "
                     + ", ".join(f"{s} ({n})" for s, n in sorted(unsealed.items())))
    if found["broken"]:
        parts.append(f"{found['broken']} secret(s) name a key that is lost")
    if not parts:
        return True, "every space has its key and its secrets are sealed with it", ""
    fix = "kazma spaces migrate makes the missing keys and seals those secrets."
    if found["broken"]:
        fix += " A secret whose key is lost is not recoverable here: restore the vault from a backup that holds the key."
    return False, "; ".join(parts), fix


# ── earlier_mode (the switch only) ──────────────────────────────────────


def _earlier_mode() -> tuple[bool, str, str]:
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import spaces_enforced

    if spaces_enforced():
        return True, "Spaces is on: accounts are made by People", ""
    others = sorted(a for a in identity.read_registry()["accounts"] if a != OWNER_ACCOUNT_ID)
    if not others:
        return True, "no account from the older multi-user mode", ""
    return (False,
            f"{len(others)} account(s) from the older multi-user mode: the move of what their chats "
            "filed under another key (plan 7.10) is not built",
            "Remove those users (Settings), or wait for the Kazma update that builds the move.")


# ── The table ───────────────────────────────────────────────────────────

#: id -> (title, check, asked for a new account too -- False: the switch only).
PRECONDITIONS: dict[str, tuple[str, Callable[[], tuple[bool, str, str]], bool]] = {
    "unbound_work": ("Work with no space is refused", _unbound_work, True),
    "vault_copies": ("One value per secret", _vault_copies, True),
    "daily_limits": ("A daily limit per person", _daily_limits, True),
    "feature_grants": ("Features per person", _feature_grants, True),
    "encrypted_copy": ("Copies that leave the machine are encrypted", _encrypted_copy, True),
    "owner_token": ("The deploy check signs in with an owner API token", _owner_token, True),
    "space_keys": ("A key per space", _space_keys, True),
    "earlier_mode": ("Nothing left of the older multi-user mode", _earlier_mode, False),
}


def _check_errors() -> tuple[type[BaseException], ...]:
    """What a check can fail with: its stores (settings on either backend,
    the vault, memory), a file, or an answer of a shape it did not expect."""
    import sqlite3

    from kazma_core.db.pg_helpers import store_errors

    return (*store_errors(), sqlite3.Error, OSError, RuntimeError, ValueError, LookupError,
            TypeError, AttributeError, ImportError)


def _find(pid: str) -> Finding:
    title, ask, _always = PRECONDITIONS[pid]
    try:
        ok, detail, fix = ask()
    except _check_errors() as exc:  # unknown is never passed
        logger.warning("[spaces] the precondition %s could not be checked (%s)", pid, type(exc).__name__,
                       exc_info=True)
        return Finding(pid, title, False, f"could not be checked ({type(exc).__name__})",
                       "See kazma.log for the reason, then check again.")
    return Finding(pid, title, bool(ok), detail, fix)


def check(*, for_switch: bool = True) -> list[Finding]:
    """Every precondition's finding, in order. Reads only. *for_switch*
    False leaves out the ones asked at the switch alone."""
    return [_find(pid) for pid, (_t, _c, always) in PRECONDITIONS.items() if for_switch or always]


def missing(*, for_switch: bool = False) -> list[Finding]:
    """The preconditions that do not hold."""
    return [f for f in check(for_switch=for_switch) if not f.ok]


def require(*, for_switch: bool = False) -> None:
    """Raise :class:`PreconditionsMissing` naming each precondition that
    does not hold."""
    left = missing(for_switch=for_switch)
    if left:
        raise PreconditionsMissing(left)


def notes() -> list[str]:
    """What the owner should know before the switch that refuses nothing."""
    out: list[str] = []
    import sqlite3
    from contextlib import closing

    try:
        from kazma_core import paths
        from kazma_core.memory.tenant_view import tenants_outside_spaces

        memory = Path(paths.primary_memory_db())
        outside: list[str] = []
        if memory.is_file():
            with closing(sqlite3.connect(f"file:{memory.as_posix()}?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                outside = [str(t["tenant"]) for t in tenants_outside_spaces(conn)["tenants"]]
        if outside:
            out.append(
                f"Memory is held under {len(outside)} key(s) that are no space "
                f"({', '.join(sorted(outside))}): once Spaces is on it is "
                "reachable from no space. It is counted, never merged (plan 7.4)."
            )
    except (sqlite3.Error, OSError, KeyError, ValueError) as exc:  # a note, never a refusal
        out.append(f"Memory outside every space could not be counted ({type(exc).__name__}).")
    return out


# ── The switch ──────────────────────────────────────────────────────────


def switch_on(*, by: str) -> None:
    """Turn Spaces on for this install: the one way the product does it.
    Refused (:class:`PreconditionsMissing`) while any precondition is
    missing. This process follows at once; a running Kazma reads the stored
    switch at its next start."""
    from kazma_core.spaces import identity

    require(for_switch=True)
    _store().set(identity.ENFORCED_KEY, True, category="identity")
    identity.prime_enforcement()
    logger.warning("[spaces] Spaces was switched on by %s", by)


# ── The move's own steps ────────────────────────────────────────────────


def _record(step: str, state: str) -> None:
    store = _store()
    journal = store.get(_JOURNAL_KEY, {})
    journal = dict(journal) if isinstance(journal, dict) else {}
    journal[step] = {"state": state, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    store.set(_JOURNAL_KEY, journal, category="system")


def plan() -> list[dict[str, Any]]:
    """What :func:`apply` would change, step by step. Reads only. A step
    with nothing to do is listed with ``changes`` empty."""
    steps: list[dict[str, Any]] = []
    vault = _vault()
    copies = vault.copies_that_disagree() if vault is not None else []
    steps.append({
        "step": "vault_copies",
        "what": "Give each secret held in copies that disagree its winning copy's value",
        "changes": [
            f"{e['name']} ({_whose(e)}): the copy of {e['wins']['updated_at'] or 'unknown date'} "
            f"(tenant {e['wins']['tenant_id'] or 'none'}) is written over {len(e['copies']) - 1} other(s)"
            for e in copies
        ],
    })
    from kazma_core.spaces import space_keys

    keys = space_keys.report() if vault is not None else {"no_key": [], "unsealed": {}}
    changes = [f"make a key for space {s}" for s in keys["no_key"]]
    changes += [f"seal {n} secret(s) of space {s} with its key"
                for s, n in sorted(keys["unsealed"].items()) if n]
    steps.append({"step": "space_keys", "what": "A key for every space, and its secrets sealed with it",
                  "changes": changes})
    token = _owner_token_state()
    steps.append({
        "step": "owner_token",
        "what": "The owner's API token for the deploy check",
        "changes": [] if token == "ok" else [
            f"make an admin API token named {_OWNER_TOKEN_NAME!r} (90 days) and keep it in "
            f"{owner_token_path()}, readable by you only"
        ],
    })
    return steps


def _back_up_vault() -> str:
    """A copy of the vault's file beside it, before the vault is changed.
    Returns its path ("" when there is no vault file)."""
    from kazma_core import paths
    from kazma_core.db.sqlite_snapshot import snapshot_copy
    from kazma_core.security.file_access import restrict_to_owner

    source = Path(paths.vault_db_path())
    if not source.is_file():
        return ""
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    copy = source.with_name(f".migrate-vault-backup-spaces-{stamp}.db")
    snapshot_copy(source, copy)
    if not restrict_to_owner(copy):
        copy.unlink(missing_ok=True)
        raise OSError("the copy of the vault could not be made readable by you only; nothing was changed")
    return str(copy)


def _make_owner_token() -> None:
    from kazma_core.security.file_access import write_private
    from kazma_core.settings_manager import create_api_token_in

    made = create_api_token_in(_store(), _OWNER_TOKEN_NAME, "admin")
    write_private(owner_token_path(), made["token"] + "\n")


def apply() -> dict[str, Any]:
    """Make the steps :func:`plan` lists. The vault is copied first; each
    step is recorded before it is made and after; a second run changes
    nothing. Returns what was done. It never turns Spaces on
    (:func:`switch_on`)."""
    from kazma_core.spaces import space_keys

    todo = {s["step"]: s["changes"] for s in plan()}
    done: dict[str, Any] = {"backup": "", "vault_copies": [], "space_keys": {"keyed": [], "sealed": {}},
                            "owner_token": False}
    vault = _vault()
    if vault is not None and (todo["vault_copies"] or todo["space_keys"]):
        done["backup"] = _back_up_vault()
    if todo["vault_copies"]:
        _record("vault_copies", "started")
        done["vault_copies"] = vault.settle_copies()
        _record("vault_copies", "done")
    if todo["space_keys"]:
        _record("space_keys", "started")
        found = space_keys.report()
        done["space_keys"]["keyed"] = space_keys.ensure_keys(found["no_key"])
        for space, count in sorted(found["unsealed"].items()):
            if count:
                done["space_keys"]["sealed"][space] = vault.seal_space(space)
        _record("space_keys", "done")
    if todo["owner_token"]:
        _record("owner_token", "started")
        _make_owner_token()
        done["owner_token"] = True
        _record("owner_token", "done")
    return done
