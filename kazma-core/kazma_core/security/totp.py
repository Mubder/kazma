"""Time-based one-time codes (RFC 6238), the second factor of a sign-in.

Standard library only. A secret is 20 random bytes, shown base32 (what an
authenticator app takes, by hand or from the ``otpauth://`` link); a code is
six digits from HMAC-SHA1 over the 30-second step (RFC 4226's dynamic
truncation). A code is taken from the step before, now and after (a phone's
clock a little off), and never twice: :func:`verify` answers the step the
code matched, and the caller keeps the last step used per account
(Spaces S4-02).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

__all__ = ["DIGITS", "PERIOD", "WINDOW", "new_secret", "provisioning_uri", "verify"]

DIGITS = 6
PERIOD = 30
#: Steps on either side of now a code may come from.
WINDOW = 1


def new_secret() -> str:
    """A fresh secret: 20 random bytes, base32 without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    clean = "".join(str(secret).split()).upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def _code_at(secret: str, step: int) -> str:
    """The code of *step* (RFC 4226 HOTP over the step counter)."""
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, *, now: float | None = None, after_step: int = -1) -> int | None:
    """The step *code* belongs to, within :data:`WINDOW` steps of now and
    later than *after_step* (a code is used once); else None. Compared in
    constant time."""
    typed = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(typed) != DIGITS:
        return None
    current = int((time.time() if now is None else now) // PERIOD)
    found = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step <= after_step:
            continue
        if hmac.compare_digest(_code_at(secret, step), typed) and found is None:
            found = step
    return found


def provisioning_uri(secret: str, *, account: str, issuer: str = "Kazma") -> str:
    """The ``otpauth://totp/`` link an authenticator app reads."""
    label = quote(f"{issuer}:{account}", safe=":@")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                       "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{query}"
