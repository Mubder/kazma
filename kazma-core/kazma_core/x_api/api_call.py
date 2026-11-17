"""One X API call, checked against X's own spec before anything is sent.

``x_api.operations`` says what each operation is; this module turns a
request for one -- its path values, query and body -- into the exact request
X expects, or refuses it with what is wrong, before any network call:

* every ``{name}`` of the path given, matching the pattern X's spec gives it
  (an id is digits, a username its 15 characters), then percent-encoded;
* only the query parameters the operation declares, each checked against its
  type, allowed values, range and pattern; a list is joined with commas, as
  X takes ``user.fields=a,b``;
* a JSON body only where the operation takes one, only its declared
  top-level fields, every required one present, at most 64 KiB;
* the authentication Kazma has for it (``x_api.auth_tokens``), in
  ``operations.AUTH_ORDER``.

Then ``XClient`` sends it (``request_operation``): the X read budget for every
GET, the audit row for every call, no retry of a write.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from kazma_core.x_api.operations import Operation

__all__ = ["CallRefused", "PreparedCall", "prepare"]

_MAX_BODY_BYTES = 64 * 1024
_MAX_VALUE_CHARS = 4096
_DEFAULT_PATH_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"
_PATH_BREAKERS = re.compile(r"[/?#%\\\s]|^\.{1,2}$")


class CallRefused(ValueError):
    """The request does not match the operation; nothing was sent."""


def _fits(pattern: str, text: str) -> bool:
    """Whether *text* is in the form X's spec gives. A few of X's patterns
    use Unicode classes Python's ``re`` cannot read (a letter of any script:
    ``p{L}`` after a backslash, in a list's
    name); for those the value only has to be printable text."""
    try:
        return re.fullmatch(pattern.strip("^$"), text) is not None
    except re.error:
        return text.isprintable()


@dataclass(frozen=True)
class PreparedCall:
    operation: Operation
    path: str          # the path with its values, and the query string
    body: dict[str, Any] | None


def _text(value: Any, name: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_CHARS:
            raise CallRefused(f"{name} is longer than {_MAX_VALUE_CHARS} characters.")
        return value
    raise CallRefused(f"{name} must be text, a number or true/false.")


def _check_scalar(spec: dict[str, Any], text: str, name: str) -> None:
    kind = spec.get("type")
    if kind == "integer":
        if not re.fullmatch(r"-?[0-9]{1,19}", text):
            raise CallRefused(f"{name} must be a whole number.")
        number = int(text)
        if "minimum" in spec and number < spec["minimum"]:
            raise CallRefused(f"{name} is below its minimum ({spec['minimum']}).")
        if "maximum" in spec and number > spec["maximum"]:
            raise CallRefused(f"{name} is above its maximum ({spec['maximum']}).")
    if kind == "boolean" and text not in ("true", "false"):
        raise CallRefused(f"{name} must be true or false.")
    if spec.get("enum") and text not in [str(v) for v in spec["enum"]]:
        raise CallRefused(f"{name} must be one of: {', '.join(map(str, spec['enum']))}.")
    if spec.get("pattern") and not _fits(spec["pattern"], text):
        raise CallRefused(f"{name} is not in the form X accepts.")
    if "minLength" in spec and len(text) < spec["minLength"]:
        raise CallRefused(f"{name} is shorter than {spec['minLength']} characters.")
    if "maxLength" in spec and len(text) > spec["maxLength"]:
        raise CallRefused(f"{name} is longer than {spec['maxLength']} characters.")


def _query_value(spec: dict[str, Any], value: Any, name: str) -> str:
    if spec.get("type") == "array":
        items = value if isinstance(value, (list, tuple)) else str(_text(value, name)).split(",")
        items = [_text(v, name).strip() for v in items if str(v).strip()]
        if spec.get("max_items") and len(items) > spec["max_items"]:
            raise CallRefused(f"{name} takes at most {spec['max_items']} values.")
        allowed = spec.get("items_enum")
        for item in items:
            if allowed and item not in allowed:
                raise CallRefused(f"{name}: {item!r} is not one X accepts.")
            if spec.get("items_pattern") and not _fits(spec["items_pattern"], item):
                raise CallRefused(f"{name}: {item!r} is not in the form X accepts.")
        return ",".join(items)
    text = _text(value, name)
    _check_scalar(spec, text, name)
    return text


def prepare(op: Operation, path: dict[str, Any] | None = None, query: dict[str, Any] | None = None,
            body: dict[str, Any] | None = None) -> PreparedCall:
    """The request *op* means with these values, or :class:`CallRefused`."""
    path = dict(path or {})
    query = dict(query or {})
    url = op.path
    for name in op.path_names:
        if name not in path or str(path[name]).strip() == "":
            raise CallRefused(f"The path needs {name}.")
        value = _text(path.pop(name), name).strip()
        spec = op.param(name, "path") or {}
        # A path value never carries a separator, whatever X's pattern allows
        # (one is printable text when Python cannot read X's pattern).
        if not _fits(spec.get("pattern") or _DEFAULT_PATH_PATTERN, value) or _PATH_BREAKERS.search(value):
            raise CallRefused(f"{name} is not in the form X accepts.")
        url = url.replace("{" + name + "}", quote(value, safe=""))
    if path:
        raise CallRefused(f"{op.id} has no path value named {', '.join(sorted(path))}.")
    pairs = []
    for name, value in query.items():
        spec = op.param(str(name), "query")
        if spec is None:
            known = ", ".join(p["name"] for p in op.params if p.get("in") == "query") or "none"
            raise CallRefused(f"{op.id} takes no query value {name!r} (it takes: {known}).")
        if value is None or value == "" or value == []:
            continue
        pairs.append((str(name), _query_value(spec, value, str(name))))
    given = {n for n, _ in pairs}
    for spec in op.params:
        if spec.get("in") == "query" and spec.get("required") and spec["name"] not in given:
            raise CallRefused(f"{op.id} needs the query value {spec['name']}.")
    if pairs:
        url = url + "?" + urlencode(pairs, quote_via=quote, safe=",")
    sent: dict[str, Any] | None = None
    if body:
        if not op.body or op.body.get("type") != "json":
            raise CallRefused(f"{op.id} takes no JSON body.")
        if not isinstance(body, dict):
            raise CallRefused("The body must be a JSON object.")
        fields = set(op.body.get("fields") or ())
        if fields:
            unknown = sorted(set(body) - fields)
            if unknown:
                raise CallRefused(f"{op.id}'s body has no field {', '.join(unknown)} (it takes: {', '.join(sorted(fields))}).")
        if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > _MAX_BODY_BYTES:
            raise CallRefused("The body is larger than 64 KiB.")
        sent = body
    if op.body and op.body.get("type") == "json":
        missing = [f for f in op.body.get("required") or () if f not in (sent or {})]
        if missing:
            raise CallRefused(f"{op.id}'s body needs {', '.join(missing)}.")
    return PreparedCall(operation=op, path=url, body=sent)
