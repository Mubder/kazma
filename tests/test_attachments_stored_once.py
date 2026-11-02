"""The same attachment sent twice is kept once (2026-11, FA-146).

Every web upload and every file a chat app delivered was written under a new
random name and kept for good: a file sent ten times was ten files. Both are
named by their content's hash now. A web upload's id IS the hash (the browser
gets the same opaque id again); a chat app's file keeps the sender's name
(the model reads it) plus the hash, so two different files of one name stay
two files.

The old random naming, run as a stand-in, is the negative control.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


@pytest.fixture
def web_store(tmp_path, monkeypatch):
    from kazma_ui import chat_attachments

    monkeypatch.setattr(chat_attachments, "ATTACHMENT_DIR", tmp_path / "attachments")
    return chat_attachments


def test_a_web_upload_sent_twice_is_one_file(web_store):
    first = web_store.store_uploaded_attachment(b"a quarterly report", "report.pdf")
    again = web_store.store_uploaded_attachment(b"a quarterly report", "report (1).pdf")
    assert first == again
    assert len(list(web_store.ATTACHMENT_DIR.iterdir())) == 1
    assert web_store._load_uploaded_attachment(first) == b"a quarterly report"


def test_two_different_uploads_stay_two(web_store):
    a = web_store.store_uploaded_attachment(b"one", "a.txt")
    b = web_store.store_uploaded_attachment(b"two", "a.txt")
    assert a != b and len(list(web_store.ATTACHMENT_DIR.iterdir())) == 2


def test_negative_control_random_ids_keep_every_copy(web_store, monkeypatch):
    def old_store(data: bytes, filename: str) -> str:
        attachment_id = f"att_{uuid.uuid4().hex}"
        web_store.ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
        (web_store.ATTACHMENT_DIR / f"{attachment_id}{Path(filename).suffix}").write_bytes(data)
        return attachment_id

    old_store(b"a quarterly report", "report.pdf")
    old_store(b"a quarterly report", "report.pdf")
    assert len(list(web_store.ATTACHMENT_DIR.iterdir())) == 2


def test_a_chat_app_file_sent_twice_is_one_file(tmp_path, monkeypatch):
    from kazma_gateway.agent_handler import attachments
    from kazma_gateway.gateway import Attachment

    monkeypatch.setattr(attachments, "ATTACHMENT_DIR", tmp_path / "gw")
    sent = Attachment(kind="document", mime="application/octet-stream", filename="minutes.docx", data=b"the minutes")
    first = attachments._persist_attachment(sent)
    again = attachments._persist_attachment(Attachment(kind="document", mime="application/octet-stream", filename="minutes.docx",
                                                       data=b"the minutes"))
    other = attachments._persist_attachment(Attachment(kind="document", mime="application/octet-stream", filename="minutes.docx",
                                                       data=b"other minutes"))
    assert first == again and first != other
    assert Path(first).name.startswith("minutes_") and Path(first).suffix == ".docx"
    assert len(list((tmp_path / "gw").iterdir())) == 2
