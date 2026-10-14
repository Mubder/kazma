"""What a swarm-bus approval card shows of a tool call's arguments (Swarm audit SW-S03).

The card used to carry ``json.dumps(arguments)[:200]``, cut again to 100
characters, with no mark that anything was missing: an injected call
ordered ``{"content": "<a note>", "path": "~/.ssh/authorized_keys"}`` read as
a note. Now every argument NAME is on the card, each value bounded with an
explicit "[N chars hidden]" when it is cut, and a hash of the whole
arguments, so what is approved can be checked against what runs (the gate
registry keeps the whole call, ``GateRow.args_json``). A call whose argument
names cannot all be shown is refused from chat cards (``complete`` False):
the operator cannot read what they would approve.

Every card also links to the request on the Web dashboard (:func:`web_link`,
the Swarm page's approval panel), where the whole call is shown with the same
hash and the request can be answered too.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

__all__ = ["CardArguments", "arguments_digest", "render_arguments", "web_link"]

#: The whole argument block. With the card's header (worker, a 300-character
#: task line), its request id and its footer it stays inside the smallest
#: platform message, Discord's 2,000 characters.
_LIMIT = 1350
#: A value is never cut shorter than this; when the names need more room
#: than that leaves, the card is refused.
_MIN_VALUE = 40


@dataclass(frozen=True)
class CardArguments:
    text: str
    digest: str
    complete: bool


def _value(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(raw)


def _lines(pairs: list[tuple[str, str]], budget: int) -> list[str]:
    out = []
    for name, value in pairs:
        if len(value) > budget:
            value = f"{value[:budget]}… [{len(value) - budget} chars hidden]"
        out.append(f"{name}: {value}")
    return out


def arguments_digest(tool_args: str | None) -> str:
    """The hash a card shows of the call's arguments; the Web panel shows the
    same one beside the whole call, so the two can be matched."""
    return hashlib.sha256((tool_args or "").encode("utf-8")).hexdigest()[:16]


def web_link(approval_id: str) -> str:
    """Where the Web dashboard shows this request: the install's public
    address (``KAZMA_PUBLIC_URL``) when it has an http(s) one, else the
    page's path to open in Kazma's web UI."""
    path = f"/swarm?approval={quote(approval_id, safe='')}"
    public = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip().rstrip("/")
    parts = urlsplit(public)
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{public}{path}"
    return path


def render_arguments(tool_args: str | None) -> CardArguments:
    """The card's argument block for *tool_args* (the call's JSON)."""
    raw = tool_args or ""
    digest = arguments_digest(raw)
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {"arguments": raw}
    if not isinstance(parsed, dict):
        parsed = {"arguments": parsed}
    pairs = [(str(k), _value(v)) for k, v in parsed.items()]
    footer = f"arguments sha256: {digest}"
    if not pairs:
        return CardArguments(text=f"(no arguments)\n{footer}", digest=digest, complete=True)

    # The largest value budget whose whole block fits, never below the minimum.
    budget = max(len(v) for _n, v in pairs)
    while True:
        text = "\n".join([*_lines(pairs, budget), footer])
        if len(text) <= _LIMIT:
            return CardArguments(text=text, digest=digest, complete=True)
        if budget <= _MIN_VALUE:
            return CardArguments(
                text=f"{len(pairs)} arguments: too many to show on a chat card",
                digest=digest, complete=False)
        budget = max(_MIN_VALUE, budget * 3 // 4)
