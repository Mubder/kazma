"""Tenant-scoped human annotation; dataset edits never qualify or publish replies."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from kazma_core.x_api.ownership import x_tenant_id
from kazma_core.x_api.qualification import REQUIRED_CATEGORIES
from kazma_core.x_api.verification import CHECK_NAMES


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _case(raw: Any) -> dict[str, Any]:
    from kazma_core.x_api.shadow import _case_context

    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not 1 <= len(raw["id"]) <= 200:
        raise ValueError("A case needs a stable ID of at most 200 characters.")
    case = json.loads(_encoded(raw))
    if len(_encoded(case).encode()) > 128_000:
        raise ValueError("A case exceeds 128 KB. Keep only its relevant source context.")
    context = _case_context(case)
    case["context"] = context.to_dict()
    summon = case.get("summon", {})
    if not isinstance(summon, dict):
        raise ValueError("Summon context must be an object.")
    for key in ("id", "author", "text", "conversation_id"):
        if not isinstance(summon.get(key, ""), str) or len(summon.get(key, "")) > (32000 if key == "text" else 300):
            raise ValueError("Summon identity or text exceeds its bounds.")
    followers = summon.get("target_followers")
    if followers is not None and (type(followers) is not int or not 0 <= followers <= 10**10):
        raise ValueError("Follower count must be a non-negative integer or unknown.")
    if case.get("language", "") not in ("", "en", "ar", "mixed"):
        raise ValueError("Select English, Arabic or mixed language.")
    categories = case.get("categories", [])
    if not isinstance(categories, list) or len(categories) > 9 or any(c not in REQUIRED_CATEGORIES for c in categories):
        raise ValueError("Choose the declared evaluation categories.")
    expected = case.get("expected", {})
    if not isinstance(expected, dict) or any(expected.get(k) is not None and type(expected[k]) is not bool for k in ("auto", "evidence", "safety")):
        raise ValueError("Expected judgments must be yes, no or unlabeled.")
    if expected.get("target") is not None and (not isinstance(expected["target"], str) or len(expected["target"]) > 200):
        raise ValueError("Expected target must be text or unlabeled.")
    for key in ("rationale", "review_notes"):
        if not isinstance(case.get(key, ""), str) or len(case.get(key, "")) > 4000:
            raise ValueError("Review notes exceed 4,000 characters.")
    if case.get("fault") not in (None, "checker_outage"):
        raise ValueError("Unknown evaluation fault.")
    # Conversation identity is derived, never a user-selected split loophole.
    case["group"] = summon.get("conversation_id") or context.source_id or case["id"]
    case["categories"] = list(dict.fromkeys(categories))
    case["expected"] = {k: expected.get(k) for k in ("target", "auto", "evidence", "safety")}
    case.setdefault("language", "")
    case.setdefault("rationale", "")
    case.setdefault("review_notes", "")
    summon.setdefault("target_followers", None)
    for key in ("id", "author", "text", "conversation_id"):
        summon.setdefault(key, "")
    case["summon"] = summon
    case["held_out"] = case.get("held_out") is True
    case["human_reviewed"] = False
    case["labeler"] = ""
    return case


class DatasetStore:
    """Whole-document CAS keeps imports and human edits atomic across processes."""

    def __init__(self, path: Path | None = None):
        from kazma_core.paths import data_dir

        self.path = path or data_dir() / "x_datasets.db"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS x_datasets (
              tenant TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
              document TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(tenant,id));
            CREATE TABLE IF NOT EXISTS x_dataset_events (
              tenant TEXT NOT NULL, dataset_id TEXT NOT NULL, revision INTEGER NOT NULL,
              actor TEXT NOT NULL, action TEXT NOT NULL, digest TEXT NOT NULL,
              created_at REAL NOT NULL, PRIMARY KEY(tenant,dataset_id,revision));
            CREATE TABLE IF NOT EXISTS x_dataset_tuning_groups (
              tenant TEXT NOT NULL, source_group TEXT NOT NULL, PRIMARY KEY(tenant,source_group));
        """)
        return conn

    def list(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT id,revision,document,updated_at FROM x_datasets WHERE tenant=? ORDER BY updated_at DESC LIMIT 100", (x_tenant_id(),)).fetchall()
        result = []
        for row in rows:
            doc = json.loads(row["document"])
            cases = [c for c in doc["cases"] if not c.get("archived")]
            result.append({"id": row["id"], "revision": row["revision"], "name": doc["name"],
                           "purpose": doc["purpose"], "cases": len(cases),
                           "reviewed": sum(c.get("human_reviewed") is True for c in cases),
                           "groups": len({c["group"] for c in cases}), "updated_at": row["updated_at"]})
        return result

    def get(self, ident: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT revision,document FROM x_datasets WHERE tenant=? AND id=?", (x_tenant_id(), ident)).fetchone()
        if not row:
            raise KeyError("Dataset not found.")
        return {**json.loads(row["document"]), "id": ident, "revision": row["revision"]}

    def _write(self, ident: str, doc: dict[str, Any], *, revision: int, actor: str, action: str) -> dict[str, Any]:
        encoded = _encoded(doc)
        if len(doc["cases"]) > 1000 or len(encoded.encode()) > 2_000_000:
            raise ValueError("Dataset limit: 1,000 cases and 2 MB. Split larger collections.")
        tenant = x_tenant_id()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision FROM x_datasets WHERE tenant=? AND id=?", (tenant, ident)).fetchone()
            if (row["revision"] if row else 0) != revision:
                raise ValueError("Dataset changed in another editor. Reload before saving.")
            if not row and conn.execute("SELECT COUNT(*) FROM x_datasets WHERE tenant=?", (tenant,)).fetchone()[0] >= 100:
                raise ValueError("Collection limit: 100 per tenant. Reuse an existing collection.")
            for case in doc["cases"]:
                if doc["purpose"] == "tuning":
                    conn.execute("INSERT OR IGNORE INTO x_dataset_tuning_groups VALUES (?,?)", (tenant, case["group"]))
                if case.get("held_out"):
                    used = conn.execute("SELECT 1 FROM x_dataset_tuning_groups WHERE tenant=? AND source_group=?", (tenant, case["group"])).fetchone()
                    if doc["purpose"] != "release" or used:
                        raise ValueError("A conversation used for tuning cannot be labeled held-out. Use a separate untouched release collection.")
            conn.execute("INSERT OR REPLACE INTO x_datasets VALUES (?,?,?,?,?)", (tenant, ident, revision + 1, encoded, time.time()))
            conn.execute("INSERT INTO x_dataset_events VALUES (?,?,?,?,?,?,?)",
                         (tenant, ident, revision + 1, actor[:200], action, hashlib.sha256(encoded.encode()).hexdigest(), time.time()))
            conn.commit()
        except (sqlite3.Error, ValueError):
            conn.rollback()
            raise
        finally:
            conn.close()
        return {**doc, "id": ident, "revision": revision + 1}

    def create(self, name: str, purpose: str, *, actor: str, document: dict[str, Any] | None = None) -> dict[str, Any]:
        if not name.strip() or len(name) > 120 or purpose not in ("collection", "tuning", "release"):
            raise ValueError("Give the collection a name and select collection, tuning or release.")
        document = document or {"cases": []}
        if not isinstance(document.get("cases"), list) or len(document["cases"]) > 1000:
            raise ValueError("Import a JSON document containing a cases list.")
        cases = [_case(c) for c in document["cases"]]
        if len({c["id"] for c in cases}) != len(cases):
            raise ValueError("Duplicate case IDs are not allowed.")
        for case in cases:
            if purpose != "release":
                case["held_out"] = False
        doc = {"name": name.strip(), "purpose": purpose, "cases": cases}
        if any("actual" in c for c in cases):
            fingerprint, evaluated_at = document.get("fingerprint"), document.get("evaluated_at")
            if not isinstance(fingerprint, str) or len(fingerprint) != 64 or type(evaluated_at) not in (int, float):
                raise ValueError("Recorded outcomes require the original shadow report fingerprint and evaluation time.")
            doc.update(fingerprint=fingerprint, evaluated_at=evaluated_at)
            for case in cases:
                actual = case.get("actual")
                if (not isinstance(actual, dict) or type(actual.get("auto")) is not bool
                        or not isinstance(actual.get("target"), str) or not isinstance(actual.get("checks"), dict)
                        or set(actual["checks"]) != set(CHECK_NAMES)
                        or any(v not in ("pass", "fail", "unknown") for v in actual["checks"].values())):
                    raise ValueError("Import the complete measured shadow outcomes, including all five checks.")
                for field in ("latency_ms", "model_calls", "output_tokens"):
                    value = actual.get(field)
                    if type(value) not in (int, float) or not 0 <= value <= 10**9:
                        raise ValueError("Recorded usage and latency measurements are missing or invalid.")
        return self._write(uuid.uuid4().hex, doc, revision=0, actor=actor, action="import" if cases else "create")

    def save_case(self, ident: str, raw: dict[str, Any], *, revision: int, actor: str,
                  reviewed: bool = False, critical_violations: int | None = None) -> dict[str, Any]:
        doc = self.get(ident)
        doc.pop("id")
        doc.pop("revision")
        case = _case(raw)
        old = next((c for c in doc["cases"] if c["id"] == case["id"]), None)
        observation_fields = ("actual", "candidate", "decision", "models", "reason", "observation",
                              "observed_candidate", "observed_at", "observed_status", "observed_reason")
        for key in observation_fields:
            case.pop(key, None)
        if old and all(case.get(k) == old.get(k) for k in ("context", "summon", "fault")):
            for key in observation_fields:
                if key in old:
                    case[key] = old[key]
        if reviewed:
            expected = case.get("expected", {})
            if (case.get("language") not in ("en", "ar", "mixed") or not case["categories"]
                    or not isinstance(expected.get("target"), str)
                    or any(type(expected.get(k)) is not bool for k in ("auto", "evidence", "safety"))
                    or not case.get("rationale", "").strip()):
                raise ValueError("Complete language, categories, expected labels and rationale before attesting human review.")
            if "actual" in case:
                if type(critical_violations) is not int or not 0 <= critical_violations <= 1000:
                    raise ValueError("Inspect the recorded candidate and enter its actual critical violation count.")
                case["actual"] = {**case["actual"], "critical_violations": critical_violations}
            case.update(human_reviewed=True, labeler=actor, reviewed_at=time.time())
        doc["cases"] = [case if c["id"] == case["id"] else c for c in doc["cases"]]
        if not old:
            doc["cases"].append(case)
        return self._write(ident, doc, revision=revision, actor=actor, action="review" if reviewed else "edit")

    def collect(self, ident: str, *, revision: int, actor: str) -> dict[str, Any]:
        from kazma_core.x_api.corpus import collect_cases

        doc = self.get(ident)
        doc.pop("id")
        doc.pop("revision")
        known = {c["id"] for c in doc["cases"]}
        doc["cases"].extend(_case(c) for c in collect_cases(limit=1000) if c["id"] not in known)
        return self._write(ident, doc, revision=revision, actor=actor, action="collect")

    def export(self, ident: str, *, report: bool = False) -> dict[str, Any]:
        doc = self.get(ident)
        cases = [c for c in doc["cases"] if not c.get("archived")]
        if not report:
            return {"cases": cases, "annotation": "Real context and explicit human labels; collection export does not qualify publication."}
        if not doc.get("fingerprint") or any("actual" not in c for c in cases):
            raise ValueError("Import a full shadow report before exporting observed outcomes.")
        if any(not c.get("human_reviewed") or not c.get("held_out") for c in cases):
            raise ValueError("Every release case needs explicit held-out status and human review of its recorded candidate.")
        if len({c["group"] for c in cases}) != len(cases):
            raise ValueError("Release qualification counts independent conversations. Move related cases to the tuning set.")
        with closing(self._connect()) as conn:
            tuned = {row[0] for row in conn.execute("SELECT source_group FROM x_dataset_tuning_groups WHERE tenant=?", (x_tenant_id(),))}
        if any(c["group"] in tuned for c in cases):
            raise ValueError("A conversation used for tuning cannot be labeled held-out. Collect fresh release cases.")
        return {"cases": cases, "fingerprint": doc["fingerprint"], "evaluated_at": doc["evaluated_at"]}
