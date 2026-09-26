"""Email fails closed the way Calendar does (AGENTS.md §34).

An email provider or account named explicitly -- in the call, by
EMAIL_DEFAULT_PROVIDER, or as an account alias -- that is not connected was
answered by the sandbox mailbox: "Sandbox sent to …" after asking for Gmail,
a send that did not happen reported as one that did, and a typo'd account
alias did the same. The sandbox now answers only when nothing was named and
nothing is connected, or when the sandbox itself was asked for.
"""

from __future__ import annotations

import inspect
import os

import pytest


@pytest.fixture
def no_mail(tmp_path, monkeypatch):
    """Nothing connected: no EMAIL_* variables, an empty vault."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    for key in list(os.environ):
        if key.startswith("EMAIL_"):
            monkeypatch.delenv(key, raising=False)
    import kazma_skills.native.email_manager.credentials as creds
    import kazma_skills.native.email_manager.router as router

    monkeypatch.setattr(creds, "vault_retrieve", lambda name: "")
    monkeypatch.setattr(router, "vault_retrieve", lambda name: "")
    return router


@pytest.mark.parametrize("provider", ["gmail", "microsoft", "outlook", "imap", "pop"])
def test_a_named_provider_that_is_not_connected_is_refused(no_mail, provider) -> None:
    with pytest.raises(no_mail.EmailNotConnectedError) as err:
        no_mail.get_backend(provider)
    assert "Settings" in err.value.hint


def test_the_environment_names_a_provider_as_explicitly_as_the_call(no_mail, monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_DEFAULT_PROVIDER", "gmail")
    with pytest.raises(no_mail.EmailNotConnectedError):
        no_mail.get_backend()


@pytest.mark.parametrize("alias_env", [
    {},  # a typo: no such account
    {"EMAIL_ACCOUNT_WORK_ADDRESS": "w@example.com"},  # set up without a TYPE
    {"EMAIL_ACCOUNT_WORK_TYPE": "gmail"},  # no token, no password
    {"EMAIL_ACCOUNT_WORK_TYPE": "imap", "EMAIL_ACCOUNT_WORK_ADDRESS": "w@example.com"},
    {"EMAIL_ACCOUNT_WORK_TYPE": "carrier-pigeon"},
])
def test_a_named_account_that_is_not_connected_is_refused(no_mail, monkeypatch, alias_env) -> None:
    monkeypatch.setenv("EMAIL_ACCOUNTS", "work")
    for key, value in alias_env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(no_mail.EmailNotConnectedError) as err:
        no_mail.get_backend(account="work")
    assert "'work'" in err.value.hint


def test_an_unknown_provider_is_refused_not_answered_by_the_sandbox(no_mail) -> None:
    with pytest.raises(no_mail.EmailNotConnectedError) as err:
        no_mail.get_backend("gmial")
    assert "gmial" in err.value.hint


def test_the_sandbox_still_answers_when_asked_for_or_when_nothing_was_named(no_mail, monkeypatch) -> None:
    assert no_mail.get_backend("sandbox").name == "sandbox"
    assert no_mail.get_backend("auto").name == "sandbox"
    assert no_mail.get_backend().name == "sandbox"
    monkeypatch.setenv("EMAIL_ACCOUNTS", "demo")
    monkeypatch.setenv("EMAIL_ACCOUNT_DEMO_TYPE", "sandbox")
    assert no_mail.get_backend(account="demo").name == "sandbox"


def test_negative_control_a_connected_provider_is_not_refused(no_mail, monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("EMAIL_GMAIL_APP_PASSWORD", "app-password")
    backend = no_mail.get_backend("gmail")
    assert backend.name != "sandbox"


# ── every email tool turns the refusal into its answer ───────────────────


def _email_tools():
    from kazma_skills.native.email_manager import tools

    found = []
    for name, fn in vars(tools).items():
        if name.startswith("email_") and inspect.iscoroutinefunction(fn):
            if "provider" in inspect.signature(fn).parameters:
                found.append((name, fn))
    return found


_ARGS = {
    "email_get": {"message_id": "m1"},
    "email_send": {"to": "a@example.com", "subject": "s", "body": "b"},
    "email_delete": {"message_id": "m1"},
    "email_categorize": {"message_id": "m1", "star": True},
    "email_analyze": {"message_id": "m1"},
}


def test_the_tool_list_is_enumerated() -> None:
    names = {n for n, _ in _email_tools()}
    assert {"email_list", "email_get", "email_send", "email_delete",
            "email_categorize", "email_analyze"} <= names, names


@pytest.mark.parametrize("name", [n for n, _ in _email_tools()])
async def test_every_email_tool_refuses_a_named_unconnected_provider(no_mail, name, caplog) -> None:
    from kazma_skills.native.email_manager import tools

    fn = getattr(tools, name)
    out = await fn(provider="gmail", **_ARGS.get(name, {}))
    assert out.startswith("Error: Gmail is not connected"), out
    assert "sandbox" not in out.lower(), out
    # An expected refusal is the answer, not a logged crash.
    assert not [r for r in caplog.records if r.exc_info and r.name.startswith("kazma_skills")]
