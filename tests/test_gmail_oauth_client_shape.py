"""Reject an obviously-wrong Gmail OAuth client pair at entry, not at Google.

A Google client ID ends in ``.apps.googleusercontent.com``; a client secret
starts with ``GOCSPX-``. They sit next to each other in the Cloud Console and
look alike, so pasting the secret into both fields is easy — and Google's
failure for it is ``Error 401: invalid_client — The OAuth client was not
found``, raised on Google's own page, long after saving, naming no field.

Seen live 2026-08-29: both stored values were byte-identical and started
``GOCSPX-``.
"""

from __future__ import annotations

import pytest

from kazma_skills.native.email_manager.oauth_gmail import _client_shape_error
from kazma_ui.email_api import _gmail_client_format_error

GOOD_ID = "123456789-abcdefghij.apps.googleusercontent.com"
GOOD_SECRET = "GOCSPX-AbCdEf1234567890"

BAD_PAIRS = [
    ("secret pasted into both fields", "GOCSPX-PI8wER6Lx", "GOCSPX-PI8wER6Lx"),
    ("secret pasted as the client id", "GOCSPX-PI8wER6Lx", GOOD_SECRET),
    ("truncated / non-Google client id", "123456789", GOOD_SECRET),
    ("fields swapped", GOOD_SECRET, GOOD_ID),
]


@pytest.mark.parametrize("label,cid,secret", BAD_PAIRS, ids=[p[0] for p in BAD_PAIRS])
def test_save_rejects_malformed_pair(label, cid, secret):
    """The save endpoint's guard must reject it before it reaches the vault."""
    msg = _gmail_client_format_error(cid, secret)
    assert msg, f"{label} was accepted"
    # The message must name the field, not just say "invalid".
    assert "Client ID" in msg or "Client SECRET" in msg


@pytest.mark.parametrize("label,cid,secret", BAD_PAIRS, ids=[p[0] for p in BAD_PAIRS])
def test_connect_rejects_malformed_pair(label, cid, secret):
    """Credentials already in the vault predate the save guard, so the
    connect path must catch them too — otherwise the only feedback is
    Google's opaque invalid_client page."""
    assert _client_shape_error(cid, secret), f"{label} reached Google"


def test_correct_pair_passes_both_guards():
    """A real pair must not be blocked by either check."""
    assert _gmail_client_format_error(GOOD_ID, GOOD_SECRET) == ""
    assert _client_shape_error(GOOD_ID, GOOD_SECRET) == ""


def test_guards_are_format_only_not_existence():
    """A well-formed client that was deleted in the Console still passes.

    Documents the limit deliberately: these guards catch a typo, not a stale
    or revoked client — that failure still surfaces from Google.
    """
    deleted_but_well_formed = "999999-deleted.apps.googleusercontent.com"
    assert _client_shape_error(deleted_but_well_formed, GOOD_SECRET) == ""
