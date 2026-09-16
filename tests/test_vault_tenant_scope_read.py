"""A secret saved in the UI must be readable by code that has no tenant.

Live, 2026-09-16. The operator re-entered the DeepSeek API key in Settings ->
Providers. It encrypted and stored correctly. Then:

    Profile provider=deepseek model=deepseek-flash has no usable API key;
    using Z.AI/glm-5.3-flash which has a configured key
    ...
    HTTP 400 {"error":{"code":"1211","message":"Unknown Model, please check
    the model code."}}

The key was in the right vault, under the right name, decrypting with the
right KAZMA_VAULT_KEY. `auth.py` sets the tenant ContextVar to "default" for
a web request, so `store()` wrote `tenant_id='default'`. `retrieve()` fell
back tenant -> global and stopped, so every caller WITHOUT a tenant context
-- the CLI, cron, the agent turn that services Telegram -- read None. The
registry concluded the provider was keyless and substituted another vendor.

`kazma doctor` then reported the secret "lives in another install's vault",
which was confidently wrong and sent the operator to re-enter a key that was
already there.

Fixed at the call sites, not in the vault: `resolve_live_client` installs the
turn's tenant and `kazma_cli.main` installs the process's, exactly as the cron
scheduler was fixed on 2026-09-12. Widening `retrieve` to fall back
global -> tenant would let any context-less background task read another
tenant's credentials -- see `tests/test_cron_tenant_context.py`.
"""

from __future__ import annotations

import logging

import pytest
from kazma_core.security.vault import SecretVault
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

NAME = "cfg:providers.list.deepseek.api_key"
SECRET = "sk-not-a-real-key-0000"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "a" * 64)
    return SecretVault(db_path=str(tmp_path / "vault.db"))


@pytest.fixture
def as_tenant():
    tokens = []

    def _set(tid):
        tokens.append(set_current_tenant_id(tid))

    yield _set
    for tok in reversed(tokens):
        reset_current_tenant_id(tok)


def test_the_llm_chokepoint_runs_as_the_turns_tenant(vault, as_tenant, monkeypatch):
    """The exact live sequence: saved in a request, read from an agent turn."""
    as_tenant("default")
    vault.store(NAME, SECRET, category="config")
    as_tenant(None)  # an agent turn starts with no tenant context

    assert vault.retrieve(NAME) is None, "the hole itself, still present"

    seen = {}
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    with live_llm._turn_tenant({"tenant_id": "default"}):
        seen["tenant"] = get_current_tenant_id()
        seen["key"] = vault.retrieve(NAME)

    assert seen["tenant"] == "default"
    assert seen["key"] == SECRET, (
        "the registry builds its client inside this block; reading None here "
        "is what substituted Z.AI and produced the 1211"
    )
    assert get_current_tenant_id() is None, "the tenant must not leak out"


def test_turn_tenant_defaults_when_state_carries_none(vault):
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    for state in ({}, None, {"tenant_id": ""}):
        with live_llm._turn_tenant(state):
            assert get_current_tenant_id() == "default"


def test_turn_tenant_never_overrides_an_ambient_one(as_tenant):
    """An HTTP request already resolved its tenant; state must not beat it."""
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    as_tenant("alice")
    with live_llm._turn_tenant({"tenant_id": "bob"}):
        assert get_current_tenant_id() == "alice"


def test_turn_tenant_resets_even_when_the_body_raises():
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    with pytest.raises(RuntimeError), live_llm._turn_tenant({"tenant_id": "x"}):
        raise RuntimeError("turn blew up")
    assert get_current_tenant_id() is None


def test_resolve_live_client_builds_the_client_inside_the_tenant():
    """Wiring guard: the fix is worthless if the block moves off get_client."""
    import inspect

    from kazma_core.runtime import live_llm

    src = inspect.getsource(live_llm.resolve_live_client)
    assert "with _turn_tenant(state):" in src
    body = src.split("with _turn_tenant(state):", 1)[1]
    assert body.lstrip().startswith("registry_client = get_model_registry().get_client")


def test_the_cli_installs_a_tenant_at_entry():
    """`kazma doctor` must see what the app sees."""
    import inspect

    from kazma_cli import main as cli_main

    src = inspect.getsource(cli_main.main)
    assert "set_current_tenant_id(" in src
    assert "KAZMA_TENANT_ID" in src, "multi-tenant operators need the override"


def test_ambient_tenant_still_wins_over_another_tenants_row(vault, as_tenant):
    """The fallback must not override a caller that HAS a tenant."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")
    as_tenant("bob")
    vault.store(NAME, "bob-key")

    as_tenant("alice")
    assert vault.retrieve(NAME) == "alice-key"


def test_global_row_still_wins_over_a_tenant_row(vault, as_tenant):
    """The new path is a last resort, reached only when global has nothing."""
    vault.store(NAME, "global-key", tenant_id=None)
    as_tenant("default")
    vault.store(NAME, "tenant-key")
    as_tenant(None)

    assert vault.retrieve(NAME) == "global-key"


def test_a_tenant_scoped_secret_never_leaks_to_a_context_less_caller(
    vault, as_tenant
):
    """The property the cron fix chose to preserve. Do not widen this."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")
    as_tenant(None)

    assert vault.retrieve(NAME) is None


def test_missing_name_is_still_none(vault):
    assert vault.retrieve("cfg:nothing.here") is None


def test_explicit_tenant_arg_is_not_widened(vault, as_tenant):
    """Asking for a named tenant means that tenant, not "whoever has one"."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")

    assert vault.retrieve(NAME, tenant_id="bob") is None


def test_describe_secret_reports_scope_without_leaking_the_value(vault, as_tenant):
    """doctor's evidence source: facts about the row, never the row."""
    as_tenant("default")
    vault.store(NAME, SECRET)

    rows = vault.describe_secret(NAME)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"
    assert rows[0]["decrypts"] is True
    assert SECRET not in repr(rows)
    assert vault.describe_secret("cfg:nothing.here") == []


def test_describe_secret_flags_a_row_written_under_a_different_vault_key(
    tmp_path, monkeypatch, as_tenant
):
    """The case doctor used to assert for every unreadable pointer."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "a" * 64)
    db = str(tmp_path / "vault.db")
    as_tenant("default")
    SecretVault(db_path=db).store(NAME, SECRET)

    monkeypatch.setenv("KAZMA_VAULT_KEY", "b" * 64)
    rows = SecretVault(db_path=db).describe_secret(NAME)
    assert rows and rows[0]["decrypts"] is False


# ── the tripwire: any FUTURE entry point that forgets ──────────────────────
#
# The three fixes above are call-site fixes, and a test that lists call sites
# is the gate that let all seven 2026-09-16 audit defects through: it passes
# while the entry point nobody listed is wrong. So the durable guard is at
# the miss itself, in the vault, where every caller passes.


def test_a_context_less_miss_says_the_caller_is_at_fault(vault, as_tenant, caplog):
    as_tenant("default")
    vault.store(NAME, SECRET)
    as_tenant(None)

    with caplog.at_level("WARNING"):
        assert vault.retrieve(NAME) is None

    assert "NOT FOUND" in caplog.text
    assert "default" in caplog.text, "name the scope that does have it"
    assert "set_current_tenant_id" in caplog.text, "name the actual fix"
    assert SECRET not in caplog.text, "a warning must never print the secret"


def test_a_genuinely_absent_secret_is_quiet(vault, caplog):
    """Absent is not a bug; warning about it would train people to ignore it."""
    with caplog.at_level("WARNING"):
        assert vault.retrieve("cfg:never.stored") is None
    assert "NOT FOUND" not in caplog.text


def test_an_explicit_tenant_miss_is_quiet(vault, as_tenant, caplog):
    """Asking for bob and getting nothing is an answer, not a mistake."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")

    with caplog.at_level("WARNING"):
        assert vault.retrieve(NAME, tenant_id="bob") is None
    assert "NOT FOUND" not in caplog.text


def test_the_warning_fires_once_per_name_not_once_per_turn(vault, as_tenant, caplog):
    """A missing tenant repeats every turn; a log that repeats is ignored."""
    as_tenant("default")
    vault.store(NAME, SECRET)
    as_tenant(None)

    with caplog.at_level("WARNING"):
        for _ in range(5):
            vault.retrieve(NAME)
    assert caplog.text.count("NOT FOUND") == 1


def test_a_second_name_still_gets_its_own_warning(vault, as_tenant, caplog):
    as_tenant("default")
    vault.store(NAME, SECRET)
    vault.store("cfg:providers.list.groq.api_key", SECRET)
    as_tenant(None)

    with caplog.at_level("WARNING"):
        vault.retrieve(NAME)
        vault.retrieve("cfg:providers.list.groq.api_key")
    assert caplog.text.count("NOT FOUND") == 2


def test_an_absent_secret_is_looked_up_once_not_once_per_call(vault, as_tenant):
    """The tripwire must not cost a query per miss.

    The first version recorded only names it actually warned about, so a
    GENUINELY ABSENT secret re-ran the locked `SELECT DISTINCT tenant_id` on
    every single miss. Config resolution reads absent keys constantly, and CI
    wall time went 1,487s -> 1,937s with one file tipping over into a hang.
    A diagnostic that costs a query per call is a performance bug in a
    helpful hat.
    """
    as_tenant(None)
    seen: list[str] = []

    class CountingConn:
        """sqlite3.Connection.execute is read-only, so wrap the connection."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **kw):
            seen.append(" ".join(str(sql).split()))
            return self._inner.execute(sql, *a, **kw)

        def __getattr__(self, item):
            return getattr(self._inner, item)

    real = vault._conn
    vault._conn = CountingConn(real)
    try:
        for _ in range(20):
            assert vault.retrieve("cfg:never.stored") is None
    finally:
        vault._conn = real

    probes = [s for s in seen if "SELECT DISTINCT tenant_id" in s]
    assert len(probes) == 1, (
        f"{len(probes)} scope probes for 20 misses — it must be cached after "
        "the first, whatever the verdict was"
    )


def test_a_miss_acquires_the_lock_exactly_once(vault, as_tenant):
    """The tripwire must not add a second acquire/release cycle.

    The first version released the lock, then took it again to run its probe.
    That window let another thread in, and `test_documents_api_phase8.py`
    hung in app SHUTDOWN on Linux — twice on the same commit — while passing
    on Windows in identical wall-clock time. A hang that depends on thread
    scheduling is what a lock-window bug looks like, and KNOWN_GAPS already
    records the class: "adding a read inside a lock-holding method can
    deadlock it".
    """
    as_tenant("default")
    vault.store(NAME, SECRET)
    as_tenant(None)

    acquires = {"n": 0}
    real = vault._lock

    class CountingLock:
        def __enter__(self):
            acquires["n"] += 1
            return real.__enter__()

        def __exit__(self, *a):
            return real.__exit__(*a)

    vault._lock = CountingLock()
    try:
        assert vault.retrieve(NAME) is None
    finally:
        vault._lock = real

    assert acquires["n"] == 1, (
        f"a miss took the lock {acquires['n']}x; the probe belongs inside the "
        "acquisition retrieve already makes"
    )


def test_the_warning_is_emitted_outside_the_lock(vault, as_tenant):
    """A logging handler is arbitrary code, and this path runs at shutdown."""
    as_tenant("default")
    vault.store(NAME, SECRET)
    as_tenant(None)

    held: list[bool] = []

    class Spy(logging.Handler):
        def emit(self, record):
            held.append(vault._lock.locked())

    import kazma_core.security.vault as vault_mod

    handler = Spy()
    vault_mod.logger.addHandler(handler)
    try:
        vault.retrieve(NAME)
    finally:
        vault_mod.logger.removeHandler(handler)

    assert held, "the warning did not fire"
    assert not any(held), "the vault lock was held while calling a log handler"
