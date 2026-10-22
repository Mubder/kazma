"""A convert or redact hands the agent a file it can send.

Live 2026-10-22: the owner asked for a scanned booklet as a Word file.
``document_convert`` worked in four seconds and answered with an
``artifact_id`` only; no agent tool takes one, so the model searched the disk
for two and a half minutes (a filesystem search timed out at 120 s), read the
document store's internals, and finally made a new file with
``generate_docx``. The tool now copies its artifact where the generators
write and names that path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from kazma_skills.native.document_platform import tools


class _Service:
    def __init__(self, source: Path, *, resolve_error: Exception | None = None):
        self.source = source
        self.resolve_error = resolve_error
        self.resolved_for: list[tuple[str, str, str]] = []

    async def convert_document(self, **kwargs):
        return self._data()

    async def redact_document(self, **kwargs):
        return self._data()

    def _data(self):
        return {
            "artifact_id": "a" * 32,
            "document_id": "doc-1",
            "manifest": {"output": {"extension": "docx", "size": self.source.stat().st_size}},
            "warnings": [],
        }

    def resolve_artifact_blob(self, *, tenant_id, actor_id, artifact_id):
        self.resolved_for.append((tenant_id, actor_id, artifact_id))
        if self.resolve_error is not None:
            raise self.resolve_error
        return {"path": self.source, "filename": "convert-aaaaaaaa.docx", "mime_type": "x"}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    source = tmp_path / "blob"
    source.write_bytes(b"PK\x03\x04 a word file")

    def install(**kwargs):
        svc = _Service(source, **kwargs)

        async def ensure():
            return svc

        async def scope():
            return ("default", "w", "operator")

        monkeypatch.setattr(tools, "_ensure_service", ensure)
        monkeypatch.setattr(tools, "_scope", scope)
        return svc

    return source, install


def _saved_path(reply: str) -> Path:
    line = next(item for item in reply.splitlines() if "Saved to:" in item)
    return Path(line.split("Saved to:", 1)[1].strip())


@pytest.mark.parametrize("call", ["convert", "redact"])
def test_the_answer_names_a_file_with_the_artifact_bytes(wired, call):
    from kazma_core.paths import data_dir

    source, install = wired
    svc = install()
    if call == "convert":
        reply = asyncio.run(tools.document_convert("doc-1", "docx", "مذكرات بو محمد"))
    else:
        reply = asyncio.run(tools.document_redact("doc-1", ["secret"], "مذكرات"))
    saved = _saved_path(reply)
    assert saved.is_file() and saved.read_bytes() == source.read_bytes()
    assert saved.parent == (data_dir() / "documents").resolve()
    assert saved.suffix == ".docx" and "مذكرات" in saved.name
    assert "send_file" in reply
    # The copy is read through the artifact's own access check, as the tool's actor.
    assert svc.resolved_for == [("default", "operator", "a" * 32)]


def test_negative_control_the_old_answer_named_no_file():
    data = {"artifact_id": "a" * 32, "document_id": "doc-1", "manifest": {"output": {}}}
    old = "\n".join(tools._format_artifact("Converted", data).splitlines()[:3])
    assert "Saved to:" not in old and "/" not in old.split("artifact_id:", 1)[1]


def test_a_copy_that_fails_leaves_the_convert_standing(wired):
    _source, install = wired
    install(resolve_error=OSError("disk"))
    reply = asyncio.run(tools.document_convert("doc-1", "docx"))
    assert reply.startswith("Converted.")
    assert "Saved to:" not in reply and "Documents page" in reply
