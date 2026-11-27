"""A signed receipt reports only what Kazma already recorded.

A planted secret, by its key's name, by an Arabic label, and by a vault
value with no token shape, must not appear in any rendering. A commitment's
result is a digest, not the tool's output. A changed byte must fail
verification. A missing record says it was not recorded. One run is one
turn. The signing key is made once and is not replaced.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_cli.receipt import run, run_verify

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "kazma-cli" / "kazma_cli" / "receipt.py"

_SECRET_VALUE = "plain-vault-value-ZZ9"
_URL = "https://user:s3cret-pw@files.example/a"
_TOKEN = "sk-testtokenvalue"
_ASKER = "secret-asker-QQ"
_TARGET = "secret-target-QQ"
_EFFECT_RESULT = "effect-result-SHOULD-NOT-APPEAR"
_PROVES = (
    "A valid signature proves this install wrote this file and it is unchanged.",
    "التوقيع الصحيح يثبت أن هذا التثبيت كتب هذا الملف وأنه لم يتغيّر.",
    "It does not prove that an outside effect happened exactly once.",
    "لا يثبت أن أثراً خارجياً حدث مرة واحدة بالضبط.",
)
_CHANGED = (
    "A byte of this file changed, or it was not signed by this install.",
    "تغيّر بايت من هذا الملف، أو لم يوقّعه هذا التثبيت.",
)
_MISMATCH = (
    "This file was not signed by this install.",
    "لم يوقّع هذا التثبيت هذا الملف.",
)
_RECORD_CALLS = (
    "get_run",
    "get_turn_broker",
    "inspect_effects",
    "thread_decisions",
    "list_by_thread",
    "recorded_usage",
    "shown_record",
    "mask_deep",
    "redact_text",
    "mask_stored_values",
    "read_only_diagnostic",
    "store_install_once",
    "write_private",
    "prime_enforcement",
    "visible_here",
    "is_kazma_store",
    "stored_record",
)
_PASSWORD = "envplain-EE11"
_AR_SECRET = "arpw-AR99"
_VAULT_PLAIN = "vaultplain-VV77"
_TOOL_OUT = "toolout-OO11"
_FENCE = "historical observation data"


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A temp data dir and vault. Nothing here is the live install."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.setenv("KAZMA_VAULT_KEY", "b" * 64)
    from kazma_core.config_store import reset_config_store
    from kazma_core.security.vault import SecretVault
    from kazma_core.spaces.scope import set_enforced
    from kazma_ui.delivery import reset_turn_broker

    # prime_enforcement reads the process-wide store. Point it at this
    # temp dir before the receipt runs, and leave Spaces off afterwards.
    reset_config_store()
    set_enforced(False)
    reset_turn_broker()
    vault = SecretVault(db_path=str(tmp_path / "vault.db"))
    _patch(monkeypatch, "kazma_core.security.vault.get_vault", lambda: vault)

    async def unlisted(_run_id):
        return None

    _patch(monkeypatch, "kazma_core.runs.registry.get_run", unlisted)
    holder = {"vault": vault, "data": data, "ops": tmp_path / "memory_ops.db", "root": tmp_path}
    yield holder
    vault.close()
    reset_turn_broker()
    set_enforced(False)
    reset_config_store()


def _patch(monkeypatch, dotted: str, value) -> None:
    """Patch *dotted* on its module.

    A test that writes ``setattr("kazma_core....name"`` is counted against
    every product file that imported that name by value. These readers are
    imported inside the function that calls them, so the module attribute is
    what the receipt sees.
    """
    module_name, _, attr = dotted.rpartition(".")
    module = __import__(module_name, fromlist=[attr])
    monkeypatch.setattr(module, attr, value)


def _payload(blob: bytes) -> dict:
    head, sep, _trailer = blob.rpartition(b"\n.kazma-receipt-ed25519 ")
    assert sep, "not a receipt file"
    return json.loads(head.decode("utf-8"))


def _section(body: dict, sid: str) -> dict:
    return next(item for item in body["sections"] if item["id"] == sid)


def _write(root: Path, name: str, args: list[str]) -> tuple[int, bytes]:
    path = root / name
    code = run([*args, "--out", str(path)])
    blob = path.read_bytes() if path.is_file() else b""
    return code, blob


def _missing_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return [name for name in _RECORD_CALLS if name not in found]


def _flip_b64(text: str) -> str:
    for index, char in enumerate(text):
        if char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/":
            replacement = "B" if char == "A" else "A"
            return text[:index] + replacement + text[index + 1:]
    raise AssertionError("no base64 character to change")


def _trailer_parts(blob: bytes) -> tuple[bytes, str, str]:
    """Payload bytes including their newline, then the stated key and signature."""
    head, sep, last = blob.rpartition(b"\n")
    assert sep and last == b""
    body, sep, trailer = head.rpartition(b"\n")
    assert sep
    mark, pub, sig = trailer.decode("ascii").split(" ")
    assert mark == ".kazma-receipt-ed25519"
    return body + b"\n", pub, sig


def _settle(*paths: Path) -> None:
    """Make a writer's commit visible to an immutable read. The receipt never does this."""
    for path in paths:
        if not path.is_file():
            continue
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()


def _use_journal(monkeypatch, *, turn_id: str = "turn-9", text: str = "hello-from-the-journal",
                 turn_key: str | None = None, gap: bool = False, empty: bool = False) -> None:
    frame = {"text": text, "turn_id": turn_id}
    if turn_key:
        frame["turn_key"] = turn_key

    class Broker:
        def resume(self, thread, last):
            if empty:
                return ([], False, 0)
            return ([frame], gap, 1)

    _patch(monkeypatch, "kazma_ui.delivery.get_turn_broker", lambda: Broker())


def _commit(thread: str, turn: str, goal: str, *, tenant: str = "default",
            evidence: dict | None = None, result: dict | None = None,
            slots: dict | None = None, cid: str = "c1") -> None:
    from kazma_core.safety.commitment.store import Commitment, create_commitment

    create_commitment(Commitment(
        commitment_id=cid, thread_id=thread, turn_id=turn, tenant_id=tenant,
        act="tool", status="committed", goal_text=goal, slots=slots or {},
        evidence=evidence or {}, result=result or {}, tool_name="web",
        policy_decision="allow", args_digest="abc",
    ))


def _gate(thread: str, turn: str, message: str, *, tenant: str = "default", gate_id: str = "g1") -> None:
    from kazma_core.safety.hitl_gates import GateRow, register_gate

    register_gate(GateRow(
        gate_id=gate_id, thread_id=thread, tool="shell_exec", turn_id=turn,
        tenant_id=tenant, message=message, state="settled", decision="approve", actor="owner",
    ))


def _effect(thread: str, turn: str, effect_id: str, tool: str, *,
            space: str = "", result: dict | None = None) -> None:
    from kazma_core.agent.effect_journal import _EffectJournal
    from kazma_core.paths import tool_effects_db

    book = _EffectJournal(tool_effects_db())
    book.begin(effect_id, "h-" + effect_id, thread, tool, turn_id=turn, space=space)
    if result is not None:
        book.finish(effect_id, result)


def _ledger_on_disk(monkeypatch, data: Path, **kwargs) -> None:
    """One real ledger row in this install's file, then the connection is closed.

    The ledger keeps one process-wide connection. Point that at nothing for
    this test, so the row lands in *data*, and close it before the test ends.
    """
    from kazma_core.observability import llm_ledger

    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(data / "llm_calls.db"))
    try:
        if kwargs:
            llm_ledger.record_llm_call(**kwargs)
        else:
            conn.execute("DELETE FROM llm_calls")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        opened = llm_ledger._conn
        if opened is not None:
            opened.close()


def _recall(key: str, tenant: str, episode_id: str) -> None:
    from kazma_core.memory.recall_records import record_recall

    result = SimpleNamespace(
        episodes=[SimpleNamespace(id=episode_id)],
        beliefs=[], summaries=[], degraded="", failed=False,
    )
    record_recall(key, tenant_id=tenant, result=result)


def test_help_and_conflicts_do_not_open_the_vault(monkeypatch, tmp_path):
    def boom():
        raise AssertionError("the vault was opened")

    _patch(monkeypatch, "kazma_core.security.vault.get_vault", boom)
    with pytest.raises(SystemExit) as help_exit:
        run(["--help"])
    assert help_exit.value.code == 0
    with pytest.raises(SystemExit) as both:
        run(["chat_turn:t", "--json", "--html"])
    assert both.value.code == 2
    with pytest.raises(SystemExit) as together:
        run(["chat_turn:t", "--public-key"])
    assert together.value.code == 2
    with pytest.raises(SystemExit) as missing:
        run([])
    assert missing.value.code == 2

    class EmptyVault:
        def retrieve(self, name, tenant_id=None):
            return None

        def store_install_once(self, *args, **kwargs):
            raise AssertionError("verify-receipt created a signing key")

    _patch(monkeypatch, "kazma_core.security.vault.get_vault", lambda: EmptyVault())
    receipt = tmp_path / "foreign.txt"
    receipt.write_bytes(b'{"kazma_receipt": 1}\n.kazma-receipt-ed25519 AAAA BBBB\n')
    code = run_verify([str(receipt)])
    assert code == 1


def test_verify_without_a_key_does_not_make_one(monkeypatch, tmp_path, capsys):
    class EmptyVault:
        def __init__(self):
            self.stored = False

        def retrieve(self, name, tenant_id=None):
            return None

        def store_install_once(self, *args, **kwargs):
            self.stored = True
            raise AssertionError("verify-receipt created a signing key")

    vault = EmptyVault()
    _patch(monkeypatch, "kazma_core.security.vault.get_vault", lambda: vault)
    receipt = tmp_path / "looks-signed.txt"
    receipt.write_bytes(b'{"kazma_receipt":1}\n.kazma-receipt-ed25519 AAAA BBBB\n')
    code = run_verify([str(receipt)])
    out = capsys.readouterr().out
    assert code == 1
    assert vault.stored is False
    assert "This check was not given a public key, and this install has no signing key." in out
    assert "لا مفتاح توقيع لهذا التثبيت." in out


def test_receipt_calls_the_records_it_quotes():
    source = RECEIPT.read_text(encoding="utf-8")
    assert _missing_calls(source) == []
    tree = ast.parse(source)
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert "collect" not in names
    assert [name for name in names if name.startswith(("mask", "redact", "scrub", "sanitize"))] == []
    public = [
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    ]
    assert public == ["run", "run_verify"]


def test_negative_control_a_reader_that_skips_a_record_is_reported():
    planted = "def planted():\n    return get_run()\n"
    missing = _missing_calls(planted)
    assert "inspect_effects" in missing
    assert missing


def test_the_key_is_install_scoped_and_stored_once(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.setenv("KAZMA_VAULT_KEY", "c" * 64)
    from kazma_core.security.vault import (
        SecretVault,
        is_install_scoped_secret,
        is_kazma_owned_secret,
    )

    assert is_install_scoped_secret("receipt.signing_key") is True
    assert is_kazma_owned_secret("receipt.signing_key") is True
    vault = SecretVault(db_path=str(tmp_path / "vault.db"))
    try:
        with pytest.raises(ValueError):
            vault.store_install_once("user.note", "nope")
        left = vault._conn.execute(
            "SELECT COUNT(*) FROM secrets WHERE name = ?", ("user.note",),
        ).fetchone()[0]
        assert left == 0
        first = vault.store_install_once("receipt.signing_key", "pem-one")
        second = vault.store_install_once("receipt.signing_key", "pem-two")
        assert first == "pem-one"
        assert second == "pem-one"
        assert vault.retrieve("receipt.signing_key") == "pem-one"
        rows = vault._conn.execute(
            "SELECT encrypted_value FROM secrets WHERE name = ? AND tenant_id IS NULL",
            ("receipt.signing_key",),
        ).fetchall()
        assert len(rows) == 1
        blob = bytes(rows[0][0])
        flipped = bytes([blob[0] ^ 0xFF]) + blob[1:]
        vault._conn.execute(
            "UPDATE secrets SET encrypted_value = ? WHERE name = ? AND tenant_id IS NULL",
            (flipped, "receipt.signing_key"),
        )
        assert vault.store_install_once("receipt.signing_key", "pem-three") is None
        after = vault._conn.execute(
            "SELECT encrypted_value FROM secrets WHERE name = ? AND tenant_id IS NULL",
            ("receipt.signing_key",),
        ).fetchall()
        assert len(after) == 1
        assert bytes(after[0][0]) == flipped
    finally:
        vault.close()


def test_shown_record_returns_ids_only(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    from kazma_core.memory.quality_watch import ops_conn
    from kazma_core.memory.recall_records import shown_record

    conn = ops_conn()
    assert conn is not None
    conn.execute(
        "INSERT INTO memory_recall_records (turn_key, tenant_id, asker, at, shown_json, "
        "shown_count, empty, degraded, failed, latency_ms, used) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            "k1", "owner", "asker-secret-QQ", 1.0,
            json.dumps({
                "episodes": ["ep-1", {"id": "skip"}],
                "beliefs": "not-a-list-secret-BB",
                "summaries": ["sum-1"],
                "prose": "memory-prose-secret-CC",
            }),
            2, 0, "", 0, 12,
        ),
    )
    conn.commit()
    conn.close()
    row = shown_record("k1")
    assert row is not None
    assert row["used"] is None
    assert row["shown"] == {"episodes": ["ep-1"], "beliefs": [], "summaries": ["sum-1"]}
    text = json.dumps(row)
    assert "asker-secret-QQ" not in text
    assert "not-a-list-secret-BB" not in text
    assert "memory-prose-secret-CC" not in text
    assert shown_record("missing") is None


def test_planted_secret_never_appears_and_a_changed_byte_fails(install, monkeypatch, capsys):
    from kazma_core.runs.contract import Run, RunState

    run_row = Run(
        run_id="chat_turn:thr",
        kind="chat_turn",
        native_id="thr",
        title="hello",
        state=RunState.RUNNING,
        native_state="running",
        origin={"platform": "web", "target": _TARGET, "thread_id": "other-thread"},
        asker=_ASKER,
    )

    async def found(_run_id):
        return run_row

    _patch(monkeypatch, "kazma_core.runs.registry.get_run", found)
    journal = (
        f"hello-from-the-journal {_TOKEN} <script>alert(1)</script> "
        f"password: {_PASSWORD} OPENAI_API_KEY={_PASSWORD} "
        f"كلمة السر: {_AR_SECRET} {_VAULT_PLAIN} {_URL}"
    )
    _use_journal(monkeypatch, text=journal)
    install["vault"].store("planted-note", _VAULT_PLAIN, category="note")
    _commit(
        "thr", "turn-9",
        f"password: {_PASSWORD} OPENAI_API_KEY={_PASSWORD} "
        f"كلمة السر: {_AR_SECRET} كلمة المرور: {_AR_SECRET} {_VAULT_PLAIN}",
        slots={"api_key": _SECRET_VALUE, "note": "visible-note"},
        evidence={"output": _TOOL_OUT}, result={"output": _TOOL_OUT},
    )
    _gate(
        "thr", "turn-9",
        f"see {_URL} {_VAULT_PLAIN} كلمة السر: {_AR_SECRET} password: {_PASSWORD}",
    )
    _effect("thr", "turn-9", "e-planted", "shell_exec", result={"output": _TOOL_OUT})
    _settle(install["data"] / "tool_effects.db", install["data"] / "hitl_gates.db", install["ops"])

    code = run(["--public-key"])
    public_out = capsys.readouterr().out
    assert code == 0
    pem = install["vault"].retrieve("receipt.signing_key")
    assert pem and "BEGIN PRIVATE KEY" in pem
    assert pem not in public_out
    assert "BEGIN PRIVATE KEY" not in public_out
    assert "The public half of this install's receipt key." in public_out
    assert "النصف العلني لمفتاح إيصالات هذا التثبيت." in public_out

    files = {}
    for kind, flag in (("text", None), ("json", "--json"), ("html", "--html")):
        args = ["chat_turn:thr"]
        if flag:
            args.append(flag)
        status, blob = _write(install["root"], f"receipt.{kind}", args)
        assert status == 0
        files[kind] = blob
        text = blob.decode("utf-8")
        for secret in (
            _SECRET_VALUE, "s3cret-pw", _TOKEN, _ASKER, _TARGET, pem, "BEGIN PRIVATE KEY",
            _PASSWORD, _AR_SECRET, _VAULT_PLAIN, _TOOL_OUT, _FENCE, "<kazma:data",
        ):
            assert secret not in text, secret
        for sentence in _PROVES:
            assert sentence in text
        assert "visible-note" in text
        assert "hello-from-the-journal" in text
        assert "issued_at:" in text or '"issued_at":' in text
        assert "key_id:" in text or '"key_id":' in text

    html = files["html"].decode("utf-8")
    assert "<script" not in html.lower()
    assert "<html lang=\"en\">" in html
    assert 'lang="en"' in html and 'dir="ltr"' in html
    assert 'lang="ar"' in html and 'dir="rtl"' in html
    assert "script-src &#x27;none&#x27;" in html
    pres = [line for line in html.split("<pre") if line.startswith(" ") or line.startswith(" dir")]
    assert pres
    assert all(piece.startswith(' dir="ltr"') for piece in pres)
    body = _payload(files["json"])
    assert body["kind"]["label_ar"] == "دورة محادثة"
    assert body["key_id"]
    assert "T" in body["issued_at"]
    _, trailer_key, _sig = _trailer_parts(files["json"])
    assert body["key_id"] == trailer_key
    assert _section(body, "approvals")["status"] == "recorded"
    commitment = _section(body, "commitment")
    assert commitment["status"] == "recorded"
    evidence = commitment["items"][0]["evidence"]
    assert evidence["shown"] == "summary"
    assert evidence["sha256"]
    assert _TOOL_OUT not in evidence["sha256"]
    started = _section(body, "effects")["items"][0]["started_at"]
    assert isinstance(started, (int, float)) and started > 1_000_000_000
    assert str(int(started)) not in html

    for kind, blob in files.items():
        path = install["root"] / f"ok.{kind}"
        path.write_bytes(blob)
        assert run_verify([str(path)]) == 0
        out = capsys.readouterr().out
        for sentence in _PROVES:
            assert sentence in out

    raw = files["json"]
    flipped = bytearray(raw)
    flipped[2] ^= 0x01
    changed = install["root"] / "changed-byte.txt"
    changed.write_bytes(flipped)
    assert run_verify([str(changed)]) == 1
    out = capsys.readouterr().out
    for sentence in _CHANGED:
        assert sentence in out

    payload, pub, sig = _trailer_parts(raw)
    bad_sig = install["root"] / "changed-sig.txt"
    bad_sig.write_bytes(payload + f".kazma-receipt-ed25519 {pub} {_flip_b64(sig)}\n".encode("ascii"))
    assert run_verify([str(bad_sig)]) == 1
    out = capsys.readouterr().out
    for sentence in _CHANGED:
        assert sentence in out

    bad_key = install["root"] / "other-key.txt"
    bad_key.write_bytes(payload + f".kazma-receipt-ed25519 {_flip_b64(pub)} {sig}\n".encode("ascii"))
    assert run_verify([str(bad_key)]) == 1
    out = capsys.readouterr().out
    for sentence in _MISMATCH:
        assert sentence in out
    assert _CHANGED[0] not in out


def test_partial_records_say_not_recorded(install, monkeypatch, capsys):
    async def refuse(_run_id):
        raise AssertionError("a bad id is not a run")

    _patch(monkeypatch, "kazma_core.runs.registry.get_run", refuse)
    code, blob = _write(install["root"], "bad.json", ["not-a-run", "--json"])
    assert code == 0
    body = _payload(blob)
    assert _section(body, "supervision")["detail_en"] == "not recorded. This is not a run id."
    assert body["kind"]["status"] == "not_recorded"

    async def unlisted(_run_id):
        return None

    _patch(monkeypatch, "kazma_core.runs.registry.get_run", unlisted)

    _use_journal(monkeypatch, text="partial-frame-must-not-be-quoted", gap=True)
    code, blob = _write(install["root"], "gap.json", ["chat_turn:thr", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    body = _payload(blob)
    assert "partial-frame-must-not-be-quoted" not in text
    assert _section(body, "supervision")["detail_en"] == "not recorded. This run is not in the current listing."
    assert _section(body, "turn_journal")["status"] == "not_recorded"
    assert "This process has no complete turn journal" in _section(body, "turn_journal")["detail_en"]
    assert "لا سجل دورة كاملاً" in text
    assert _section(body, "memories")["detail_en"].startswith("not recorded. The memory record is keyed by a hash")
    assert _section(body, "llm")["status"] == "not_recorded"
    assert "The model ledger file is not there." in _section(body, "llm")["detail_en"]
    assert "could not be read" not in _section(body, "llm")["detail_en"]
    assert "does not list the whole chat" in _section(body, "effects")["detail_en"]
    assert "not one turn's total" not in text

    (install["data"] / "llm_calls.db").write_bytes(b"")
    code, blob = _write(install["root"], "unread.json", ["chat_turn:thr", "--json"])
    assert code == 0
    body = _payload(blob)
    assert _section(body, "llm")["status"] == "not_recorded"
    assert "not readable" in _section(body, "llm")["detail_en"]
    assert "could not be read" not in _section(body, "llm")["detail_en"]
    assert "not one turn's total" not in blob.decode("utf-8")

    (install["data"] / "llm_calls.db").unlink()
    _ledger_on_disk(monkeypatch, install["data"])
    _use_journal(monkeypatch, empty=True)
    code, blob = _write(install["root"], "zero.json", ["chat_turn:thr", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    body = _payload(blob)
    ledger = _section(body, "llm")
    assert ledger["status"] == "not_recorded"
    assert "365" in ledger["detail_en"]
    assert "A count of zero is not a record" in ledger["detail_en"]
    assert "0 calls" not in text
    assert "nothing stored" not in text
    assert "not one turn's total" not in text
    unverified = _section(body, "unverified")
    assert any(item.get("what") == "llm" for item in unverified["items"])

    _effect("thr", "turn-other", "e-other", "marker-whole-chat", result={"output": _EFFECT_RESULT})
    _settle(install["data"] / "tool_effects.db")
    code, blob = _write(install["root"], "whole-chat.json", ["chat_turn:thr", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    assert "marker-whole-chat" not in text
    assert _EFFECT_RESULT not in text
    assert _section(_payload(blob), "effects")["status"] == "not_recorded"
    assert "does not list the whole chat" in text
    capsys.readouterr()


def test_a_refused_write_is_not_a_receipt(install, monkeypatch):
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused

    _use_journal(monkeypatch)
    (install["data"] / "tool_effects.db").write_bytes(b"")

    def boom(thread_id, **kwargs):
        raise DiagnosticWriteRefused("kazma receipt", "effect", "tool_effects.db")

    _patch(monkeypatch, "kazma_core.agent.effect_journal.inspect_effects", boom)
    with pytest.raises(DiagnosticWriteRefused):
        run(["chat_turn:thr", "--out", str(install["root"] / "refused.txt")])
    assert not (install["root"] / "refused.txt").exists()


def test_one_turn_not_the_whole_chat(install, monkeypatch):
    _use_journal(monkeypatch, turn_id="turn-9")
    _effect("thr", "turn-9", "e-this", "tool-this-turn")
    _effect("thr", "turn-other", "e-other", "tool-other-turn", result={"output": _EFFECT_RESULT})
    _commit("thr", "turn-9", "goal-this-turn", cid="c-this")
    _commit("thr", "turn-other", "goal-other-turn", cid="c-other")
    _gate("thr", "turn-9", "approval-this-turn", gate_id="g-this")
    _gate("thr", "turn-other", "approval-other-turn", gate_id="g-other")
    _settle(install["data"] / "tool_effects.db", install["data"] / "hitl_gates.db", install["ops"])
    code, blob = _write(install["root"], "one-turn.json", ["chat_turn:thr", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    body = _payload(blob)
    assert "tool-this-turn" in text
    assert "goal-this-turn" in text
    assert "approval-this-turn" in text
    assert "tool-other-turn" not in text
    assert "goal-other-turn" not in text
    assert "approval-other-turn" not in text
    assert _EFFECT_RESULT not in text
    assert _section(body, "effects")["status"] == "recorded"
    assert any(item.get("what") == "effect" for item in _section(body, "unverified")["items"])
    assert "not recorded whether this effect finished" in text


def test_spaces_on_refuses_another_space_and_filters(install, monkeypatch, capsys):
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces.identity import ENFORCED_KEY
    from kazma_core.spaces.scope import bind_scope, set_enforced
    from kazma_core.spaces.threads import record_thread

    _use_journal(monkeypatch, turn_id="turn-9", turn_key="k-other")
    record_thread("thr-own", "default", "owner")
    record_thread("thr-other", "sp_other", "owner")
    _commit("thr-own", "turn-9", "marker-own-AA11", tenant="default", cid="c-own")
    _commit("thr-own", "turn-9", "marker-other-AA11", tenant="sp_other", cid="c-cross")
    _commit("thr-other", "turn-9", "marker-foreign-AA11", tenant="sp_other", cid="c-foreign")
    _effect("thr-own", "turn-9", "e-own", "effect-own-AA11", space="default")
    _effect("thr-own", "turn-9", "e-cross", "effect-cross-AA11", space="sp_other")
    _gate("thr-own", "turn-9", "approval-own-AA11", tenant="default", gate_id="g-own")
    _gate("thr-own", "turn-9", "approval-cross-AA11", tenant="sp_other", gate_id="g-cross")
    _recall("k-other", "sp_other", "mem-other-EE1")
    _ledger_on_disk(
        monkeypatch, install["data"], thread_id="thr-own", turn_id="turn-9", model="model-own-AA11",
        prompt_tokens=3, completion_tokens=1, space="default",
    )
    _ledger_on_disk(
        monkeypatch, install["data"], thread_id="thr-own", turn_id="turn-9", model="model-cross-AA11",
        prompt_tokens=4, completion_tokens=1, space="sp_other",
    )
    _settle(
        install["data"] / "thread_scopes.db", install["data"] / "tool_effects.db",
        install["data"] / "hitl_gates.db", install["ops"], install["data"] / "llm_calls.db",
    )
    store = get_config_store()
    store.set(ENFORCED_KEY, True)
    try:
        code, blob = _write(install["root"], "other.json", ["chat_turn:thr-other", "--json"])
        captured = capsys.readouterr()
        assert code == 1
        assert blob == b""
        assert "marker-foreign-AA11" not in captured.out + captured.err
        assert "another space" in captured.err
        assert "مساحة أخرى" in captured.err

        code, blob = _write(install["root"], "own.json", ["chat_turn:thr-own", "--json"])
        assert code == 0
        text = blob.decode("utf-8")
        assert "marker-own-AA11" in text
        assert "effect-own-AA11" in text
        assert "approval-own-AA11" in text
        assert "marker-other-AA11" not in text
        assert "effect-cross-AA11" not in text
        assert "approval-cross-AA11" not in text
        assert "mem-other-EE1" not in text
        assert "marker-foreign-AA11" not in text
        body = _payload(blob)
        assert _section(body, "llm")["items"][0]["calls"] == 1

        with bind_scope("sp_other", "owner"):
            code, blob = _write(install["root"], "kept.json", ["chat_turn:thr-other", "--json"])
            assert code == 0
            assert "marker-foreign-AA11" in blob.decode("utf-8")
            assert "marker-own-AA11" not in blob.decode("utf-8")
            refused = install["root"] / "kept-own.json"
            assert run(["chat_turn:thr-own", "--json", "--out", str(refused)]) == 1
            assert not refused.is_file()
    finally:
        store.set(ENFORCED_KEY, False)
        set_enforced(False)


def test_spaces_off_does_not_filter_or_refuse(install, monkeypatch):
    from kazma_core.spaces.threads import record_thread

    _use_journal(monkeypatch, turn_id="turn-9", turn_key="k-other")
    record_thread("thr-other", "sp_other", "owner")
    _commit("thr-other", "turn-9", "marker-foreign-AA11", tenant="sp_other", cid="c-foreign")
    _commit("thr-own", "turn-9", "marker-own-AA11", tenant="default", cid="c-own")
    _commit("thr-own", "turn-9", "marker-other-AA11", tenant="sp_other", cid="c-cross")
    _recall("k-other", "sp_other", "mem-other-EE1")
    _settle(install["data"] / "thread_scopes.db", install["ops"])
    code, blob = _write(install["root"], "off-other.json", ["chat_turn:thr-other", "--json"])
    assert code == 0
    assert "marker-foreign-AA11" in blob.decode("utf-8")
    code, blob = _write(install["root"], "off-own.json", ["chat_turn:thr-own", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    assert "marker-own-AA11" in text
    assert "marker-other-AA11" in text
    assert "mem-other-EE1" in text
    scopes = install["data"] / "thread_scopes.db"
    assert scopes.is_file()


def test_receipt_does_not_create_a_thread_record(install, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces.identity import ENFORCED_KEY
    from kazma_core.spaces.scope import set_enforced

    _use_journal(monkeypatch)
    _commit("thr", "turn-9", "marker-unrecorded", cid="c-plain")
    _settle(install["ops"])
    store = get_config_store()
    store.set(ENFORCED_KEY, True)
    try:
        assert not (install["data"] / "thread_scopes.db").exists()
        code, blob = _write(install["root"], "norecord.json", ["chat_turn:thr", "--json"])
        assert code == 0
        assert "marker-unrecorded" in blob.decode("utf-8")
        assert not (install["data"] / "thread_scopes.db").exists()
    finally:
        store.set(ENFORCED_KEY, False)
        set_enforced(False)


def test_a_read_creates_no_wal(install, monkeypatch):
    run(["--public-key"])
    _use_journal(monkeypatch)
    _effect("thr", "turn-9", "e1", "shell_exec")
    _gate("thr", "turn-9", "an approval")
    _commit("thr", "turn-9", "a goal")
    _ledger_on_disk(
        monkeypatch, install["data"], thread_id="thr", turn_id="turn-9",
        prompt_tokens=2, completion_tokens=1,
    )
    from kazma_core.spaces.threads import record_thread

    record_thread("thr", "default", "owner")
    # The test harness points the approval registry at tmp/hitl_gates.db.
    # The receipt must read that same file and leave it, and its sidecars, unchanged.
    watched = (
        install["data"] / "tool_effects.db",
        install["root"] / "hitl_gates.db",
        install["ops"],
        install["data"] / "llm_calls.db",
        install["data"] / "thread_scopes.db",
    )
    _settle(*watched)
    for path in watched:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
    before = {path.name: path.stat().st_size for path in watched}
    code, _blob = _write(install["root"], "quiet.txt", ["chat_turn:thr"])
    assert code == 0
    for path in watched:
        assert path.stat().st_size == before[path.name]
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()
    assert not (install["data"] / "hitl_gates.db").exists()


def test_verify_with_a_public_key_does_not_open_the_vault(install, monkeypatch, capsys, tmp_path):
    import base64

    from kazma_cli.receipt import _load_private, _sign

    _use_journal(monkeypatch, text="hello-from-the-journal")
    code, blob = _write(install["root"], "signed.json", ["chat_turn:thr", "--json"])
    assert code == 0
    body = _payload(blob)
    public = body["key_id"]
    path = install["root"] / "signed.json"

    def boom():
        raise AssertionError("the vault was opened")

    _patch(monkeypatch, "kazma_core.security.vault.get_vault", boom)
    assert run_verify([str(path), "--public-key", public]) == 0
    out = capsys.readouterr().out
    for sentence in _PROVES:
        assert sentence in out
    assert "Checked with this install's key." not in out

    key_file = tmp_path / "pub.txt"
    key_file.write_text(public, encoding="utf-8")
    assert run_verify([str(path), "--public-key", str(key_file)]) == 0
    capsys.readouterr()

    assert run_verify([str(path), "--public-key", "not-a-key"]) == 1
    shown = "".join(capsys.readouterr())
    assert "That public key could not be read." in shown
    assert "تعذّر قراءة هذا المفتاح العلني." in shown

    private = _load_private(install["vault"].retrieve("receipt.signing_key"))
    swapped = dict(body)
    swapped["key_id"] = base64.b64encode(b"\x11" * 32).decode("ascii")
    payload = json.dumps(swapped, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    forged = install["root"] / "wrong-body-key.json"
    forged.write_bytes(_sign(payload + b"\n", private))
    assert run_verify([str(forged), "--public-key", public]) == 1
    out = capsys.readouterr().out
    assert "This file was not signed by the key that was given." in out
    assert "لم يوقَّع هذا الملف بالمفتاح الذي أُعطي." in out

    missing_time = dict(body)
    del missing_time["issued_at"]
    payload = json.dumps(missing_time, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    cut = install["root"] / "no-issued.json"
    cut.write_bytes(_sign(payload + b"\n", private))
    assert run_verify([str(cut), "--public-key", public]) == 1
    assert "not a Kazma receipt" in capsys.readouterr().out

    _patch(monkeypatch, "kazma_core.security.vault.get_vault", lambda: install["vault"])
    assert run_verify([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Checked with this install's key." in out
    assert "فُحص بمفتاح هذا التثبيت." in out


def test_calm_errors_not_a_traceback(install, monkeypatch, capsys):
    assert not __import__("os").environ.get("KAZMA_VERBOSE_ERRORS")
    _use_journal(monkeypatch)
    (install["data"] / "tool_effects.db").write_bytes(b"x")

    def nan_rows(thread_id, **kwargs):
        return [{
            "effect_id": "e1", "tool": "shell_exec", "state": "completed",
            "started_at": float("nan"), "completed_at": None, "result_uncertain": 0,
        }]

    _patch(monkeypatch, "kazma_core.agent.effect_journal.inspect_effects", nan_rows)
    code = run(["chat_turn:thr", "--json", "--out", str(install["root"] / "nan.json")])
    captured = capsys.readouterr()
    shown = captured.out + captured.err
    assert code == 1
    assert "The receipt could not be signed" in shown
    assert "تعذّر توقيع الإيصال." in shown
    assert "Traceback" not in shown
    assert "AttributeError" not in shown
    assert not (install["root"] / "nan.json").exists()

    _patch(monkeypatch, "kazma_core.agent.effect_journal.inspect_effects", lambda thread_id, **kwargs: [])
    folder = install["root"] / "out-dir"
    folder.mkdir()
    code = run(["chat_turn:thr", "--out", str(folder)])
    shown = "".join(capsys.readouterr())
    assert code == 1
    assert "The receipt could not be written" in shown
    assert "تعذّر كتابة الإيصال." in shown
    assert "Traceback" not in shown
    assert "AttributeError" not in shown

    blocked = install["root"] / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    code = run(["chat_turn:thr", "--out", str(blocked / "receipt.txt")])
    shown = "".join(capsys.readouterr())
    assert code == 1
    assert "The receipt could not be written" in shown
    assert "تعذّر كتابة الإيصال." in shown
    assert "Traceback" not in shown

    code = run_verify([str(install["root"] / "missing-receipt.txt")])
    shown = "".join(capsys.readouterr())
    assert code == 1
    assert "The receipt could not be read" in shown
    assert "تعذّر قراءة الإيصال." in shown
    assert "Traceback" not in shown
    assert "AttributeError" not in shown


def test_ledger_off_says_not_recorded(install, monkeypatch):
    from kazma_core.config_store import get_config_store

    get_config_store().set("agent.nonstop.ledger.enabled", False)
    _ledger_on_disk(
        monkeypatch, install["data"], thread_id="thr", turn_id="turn-9",
        prompt_tokens=8, completion_tokens=1, model="should-not-be-a-zero",
    )
    _use_journal(monkeypatch)
    code, blob = _write(install["root"], "off.json", ["chat_turn:thr", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    ledger = _section(_payload(blob), "llm")
    assert ledger["status"] == "not_recorded"
    assert "switched off" in ledger["detail_en"]
    assert "مطفأ" in ledger["detail_ar"]
    assert "0 calls" not in text
    assert "calls" not in ledger


def test_out_refuses_a_kazma_store(install, monkeypatch, capsys):
    _use_journal(monkeypatch, text="hello-from-the-journal")
    target = install["root"] / "copies" / "vault.db"
    code = run(["chat_turn:thr", "--out", str(target)])
    shown = "".join(capsys.readouterr())
    assert code == 1
    assert not target.exists()
    assert "That path is one of Kazma's own files." in shown
    assert "هذا المسار أحد ملفات كاظمه." in shown


def test_two_connections_store_one_signing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.setenv("KAZMA_VAULT_KEY", "d" * 64)
    from kazma_core.security.vault import SecretVault

    path = str(tmp_path / "vault.db")
    first = SecretVault(db_path=path)
    second = SecretVault(db_path=path)
    barrier = threading.Barrier(2)
    found: list[str | None] = []

    def once(vault: SecretVault, pem: str) -> None:
        barrier.wait(timeout=5)
        found.append(vault.store_install_once("receipt.signing_key", pem, category="receipt"))

    threads = (
        threading.Thread(target=once, args=(first, "pem-from-a")),
        threading.Thread(target=once, args=(second, "pem-from-b")),
    )
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert found[0] == found[1]
        assert found[0] in ("pem-from-a", "pem-from-b")
        count = first._conn.execute(
            "SELECT COUNT(*) FROM secrets WHERE name = ?", ("receipt.signing_key",),
        ).fetchone()[0]
        assert count == 1
    finally:
        first.close()
        second.close()
