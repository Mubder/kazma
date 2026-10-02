"""Does this text hold a credential? -- the one answer.

The security report's source scan and the skill-manifest scan each asked it
with their own patterns: keyword regexes that flagged any quoted string after
``token =`` -- an environment variable's name, a settings key, the known-bad
default ``serve.py`` keeps in order to refuse it -- and missed a credential in
a known format written without a keyword (a token inside a URL, a PEM
block). The report counted 48 "hardcoded secrets" on the live install, none
of them a credential: the install's own code held one false hit and the rest
were inside a package cache (2026-10-02).

:func:`find_credentials` returns ``(line, kind)`` pairs, never the value. A
line is a finding when it holds

* a credential in a format its issuer publishes (a private key block, an AWS
  access key id, a GitHub, Slack, Google, Stripe, OpenAI, Anthropic or
  Telegram token, a JSON Web Token), or
* a quoted value assigned to a credential-named key (``api_key = "..."``,
  ``password: '...'``) that looks random: at least 16 characters (10 for a
  password), letters and digits, varied enough.

A placeholder is never a finding: words such as ``example``, ``your``,
``changeme`` or ``demo``, a template (``<token>``, ``${VAR}``, ``{{ x }}``),
a ``vault://`` pointer, an environment variable's NAME, a dotted settings
key, or a run of one repeated character.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = ["CredentialFinding", "find_credentials"]

#: Lines longer than this are read only this far (minified bundles).
_MAX_LINE = 4096

#: Credential formats as their issuers publish them.
_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b")),
    ("Slack token", re.compile(r"\b(?:xox[abposr]-[0-9A-Za-z-]{10,250}|xapp-\d-[A-Z0-9]+-\d+-[a-z0-9]{16,})\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:AA[0-9A-Za-z_-]{33}\b")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}")),
)

#: One pass that says whether any format can match a line at all.
_ANY_FORMAT = re.compile("|".join(f"(?:{pattern.pattern})" for _kind, pattern in _FORMATS))

#: A private key's first line. Only a key when key material follows it: a
#: parser that looks for the line is not a key.
_PEM_HEADER = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
_PEM_BODY = re.compile(r"[A-Za-z0-9+/=]{40,}")
_BACKSLASH = chr(92)

#: ``name = "value"`` where the name says it holds a credential.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>[a-z0-9_.-]{0,40}?
        (?:api[_.-]?key|apikey|secret|token|passw(?:or)?d|passwd|pwd
          |private[_.-]?key|access[_.-]?key|auth[_.-]?key|credentials?)
        [a-z0-9_.-]{0,40})
    ["']?\s*(?::=|=>|:|=)\s*
    [rbuf]{0,2}(?P<q>["'])(?P<value>[^"'\s]{8,512})(?P=q)
    """
)
#: ``name: value`` / ``NAME=value`` unquoted, a whole line of a config file
#: (YAML, INI, ``.env``); code assigns names, so it is never read this way.
_BARE_ASSIGNMENT = re.compile(
    r"""(?ix)
    ^\s*(?:export\s+)?-?\s*
    (?P<name>[a-z0-9_.-]{0,40}?
        (?:api[_.-]?key|apikey|secret|token|passw(?:or)?d|passwd|pwd
          |private[_.-]?key|access[_.-]?key|auth[_.-]?key|credentials?)
        [a-z0-9_.-]{0,40})
    \s*[:=]\s*
    (?P<value>[A-Za-z0-9_+/=.@!%^*~-]{8,512})
    \s*(?:[#;].*)?$
    """
)
_PASSWORD_NAME = re.compile(r"(?i)passw(?:or)?d|passwd|pwd")
#: A line holds none of these, lowercased: no credential name can match.
_NAME_WORDS = ("key", "secret", "token", "pass", "pwd", "credential")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>`]+")

#: Words a value holds when it stands in for a credential.
_PLACEHOLDER_WORDS = (
    "example", "sample", "placeholder", "changeme", "change-me", "change_me",
    "dummy", "fake", "your", "redacted", "replace", "insert", "demo",
    "test", "xxxx", "****", "...", "notreal", "not-real", "invalid",
)
_TEMPLATE_MARKS = ("{", "}", "%(", "<", ">", "$(", "vault://", "env:", "cfg:")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
#: A settings key or an identifier (``connectors.x.bot_token``, ``kazma-dev``):
#: word segments, each letters then optional digits. A UUID or hex key is not.
_IDENTIFIER = re.compile(r"^[a-z]+[0-9]*(?:[._:/-][a-z]+[0-9]*)+$")


@dataclass(frozen=True)
class CredentialFinding:
    """One credential-looking value: where, and of what kind (never the value)."""

    line: int
    kind: str


def _entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _is_placeholder(value: str) -> bool:
    """True when *value* stands in for a credential rather than being one."""
    low = value.lower()
    if any(word in low for word in _PLACEHOLDER_WORDS):
        return True
    if any(mark in value for mark in _TEMPLATE_MARKS):
        return True
    # One character repeated (``aaaaaaaa``) or barely varied: a mask, not a key.
    return len(set(value)) <= max(3, len(value) // 6)


def _names_something(value: str) -> bool:
    """*value* is a name, not a secret: an environment variable or a settings key.

    Asked of a value assigned to a credential-named key only -- an AWS key id
    is shaped like an environment variable's name.
    """
    return bool(_ENV_NAME.match(value) or _IDENTIFIER.match(value))


def _looks_random(value: str, *, password: bool) -> bool:
    if len(value) < (10 if password else 16):
        return False
    if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return False
    return _entropy(value) >= 3.0


def _private_key_at(line: str, lines: list[str], index: int) -> bool:
    """A private key block starts on this line: its header, then key material."""
    header = _PEM_HEADER.search(line)
    if not header:
        return False
    after = " ".join([line[header.end():], *lines[index + 1:index + 5]])
    # A key inside one string literal carries its line breaks escaped.
    after = after.replace(_BACKSLASH + "n", " ").replace(_BACKSLASH + "r", " ")
    return bool(_PEM_BODY.search(after[:600]))


def _line_kind(line: str, lines: list[str], index: int, *, bare_values: bool) -> str:
    if "PRIVATE KEY" in line and _private_key_at(line, lines, index):
        return "private key"
    if _ANY_FORMAT.search(line):
        for kind, pattern in _FORMATS:
            for match in pattern.finditer(line):
                if not _is_placeholder(match.group(0)):
                    return kind
    if "://" in line and "@" in line:
        for match in _URL.finditer(line):
            if _url_password(match.group(0)):
                return "password in a URL"
    low = line.lower()
    if not any(word in low for word in _NAME_WORDS):
        return ""
    for match in _ASSIGNMENT.finditer(line):
        kind = _assigned_kind(match.group("name"), match.group("value"))
        if kind:
            return kind
    if bare_values:
        match = _BARE_ASSIGNMENT.match(line)
        if match:
            return _assigned_kind(match.group("name"), match.group("value"))
    return ""


def _assigned_kind(name: str, value: str) -> str:
    """What a value assigned to a credential-named key is, or ''."""
    if "://" in value:
        return ""  # a URL: a credential only with a password (judged above)
    password = bool(_PASSWORD_NAME.search(name))
    if _is_placeholder(value) or _names_something(value) or not _looks_random(value, password=password):
        return ""
    return "password" if password else "credential"


def _url_password(url: str) -> bool:
    """*url* carries a real password (``scheme://user:password@host``)."""
    try:
        secret = urlsplit(url).password or ""
    except ValueError:
        return False
    return bool(secret) and not _is_placeholder(secret) and _looks_random(secret, password=True)


def find_credentials(text: str, *, bare_values: bool = False) -> list[CredentialFinding]:
    """Every line of *text* that holds a credential, with its kind.

    *bare_values*: the text is a config file (YAML, INI, ``.env``), where
    ``password: value`` holds a value without quotes.
    """
    lines = text.splitlines()
    findings: list[CredentialFinding] = []
    for index, raw in enumerate(lines):
        kind = _line_kind(raw[:_MAX_LINE], lines, index, bare_values=bare_values)
        if kind:
            findings.append(CredentialFinding(index + 1, kind))
    return findings
