"""Every X API v2 operation, from X's own OpenAPI spec (2026-11-17).

``x_api_operations.json`` beside this module is X's spec
(``https://api.x.com/2/openapi.json``) reduced to what a call needs: each
operation's id, method, path, the authentication X accepts for it, its
parameters with their types, patterns and allowed values, and its body's
fields. ``scripts/x_api_catalog.py --write`` rebuilds it from the live spec;
without ``--write`` it says what X added or removed since.

This module is the one answer to "may Kazma call this, and how": for every
operation it gives

* ``kind`` -- ``read`` (a GET), ``write`` (anything else), ``stream`` (a
  long-lived GET: read for a bounded time only) or ``refused`` (never through
  the generic tools, with the reason and the tool that does it instead:
  posting and deleting a post go through ``x_post`` / ``x_delete_post`` and
  their drafts, caps and approval; an operation answering with a credential
  is not called at all);
* the authentication Kazma would use -- the OAuth 1.0a user keys of
  Settings -> X when X accepts them, else the OAuth 2.0 user sign-in, else
  the app's Bearer token -- and whether the install has it.

Live 2026-11-17: asked whether the account had new followers, Kazma had no X
read for it and opened x.com in the browser, which X answers with a sign-in
page. With this catalog every operation X offers is either callable or says
why not.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "AUTH_ORDER",
    "Operation",
    "all_operations",
    "areas",
    "authorize_url",
    "find",
    "oauth2_scopes",
    "operation",
    "spec_version",
    "TOKEN_URL",
]

CATALOG = Path(__file__).with_name("x_api_operations.json")

#: Kazma's choice among the authentications X accepts for an operation: the
#: OAuth 1.0a user keys first (what posting already uses), then the OAuth 2.0
#: user sign-in, then the app's Bearer token (app-only reads and admin).
AUTH_ORDER = ("user", "oauth2", "app")

#: Posting and deleting a post have their own tools: drafts, caps, the
#: 30-day duplicate rule and the proposal gate. Never around them.
_OWN_TOOL = {
    ("POST", "/2/tweets"): "x_post (or x_schedule_post) -- drafts, caps and the approval card",
    ("DELETE", "/2/tweets/{id}"): "x_delete_post",
    # A file goes up from the workspace in chunks, never as base64 the model
    # writes into a body.
    ("POST", "/2/media/upload"): "x_upload_media",
    ("POST", "/2/media/upload/initialize"): "x_upload_media",
    ("POST", "/2/media/upload/{id}/append"): "x_upload_media",
    ("POST", "/2/media/upload/{id}/finalize"): "x_upload_media",
}

#: Operations whose answer is a credential (a stream key, a bot's token):
#: never called, so the secret never reaches the model or the log.
_ANSWERS_A_CREDENTIAL = {
    ("GET", "/2/sources/{id}/stream_key"),
    ("POST", "/2/bots"),
    ("POST", "/2/bots/{id}/token"),
}

_STREAM_PATH = re.compile(r"/stream(?:/lang/[a-z]{2})?$")


@dataclass(frozen=True)
class Operation:
    id: str
    tag: str
    method: str
    path: str
    summary: str
    auth: tuple[str, ...]
    params: tuple[dict[str, Any], ...]
    body: dict[str, Any] | None = None
    kind: str = "read"
    refused: str = ""
    path_names: tuple[str, ...] = field(default_factory=tuple)
    scopes: tuple[str, ...] = field(default_factory=tuple)

    def param(self, name: str, where: str) -> dict[str, Any] | None:
        for p in self.params:
            if p.get("name") == name and p.get("in") == where:
                return p
        return None

    def describe(self) -> dict[str, Any]:
        """What a model needs to call it (the catalog tool's answer)."""
        out: dict[str, Any] = {
            "operation": self.id,
            "area": self.tag,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "kind": self.kind,
            "auth": list(self.auth),
        }
        if self.refused:
            out["refused"] = self.refused
        if self.path_names:
            out["path_params"] = list(self.path_names)
        query = [p for p in self.params if p.get("in") == "query"]
        if query:
            out["query_params"] = [
                {k: v for k, v in p.items() if k in ("name", "required", "type", "enum", "items_enum",
                                                         "minimum", "maximum", "about")}
                for p in query
            ]
        if self.body:
            out["body"] = self.body
        return out


def _kind(method: str, path: str) -> tuple[str, str]:
    if (method, path) in _OWN_TOOL:
        return "refused", f"Use {_OWN_TOOL[(method, path)]}."
    if (method, path) in _ANSWERS_A_CREDENTIAL:
        return "refused", "Its answer is a credential; Kazma does not call it."
    if method == "GET" and _STREAM_PATH.search(path):
        return "stream", ""
    if method == "GET":
        return "read", ""
    return "write", ""


def _build(entry: dict[str, Any]) -> Operation:
    method = str(entry["method"]).upper()
    path = str(entry["path"])
    kind, refused = _kind(method, path)
    body = entry.get("body")
    if kind == "write" and isinstance(body, dict) and body.get("type") not in (None, "json"):
        kind, refused = "refused", "Its body is a file, not JSON; Kazma does not send files through it."
    return Operation(
        id=str(entry["id"]),
        tag=str(entry.get("tag") or ""),
        method=method,
        path=path,
        summary=str(entry.get("summary") or ""),
        auth=tuple(a for a in AUTH_ORDER if a in (entry.get("auth") or [])),
        params=tuple(entry.get("params") or ()),
        body=body if isinstance(body, dict) else None,
        kind=kind,
        refused=refused,
        path_names=tuple(re.findall(r"\{([^{}]+)\}", path)),
        scopes=tuple(entry.get("scopes") or ()),
    )


@functools.lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Any], dict[str, Operation]]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    ops = {}
    for entry in data.get("operations") or []:
        op = _build(entry)
        ops[op.id] = op
    return data, ops


def all_operations() -> list[Operation]:
    return list(_catalog()[1].values())


def spec_version() -> str:
    return str(_catalog()[0].get("spec_version") or "")


def operation(name: str) -> Operation | None:
    """The operation named by its id (``getUsersFollowers``), or by
    ``METHOD /path`` (``GET /2/users/{id}/followers``)."""
    name = str(name or "").strip()
    ops = _catalog()[1]
    if name in ops:
        return ops[name]
    m = re.fullmatch(r"(GET|POST|PUT|DELETE|PATCH)\s+(/2/\S+)", name, flags=re.I)
    if m:
        method, path = m.group(1).upper(), m.group(2)
        for op in ops.values():
            if op.method == method and op.path == path:
                return op
    return None


def find(text: str = "", area: str = "", kind: str = "", limit: int = 40) -> list[Operation]:
    """Operations whose id, path or summary holds every word of *text*, in
    *area* (an X API group: Posts, Users, Lists, Direct Messages...) and of
    *kind* when given."""
    words = [w.lower() for w in re.findall(r"[\w.]+", str(text or ""))]
    out = []
    for op in _catalog()[1].values():
        if area and op.tag.lower() != area.strip().lower():
            continue
        if kind and op.kind != kind:
            continue
        hay = f"{op.id} {op.path} {op.summary} {op.tag}".lower()
        if all(w in hay for w in words):
            out.append(op)
    return out[: max(1, min(int(limit or 40), 300))]


#: Where a person signs in with X. X's spec names api.x.com's own address,
#: which sends the browser on to this one; going here saves a redirect.
_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"


def authorize_url() -> str:
    return _AUTHORIZE_URL


#: Where a sign-in's code and a refresh token are exchanged (X's spec names
#: the same address; written here so no catalog value chooses where a code or
#: a refresh token goes).
TOKEN_URL = "https://api.x.com/2/oauth2/token"


def oauth2_scopes() -> dict[str, str]:
    """Every OAuth 2.0 scope X's spec lists, with what it lets Kazma do."""
    return dict((_catalog()[0].get("oauth2") or {}).get("scopes") or {})


def areas() -> dict[str, int]:
    counts: dict[str, int] = {}
    for op in _catalog()[1].values():
        counts[op.tag] = counts.get(op.tag, 0) + 1
    return dict(sorted(counts.items()))
