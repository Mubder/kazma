"""The full live reply path can be measured without remote or source-store writes."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest


@pytest.fixture
def shadow_env(tmp_path, monkeypatch):
    from kazma_core.x_api import ledger
    from kazma_core.x_api.config import XCredentials, get_x_config
    from kazma_core.x_api.reply_store import reset_reply_store
    from kazma_core.x_api.schedule import reset_x_scheduled_store
    from kazma_core.x_api.stance import Subject, get_reply_config

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    (tmp_path / ".x-shadow-isolated").write_text(str(os.getpid()), encoding="utf-8")
    reset_reply_store(tmp_path / "x_replies.db")
    reset_x_scheduled_store(tmp_path / "x_scheduled.db")
    monkeypatch.setattr(ledger, "_ledger", ledger.XPostLedger(tmp_path / "x_posts.db"))
    cfg = replace(get_reply_config(), enabled=False, mode="draft", summoners=("owner",), trigger="",
                      subjects=(Subject(id="coffee", side="support", match=("coffee",),
                                        target="Coffee", allow_auto=True, evidence_policy="opinion_only"),))
    xcfg = replace(get_x_config(), enabled=True, account_id="123", handle="kazma",
                   credentials=XCredentials("k", "s", "t", "ts"))
    monkeypatch.setattr("kazma_core.x_api.config.get_x_config", lambda: xcfg)
    monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: cfg)
    monkeypatch.setattr("kazma_core.x_api.qualification.pipeline_fingerprint", lambda cfg: "test-pipeline")
    calls = []
    body = "Coffee is a good choice."

    class Client:
        config = SimpleNamespace(model="fixture")

        async def chat(self, messages, **kwargs):
            calls.append(messages)
            prompt = messages[0]["content"]
            if "Requested checks: " in prompt:
                names = json.JSONDecoder().raw_decode(prompt.split("Requested checks: ", 1)[1])[0]
                rows = [{"check": name, "verdict": "pass", "reason": "Fits the original context.",
                         "evidence": body, "source_ids": [],
                         "claims": [{"text": body, "kind": "opinion", "status": "opinion", "source_ids": []}]
                         if name == "evidence" else []} for name in names]
                return SimpleNamespace(content=json.dumps({"checks": rows}), usage={"completion_tokens": 20})
            return SimpleNamespace(content=body, usage={"completion_tokens": 6})

    class Registry:
        def get_client(self):
            return Client()

    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry())

    async def no_remote(*args, **kwargs):
        raise AssertionError("Shadow evaluation must never call an X write")

    monkeypatch.setattr("kazma_core.x_api.client.XClient.create_tweet", no_remote)
    case = {"id": "real-case-shape", "language": "en", "categories": ["negation"],
            "context": {"source_id": "78900", "text": "I like coffee", "author_handle": "target",
                        "verified_source": True, "author_resolved": True},
            "summon": {"id": "45600", "author": "owner", "text": "@kazma comment", "conversation_id": "78900"}}
    return tmp_path, case, cfg, xcfg, calls


async def test_same_live_preflight_records_eligibility_without_posting(shadow_env):
    from kazma_core.x_api.shadow import evaluate_case

    root, case, cfg, xcfg, calls = shadow_env
    report = await evaluate_case(case, isolated_root=root)
    assert report["actual"]["auto"], json.dumps(report)
    assert report["candidate"] == "Coffee is a good choice."
    assert len(calls) == 5
    assert report["actual"]["model_calls"] == 5
    assert report["actual"]["output_tokens"] == 86
    assert not report["human_reviewed"] and report["actual"]["critical_violations"] is None
    assert report["expected"] is None and report["labeler"] == ""
    assert not cfg.enabled and cfg.mode == "draft"


@pytest.mark.parametrize("hold", ["stranger", "context", "outage", "draft_only", "quota"])
async def test_authority_context_outage_permission_and_caps_still_hold(shadow_env, monkeypatch, hold):
    from kazma_core.x_api.account_binding import credential_revision
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.shadow import evaluate_case

    root, case, cfg, xcfg, calls = shadow_env
    if hold == "stranger":
        case["summon"]["author"] = "stranger"
        case["summon"]["text"] = "@kazma #Open"
    elif hold == "context":
        case["context"]["truncated"] = True
    elif hold == "outage":
        case["fault"] = "checker_outage"
    elif hold == "draft_only":
        monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: replace(cfg, subjects=(replace(cfg.subjects[0], allow_auto=False),)))
    else:
        import time

        store = get_publication_store()
        for index in range(xcfg.max_posts_per_day):
            store.reserve(tenant_id="default", account_id="123", credential_revision=credential_revision(xcfg.credentials),
                          idempotency_key=f"other-{index}", text=f"Other commitment {index}", reply_to_id="",
                          due_at=time.time(), max_day=xcfg.max_posts_per_day, max_month=xcfg.max_posts_per_month,
                          duplicate_days=30, origin="immediate")
    report = await evaluate_case(case, isolated_root=root)
    assert not report["actual"]["auto"] and report["reason"]
    if hold == "stranger":
        assert not calls


async def test_missing_isolation_marker_refuses_evaluation(shadow_env):
    from kazma_core.x_api.shadow import evaluate_case

    root, case, cfg, xcfg, calls = shadow_env
    (root / ".x-shadow-isolated").unlink()
    with pytest.raises(ValueError, match="isolated"):
        await evaluate_case(case, isolated_root=root)
    assert not calls


async def test_process_guard_blocks_direct_client_transport_without_scope(monkeypatch):
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import XCredentials

    monkeypatch.setenv("KAZMA_X_SHADOW", "1")
    with pytest.raises(XApiError, match="blocked") as failure:
        await XClient(XCredentials("k", "s", "t", "ts"))._request("POST", "/2/tweets")
    assert failure.value.outcome == "not_sent"


def test_collection_exports_real_observations_with_no_invented_labels(shadow_env):
    from kazma_core.x_api.corpus import collect_cases
    from kazma_core.x_api.reply_store import get_reply_store

    store = get_reply_store()
    store.claim(summon_id="45600", parent_id="78900", target_handle="target", summoner="owner",
                parent_text="Coffee is interesting", summon_text="@kazma comment")
    store.mark_awaiting("45600", draft="Stored candidate", subject_id="coffee")
    cases = collect_cases()
    assert len(cases) == 1 and cases[0]["observed_candidate"] == "Stored candidate"
    assert cases[0]["context"]["fallback_text"]
    assert cases[0]["expected"]["auto"] is None
    assert not cases[0]["held_out"] and not cases[0]["human_reviewed"]


def test_cli_collection_upgrades_only_its_copy_and_preserves_arabic(shadow_env, monkeypatch):
    import hashlib
    import sqlite3

    from kazma_core.x_api.reply_store import get_reply_store

    from scripts.x_shadow import _collect

    root = shadow_env[0]
    get_reply_store().claim(summon_id="45600", parent_id="78900", target_handle="target", summoner="owner",
                            parent_text="رأي عن القهوة", summon_text="@kazma comment")
    source = root / "x_replies.db"
    with sqlite3.connect(source) as conn:
        conn.execute("ALTER TABLE x_replies DROP COLUMN decision_json")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    cases = _collect(tenant="default", limit=1)
    assert cases[0]["context"]["text"] == "رأي عن القهوة"
    assert not cases[0]["human_reviewed"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with sqlite3.connect(source) as conn:
        assert "decision_json" not in {row[1] for row in conn.execute("PRAGMA table_info(x_replies)")}


def test_cli_snapshot_leaves_source_stores_unchanged(tmp_path):
    import hashlib
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("KAZMA_")}
    environment.update(KAZMA_DATA_DIR=str(source), KAZMA_USER_HOME=str(tmp_path / "home"),
                       KAZMA_PROJECT_ROOT=str(tmp_path), KAZMA_ENV_FILE=str(tmp_path / "empty.env"),
                       KAZMA_DB_BACKEND="sqlite", KAZMA_X_POST="0", PYTHONIOENCODING="ascii")
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    setup = """
from kazma_core.config_store import get_config_store
from kazma_core.x_api.qualification import pipeline_fingerprint
from kazma_core.x_api.stance import get_reply_config
cs = get_config_store()
cs.batch_set([('connectors.x.reply.summoners', ['owner'], 'connectors'),
              ('connectors.x.reply.enabled', False, 'connectors'),
              ('connectors.x.api_key', 'private-shadow-test-key', 'connectors')])
pipeline_fingerprint(get_reply_config())
cs.close()
"""
    initialized = subprocess.run([sys.executable, "-c", setup], cwd=tmp_path, env=environment,
                                 capture_output=True, text=True, timeout=60)
    assert initialized.returncode == 0, initialized.stderr
    before = hashlib.sha256((source / "settings.db").read_bytes()).hexdigest()
    case = {"id": "captured", "context": {"source_id": "123", "text": "رأي عن القهوة", "author_handle": "target",
                                         "verified_source": True, "author_resolved": True},
            "summon": {"id": "456", "author": "stranger", "text": "#Open", "conversation_id": "123"}}
    incoming, outgoing = tmp_path / "cases.json", tmp_path / "observed.json"
    incoming.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    process = subprocess.run([sys.executable, str(root / "scripts/x_shadow.py"), "--evaluate", str(incoming),
                              "--output", str(outgoing)], cwd=tmp_path, env=environment,
                             capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert process.returncode == 0, process.stderr
    assert hashlib.sha256((source / "settings.db").read_bytes()).hexdigest() == before
    assert not (source / "x_replies.db").exists()
    assert not (source / ".x-shadow-isolated").exists()
    report = json.loads(outgoing.read_text(encoding="utf-8"))
    assert report["cases"][0]["context"]["text"] == case["context"]["text"]
    assert not report["cases"][0]["actual"]["auto"]
    assert report["cases"][0]["actual"]["critical_violations"] is None
    assert "private-shadow-test-key" not in outgoing.read_text(encoding="utf-8")
