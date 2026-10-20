"""An inbox listing shows each mail's sender, date and real subject.

Live 2026-10-20 (Telegram), test 22: "What's in my inbox from today?" -- the
Gmail listing came back with every sender and date blank, and the agent
opened 8 mails one by one to answer. The listing asked for its headers as
``metadataHeaders=From,To,Subject,Date``; Gmail takes that as ONE header's
name and returns none, and the subject fell back to the mail's preview text.
The fake Gmail here answers as Gmail does: only the headers named, each by
its own parameter.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from kazma_skills.native.email_manager.backends import gmail_api
from kazma_skills.native.email_manager.models import ListQuery

HEADERS = {"From": "OpenRouter <noreply@openrouter.ai>", "To": "me@example.com",
           "Subject": "GPT-4o and GPT-4.1 models retire January 14, 2027",
           "Date": "Tue, 20 Oct 2026 09:12:00 +0000"}


def _fake_gmail(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/users/me/messages"):
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})
    named = request.url.params.get_list("metadataHeaders")
    return httpx.Response(200, json={
        "id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"],
        "snippet": "We are retiring two models from the catalogue on January 14",
        "payload": {"headers": [{"name": n, "value": v} for n, v in HEADERS.items() if n in named]},
    })


@pytest.fixture
def gmail(monkeypatch):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("verify", None)
        return real(*args, transport=httpx.MockTransport(_fake_gmail), **kwargs)

    monkeypatch.setattr(gmail_api.httpx, "AsyncClient", client)
    return gmail_api.GmailApiBackend(access_token="demo-gmail-token")


def _listed(backend):
    return asyncio.run(backend.list_messages(ListQuery(folder="INBOX", limit=5)))


def test_the_listing_shows_sender_date_and_subject(gmail):
    (mail,) = _listed(gmail)
    assert mail.from_addr == HEADERS["From"]
    assert mail.date == HEADERS["Date"]
    assert mail.subject == HEADERS["Subject"]


def test_negative_control_one_comma_joined_parameter_names_no_header(gmail, monkeypatch):
    monkeypatch.setattr(gmail_api, "_LIST_HEADERS", ("From,To,Subject,Date",))
    (mail,) = _listed(gmail)
    assert mail.from_addr == "" and mail.date == ""
    assert mail.subject.startswith("We are retiring"), "the preview stood in for the subject"
