"""A signed receipt reports only what Kazma already recorded.

A planted secret, by its key's name and by its value, must not appear in any
rendering. A changed byte must fail verification. A missing record says it
was not recorded. The signing key is made once and is not replaced.
"""

from __future__ import annotations

import ast
import json
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
    "thread_usage",
    "turn_usage",
    "shown_record",
    "mask_deep",
    "format_untrusted_block",
    "read_only_diagnostic",
    "store_install_once",
    "write_private",
)


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A temp data dir and vault. Nothing here is the live install."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.setenv("KAZMA_VAULT_KEY", "b" * 64)
    from kazma_core.security.vault import SecretVault
    from kazma_ui.delivery import reset_turn_broker

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
    assert "This install has no signing key." in out
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

    class Broker:
        def resume(self, thread, last):
            assert thread == "thr"
            return ([{
                "text": f"hello-from-the-journal {_TOKEN} <script>alert(1)</script>",
                "turn_id": "turn-9",
            }], False, 1)

    _patch(monkeypatch, "kazma_ui.delivery.get_turn_broker", lambda: Broker())
    install["ops"].write_bytes(b"")

    def commitments(thread_id, *, status=None, tenant_id=None):
        assert thread_id == "thr"
        return [SimpleNamespace(
            commitment_id="c1", status="checked", act="tool", tool_name="web",
            policy_decision="allow", args_digest="abc",
            goal_text=f"fetch {_URL}",
            slots={"api_key": _SECRET_VALUE, "note": "visible-note"},
            evidence={}, conflicts=[], result={},
        )]

    def decisions(thread_id):
        return [{
            "gate_id": "g1", "tool": "shell_exec", "state": "settled",
            "decision": "approve", "actor": "owner",
            "created_at": 1, "claimed_at": 1, "settled_at": 2,
            "message": f"see {_URL}",
            "api_key": _SECRET_VALUE,
        }]

    _patch(monkeypatch, "kazma_core.safety.commitment.store.list_by_thread", commitments)
    _patch(monkeypatch, "kazma_core.safety.hitl_gates.thread_decisions", decisions)

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
        for secret in (_SECRET_VALUE, "s3cret-pw", _TOKEN, _ASKER, _TARGET, pem, "BEGIN PRIVATE KEY"):
            assert secret not in text
        for sentence in _PROVES:
            assert sentence in text

    html = files["html"].decode("utf-8")
    assert "<script" not in html.lower()
    assert "&lt;kazma:data" in html
    assert 'lang="en"' in html and 'dir="ltr"' in html
    assert 'lang="ar"' in html and 'dir="rtl"' in html
    assert "script-src &#x27;none&#x27;" in html
    assert "hello-from-the-journal" in html
    assert "visible-note" in html
    body = _payload(files["json"])
    assert body["kind"]["label_ar"] == "دورة محادثة"
    assert _section(body, "approvals")["status"] == "recorded"
    assert _section(body, "commitment")["status"] == "recorded"

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

    class Gapped:
        def resume(self, thread, last):
            return ([{"text": "partial-frame-must-not-be-quoted", "turn_id": "turn-real"}], True, 1)

    _patch(monkeypatch, "kazma_ui.delivery.get_turn_broker", lambda: Gapped())
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
    assert "could not be read" in _section(body, "llm")["detail_en"]
    assert "not one turn's total" not in text

    (install["data"] / "llm_calls.db").write_bytes(b"")
    _patch(monkeypatch, "kazma_core.observability.llm_ledger.thread_usage", lambda thread_id: None)
    code, blob = _write(install["root"], "unread.json", ["chat_turn:thr", "--json"])
    assert code == 0
    body = _payload(blob)
    assert _section(body, "llm")["status"] == "not_recorded"
    assert "could not be read" in _section(body, "llm")["detail_en"]
    assert "not one turn's total" not in blob.decode("utf-8")

    def zeros(thread_id):
        return {
            "calls": 0, "tokens": 0, "cost": 0, "unpriced": 0,
            "prompt_tokens": 0, "cached_tokens": 0,
        }

    _patch(monkeypatch, "kazma_core.observability.llm_ledger.thread_usage", zeros)

    class EmptyJournal:
        def resume(self, thread, last):
            return ([], False, 0)

    _patch(monkeypatch, "kazma_ui.delivery.get_turn_broker", lambda: EmptyJournal())
    code, blob = _write(install["root"], "zero.json", ["chat_turn:thr", "--json"])
    assert code == 0
    body = _payload(blob)
    ledger = _section(body, "llm")
    assert ledger["status"] == "recorded"
    assert ledger["detail_en"] == "The chat's model ledger, not one turn's total."
    assert ledger["items"][0]["calls"] == 0
    assert ledger["turn"]["status"] == "not_recorded"
    assert "does not know the turn id" in ledger["turn"]["en"]
    unverified = _section(body, "unverified")
    assert any(item.get("what") == "llm_turn" for item in unverified["items"])

    (install["data"] / "tool_effects.db").write_bytes(b"")

    def effects(thread_id, *, limit=200):
        return [{
            "effect_id": "e1", "tool": "shell_exec", "state": "started",
            "started_at": 1, "completed_at": None, "result_uncertain": 0,
            "result_json": _EFFECT_RESULT,
        }]

    _patch(monkeypatch, "kazma_core.agent.effect_journal.inspect_effects", effects)
    code, blob = _write(install["root"], "effect.json", ["chat_turn:thr", "--json"])
    assert code == 0
    text = blob.decode("utf-8")
    assert _EFFECT_RESULT not in text
    body = _payload(blob)
    assert _section(body, "effects")["status"] == "recorded"
    assert any(item.get("what") == "effect" for item in _section(body, "unverified")["items"])
    assert "not recorded whether this effect finished" in text
    capsys.readouterr()


def test_a_refused_write_is_not_a_receipt(install, monkeypatch):
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused

    (install["data"] / "tool_effects.db").write_bytes(b"")

    def boom(thread_id, *, limit=200):
        raise DiagnosticWriteRefused("kazma receipt", "effect", "tool_effects.db")

    _patch(monkeypatch, "kazma_core.agent.effect_journal.inspect_effects", boom)
    with pytest.raises(DiagnosticWriteRefused):
        run(["chat_turn:thr", "--out", str(install["root"] / "refused.txt")])
    assert not (install["root"] / "refused.txt").exists()
