"""The shared error redactor strips secrets and paths -- and nothing else.

``kazma_core.errors`` redacts every message that reaches a client or an ops
alert. On 2026-09-25 the Postgres-dump alert started carrying its reason,
which made two gaps matter: a password inside a connection string
(``postgresql://user:secret@host``) passed straight through, and the
Windows-path rule matched the ``s://`` of every URL, so ``https://docs`` came
out as ``http<redacted>`` -- a redactor that eats the useful half of a message.
"""

from __future__ import annotations

import re

import pytest
from kazma_core.errors import _REDACT, validation_error


@pytest.mark.parametrize("raw,gone,kept", [
    ("connect postgresql://kazma:s3cr3t@localhost:5433/kazma failed", "s3cr3t", "localhost:5433"),
    (r"cannot open C:\Users\balfa\kazma\kazma-data\x.db", r"C:\Users", "cannot open"),
    ("cannot open C:/Users/balfa/x.db", "C:/Users", "cannot open"),
    ("password=hunter2 in the payload", "hunter2", "payload"),
    ("token: ghp_abcdefghijkl1234", "ghp_abcdefghijkl1234", ""),
])
def test_secrets_and_paths_are_redacted(raw, gone, kept):
    out = validation_error(RuntimeError(raw))
    assert gone not in out
    assert kept in out


@pytest.mark.parametrize("url", [
    "https://docs.example.com/guide",
    "http://127.0.0.1:9090/health/ready",
    "see https://github.com/Mubder/kazma/actions for the run",
])
def test_a_url_without_credentials_survives(url):
    assert validation_error(RuntimeError(url)) == url


def test_negative_control_the_old_path_rule_ate_urls():
    """The previous Windows-path alternative, on its own, mangles a URL."""
    old = re.compile(r"""[A-Za-z]:[\\/][^\s'"]*""")
    assert old.sub("<redacted>", "see https://docs.example.com") == "see http<redacted>"
    assert _REDACT.sub("<redacted>", "see https://docs.example.com") == "see https://docs.example.com"
