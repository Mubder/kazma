"""No space, no data: the scope has no default once Spaces is enforced (S1-02).

``require_tenant_id()`` answered ``"default"`` -- the owner's data -- to any
code with no tenant bound, whatever the install was, and swallowed every
error on the way. Right for one operator; with a second person it is the one
way their work quietly acts as the owner. The scope
(``kazma_core.spaces.scope``) is two values bound together, the space and
the asker:

* bound: that scope;
* install work (``install_work``): no space, ever;
* nothing bound: the owner's scope while the install does not enforce Spaces,
  ``NoSpaceBound`` once it does.

The old fallback is the negative control. The source ratchet at the end
counts the tenant fallbacks still written out in product code: each store's
item of stage 1 removes its own, and the number may only go down.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest
from kazma_core import tenant_isolation
from kazma_core.spaces import scope
from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    NoSpaceBound,
    Scope,
    bind_scope,
    install_work,
)
from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch):
    """This process as an install that enforces Spaces."""
    monkeypatch.setattr(scope, "_enforced", True)


# ── One operator: exactly as before Spaces ───────────────────────────────


def test_one_operator_unbound_work_is_the_owners() -> None:
    assert scope.spaces_enforced() is False
    assert scope.require_scope() == Scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert scope.require_space_id() == "default"
    assert tenant_isolation.require_tenant_id() == "default"


def test_one_operator_a_bound_tenant_is_the_space() -> None:
    """Older entry points bind the tenant alone; the space is that tenant."""
    with tenant_scope("acme"):
        assert tenant_isolation.require_tenant_id() == "acme"
        assert scope.require_scope() == Scope("acme", OWNER_ACCOUNT_ID)


# ── A bound scope ────────────────────────────────────────────────────────


def test_a_bound_scope_is_the_answer_and_binds_the_tenant() -> None:
    assert scope.current_scope() is None
    with bind_scope("family", "layla") as bound:
        assert bound == Scope("family", "layla")
        assert scope.require_scope() == bound
        assert scope.require_space_id() == "family"
        assert tenant_isolation.require_tenant_id() == "family"
        assert get_current_tenant_id() == "family", "stores that read the tenant read this space"
        with bind_scope("layla-own", "layla"):
            assert scope.require_space_id() == "layla-own"
        assert scope.require_space_id() == "family", "the outer scope is back"
    assert scope.current_scope() is None
    assert get_current_tenant_id() is None


def test_the_scope_follows_threads_and_tasks() -> None:
    async def _run() -> tuple[Scope, Scope]:
        with bind_scope("family", "layla"):
            in_thread = await asyncio.to_thread(scope.require_scope)
            in_task = await asyncio.create_task(asyncio.to_thread(scope.require_scope))
            return in_thread, in_task

    in_thread, in_task = asyncio.run(_run())
    assert in_thread == in_task == Scope("family", "layla")


@pytest.mark.parametrize("space_id, asker", [
    ("", "layla"), ("Family", "layla"), ("../etc", "layla"), ("a b", "layla"),
    ("family", ""), ("family", "Layla Al"), ("x" * 65, "layla"), (None, "layla"),
])
def test_a_scope_needs_a_real_space_and_asker(space_id, asker) -> None:
    with pytest.raises(ValueError):
        with bind_scope(space_id, asker):
            pass
    assert scope.current_scope() is None


# ── Enforced: no space, no data ──────────────────────────────────────────


def test_enforced_unbound_work_is_refused(enforced) -> None:
    for ask in (scope.require_scope, scope.require_space_id, tenant_isolation.require_tenant_id):
        with pytest.raises(NoSpaceBound):
            ask()


def test_enforced_a_bound_scope_still_works(enforced) -> None:
    with bind_scope("family", "layla"):
        assert tenant_isolation.require_tenant_id() == "family"
        assert scope.require_scope().asker == "layla"
    with pytest.raises(NoSpaceBound):
        tenant_isolation.require_tenant_id()


def test_enforced_a_tenant_with_no_asker_is_half_a_scope(enforced) -> None:
    """An older entry point that binds the tenant alone: the space is known,
    so storage keys still resolve, and asking who is asking is refused."""
    with tenant_scope("family"):
        assert scope.require_space_id() == "family"
        with pytest.raises(NoSpaceBound):
            scope.require_scope()


def test_enforced_tenant_key_refuses_too(enforced) -> None:
    with pytest.raises(NoSpaceBound):
        tenant_isolation.tenant_key("connectors.x.api_key")
    with bind_scope("family", "layla"):
        assert tenant_isolation.tenant_key("connectors.x.api_key") == (
            "tenant.family.connectors.x.api_key"
        )


def test_control_the_old_fallback_answered_the_owners_space(enforced) -> None:
    """``require_tenant_id`` as it was until 2026-11: with nothing bound it
    answers the owner's space on any install."""

    def _old_require_tenant_id() -> str:
        try:
            tid = get_current_tenant_id()
            if tid and str(tid).strip():
                return str(tid).strip()
        except Exception:
            pass
        return "default"

    assert _old_require_tenant_id() == OWNER_SPACE_ID
    with pytest.raises(NoSpaceBound):
        tenant_isolation.require_tenant_id()


# ── Install work reads no space ──────────────────────────────────────────


class _no_refusal:
    """A ``with`` block that expects no exception."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc) -> bool:
        return False


@pytest.mark.parametrize("is_enforced", [False, True])
def test_install_work_has_no_space(monkeypatch, is_enforced: bool) -> None:
    monkeypatch.setattr(scope, "_enforced", is_enforced)
    with bind_scope("family", "layla"):
        with install_work("the nightly backup"):
            assert scope.current_scope() is None
            assert get_current_tenant_id() is None
            for ask in (scope.require_scope, scope.require_space_id,
                        tenant_isolation.require_tenant_id):
                with pytest.raises(NoSpaceBound) as refused:
                    ask()
                assert "the nightly backup" in str(refused.value)
        assert scope.require_space_id() == "family", "the scope is back after install work"
    with pytest.raises(NoSpaceBound) if is_enforced else _no_refusal():
        scope.require_space_id()


def test_install_work_must_say_what_it_is() -> None:
    with pytest.raises(ValueError):
        with install_work("  "):
            pass


# ── The switch is read from memory ───────────────────────────────────────


def test_asking_for_a_space_reads_no_settings(monkeypatch) -> None:
    """Nothing on the path of a store call may read the settings store: the
    switch is kept in memory (``identity.prime_enforcement`` sets it)."""
    import kazma_core.config_store as config_store

    def _no_reads(*_a, **_k):
        raise AssertionError("asking for a space read the settings store")

    monkeypatch.setattr(config_store, "get_config_store", _no_reads)
    assert tenant_isolation.require_tenant_id() == "default"
    monkeypatch.setattr(scope, "_enforced", True)
    with pytest.raises(NoSpaceBound):
        tenant_isolation.require_tenant_id()


def test_the_stored_switch_reaches_the_process(monkeypatch) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity

    store = get_config_store()
    monkeypatch.setattr(scope, "_enforced", False)
    try:
        store.set(identity.ENFORCED_KEY, True, category="auth")
        assert identity.prime_enforcement() is True
        assert scope.spaces_enforced() is True
        store.set(identity.ENFORCED_KEY, False, category="auth")
        assert identity.prime_enforcement() is False
    finally:
        store.delete(identity.ENFORCED_KEY)


def test_an_unreadable_switch_keeps_the_last_answer(monkeypatch) -> None:
    from kazma_core.spaces import identity

    class _Down:
        def get(self, *_a, **_k):
            raise OSError("database is away")

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(identity, "_store", lambda: _Down())
    assert identity.prime_enforcement() is True, "an outage never switches Spaces off"


# ── The ratchet: tenant fallbacks still written out in product code ──────

#: ``<tenant-ish> or "default"`` and ``tenant_id="default"`` defaults on
#: product code paths. Each store's item of stage 1 (S1-08 onward) replaces
#: its own with a bound handle; this number may only go down, and lowering it
#: means lowering the baseline in the same change.
FALLBACK_BASELINE = 28

_FALLBACK_RE = re.compile(
    r"""(?:tenant\w*|tid)\b[^\n=]{0,40}\bor\s+["']default["']"""
    r"""|tenant_id\s*:\s*str(?:\s*\|\s*None)?\s*=\s*["']default["']"""
    r"""|tenant_id\s*=\s*["']default["']"""
)

_PACKAGES = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
             "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui")


def _count_fallbacks(text: str) -> int:
    return sum(1 for _ in _FALLBACK_RE.finditer(text))


def _product_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *_PACKAGES],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [REPO / name for name in listed
            if name.endswith(".py") and "/tests/" not in name
            and not name.endswith("spaces/scope.py")]


def test_tenant_fallbacks_only_go_down() -> None:
    total = sum(_count_fallbacks(path.read_text(encoding="utf-8")) for path in _product_files())
    assert total <= FALLBACK_BASELINE, (
        f"{total} tenant fallbacks to 'default' in product code, above the baseline of "
        f"{FALLBACK_BASELINE}: new code asks kazma_core.spaces.scope for its space"
    )
    assert total >= FALLBACK_BASELINE, (
        f"{total} tenant fallbacks is below the baseline of {FALLBACK_BASELINE}: "
        "lower FALLBACK_BASELINE in this change so the gain is kept"
    )


def test_the_ratchet_sees_each_fallback_shape() -> None:
    assert _count_fallbacks('tid = tenant_id or "default"') == 1
    assert _count_fallbacks("tenant = (get_current_tenant_id() or 'default')") == 1
    assert _count_fallbacks('def f(x, tenant_id: str = "default"): ...') == 1
    assert _count_fallbacks('store.read(key, tenant_id="default")') == 1
    assert _count_fallbacks('def f(x, tenant_id: str | None = "default"): ...') == 1
    assert _count_fallbacks('name = title or "default"') == 0
    assert _count_fallbacks("space_id = require_space_id()") == 0
