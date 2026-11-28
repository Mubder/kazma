"""A password inside a URL is a credential, whatever the key holding it is called.

``postgresql://kazma:<password>@host/db`` carries its secret in the VALUE, so
rules that decide by key name (``*.database_url``, ``*_password``) cannot see
it. ``memory.backends.state.url`` held the live Postgres DSN in plaintext, and
``GET /api/settings/memory/backends`` returned it to the browser unmasked
(2026-09-25). These helpers find and mask it by value and leave the rest of
the URL readable: the operator still needs to see which host and database a
setting points at.

The one home for this rule: ConfigStore (vault routing, the write veto, the
``Setting updated`` log line), the memory-backend Settings API and
``settings.mask_deep`` all ask here.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "URL_PASSWORD_MASK",
    "mask_url_credentials",
    "mask_url_credentials_deep",
    "mask_urls_in_text",
    "restore_masked_url",
    "same_address",
    "url_has_credentials",
    "url_password_is_masked",
]

#: What replaces a password. Four stars on purpose: ConfigStore refuses to
#: write any value containing ``****`` (``is_masked_secret_placeholder``), so
#: a form that posts a masked URL back can never overwrite the stored one.
URL_PASSWORD_MASK = "****"

#: ``scheme://`` + authority + the rest. The authority ends at the first
#: ``/``, ``?`` or ``#``; its userinfo ends at its LAST ``@`` (libpq's reading
#: of an unencoded ``@`` in a password).
_URL = re.compile(
    r"\A(?P<lead>\s*)(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)"
    r"(?P<authority>[^/?#]*)(?P<rest>.*)\Z",
    re.DOTALL,
)

#: A password passed as a query parameter (libpq accepts ``?password=``).
#: A secret carried in a query parameter: a password, and a webhook's verify
#: token or an access token in an address (WhatsApp's verification call put
#: its ``hub.verify_token`` into the request log, 2026-11-16). Both spellings
#: Meta sends (``hub.verify_token`` and ``hub_verify_token``) end in a name
#: this matches.
_QUERY_PASSWORD = re.compile(
    r"(?P<key>[?&](?:[A-Za-z0-9]{1,40}[._-])?"
    r"(?:password|passwd|pwd|verify_token|access_token|token|secret|client_secret|api_key|apikey|key|code|state)=)"
    r"(?P<value>[^&#\s'\"]*)",
    re.IGNORECASE,
)

#: URL userinfo anywhere inside free text: a ``repr``, a JSON blob, a log line.
_EMBEDDED_USERINFO = re.compile(
    r"(?P<head>\b[A-Za-z][A-Za-z0-9+.\-]*://[^/?#\s'\"@:]*:)"
    r"(?P<password>[^/?#\s'\"]+)(?P<at>@)"
)


def _password_span(authority: str) -> tuple[int, int] | None:
    """Where the password sits in ``user:password@host``; None if there is none."""
    at = authority.rfind("@")
    if at < 0:
        return None
    colon = authority.find(":", 0, at)
    if colon < 0 or colon + 1 >= at:
        return None  # a user and no password, or an empty one
    return colon + 1, at


def _match(value: Any) -> re.Match[str] | None:
    if not isinstance(value, str) or "://" not in value:
        return None
    return _URL.match(value)


def url_has_credentials(value: Any) -> bool:
    """True when *value* is a URL carrying a non-empty password."""
    m = _match(value)
    if m is None:
        return False
    if _password_span(m["authority"]) is not None:
        return True
    return any(q["value"] for q in _QUERY_PASSWORD.finditer(m["rest"]))


def mask_url_credentials(value: Any) -> Any:
    """*value* with any URL password replaced by ``****``; anything else as it is.

    Not a URL, or no password: the same object back. Non-strings pass through
    untouched — walking containers is the caller's job (``mask_deep``).
    """
    m = _match(value)
    if m is None or not url_has_credentials(value):
        return value
    authority = m["authority"]
    span = _password_span(authority)
    if span is not None:
        authority = authority[: span[0]] + URL_PASSWORD_MASK + authority[span[1] :]
    rest = _QUERY_PASSWORD.sub(
        lambda q: q["key"] + (URL_PASSWORD_MASK if q["value"] else ""), m["rest"]
    )
    return m["lead"] + m["scheme"] + authority + rest


def mask_urls_in_text(text: str) -> str:
    """Mask every URL password inside free text (a ``repr``, a log line),
    credential phrases in either language (``errors.hide_prose_secrets``),
    and every token-shaped value (``security.token_shapes``: a ``Bearer``
    credential in a command, an ``xapp-`` token in a log line). Every masker
    built on this one -- the log writer, :func:`mask_url_credentials_deep`,
    the approval cards -- reads the one table of token shapes.

    ASCII ``name: value`` and ``name=value`` for a credential label are the
    same rule as ``errors.hide_prose_secrets`` (one copy, both languages),
    so a log line hides ``password: secret`` and ``secret=value``. A
    fullwidth colon, ``password is <secret>``, and the Arabic label phrases
    are there too. "token: expired" is left as written;
    ``errors.redact_secrets`` is what masks that form.
    """
    from kazma_core.errors import hide_prose_secrets
    from kazma_core.security.token_shapes import mask_tokens

    if "://" in text:
        text = _EMBEDDED_USERINFO.sub(
            lambda m: m["head"] + URL_PASSWORD_MASK + m["at"], text
        )
    if "=" in text:
        # A path with its query, as a request log line writes it, has no "://".
        text = _QUERY_PASSWORD.sub(
            lambda q: q["key"] + (URL_PASSWORD_MASK if q["value"] else ""), text
        )
    return mask_tokens(hide_prose_secrets(text, URL_PASSWORD_MASK), URL_PASSWORD_MASK)


def mask_url_credentials_deep(node: Any, _depth: int = 0) -> Any:
    """*node* with every URL password masked, walking dicts, lists and tuples.

    The value half of a masker that decides by key name: a key rule masks
    ``api_key``; this catches the password in ``state.url`` or in a
    ``git clone https://user:token@…`` command. Strings are masked as text,
    so a URL inside a JSON blob or a command line is caught too. Keys and
    non-string leaves are returned unchanged.
    """
    if _depth > 12:
        # Config and tool arguments are shallow; deeper is a cycle or a
        # pathological payload, replaced whole rather than returned unread.
        return URL_PASSWORD_MASK
    if isinstance(node, str):
        return mask_urls_in_text(node)
    if isinstance(node, dict):
        return {k: mask_url_credentials_deep(v, _depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [mask_url_credentials_deep(v, _depth + 1) for v in node]
    if isinstance(node, tuple):
        return tuple(mask_url_credentials_deep(v, _depth + 1) for v in node)
    return node


def _is_mask(text: str) -> bool:
    return len(text) >= 3 and set(text) == {"*"}


def url_password_is_masked(value: Any) -> bool:
    """True when *value* is a URL whose every password is a run of stars.

    What a form posts back after showing a masked URL: writing it would
    replace the stored password with asterisks.
    """
    m = _match(value)
    if m is None:
        return False
    found = False
    span = _password_span(m["authority"])
    if span is not None:
        if not _is_mask(m["authority"][span[0] : span[1]]):
            return False
        found = True
    for q in _QUERY_PASSWORD.finditer(m["rest"]):
        if q["value"]:
            if not _is_mask(q["value"]):
                return False
            found = True
    return found


def _address_key(value: Any) -> tuple[str, str, int | None, str] | None:
    from urllib.parse import urlsplit

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ("", text.rstrip("/").lower(), None, "")
    scheme = parts.scheme.lower()
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return (scheme, (parts.hostname or "").lower(), port, parts.path.rstrip("/"))


def same_address(posted: Any, stored: Any) -> bool:
    """Whether *posted* names the server *stored* names: the same scheme,
    host, port and path (case of the scheme and host, a default port and a
    trailing slash aside; userinfo and query ignored). Two empty addresses
    are the same; an empty and a set one are not.

    The rule a masked secret follows (2026-11-15): a form that posts a key
    back masked keeps the stored key only for the address it was saved with.
    Posted with another address, the stored key would be sent to a server
    someone just typed in -- the leak the mask exists to prevent, the same
    rule :func:`restore_masked_url` applies to a URL's own password.
    """
    return _address_key(posted) == _address_key(stored)


def restore_masked_url(posted: Any, stored: Any) -> Any:
    """The stored URL when *posted* is exactly its masked form, else *posted*.

    A form that showed ``https://u:****@host/v1`` and posts it back unchanged
    means "keep the password", and gets the stored URL back. Posted with any
    other change -- a new host, user, path -- the stored password is NOT
    carried over: moving a credential to a server someone just typed in is
    the leak the mask exists to prevent. The caller refuses a URL that still
    holds a masked password (``url_password_is_masked``).
    """
    if not url_password_is_masked(posted) or not url_has_credentials(stored):
        return posted
    return stored if mask_url_credentials(stored) == posted else posted

