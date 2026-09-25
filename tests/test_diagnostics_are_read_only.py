"""A diagnostic may read. It may not write — and that is enforced, not hoped.

Pressing **Test** on a provider once deleted every saved API key: a health
write round-tripped the credential list through a view that could not decrypt
it. The damage got a guard; the class stayed open ("no test or lint asserts
that a diagnostic path may not call a mutating one" — KNOWN_GAPS). It was
already recurring: ``/health/deep`` ran a real ``recall()``, which bumps
``access_count``/``last_accessed`` on what it returns, so every canary poll
kept one arbitrary memory "in use" forever and penalised it in real ranking.

``kazma_core.diagnostic_scope.read_only_diagnostic`` is the rule:

* explicit writes at the ConfigStore and vault chokepoints raise, except the
  keys a scope allows (``/health/deep`` keeps its one canary roundtrip);
* write side effects of reads (recall's access bump, ConfigStore's lazy
  plaintext->vault migration) are skipped;
* every ``/health``, ``readiness`` and ``diagnostics`` route, and
  ``kazma doctor``, runs inside a scope — the gate at the bottom enumerates
  them from the source, with a negative control.

Item 6 of the same plan lives here too: the write veto is a typed object that
cannot be serialized, so a caller that forgets to check it raises instead of
writing ``"null"`` over a secret.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sqlite3
import textwrap
from pathlib import Path

import pytest
from kazma_core import diagnostic_scope as ds
from kazma_core.config_store import (
    _MISSING,
    _VETO_BLANKING,
    _VETO_MASKED,
    ConfigStore,
    _Veto,
    is_vault_ref,
)
from kazma_core.security import vault as vault_mod

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    cs = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    yield cs
    cs.close()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "diagnostic-scope-test-key-0123456789")
    vault_mod.reset_vault()
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    vault_mod._vault = v
    vault_mod._vault_init_attempted = True
    yield v
    vault_mod.reset_vault()


# ── the scope itself ──────────────────────────────────────────────────────


def test_outside_a_diagnostic_nothing_is_refused():
    assert ds.active_diagnostic() is None
    assert ds.writes_suppressed() is False
    ds.refuse_write("config", "anything")  # no-op


def test_inside_a_diagnostic_writes_raise_unless_allowed():
    with ds.read_only_diagnostic("probe", allow=("system.canary.*",)):
        assert ds.writes_suppressed() is True
        ds.refuse_write("config", "system.canary.x")
        with pytest.raises(ds.DiagnosticWriteRefused) as exc:
            ds.refuse_write("config", "providers.list")
    assert "probe" in str(exc.value) and "providers.list" in str(exc.value)
    assert ds.active_diagnostic() is None, "the scope must not leak out"


def test_nested_scopes_only_narrow():
    with ds.read_only_diagnostic("outer", allow=("a.*",)):
        with ds.read_only_diagnostic("inner", allow=("a.*", "b.*")):
            ds.refuse_write("config", "a.1")
            with pytest.raises(ds.DiagnosticWriteRefused):
                ds.refuse_write("config", "b.1")  # the outer scope never allowed it
            assert ds.active_diagnostic() == "outer > inner"


def test_the_scope_follows_to_thread():
    """The deep canary runs its checks through asyncio.to_thread."""

    async def run():
        with ds.read_only_diagnostic("threaded"):
            return await asyncio.to_thread(ds.active_diagnostic)

    assert asyncio.run(run()) == "threaded"


# ── ConfigStore chokepoints ───────────────────────────────────────────────

_MUTATIONS = {
    "set": lambda s: s.set("k.one", "v"),
    "set_if_absent": lambda s: s.set_if_absent("k.one", {"holder": "h"}, ttl=5),
    "atomic_update": lambda s: s.atomic_update("k.one", lambda v: "v2"),
    "batch_set": lambda s: s.batch_set([("k.one", "v", "general")]),
    "delete": lambda s: s.delete("k.one"),
    "import_yaml": lambda s: s.import_yaml("k:\n  one: v\n"),
    "reset_all": lambda s: s.reset_all(),
    "transaction": lambda s: s.transaction().__enter__(),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_every_config_mutation_is_refused_in_a_diagnostic(store, name):
    store.set("k.one", "before")
    with ds.read_only_diagnostic("probe"):
        with pytest.raises(ds.DiagnosticWriteRefused):
            _MUTATIONS[name](store)
        assert store.get("k.one") == "before", "reads still work"
    assert store.get("k.one") == "before", "and nothing was written"


def test_an_allowed_key_round_trips(store):
    with ds.read_only_diagnostic("/health/deep", allow=("system.canary.config_roundtrip",)):
        store.set("system.canary.config_roundtrip", "ping")
        assert store.get("system.canary.config_roundtrip") == "ping"
        assert store.delete("system.canary.config_roundtrip") is True


def test_a_read_does_not_migrate_a_plaintext_secret_inside_a_diagnostic(store, vault):
    """get() of a plaintext secret normally moves it into the vault. Not here."""
    store._write_db_value("llm.api_key", "sk-plaintext-0001", category="llm")

    with ds.read_only_diagnostic("kazma doctor"):
        assert store.get("llm.api_key") == "sk-plaintext-0001"
    assert vault.retrieve("cfg:llm.api_key") is None, "no vault write from a diagnostic"
    assert store._db_get_raw("llm.api_key") == "sk-plaintext-0001"

    store._clear_cache()
    assert store.get("llm.api_key") == "sk-plaintext-0001"
    assert vault.retrieve("cfg:llm.api_key") == "sk-plaintext-0001", "outside, it migrates"
    assert is_vault_ref(store._db_get_raw("llm.api_key"))


def test_the_vault_refuses_writes_in_a_diagnostic(vault):
    with ds.read_only_diagnostic("probe"):
        with pytest.raises(ds.DiagnosticWriteRefused):
            vault.store("n", "v")
        with pytest.raises(ds.DiagnosticWriteRefused):
            vault.delete("n")


# ── recall's access bump ──────────────────────────────────────────────────


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE beliefs (id TEXT, access_count INTEGER, last_accessed REAL)")
    conn.execute("CREATE TABLE episodes (id TEXT, access_count INTEGER, last_accessed REAL)")
    conn.execute("INSERT INTO beliefs VALUES ('b1', 0, NULL)")
    conn.execute("INSERT INTO episodes VALUES ('e1', 0, NULL)")
    conn.commit()
    return conn


def test_recall_does_not_bump_access_inside_a_diagnostic():
    from kazma_core.memory.recall import RecallHit, _bump_access

    conn = _memory_conn()
    hit_b = RecallHit(id="b1", content="", score=1.0, kind="belief")
    hit_e = RecallHit(id="e1", content="", score=1.0)

    with ds.read_only_diagnostic("/health/deep"):
        _bump_access(conn, [hit_b], [hit_e])
    assert conn.execute("SELECT access_count FROM beliefs").fetchone()[0] == 0
    assert conn.execute("SELECT access_count FROM episodes").fetchone()[0] == 0

    _bump_access(conn, [hit_b], [hit_e])  # negative control: a real turn bumps
    assert conn.execute("SELECT access_count FROM beliefs").fetchone()[0] == 1


# ── the routes and the CLI run inside a scope ─────────────────────────────


def test_the_deep_canary_runs_its_checks_in_the_scope(monkeypatch):
    from kazma_ui import health

    seen: dict[str, object] = {}

    def probe(name):
        def _check():
            seen[name] = ds.active_diagnostic()
            return {"status": "ok", "component": name}
        return _check

    async def recall_probe():
        seen["memory_recall"] = (ds.active_diagnostic(), ds.writes_suppressed())
        return {"status": "ok", "component": "memory_recall"}

    for attr in ("_check_config_roundtrip", "_check_workspace_binding",
                 "_check_research_stack", "_check_brain_imports", "check_database"):
        monkeypatch.setattr(health, attr, probe(attr))
    monkeypatch.setattr(health, "_check_memory_recall", recall_probe)
    health._deep_cache.update(ts=0.0, payload=None)

    asyncio.run(health.deep_canary())
    assert seen["memory_recall"] == ("/health/deep", True)
    assert seen["_check_config_roundtrip"] == "/health/deep"
    assert seen["check_database"] == "/health/deep"


def test_the_canary_roundtrip_is_the_one_allowed_write(store, monkeypatch):
    from kazma_ui import health

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    with ds.read_only_diagnostic("/health/deep", allow=(health._CANARY_KEY,)):
        assert health._check_config_roundtrip()["status"] == "ok"
    with ds.read_only_diagnostic("some other probe"):
        result = health._check_config_roundtrip()
    assert result["status"] == "failed" and "DiagnosticWriteRefused" in result["error"]


def test_kazma_doctor_runs_as_a_diagnostic(monkeypatch):
    from kazma_cli import doctor

    seen = {}

    def fake_collect():
        seen["scope"] = ds.active_diagnostic()
        return []

    monkeypatch.setattr(doctor, "_collect", fake_collect)
    doctor.collect()
    assert seen["scope"] == "kazma doctor"


_DIAG_PATH = re.compile(r"(^|/)(health|readiness|diagnostics)(/|$)")
_PRODUCT = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui")


def _route_path(dec: ast.expr) -> str | None:
    if (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in ("get", "post", "api_route")
        and dec.args
        and isinstance(dec.args[0], ast.Constant)
        and isinstance(dec.args[0].value, str)
    ):
        return dec.args[0].value
    return None


def _opens_the_scope(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call):
                    f = call.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name == "read_only_diagnostic":
                        return True
    return False


def _unscoped_diagnostics(sources: dict[str, str]) -> list[str]:
    out: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            paths = [p for p in map(_route_path, fn.decorator_list) if p]
            if any(_DIAG_PATH.search(p) for p in paths) and not _opens_the_scope(fn):
                out.append(f"{rel}:{fn.lineno} {paths[0]}")
    return sorted(out)


def _product_sources() -> dict[str, str]:
    out = {}
    for pkg in _PRODUCT:
        for p in (REPO / pkg).rglob("*.py"):
            if "tests" in p.parts or "__pycache__" in p.parts:
                continue
            out[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def test_every_diagnostic_route_runs_read_only():
    sources = _product_sources()
    missing = _unscoped_diagnostics(sources)
    assert not missing, (
        "A health / readiness / diagnostics route that does not open\n"
        "read_only_diagnostic(...). A probe that writes changes what it probes:\n  "
        + "\n  ".join(missing)
    )
    doctor = ast.parse(sources["kazma-cli/kazma_cli/doctor.py"])
    collect = next(n for n in doctor.body if isinstance(n, ast.FunctionDef) and n.name == "collect")
    assert _opens_the_scope(collect), "kazma doctor must run as a read-only diagnostic"


def test_the_route_gate_sees_an_unscoped_probe():
    """Negative control: a planted /health route with no scope is reported."""
    planted = {
        "x.py": textwrap.dedent(
            '''
            @router.get("/health/extra")
            async def extra():
                get_config_store().set("k", 1)

            @router.get("/api/things/health")
            async def fine():
                with read_only_diagnostic("things"):
                    return {}

            @router.get("/healthy-snacks")
            async def unrelated():
                return {}
            '''
        )
    }
    # the `def` line: decorated functions report it, not the decorator's
    assert _unscoped_diagnostics(planted) == ["x.py:3 /health/extra"]


# ── item 6: the write veto cannot be written ──────────────────────────────


@pytest.mark.parametrize("veto", [_VETO_MASKED, _VETO_BLANKING])
def test_a_veto_cannot_be_serialized(veto):
    assert isinstance(veto, _Veto)
    with pytest.raises(TypeError):
        json.dumps(veto)


def test_setting_none_stores_nothing(store):
    store.set("k.x", None)
    assert store._db_get_raw("k.x") is _MISSING, "no row -- not a stored JSON null"
    assert store.get("k.x", "default") == "default"


def test_atomic_update_cannot_blank_a_stored_secret(store, vault):
    store.set("connectors.x.token", "tok-real-1234")
    result = store.atomic_update("connectors.x.token", lambda _cur: "")
    assert is_vault_ref(result), "vetoed: it returns what is stored, untouched"
    assert store.get("connectors.x.token") == "tok-real-1234"
    assert is_vault_ref(store._db_get_raw("connectors.x.token")), "the pointer survives"


def test_atomic_update_cannot_null_a_stored_secret(store, vault):
    """The bug the None sentinel hid: a None result was not a refusal.

    ``_refused_the_write`` could not tell "the guard said no" from "the
    updater asked for null" -- both were None -- so it chose null and wrote
    it over the vault pointer. A plain key may still be set to null
    (``test_atomic_update_does_not_deadlock``); a stored secret may not.
    """
    store.set("connectors.x.token", "tok-real-5678")
    store.atomic_update("connectors.x.token", lambda _cur: None)
    assert store.get("connectors.x.token") == "tok-real-5678"
    assert is_vault_ref(store._db_get_raw("connectors.x.token"))
