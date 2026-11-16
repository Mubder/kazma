"""Fuzz the MCP sign-in challenge reader: a server's 401 header never breaks it.

``mcp.oauth.parse_www_authenticate`` reads the ``WWW-Authenticate`` header an
MCP server answers with -- text the server chooses. It answers a mapping of
text to text, whatever the header, and never raises.

Run under Atheris: ``python fuzz/fuzz_oauth_challenge.py -max_total_time=60``.
"""

from __future__ import annotations

import sys

TOKENS = (
    "Bearer ", "Basic ", "realm=", "scope=", "resource_metadata=", "error=", "\"",
    "\\", ",", " ", "=", "a", "https://a.example/x", "\n", "\t", "'", "\x00",
)
SEEDS = (b'Bearer realm="x", scope="a b"', b'Bearer resource_metadata="https://a/b"', b'Bearer "', b"=,=")


def check(data: bytes) -> None:
    from kazma_core.mcp.oauth import parse_www_authenticate

    text = data.decode("utf-8", errors="replace")
    params = parse_www_authenticate(text)
    assert isinstance(params, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in params.items())


def main() -> None:
    import atheris

    with atheris.instrument_imports():
        import kazma_core.mcp.oauth  # noqa: F401
    atheris.Setup(sys.argv, check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
