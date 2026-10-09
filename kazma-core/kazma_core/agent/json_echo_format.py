"""Remove JSON fences only for explicit verbatim copies of user-supplied data."""

from __future__ import annotations

import json
import re

_REQUEST = re.compile(
    r'^(?:return\s+only\s+this\s+JSON\s+object\s+verbatim|'
    r'أعد\s+كائن\s+JSON\s+التالي\s+فقط\s+كما\s+هو)\s*:\s*', re.I,
)
_FENCE = re.compile(r'\A\s*(`{3,}|~{3,})json[ \t]*\r?\n(.*?)\r?\n\1[ \t]*(?:\r?\n|$)([^`~]*)\Z', re.S | re.I)
_CLARIFICATION = re.compile(
    r'(?:This is (?:requested )?data, not (?:a request to read or write anything|a file operation|an action)|'
    r'(?:Use no|Do not (?:use|invoke|call) any|Do not (?:use|invoke|call)) tools|'
    r'هذه بيانات مطلوبة وليست طلبًا لقراءة شيء أو كتابته|لا تستخدم أدوات)', re.I,
)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON keys')
    return result


def _constant(value: str) -> None:
    raise ValueError('Non-JSON numeric constant')


def repair_json_echo(prompt: str, answer: str) -> str | None:
    """Keep unknown requests, wrong data, multiple blocks and bare JSON intact.

    This deliberately recognizes just direct EN/AR object-copy requests. It
    never synthesizes a missing answer, serializes parsed data or invokes tools.
    """
    # Offsets identify the exact literal; never normalize supplied JSON.
    match = _REQUEST.match(prompt)
    if match is None or len(prompt) > 16000 or len(answer) > 16000:
        return None
    start = match.end()
    try:
        value, end = json.JSONDecoder(object_pairs_hook=_object, parse_constant=_constant).raw_decode(prompt[start:])
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    literal = prompt[start:start + end]
    # A second object, code, quotation, or explanation instruction makes the
    # request ambiguous. The supported suffix only clarifies data/no tools.
    suffix = prompt[start + end:].strip()
    if any(not _CLARIFICATION.fullmatch(clause.strip())
           for clause in suffix.split('.') if clause.strip()):
        return None
    fenced = _FENCE.fullmatch(answer)
    if fenced is None or fenced.group(2).strip() != literal:
        return None
    if any(c in fenced.group(3) for c in '{}[]'):
        return None
    return literal
