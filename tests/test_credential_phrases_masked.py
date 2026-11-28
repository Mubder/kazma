"""Credential phrases are masked by the value's shape, in both languages.

A colon, an equals sign, or a fullwidth colon masks the next token. ``is``
/ ``هي`` / ``هو``, and a label with no separator, mask the next word only
when that word contains an underscore or both a letter and a digit, and
only across a space or a tab. The patterns are string literals on
``re.compile`` (the regex sweep cannot see a concatenation) and linear.
"""
from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path

import pytest

from kazma_core.errors import (
    _ARABIC_CREDENTIAL_LABELS,
    _ARABIC_JSON_SECRET,
    _ARABIC_SECRET,
    _PROSE_SECRETS,
    _REDACT,
    _REDACT_PATHS,
    _REDACT_SECRETS,
    is_arabic_credential_label,
    redact_secrets,
    validation_error,
)
from kazma_core.security.config_mask import MASK, mask_deep
from kazma_core.security.url_credentials import mask_urls_in_text

ROOT = Path(__file__).resolve().parents[1]
_MARK = r"[\u064B-\u065F\u0670\u0640]*+"
_SHAPED = r"(?:[^\s_]*+_[^\s]*+|(?=[^\s]*\d)(?=[^\s]*[^\W\d_])[^\s]++)"
SECRET = "envplain-EE11"
ARABIC_SECRET = "arpw-AR99"

PHRASES = [
    f"password is {SECRET}",
    f"Password IS {SECRET}",
    f"passwd is hunter2",
    f"password {SECRET}",
    f"api_key my_token",
    f"password\uFF1A{SECRET}",
    f"token\uFF1A{SECRET}",
    f"كلمة المرور هي {ARABIC_SECRET}",
    f"كلمة السر هي {ARABIC_SECRET}",
    f"الرقم السري هو {ARABIC_SECRET}",
    f"كلمةالمرور: {ARABIC_SECRET}",
    f"رمز المرور: {ARABIC_SECRET}",
    f"رمز المرور {ARABIC_SECRET}",
    f"كلمة المرور\uFF1A{ARABIC_SECRET}",
    f"كلمة الس\u0651ر: {ARABIC_SECRET}",
    f"كلمة المرور سر١٢٣",
]
CONTROLS = [
    "the password is required",
    "The secret 2024 plan",
    "api_key rotation-policy doc",
    "secret\n2. next item",
    "password 123456",
    "I forgot the password yesterday.",
    "كلمة المرور لازم تكون طويلة",
    "نسيت كلمة المرور أمس.",
    "السر ٢٠٢٤ في الخطة",
    "مفتاح سري سياسة-التدوير",
    "كلمة المرور\n٢. البند التالي",
    "token: expired",
]


def _secret_of(text: str) -> str:
    for secret in (SECRET, ARABIC_SECRET, "hunter2", "my_token", "سر١٢٣"):
        if secret in text:
            return secret
    raise AssertionError(text)


def _readers(text: str):
    from kazma_core.memory.topic_summaries import _scrub
    from kazma_core.security.approval_args import redact_text

    return {
        "redact_secrets": redact_secrets(text),
        "_REDACT": _REDACT.sub("<redacted>", text),
        "validation_error": validation_error(RuntimeError(text)),
        "redact_text": redact_text(text),
        "mask_urls_in_text": mask_urls_in_text(text),
        "_scrub": _scrub(text),
        "mask_deep": str(mask_deep({"note": text})),
    }


@pytest.mark.parametrize("text", PHRASES)
def test_each_phrase_is_masked_by_every_free_text_reader(text):
    secret = _secret_of(text)
    for name, out in _readers(text).items():
        assert secret not in out, f"{name}: {out!r}"


@pytest.mark.parametrize("text", CONTROLS)
def test_an_ordinary_sentence_is_not_masked(text):
    """``token: expired`` is prose for the URL masker and a secret for the
    error redactor, which keeps ``token`` colon-only and aggressive."""
    from kazma_core.memory.topic_summaries import _scrub

    assert mask_urls_in_text(text) == text
    assert _scrub(text) == text
    assert str(mask_deep({"note": text})) == str({"note": text})
    if text == "token: expired":
        assert redact_secrets(text) == "<redacted>"
        return
    for name, out in _readers(text).items():
        assert out == text or out == str({"note": text}), f"{name}: {out!r}"


def test_a_digit_only_pin_stays_on_the_colon_form():
    assert "123456" not in redact_secrets("password: 123456")
    assert redact_secrets("password 123456") == "password 123456"
    assert "123456" not in redact_secrets("كلمة المرور: 123456")


def test_every_arabic_label_masks_each_form_and_keeps_a_plain_word():
    for label in _ARABIC_CREDENTIAL_LABELS:
        assert is_arabic_credential_label(label)
        for text in (
            f"{label}: {ARABIC_SECRET}",
            f"{label}\uFF1A{ARABIC_SECRET}",
            f"{label} هي {ARABIC_SECRET}",
            f"{label} هو {ARABIC_SECRET}",
            f"{label} {ARABIC_SECRET}",
            f"{label} my_token",
        ):
            out = redact_secrets(text)
            assert ARABIC_SECRET not in out and "my_token" not in out, out
            assert ARABIC_SECRET not in mask_urls_in_text(text)
        plain = f"{label} لازم"
        assert redact_secrets(plain) == plain
        assert mask_urls_in_text(plain) == plain


def test_a_json_key_in_either_language_is_masked_by_name_and_by_text():
    for key in ("password", "رمز المرور", "كلمةالمرور", "كلمة المرور"):
        assert mask_deep({key: ARABIC_SECRET}) == {key: MASK}
        dumped = json.dumps({key: ARABIC_SECRET}, ensure_ascii=False)
        assert ARABIC_SECRET not in redact_secrets(dumped)
    assert '"tokens"' in redact_secrets('{"tokens": "envplain-EE11"}')
    assert SECRET in redact_secrets('{"tokens": "envplain-EE11"}')


def test_control_the_old_no_colon_form_over_masked():
    old = re.compile(
        r"(?i)(?:password|passwd|secret|api[_-]?key)\s+\S*[0-9_-]\S*",
    )
    for text in ("The secret 2024 plan", "api_key rotation-policy doc", "secret\n2. next item"):
        assert old.search(text), text
        assert redact_secrets(text) == text


def test_control_the_old_userinfo_pattern_grew_and_the_new_one_does_not():
    old = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:[^@\s/'\"]+@", re.IGNORECASE)

    def elapsed(pattern, text):
        start = time.perf_counter()
        pattern.findall(text)
        return time.perf_counter() - start

    short = elapsed(old, "a" * 6_000)
    long = elapsed(old, "a" * 12_000)
    assert long > short * 3, (short, long)
    for pattern, text in (
        (_REDACT_SECRETS, "a" * 200_000),
        (_REDACT_SECRETS, '"' + "a" * 200_000),
        (_PROSE_SECRETS, "password " + "a" * 200_000),
        (_ARABIC_SECRET, "ا" * 200_000),
        (_ARABIC_JSON_SECRET, '"' + "ا" * 200_000),
    ):
        took = elapsed(pattern, text)
        assert took < 1.0, (pattern, took)
        assert took < long


def _marked(label: str) -> str:
    parts: list[str] = []
    for char in label:
        parts.append(r"[ \t]++" if char.isspace() else re.escape(char) + _MARK)
    return "".join(parts)


def _over_broad(pattern: str) -> bool:
    return r"\S*[0-9_-]\S*" in pattern or "[a-z0-9+.-]*://" in pattern


def test_the_arabic_literal_is_every_label():
    """The sweep sees a literal. It has to be the labels, not a narrower copy."""
    labels = "|".join(_marked(label) for label in _ARABIC_CREDENTIAL_LABELS)
    copula = "|".join(_marked(word) for word in ("هي", "هو"))
    secret = (
        "(?:" + labels + ")"
        + r"(?:[ \t]*+[=:\uFF1A][ \t]*+\S++"
        + r"|[ \t]++(?:" + copula + r")[ \t]++" + _SHAPED
        + r"|[ \t]++" + _SHAPED + ")"
    )
    assert _ARABIC_SECRET.pattern == secret
    assert _ARABIC_JSON_SECRET.pattern == (
        '"' + "(?:" + labels + ")" + r'"\s*+[:\uFF1A]\s*+"[^"]*+"'
    )


def test_the_credential_patterns_are_literals_the_sweep_sees():
    from tests.test_regex_sweep import regex_literals

    path = ROOT / "kazma-core" / "kazma_core" / "errors.py"
    seen = {lit.pattern for lit in regex_literals([path])}
    for pattern in (_REDACT_SECRETS, _PROSE_SECRETS, _ARABIC_SECRET, _ARABIC_JSON_SECRET, _REDACT_PATHS):
        assert pattern.pattern in seen
        assert not _over_broad(pattern.pattern), pattern.pattern[:80]
    assert not any(_over_broad(pattern) for pattern in seen)


def test_control_a_concatenation_is_invisible_and_the_old_shape_is_broad(tmp_path):
    from tests.test_regex_sweep import regex_literals

    planted = tmp_path / "planted.py"
    planted.write_text(
        "import re\n"
        "FRAG = r'\\s+\\S*[0-9_-]\\S*'\n"
        "OLD = re.compile('(?:password)' + FRAG)\n"
        "SEEN = re.compile(r'[a-z][a-z0-9+.-]*://[^\\s]+')\n",
        encoding="utf-8",
    )
    found = {lit.pattern for lit in regex_literals([planted], root=tmp_path)}
    assert found == {r"[a-z][a-z0-9+.-]*://[^\s]+"}
    missed = ast.parse(planted.read_text(encoding="utf-8"))
    concats = [
        node for node in ast.walk(missed)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
    ]
    assert concats, "the planted concatenation was not there to be missed"
    assert _over_broad(r"(?:password)\s+\S*[0-9_-]\S*")
    assert _over_broad(r"[a-z][a-z0-9+.-]*://[^/\s]+")
    assert not _over_broad(_REDACT_SECRETS.pattern)
