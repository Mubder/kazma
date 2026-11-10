"""``read_system_logs`` hides every secret the product's redactor knows.

Audit 2026-11-10: the tool kept its own sanitiser, weaker than the product's
(``kazma_core.errors.redact_secrets``): a DSN's password, Slack ``xox`` and
GitHub ``ghp_``/``ghs_`` tokens, ``sk-proj-``/``sk-ant-`` keys, Telegram
``bot<id>:<token>`` URLs and JSON-format lines (``"password": "x"``) reached
the model. It now calls the one redactor, which learned the shapes it lacked
(Telegram tokens, JSON members, Authorization credentials, Google keys).

Every fake secret below is built at run time from pieces, so no token-shaped
literal sits in the repository (push protection refuses them). The negative
control is the tool's old sanitiser, reproduced.
"""

from __future__ import annotations

import re

import pytest

from kazma_core.errors import redact_secrets
from kazma_skills.native.system_health_monitor import tools

TAIL = "Q" * 36  # the secret part of every token: found in no output


def _token(prefix: str, sep: str = "_") -> str:
    return prefix + sep + TAIL


CORPUS = [
    "connecting to postgresql://kazma:" + "pw" + TAIL + "@db.local:5432/kazma",
    "slack: " + _token("xox" + "b", "-"),
    "github token " + _token("gh" + "p"),
    "github app " + _token("gh" + "s"),
    "openai " + _token("sk-" + "proj", "-"),
    "anthropic " + _token("sk-" + "ant", "-"),
    "GET https://api.telegram.org/" + "bot" + "123456789:" + "AA" + TAIL + "/getUpdates",
    '{"level": "INFO", "password": "' + TAIL + '"}',
    '{"client_secret": "' + TAIL + '", "event": "oauth"}',
    "Authorization: Bearer " + TAIL,
    "gemini key AI" + "za" + "Sy" + TAIL,
    "db_password=" + TAIL,
]


@pytest.mark.parametrize("line", CORPUS)
def test_the_tool_hides_every_secret(line):
    out = tools._sanitize_log_text(line)
    assert TAIL not in out, out


@pytest.mark.parametrize("line", CORPUS)
def test_the_shared_redactor_hides_it_too(line):
    from kazma_core.security.url_credentials import mask_urls_in_text

    assert TAIL not in redact_secrets(mask_urls_in_text(line))


@pytest.mark.parametrize("line", [
    "[turn] Turn finished: platform=web failed=False",
    '{"level": "INFO", "message": "max_tokens reached", "turn": 4}',
    "GET /api/chat/history 200 12ms",
    "saved C:/Users/someone/report.pdf",
])
def test_ordinary_lines_are_kept(line):
    assert tools._sanitize_log_text(line) == line


def _old_sanitize(text: str) -> str:
    """The tool's sanitiser before 2026-11-10."""
    text = re.sub(r"sk-[a-zA-Z0-9]{32,}", "[REDACTED_OPENAI_KEY]", text)
    text = re.sub(r"AIzaSy[a-zA-Z0-9_-]{33}", "[REDACTED_GEMINI_KEY]", text)
    text = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.\~]+", "Bearer [REDACTED_BEARER_TOKEN]", text)
    text = re.sub(r"(?i)(api[-_]?key|secret|password|token|jwt|auth)\s*[=:]\s*['\"][^'\"]+['\"]",
                  r'\1: "[REDACTED_SECRET]"', text)
    return re.sub(r"(?i)(api[-_]?key|secret|password|token|jwt|auth)\s*[=:]\s*[a-zA-Z0-9_\-]+",
                  r"\1=[REDACTED_SECRET]", text)


def test_negative_control_the_old_sanitiser_leaked():
    leaked = [line for line in CORPUS if TAIL in _old_sanitize(line)]
    # The DSN, Slack, GitHub, sk-proj/sk-ant, Telegram and both JSON lines.
    assert len(leaked) >= 8, leaked
    assert any("postgresql://" in line for line in leaked)
    assert any("api.telegram.org" in line for line in leaked)
    assert any(line.startswith('{"level"') for line in leaked)


def test_the_tool_has_no_sanitiser_of_its_own():
    """One redactor: the tool's module no longer compiles secret patterns."""
    import inspect

    src = inspect.getsource(tools)
    assert "redact_secrets" in src and "re.sub" not in src
