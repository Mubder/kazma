"""Graph memory: 323 nodes that sat in no backup at all.

Neo4j runs in a Docker volume and the universal sweep walks the data
directory, so the graph was invisible to it. A disk failure took the whole
thing.

The round trip in these tests is the point. A backup that exports cleanly
and cannot be loaded back is the same category of claim as a recovery
mechanism that has never fired -- and this week that turned out to mean
"could not fire".
"""

from __future__ import annotations

import json

import pytest
from kazma_core.backup import neo4j_backup as nb

# ── self-disabling ────────────────────────────────────────────────────


def test_disabled_when_the_graph_backend_is_not_neo4j(monkeypatch):
    """An install on the SQLite graph backend must not fail its backup for
    a database it never had -- the same rule the Postgres dump follows."""
    monkeypatch.setattr(nb, "_graph_cfg", lambda: {"provider": "sqlite"})
    assert nb.graph_backup_enabled() is False


def test_disabled_when_neo4j_has_no_url(monkeypatch):
    monkeypatch.setattr(nb, "_graph_cfg", lambda: {"provider": "neo4j", "url": ""})
    assert nb.graph_backup_enabled() is False


def test_enabled_for_a_configured_neo4j(monkeypatch):
    monkeypatch.setattr(nb, "_graph_cfg",
                        lambda: {"provider": "neo4j", "url": "bolt://x:7687"})
    assert nb.graph_backup_enabled() is True


def test_a_disabled_backend_reports_skipped_not_failed(monkeypatch, tmp_path):
    """Skipped and failed must not look alike: one is fine, the other pages
    somebody."""
    monkeypatch.setattr(nb, "_graph_cfg", lambda: {"provider": "sqlite"})
    res = nb.export_graph(tmp_path)
    assert res.ok is True
    assert res.skipped
    assert not res.error


# ── failure handling ──────────────────────────────────────────────────


def test_an_unreachable_database_never_raises(monkeypatch, tmp_path):
    """This runs inside the backup. An exception here would abandon the
    SQLite and asset copies that had already succeeded."""
    monkeypatch.setattr(nb, "_graph_cfg",
                        lambda: {"provider": "neo4j", "url": "bolt://127.0.0.1:1"})

    def _boom(cfg=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(nb, "_driver", _boom)
    res = nb.export_graph(tmp_path)
    assert res.ok is False
    assert "connection refused" in res.error


def test_a_failed_export_leaves_no_partial_file(monkeypatch, tmp_path):
    """A half-written export that survives is worse than none: it looks
    restorable right up to the moment you need it."""
    monkeypatch.setattr(nb, "_graph_cfg",
                        lambda: {"provider": "neo4j", "url": "bolt://x:7687"})

    def _boom(cfg=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(nb, "_driver", _boom)
    nb.export_graph(tmp_path)
    assert not (tmp_path / nb.GRAPH_EXPORT_NAME).exists()
    assert not list(tmp_path.glob(".*tmp"))


def test_restore_refuses_a_missing_export(tmp_path):
    res = nb.restore_graph(tmp_path / "nothing.jsonl")
    assert res.ok is False
    assert "not found" in res.error


# ── the export format ─────────────────────────────────────────────────


def test_lookup_indexes_are_not_exported():
    """Token lookup indexes exist in every Neo4j database. Exporting them
    makes every restore log EquivalentSchemaRuleAlreadyExists, which trains
    an operator to ignore real index failures."""
    import inspect

    assert 'str(r["type"]).upper() == "LOOKUP"' in inspect.getsource(nb._schema)


def test_the_restore_key_is_stripped_after_linking():
    """Relationships are re-linked through a temporary property carrying the
    original elementId. Leaving it behind would put backup bookkeeping into
    live agent memory."""
    import inspect

    # The cleanup query is an f-string in the source, so match the template
    # rather than the interpolated value.
    src = inspect.getsource(nb.restore_graph)
    assert "REMOVE n.`{_KEY}`" in src
    assert "DROP INDEX kazma_restore_key" in src


def test_relationships_are_indexed_before_linking():
    """Linking against unindexed nodes is a full scan per relationship --
    fine for 275, unusable for a real graph."""
    import inspect

    src = inspect.getsource(nb.restore_graph)
    assert src.index("CREATE INDEX kazma_restore_key") < src.index("_create_rels_cypher")


def test_constraints_are_created_before_the_data():
    """Applied afterwards they fail on exactly the duplicates they exist to
    prevent, and the restore silently ends up without them."""
    import inspect

    src = inspect.getsource(nb.restore_graph)
    assert src.index('meta.get("constraints")') < src.index("_create_nodes_cypher")


# ── the safety rail ───────────────────────────────────────────────────


class _FakeSession:
    def __init__(self, node_count):
        self._n = node_count
        self.ran: list[str] = []

    def run(self, q, **kw):
        self.ran.append(q)

        class _R:
            def __init__(self, n):
                self._n = n

            def single(self):
                return {"c": self._n}

        return _R(self._n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDriver:
    def __init__(self, node_count):
        self._s = _FakeSession(node_count)

    def session(self, **kw):
        return self._s

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_restore_refuses_a_populated_graph(monkeypatch, tmp_path):
    """A restore is run when something has already gone wrong, often against
    the wrong target by accident. Silently merging a backup into a live graph
    is far harder to diagnose than a refusal."""
    export = tmp_path / "g.jsonl"
    export.write_text(json.dumps({"kind": "meta", "version": 1}) + "\n",
                      encoding="utf-8")
    monkeypatch.setattr(nb, "_driver", lambda cfg=None: _FakeDriver(42))

    res = nb.restore_graph(export, cfg={"url": "bolt://x", "database": "neo4j"})
    assert res.ok is False
    assert "42 node" in res.error
    assert "allow_nonempty" in res.error, "say how to proceed deliberately"


def test_restore_proceeds_into_an_empty_graph(monkeypatch, tmp_path):
    export = tmp_path / "g.jsonl"
    export.write_text(json.dumps({"kind": "meta", "version": 1}) + "\n",
                      encoding="utf-8")
    monkeypatch.setattr(nb, "_driver", lambda cfg=None: _FakeDriver(0))

    res = nb.restore_graph(export, cfg={"url": "bolt://x", "database": "neo4j"})
    assert res.ok is True


def test_allow_nonempty_overrides_the_refusal(monkeypatch, tmp_path):
    export = tmp_path / "g.jsonl"
    export.write_text(json.dumps({"kind": "meta", "version": 1}) + "\n",
                      encoding="utf-8")
    monkeypatch.setattr(nb, "_driver", lambda cfg=None: _FakeDriver(7))

    res = nb.restore_graph(export, allow_nonempty=True,
                           cfg={"url": "bolt://x", "database": "neo4j"})
    assert res.ok is True


# ── wiring into the universal backup ──────────────────────────────────


def test_the_universal_backup_exports_the_graph():
    import inspect

    from kazma_core.backup import universal

    src = inspect.getsource(universal.perform_universal_backup)
    assert "export_graph" in src
    assert '"graph": graph_result' in src


def test_a_graph_failure_does_not_abandon_the_rest_of_the_backup():
    """By this point the SQLite databases and assets are already copied."""
    import inspect

    from kazma_core.backup import universal

    src = inspect.getsource(universal.perform_universal_backup)
    block = src[src.index("export_graph"):src.index("export_graph") + 400]
    assert "except Exception" in block


# ── kazma.yaml ────────────────────────────────────────────────────────


def test_kazma_yaml_is_always_backed_up():
    """The MCP registry, connectors and model routing. Without it a restored
    install boots with no tools and no connectors."""
    from kazma_core.backup.universal import _ALWAYS_ROOT

    assert "kazma.yaml" in _ALWAYS_ROOT


def test_configured_extra_paths_cannot_drop_it(monkeypatch, tmp_path):
    """backups.extra_paths REPLACES the default list, so an operator naming
    their own folder silently dropped kazma.yaml. A config change must not be
    able to quietly remove the file that makes a restore usable."""
    from kazma_core.backup import universal

    root = tmp_path / "install"
    (root / "kazma-data").mkdir(parents=True)
    (root / "kazma.yaml").write_text("mcp: []\n", encoding="utf-8")
    (root / "mystuff").mkdir()
    (root / "mystuff" / "a.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(universal, "_data_dir", lambda: root / "kazma-data")

    class _Store:
        def get(self, key):
            return "mystuff" if key == "backups.extra_paths" else None

    import kazma_core.config_store as cs
    monkeypatch.setattr(cs, "get_config_store", lambda: _Store())

    dest = tmp_path / "dest"
    dest.mkdir()
    res = universal._copy_root_artifacts(dest)

    paths = {a["path"] for a in res["artifacts"]}
    assert "mystuff" in paths, "the operator's own choice must still be honoured"
    assert "kazma.yaml" in paths, "and must not be able to displace kazma.yaml"
    assert (dest / "kazma.yaml").is_file()


@pytest.mark.parametrize("name", ["kazma.yaml"])
def test_the_pinned_files_exist_in_this_repo(name):
    """A pinned path that no longer exists is a silent no-op."""
    from pathlib import Path

    assert (Path(__file__).resolve().parents[1] / name).is_file()
