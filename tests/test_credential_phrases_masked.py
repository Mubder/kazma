"""Credential phrases are masked by the value's shape, in both languages.

A colon, an equals sign, or a fullwidth colon masks the next token. ``is``
/ ``هي`` / ``هو``, and a label with no separator, mask the next word when
it has an underscore, a short hyphen piece, both a letter and a digit, or
four or more digits. The gap is any whitespace but a line break. The PIN
labels mask the next word whatever it is. ``redact_secrets`` masks
``token: expired``; the phrase masker keeps it. The patterns are string
literals on ``re.compile`` (the regex sweep cannot see a concatenation)
and linear.
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
_H = r"[^\S\r\n]"
# A hyphenated word is secret-shaped when one piece is at most three
# characters. ``rotation-policy`` and ``سياسة-التدوير`` are longer pieces.
_SHAPED = (
    r"(?:[^\s_]*+_[^\s]*+"
    r"|(?=[^\s]*\d)(?=[^\s]*[^\W\d_])[^\s]++"
    r"|(?=[^\s]*-)(?:[^-\s]{1,3}-[^\s]*+"
    r"|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}-[^\s]*+"
    r"|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}+(?!\S))"
    r"|\d{4,}+(?!\d))"
)
_PIN_LABELS = ("الرقم السري", "رمز المرور")
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
    "password 2024",
    "The password is 8 characters",
    "Reset the password on 3 machines",
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


_DIGIT_AND_HYPHEN = [
    "password 123456",
    "password abc-def",
    "password is 123456",
    "password is 4821",
    "password is 2024",
    "كلمة المرور 4821",
    "كلمة المرور 1234 غير صحيحة",
    "كلمة المرور هي 4821",
    "كلمة المرور هو 4821",
    "الرقم السري هو 4821",
    "رمز المرور 9999",
    "رمز المرور شمس",
    "PIN 4821",
    "PIN is 12",
    "pin=4821",
    "secret=Sunr1se",
    "password: Sunr1se",
]
_PIECE = (
    "123456", "abc-def", "4821", "1234", "9999", "12", "2024",
    "شمس", "Sunr1se",
)


@pytest.mark.parametrize("text", _DIGIT_AND_HYPHEN)
def test_a_digit_run_a_short_hyphen_and_a_pin_are_masked(text):
    secret = next(piece for piece in _PIECE if piece in text)
    for name, out in _readers(text).items():
        assert secret not in out, f"{name}: {out!r}"


def test_a_one_digit_count_and_a_year_stay():
    """The bare year is the L3 sentence. ``is`` plus four digits is a value."""
    for text in (
        "The secret 2024 plan",
        "password 2024",
        "The password is 8 characters",
        "Reset the password on 3 machines",
        "كلمة المرور لازم تكون طويلة",
    ):
        for name, out in _readers(text).items():
            assert out == text or out == str({"note": text}), f"{name}: {out!r}"
    assert "2024" not in redact_secrets("password is 2024")
    assert "123456" not in redact_secrets("password: 123456")


_BREAKS = [
    f"password\u00A0is\u00A0{SECRET}",
    f"password\u202F{SECRET}",
    f"secret\u00A0=\u00A0{SECRET}",
    f"كلمة\u00A0المرور: {ARABIC_SECRET}",
    f"كلمة\u202Fالسر هي {ARABIC_SECRET}",
    f"رمز\u00A0المرور\u202Fهو\u202F{ARABIC_SECRET}",
]
_LINE_BREAKS = [
    f"password\nis {SECRET}",
    f"password\n{SECRET}",
    f"كلمة\nالمرور: {ARABIC_SECRET}",
    f"كلمة المرور\nهي {ARABIC_SECRET}",
    f"كلمة\rالمرور: {ARABIC_SECRET}",
]


@pytest.mark.parametrize("text", _BREAKS)
def test_a_no_break_space_between_words_is_masked_in_both_languages(text):
    secret = SECRET if SECRET in text else ARABIC_SECRET
    for name, out in _readers(text).items():
        assert secret not in out, f"{name}: {out!r}"


def test_a_no_break_space_inside_an_arabic_json_key_is_masked():
    text = '{"كلمة\u00A0المرور": "' + ARABIC_SECRET + '"}'
    assert ARABIC_SECRET not in redact_secrets(text)
    assert ARABIC_SECRET not in mask_urls_in_text(text)
    assert ARABIC_SECRET not in _REDACT.sub("<redacted>", text)


@pytest.mark.parametrize("text", _LINE_BREAKS)
def test_a_line_break_stops_the_match_in_both_languages(text):
    for name, out in _readers(text).items():
        assert out == text or out == str({"note": text}), f"{name}: {out!r}"


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
        if label in _PIN_LABELS:
            assert "لازم" not in redact_secrets(plain)
            assert "لازم" not in mask_urls_in_text(plain)
        else:
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
    for text in (
        "a" * 200_000,
        "password " + "a" * 200_000,
        "password:" + "a" * 200_000,
        "-" * 200_000,
    ):
        start = time.perf_counter()
        mask_urls_in_text(text)
        took = time.perf_counter() - start
        assert took < 1.0, (len(text), took)


def _marked(label: str) -> str:
    parts: list[str] = []
    for char in label:
        parts.append(_H + "++" if char.isspace() else re.escape(char) + _MARK)
    return "".join(parts)


def _over_broad(pattern: str) -> bool:
    return r"\S*[0-9_-]\S*" in pattern or "[a-z0-9+.-]*://" in pattern


def test_the_arabic_literal_is_every_label():
    """The sweep sees a literal. It has to be the labels, not a narrower copy."""
    labels = "|".join(_marked(label) for label in _ARABIC_CREDENTIAL_LABELS)
    copula = "|".join(_marked(word) for word in ("هي", "هو"))
    pin = "|".join(_marked(label) for label in _PIN_LABELS)
    other = "|".join(
        _marked(label) for label in _ARABIC_CREDENTIAL_LABELS if label not in _PIN_LABELS
    )
    # A value that is already stars (the URL masker's ``password=****``) is not
    # a phrase: swallowing it would drop the key the query masker kept.
    not_stars = r"(?!\*++(?!\S))"
    any_value = (
        rf"(?:{_H}*+[=:\uFF1A]{_H}*+{not_stars}\S++"
        rf"|{_H}++(?:{copula}){_H}++\S++"
        rf"|{_H}++\S++)"
    )
    shaped_value = (
        rf"(?:{_H}*+[=:\uFF1A]{_H}*+{not_stars}\S++"
        rf"|{_H}++(?:{copula}){_H}++{_SHAPED}"
        rf"|{_H}++{_SHAPED})"
    )
    assert _ARABIC_SECRET.pattern == f"(?:(?:{pin}){any_value}|(?:{other}){shaped_value})"
    assert _ARABIC_JSON_SECRET.pattern == (
        '"' + "(?:" + labels + ")" + r'"\s*+[:\uFF1A]\s*+"[^"]*+"'
    )
    assert _PROSE_SECRETS.pattern.count(_SHAPED) == 2
    for pattern in (_PROSE_SECRETS, _ARABIC_SECRET, _ARABIC_JSON_SECRET):
        assert "[ \\t]" not in pattern.pattern


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
