"""No log line, doctor row or settings log carries a password inside a URL.

A provider's or a database's address can carry ``user:password@``. Model
discovery, the model registry and ``kazma doctor`` wrote such addresses as they
were, and the settings store's change log showed a list's nested key whenever
the vault had not taken it (CodeQL py/clear-text-logging-sensitive-data,
2026-11-14). The log writer masks every message and traceback before it is
queued (``logging_config._LogQueueHandler.prepare``), so a new log call cannot
bring it back.
"""

from __future__ import annotations

import logging
import logging.handlers
import queue

from kazma_core import logging_config
from kazma_core.config_store import _redact_for_log
from kazma_cli import doctor

SECRET = "pw-7f3c9a1e"
URL = f"https://kazma:{SECRET}@llm.example.test/v1"


def _record(msg: str, *args, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    return logging.LogRecord("kazma.test", logging.WARNING, __file__, 1, msg, args, exc_info)


def _raised(text: str) -> BaseException:
    try:
        raise ConnectionError(text)
    except ConnectionError as exc:  # noqa: PERF203 - a real traceback
        return exc


def test_the_log_writer_masks_url_passwords_in_messages_and_tracebacks():
    handler = logging_config._LogQueueHandler(queue.Queue())
    out = handler.prepare(_record("Ollama not running at %s", URL,
                                  exc=_raised(f"refused by {URL}")))
    assert SECRET not in out.getMessage()
    assert SECRET not in (out.exc_text or "")
    assert "llm.example.test" in out.getMessage()  # the host still says where


def test_the_server_logs_through_that_writer():
    # setup_logging puts _LogQueueHandler in front of the file and console
    # handlers; the masking is in its prepare(), which every record passes.
    assert logging_config._LogQueueHandler.prepare is not logging.handlers.QueueHandler.prepare


def test_control_a_plain_queue_handler_keeps_the_password():
    out = logging.handlers.QueueHandler(queue.Queue()).prepare(_record("at %s", URL))
    assert SECRET in out.getMessage()


def test_doctor_rows_mask_the_endpoint():
    _status, text = doctor._line(doctor.OK, "configured: 'm' via 'p'", f"endpoint: {URL}")
    assert SECRET not in text and "llm.example.test" in text


def test_the_settings_log_masks_a_lists_nested_key():
    shown = _redact_for_log("providers.list", [{"name": "p", "api_key": "sk-live-abcdef123456"}])
    assert "sk-live" not in shown and "'name': 'p'" in shown
    assert SECRET not in _redact_for_log("memory.backends.state.url", f"postgresql://u:{SECRET}@db/kazma")


def test_control_the_old_settings_log_showed_it():
    assert "sk-live" in repr([{"name": "p", "api_key": "sk-live-abcdef123456"}])[:77]


# -- a secret in a request's query string (2026-11-16) -------------------------
# uvicorn logs a request as (client, method, path with its query, version,
# status). WhatsApp's verification call carries the webhook's verify token in
# its query, and an OAuth callback its sign-in code and state: both went into
# kazma.log as they were.

VERIFY = "5320f6b6a55482e8129028913ad64759"


def _access(path: str) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d', ("2a03:2880::1:0", "GET", path, "1.1", 200), None)


def test_the_request_log_masks_a_verify_token_and_a_sign_in_code():
    handler = logging_config._LogQueueHandler(queue.Queue())
    whatsapp = handler.prepare(_access(
        f"/api/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=219&hub.verify_token={VERIFY}"
        f"&hub_verify_token={VERIFY}"))
    oauth = handler.prepare(_access("/api/email/oauth/gmail/callback?code=4/0AbcSecret&state=st8te&scope=mail"))
    assert VERIFY not in whatsapp.getMessage()
    assert "hub.challenge=219" in whatsapp.getMessage()  # the rest still says what it was
    assert "0AbcSecret" not in oauth.getMessage() and "st8te" not in oauth.getMessage()
    assert "scope=mail" in oauth.getMessage()


def test_a_parameter_that_only_contains_a_secret_word_is_kept():
    from kazma_core.security.url_credentials import mask_urls_in_text

    path = "/search?monkey=1&q=key&keyboard=3&tokens_used=5"
    assert mask_urls_in_text(path) == path


def test_control_the_old_masker_left_the_verify_token():
    import re

    old = re.compile(r"(?P<key>[?&](?:password|passwd|pwd)=)(?P<value>[^&#\s'\"]*)", re.IGNORECASE)
    line = f"GET /api/webhooks/whatsapp?hub.verify_token={VERIFY}"
    assert VERIFY in old.sub(lambda q: q["key"] + "****", line)
