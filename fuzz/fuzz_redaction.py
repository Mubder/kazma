"""Fuzz the secret maskers: a credential in any text never comes back.

``errors.redact_secrets`` (error and log text) and
``security.url_credentials.mask_urls_in_text`` (the log writer, approval
cards) read the one table of token shapes (``security.token_shapes``). A
token of each shape and a URL password are planted in fuzzed text on a line
of their own; neither may survive either masker, and the maskers never raise.

Run under Atheris: ``python fuzz/fuzz_redaction.py -max_total_time=60``.
"""

from __future__ import annotations

import sys

#: Placeholders shaped like each kind of credential (never real ones).
PLANTED = (
    "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "xoxb-" + "1234567890-abcdefghijKLMNOP",
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "glpat-" + "abcdefghij1234567890",
    "hf_" + "abcdefghijklmnopqrstuvwxyz12",
    "sk-" + "proj-abcdefghij1234567890",
    "AIza" + "SyA-abcdefghijklmnopqrstuvwxyz12345",
    "kazma_" + "0123456789abcdef0123456789abcdef",
)
TOKENS = (
    "Bearer ", "token=", "password=", "\"api_key\": \"", "https://", "user:", "@",
    "\n", " ", "=", ":", "/", "?", "&", "'", "\"", "xoxb-", "ghp_", "AKIA", "eyJ",
    "a", "Z", "9", "_", "-", ".", "ب",
)
SEEDS = (b"", b"error: connection refused", b"Authorization: Bearer ", b"postgres://kazma@db/x")


def check(data: bytes) -> None:
    from kazma_core.errors import redact_secrets
    from kazma_core.security.url_credentials import mask_urls_in_text

    import hashlib

    text = data.decode("utf-8", errors="replace")
    secret = PLANTED[data[0] % len(PLANTED)] if data else PLANTED[0]
    password = "Pw" + hashlib.sha256(data).hexdigest()[:14]
    half = len(text) // 2
    planted = (
        f"{text[:half]}\n{secret}\n"
        f"https://kazma:{password}@db.example/x\n{text[half:]}"
    )
    for masked in (redact_secrets(planted), mask_urls_in_text(planted)):
        # The planted copies are masked: no more copies come back than the
        # fuzzed text itself held (it may hold the same characters; the first
        # Atheris run's "333..." text held its own "password").
        assert masked.count(secret) <= text.count(secret), (secret, masked)
        assert masked.count(password) <= text.count(password), (password, masked)


def main() -> None:
    import atheris

    with atheris.instrument_imports():
        import kazma_core.errors  # noqa: F401
        import kazma_core.security.token_shapes  # noqa: F401
        import kazma_core.security.url_credentials  # noqa: F401
    atheris.Setup(sys.argv, check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
