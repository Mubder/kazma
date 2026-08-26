"""persist_mcp_yaml must be a comment-preserving SECTION splice.

The old implementation round-tripped the whole kazma.yaml through
``yaml.safe_dump``, stripping every documentation comment from the
operator's live config whenever MCP servers were saved (observed as
comment-less kazma.yaml after test runs, 2026-08-26).
"""

from __future__ import annotations

import yaml

from kazma_core.mcp_servers_store import _splice_yaml_section, persist_mcp_yaml

_SAMPLE = """\
agent:
  name: kazma
  # agent-level docs must survive
  version: 0.10.0

# MCP servers — external tool providers.
mcp:
  servers: []

llm:
  model: gpt-4o-mini  # inline comment survives
  timeout: 60.0
"""


def _write_sample(tmp_path):
    p = tmp_path / "kazma.yaml"
    p.write_text(_SAMPLE, encoding="utf-8")
    return p


class TestSpliceHelper:
    def test_replaces_only_the_target_section(self):
        out = _splice_yaml_section(_SAMPLE, "mcp", "mcp:\n  servers:\n  - name: fs\n")
        assert "agent-level docs must survive" in out
        assert "inline comment survives" in out
        # The span replaced starts AT the `mcp:` line — a comment line ABOVE
        # the section survives (preserving is the safe default).
        assert "external tool providers" in out
        assert "servers: []" not in out  # old section body replaced
        assert "- name: fs" in out

    def test_appends_when_section_absent(self):
        text = "agent:\n  name: kazma\n"
        out = _splice_yaml_section(text, "mcp", "mcp:\n  servers: []\n")
        assert out.startswith("agent:")
        assert out.endswith("mcp:\n  servers: []\n")

    def test_replaces_final_section_without_following_key(self):
        text = "agent:\n  name: kazma\n\nmcp:\n  servers: []\n"
        out = _splice_yaml_section(text, "mcp", "mcp:\n  servers: [1]\n")
        assert out == "agent:\n  name: kazma\n\nmcp:\n  servers: [1]\n"

    def test_splice_output_is_valid_yaml(self):
        out = _splice_yaml_section(_SAMPLE, "mcp", "mcp:\n  servers:\n  - name: fs\n")
        loaded = yaml.safe_load(out)
        assert loaded["agent"]["name"] == "kazma"
        assert loaded["mcp"]["servers"] == [{"name": "fs"}]
        assert loaded["llm"]["model"] == "gpt-4o-mini"


class TestPersistMcpYaml:
    def test_persist_preserves_comments_outside_mcp(self, tmp_path):
        p = _write_sample(tmp_path)
        assert persist_mcp_yaml([{"name": "fs"}], yaml_path=p) is None
        text = p.read_text(encoding="utf-8")
        assert "agent-level docs must survive" in text
        assert "inline comment survives" in text
        loaded = yaml.safe_load(text)
        assert loaded["mcp"]["servers"] == [{"name": "fs"}]
        assert loaded["llm"]["timeout"] == 60.0

    def test_persist_appends_section_when_missing(self, tmp_path):
        p = tmp_path / "kazma.yaml"
        p.write_text("agent:\n  name: kazma\n  # tail comment\n", encoding="utf-8")
        assert persist_mcp_yaml([], yaml_path=p) is None
        text = p.read_text(encoding="utf-8")
        assert "# tail comment" in text
        assert yaml.safe_load(text)["mcp"] == {"servers": []}

    def test_persist_honors_mcp_section_override(self, tmp_path):
        p = _write_sample(tmp_path)
        assert (
            persist_mcp_yaml(
                [{"name": "fs"}],
                yaml_path=p,
                mcp_section={"servers": [], "enabled": True},
            )
            is None
        )
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert loaded["mcp"]["enabled"] is True
        assert loaded["mcp"]["servers"] == [{"name": "fs"}]

    def test_persist_missing_file_returns_error(self, tmp_path):
        err = persist_mcp_yaml([], yaml_path=tmp_path / "nope.yaml")
        assert isinstance(err, str) and "not found" in err
