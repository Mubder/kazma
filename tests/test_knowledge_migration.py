"""A migration carries the Knowledge Library whole, and a fresh target opens
it (Knowledge audit 2026-10-13, KB-E01).

The importer merged a bundle's Knowledge tables into the live settings.db
with its own copy of the DDL -- no folded Arabic column, no keyword index
rebuild -- and a fresh target could not open its library. The library has
its own file now (``knowledge.db``, a bundle store); a bundle in the older
layout is staged into it through the store's own schema.

Subprocesses: ``data_dir()`` comes from the environment, and the vault key
is pinned to a dummy so neither side reads or writes a real one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PAGE = ("# Webhooks\n\nWebhooks retry delivery five times.\n\n"
        "## المستخدم\n\nيستطيع المُسْتَخْدِم إعادة إرسال الطلب.\n")

_SETUP = """
import json
from kazma_core.stores import knowledge_index as ki

class _NoVectors:
    available = False
    identity = ""
    def drop(self):
        return False
    def query(self, *a, **k):
        return []

_REAL_VECTORS = ki.KnowledgeIndex._vector_store_for
ki.KnowledgeIndex._vector_store_for = lambda self, library_id: _NoVectors()
"""

_READ_BACK = """
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.memory import vector_store_global as vsg
import hashlib, math

class _Toy:
    dim = 16
    def encode(self, text):
        v = [0.0] * 16
        for w in text.split():
            v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 16] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
    def encode_batch(self, texts):
        return [self.encode(t) for t in texts]

store = KnowledgeStore()  # the target's own default file: it must open
libs = [lib["id"] for lib in store.list_libraries()]
chunks = store.list_chunks("docs", limit=50, offset=0)
en = [h for h, _s in store.fts_search("webhooks retry", "docs", limit=5)]
ar = [h for h, _s in store.fts_search("المستخدم", "docs", limit=5)]
documents = [c for c in chunks if c.get("document_id")]
vsg.get_encoder = lambda model_name=None: _Toy()
ki.KnowledgeIndex._vector_store_for = _REAL_VECTORS  # the sweep uses the real vector store
report = ki.KnowledgeIndex(store).backfill_vectors(time_budget_s=120)
print(json.dumps({"libs": libs, "chunks": len(chunks), "en": len(en), "ar": len(ar),
                  "documents": len(documents), "embedded": report["embedded"],
                  "unavailable": report["unavailable"], "fold_ok": store.fts_coverage()["ok"]}))
store.close()
"""


def _run(script: str, env: dict[str, str], cwd: Path) -> str:
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(script)], env=env, cwd=str(cwd),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return proc.stdout


def _env(tmp_path: Path, data: Path) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("KAZMA_")}
    base.update({"KAZMA_DB_BACKEND": "sqlite", "KAZMA_VAULT_KEY": "test-only-not-a-secret",
                 "KAZMA_DATA_DIR": str(data), "KAZMA_VECTOR_PATH": str(data / "vector_memory"),
                 "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    return base


def _import_and_read(tmp_path: Path, bundle: Path, name: str) -> dict:
    dst = tmp_path / name / "kazma-data"
    dst.mkdir(parents=True)
    out = _run(
        _SETUP + f"""
from kazma_core.migration.importer import import_bundle
r = import_bundle({str(bundle)!r}, target_workspace_root={str(tmp_path / name / "ws")!r})
print(json.dumps({{"ok": r.ok, "errors": r.errors, "restored": r.files_restored}}))
""" + _READ_BACK,
        _env(tmp_path, dst), tmp_path)
    lines = [json.loads(line) for line in out.strip().splitlines() if line.startswith("{")]
    imported, read = lines[0], lines[-1]
    assert imported["ok"], imported["errors"]
    read["restored"] = imported["restored"]
    read["settings_has_kb"] = _has_kb_tables(dst / "settings.db")
    return read


def _has_kb_tables(path: Path) -> bool:
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'knowledge_chunks'").fetchone())
    finally:
        conn.close()


def _export(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "kazma-data"
    src.mkdir(parents=True)
    bundle = tmp_path / "bundle.zip"
    _run(_SETUP + f"""
import hashlib
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict
store = KnowledgeStore()
store.create_library("docs", "Docs", seed_url="https://docs.example.test/")
index = ki.KnowledgeIndex(store)
page = {PAGE!r}
index.index("docs", [chunk_to_dict(c) for c in chunk_markdown_doc(
    page, source_url="https://docs.example.test/w", library_id="docs")])
text = "The signed contract renews every March."
store.publish_document_version(tenant_id=store._current_tenant(), library_id="docs", document_id="doc-1",
    version_id="v1", source_sha256="a" * 64, chunks=[{{
        "id": "docs:doc-1:v1:0", "library_id": "docs", "source_url": "document://doc-1/v1",
        "document_id": "doc-1", "version_id": "v1", "chunk_index": 0, "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(), "document_title": "Contract",
        "section_header": "", "has_code": False, "char_count": len(text)}}])
store.close()
from kazma_core.migration.exporter import export_bundle
print(export_bundle({str(bundle)!r}, include_assets=False))
""", _env(tmp_path, src), tmp_path)
    return bundle


def test_a_migration_carries_the_library_and_the_target_opens_it(tmp_path):
    bundle = _export(tmp_path)
    names = zipfile.ZipFile(bundle).namelist()
    assert "data/knowledge.db" in names
    read = _import_and_read(tmp_path, bundle, "dst")
    assert "knowledge.db" in read["restored"]
    assert read["libs"] == ["docs"]
    assert read["chunks"] >= 3 and read["documents"] == 1
    assert read["en"] >= 1 and read["ar"] >= 1  # the vocalized word, found folded
    assert read["fold_ok"] is True
    # The repair sweep rebuilds the vectors (they never travel).
    assert read["unavailable"] is False and read["embedded"] == read["chunks"]
    assert read["settings_has_kb"] is False


_LEGACY_DDL = """
CREATE TABLE knowledge_libraries (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  seed_url TEXT DEFAULT '', auto_inject INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
  chunk_count INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  tenant_id TEXT DEFAULT 'default');
CREATE TABLE knowledge_chunks (id TEXT PRIMARY KEY, library_id TEXT NOT NULL, source_url TEXT NOT NULL,
  document_title TEXT DEFAULT '', section_header TEXT DEFAULT '', chunk_index INTEGER DEFAULT 0,
  content_hash TEXT NOT NULL, has_code INTEGER DEFAULT 0, char_count INTEGER DEFAULT 0, content TEXT NOT NULL,
  metadata_json TEXT DEFAULT '{}', document_id TEXT, version_id TEXT, source_sha256 TEXT,
  active INTEGER DEFAULT 1, tombstoned INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(chunk_id UNINDEXED, content);
"""


def _legacy_settings(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_DDL)
    now = "2026-09-01T00:00:00+00:00"
    conn.execute("INSERT INTO knowledge_libraries (id, name, seed_url, created_at, updated_at) VALUES "
                 "('docs', 'Docs', 'javascript:alert(1)', ?, ?)", (now, now))
    rows = [("docs:aaa:0", "Webhooks retry delivery five times."),
            ("docs:aaa:1", "يستطيع المُسْتَخْدِم إعادة إرسال الطلب.")]
    for n, (chunk_id, text) in enumerate(rows):
        conn.execute("INSERT INTO knowledge_chunks (id, library_id, source_url, chunk_index, content_hash, "
                     "content, created_at) VALUES (?, 'docs', 'https://docs.example.test/w', ?, ?, ?, ?)",
                     (chunk_id, n, hashlib.sha256(text.encode()).hexdigest(), text, now))
        conn.execute("INSERT INTO knowledge_chunks_fts (chunk_id, content) VALUES (?, ?)", (chunk_id, text))
    conn.commit()
    conn.close()


def _as_legacy_bundle(bundle: Path, out: Path, settings: Path) -> None:
    """The same bundle in the layout written before 2026-10-14: the library
    as a KB-only ``data/settings.db``, no ``data/knowledge.db``."""
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        manifest = json.loads(src.read("manifest.json"))
        hashes = manifest["file_hashes"]
        hashes.pop("data/knowledge.db", None)
        hashes["data/settings.db"] = hashlib.sha256(settings.read_bytes()).hexdigest()
        for info in src.infolist():
            if info.filename in ("manifest.json", "data/knowledge.db"):
                continue
            dst.writestr(info, src.read(info.filename))
        dst.write(settings, "data/settings.db")
        dst.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))


def test_a_bundle_in_the_older_layout_is_staged_through_the_stores_schema(tmp_path):
    bundle = _export(tmp_path)
    legacy = tmp_path / "legacy-settings.db"
    _legacy_settings(legacy)
    old = tmp_path / "old-layout.zip"
    _as_legacy_bundle(bundle, old, legacy)
    read = _import_and_read(tmp_path, old, "dst-old")
    assert "knowledge.db" in read["restored"]
    assert read["libs"] == ["docs"] and read["chunks"] == 2
    assert read["en"] == 1 and read["ar"] == 1  # the keyword index rebuilt, Arabic folded
    assert read["settings_has_kb"] is False  # the live settings.db was never touched
    assert read["embedded"] == 2


@pytest.mark.postgres
def test_a_migration_from_a_postgres_install_carries_the_library(tmp_path):
    """The source's settings live in Postgres (the bundle carries
    ``data/postgres.dump``); the Knowledge Library is its own SQLite file and
    travels beside it. The target restores both."""
    dsn = os.environ.get("KAZMA_DATABASE_URL", "")
    if not dsn.startswith("postgres") or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a disposable Postgres (KAZMA_TEST_ALLOW_REAL_DB=1, KAZMA_DATABASE_URL)")
    # The document catalog stays SQLite here: this round trip is about the
    # Knowledge Library beside Postgres settings, not the document store.
    pg = {"KAZMA_DB_BACKEND": "postgres", "KAZMA_DATABASE_URL": dsn, "KAZMA_TEST_ALLOW_REAL_DB": "1",
          "KAZMA_DOCUMENTS_METADATA_BACKEND": "sqlite", "KAZMA_DOCUMENTS_JOBS_BACKEND": "sqlite"}
    for name in ("KAZMA_DB_CONTAINER", "KAZMA_DOCKER_BIN"):
        if os.environ.get(name):
            pg[name] = os.environ[name]
    src = tmp_path / "src" / "kazma-data"
    src.mkdir(parents=True)
    bundle = tmp_path / "bundle.zip"
    _run(_SETUP + f"""
from kazma_core.config_store import get_config_store
get_config_store().set("knowledge.smart_search", True, category="knowledge")  # the settings live in Postgres
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict
store = KnowledgeStore()
store.create_library("docs", "Docs", seed_url="https://docs.example.test/")
page = {PAGE!r}
ki.KnowledgeIndex(store).index("docs", [chunk_to_dict(c) for c in chunk_markdown_doc(
    page, source_url="https://docs.example.test/w", library_id="docs")])
store.close()
from kazma_core.migration.exporter import export_bundle
print(export_bundle({str(bundle)!r}, include_assets=False))
""", {**_env(tmp_path, src), **pg}, tmp_path)
    names = zipfile.ZipFile(bundle).namelist()
    assert "data/postgres.dump" in names and "data/knowledge.db" in names
    dst = tmp_path / "dst-pg" / "kazma-data"
    dst.mkdir(parents=True)
    out = _run(_SETUP + f"""
from kazma_core.migration.importer import import_bundle
r = import_bundle({str(bundle)!r}, target_workspace_root={str(tmp_path / "ws")!r})
print(json.dumps({{"ok": r.ok, "errors": r.errors, "restored": r.files_restored}}))
from kazma_core.config_store import get_config_store
print(json.dumps({{"smart": get_config_store().get("knowledge.smart_search")}}))
""" + _READ_BACK, {**_env(tmp_path, dst), **pg}, tmp_path)
    lines = [json.loads(line) for line in out.strip().splitlines() if line.startswith("{")]
    imported, settings, read = lines[0], lines[1], lines[-1]
    assert imported["ok"], imported["errors"]
    assert "knowledge.db" in imported["restored"] and settings["smart"] is True
    assert read["libs"] == ["docs"] and read["en"] >= 1 and read["ar"] >= 1
    assert read["embedded"] == read["chunks"]


def test_an_unfolded_index_misses_the_vocalized_word(tmp_path):
    """Negative control: the importer's own DDL -- an FTS table without the
    folded column -- cannot find the plain spelling of a vocalized word."""
    conn = sqlite3.connect(str(tmp_path / "plain.db"))
    conn.execute("CREATE VIRTUAL TABLE f USING fts5(chunk_id UNINDEXED, content)")
    conn.execute("INSERT INTO f VALUES ('c1', 'يستطيع المُسْتَخْدِم إعادة إرسال الطلب.')")
    assert conn.execute("SELECT COUNT(*) FROM f WHERE f MATCH ?", ('"المستخدم"',)).fetchone()[0] == 0
    conn.close()


def _knowledge_ddl(sources: dict[str, str]) -> list[str]:
    pattern = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE[^;\"']*knowledge_", re.I)
    return sorted(rel for rel, text in sources.items() if pattern.search(text))


def test_no_migration_code_carries_knowledge_ddl():
    """Class gate: the Knowledge schema lives in the store alone."""
    folder = REPO / "kazma-core/kazma_core/migration"
    sources = {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in folder.glob("*.py")}
    assert _knowledge_ddl(sources) == []


def test_the_ddl_gate_sees_the_old_importer():
    """Negative control: ``_merge_kb_into_settings``'s own table."""
    old = {"kazma-core/kazma_core/migration/importer.py":
           'conn.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks (id TEXT PRIMARY KEY)")'}
    assert _knowledge_ddl(old) == ["kazma-core/kazma_core/migration/importer.py"]
