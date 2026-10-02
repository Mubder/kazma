"""The one answer to "does this text hold a credential?".

Live 2026-10-02: the security report counted 48 "hardcoded secrets" in the
install, none of them a credential. Its keyword patterns flagged any quoted
string after ``token =`` (an environment variable's name, a settings key,
the known-bad default ``serve.py`` keeps in order to refuse it) and missed
a token written in a known format without a keyword. Every value here is
generated, so no test file carries a credential-shaped literal.
"""

from __future__ import annotations

import base64
import re
import secrets
import string

import pytest
from kazma_core.security.secret_scan import find_credentials

_ALNUM = string.ascii_letters + string.digits


def _random(n: int, chars: str = _ALNUM) -> str:
    while True:
        value = "".join(secrets.choice(chars) for _ in range(n))
        if any(c.isdigit() for c in value) and any(c.isalpha() for c in value):
            return value


def _kinds(text: str, *, bare: bool = False) -> list[str]:
    return [f.kind for f in find_credentials(text, bare_values=bare)]


_BODY = base64.b64encode(secrets.token_bytes(120)).decode()

FOUND = {
    "quoted api key": (lambda: f'api_key = "{_random(32)}"', False, "credential"),
    "quoted password": (lambda: f'DB_PASSWORD = "{_random(12)}"', False, "password"),
    "json key": (lambda: f'{{"client_secret": "{_random(40)}"}}', False, "credential"),
    "yaml value": (lambda: f"  api_key: {_random(32)}", True, "credential"),
    "env line": (lambda: f"export SLACK_TOKEN={_random(30)}", True, "credential"),
    "uuid key": (lambda: f'api_key = "{secrets.token_hex(4)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}-'
                         f'{secrets.token_hex(2)}-{secrets.token_hex(6)}"', False, "credential"),
    "hex secret": (lambda: f'secret = "{secrets.token_hex(16)}"', False, "credential"),
    "url password": (lambda: f'url = "postgresql://kazma:{_random(18)}@db:5432/kazma"', False, "password in a URL"),
    "github token in a url": (lambda: f"https://x-access-token:ghp_{_random(36)}@github.com/o/r", False, "GitHub token"),
    "aws key id": (lambda: "AKIA" + _random(16, string.ascii_uppercase + string.digits), False, "AWS access key id"),
    "pem block": (lambda: "-----BEGIN RSA PRIVATE KEY-----\n" + _BODY[:64] + "\n" + _BODY[64:128], False, "private key"),
    "pem in one string": (lambda: 'KEY = "-----BEGIN PRIVATE KEY-----' + chr(92) + "n" + _BODY[:64] + '"',
                          False, "private key"),
}

NOT_FOUND = {
    "an environment variable's name": ('token = "KAZMA_X_TOKEN"', False),
    "a settings key": ('key = "connectors.telegram.bot_token"', False),
    "an identifier": ('token_kind = "default_token_v2"', False),
    "serve.py's known-bad default": ('_KNOWN_BAD_SECRET = "kazma-local-dev-secret"', False),
    "a token endpoint": ('TOKEN_URL = "https://oauth2.googleapis.com/token"', False),
    "a weak local default in a url": ('url = "postgresql://kazma:kazma@localhost/kazma"', False),
    "an f-string": ('token = f"kazma_{secrets.token_hex(32)}"', False),
    "AWS's documented example": ("AKIAIOSFODNN7EXAMPLE", False),
    "an ellipsis": ('token: "123456:ABC-DEF..."', False),
    "a demo placeholder": ('token = "demo-ab12cd34ef-token"', False),
    "a mask": ('password = "********"', False),
    "a template": ("api_key: ${DEEPSEEK_API_KEY}", True),
    "a vault pointer": ("api_key: vault://cfg:providers.x", True),
    "code checking for a pem header": ('if "-----BEGIN PRIVATE KEY-----" in text:', False),
    "a call in a config-looking line": ("token = compute_ab12cd34ef56()", True),
    "an unquoted value in code": ("api_key = ab12cd34ef56gh78ij90", False),
}


@pytest.mark.parametrize("name", sorted(FOUND))
def test_a_credential_is_found(name: str) -> None:
    make, bare, kind = FOUND[name]
    text = make()
    assert _kinds(text, bare=bare) == [kind], name


@pytest.mark.parametrize("name", sorted(NOT_FOUND))
def test_a_stand_in_is_not_a_credential(name: str) -> None:
    text, bare = NOT_FOUND[name]
    assert _kinds(text, bare=bare) == [], name


def test_findings_name_the_line_and_never_the_value() -> None:
    key = _random(32)
    text = f"# config\nname = 'kazma'\napi_key = \"{key}\"\n"
    findings = find_credentials(text)
    assert [(f.line, f.kind) for f in findings] == [(3, "credential")]
    assert key not in repr(findings)


def test_the_old_keyword_patterns_flagged_the_stand_ins() -> None:
    """Negative control: the hardening module's patterns before 2026-10-02
    flag stand-ins this detector leaves alone."""
    old = [
        re.compile(r"""(?:api[_-]?key|apikey)\s*[=:]\s*['"][A-Za-z0-9_\-]{16,}['"]""", re.I),
        re.compile(r"""(?:secret|secret[_-]?key)\s*[=:]\s*['"][A-Za-z0-9_\-]{16,}['"]""", re.I),
        re.compile(r"""(?:token|access[_-]?token|auth[_-]?token)\s*[=:]\s*['"][A-Za-z0-9_\-]{16,}['"]""", re.I),
        re.compile(r"""(?:password|passwd|pwd)\s*[=:]\s*['"][^'"]{8,}['"]""", re.I),
    ]
    flagged = [
        name for name, (text, _bare) in NOT_FOUND.items()
        if any(p.search(text) for p in old)
    ]
    assert {"serve.py's known-bad default", "a demo placeholder", "a mask"} <= set(flagged)
    assert all(_kinds(NOT_FOUND[n][0], bare=NOT_FOUND[n][1]) == [] for n in flagged)
