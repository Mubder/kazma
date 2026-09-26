"""The GitHub App token mint survives a skewed system clock.

GitHub judges an App JWT by its own clock: ``iat`` must be in the past and
``exp`` no more than 600 s ahead. The mint used ``exp = now + 600`` -- the
value GitHub's docs show -- so a machine clock even one second fast was
refused. On the live install that was 386 refusals between 2026-09-24 22:32
and 2026-09-25 01:55 ("'Expiration time' claim ('exp') is too far in the
future"), each one re-signed and re-posted on the next call.

The fake GitHub below enforces the documented rules against a server clock
the test controls, so every test here is a statement about what GitHub
accepts, not about the claim arithmetic.
"""

from __future__ import annotations

import json
import time
from email.utils import formatdate
from typing import Any
from unittest.mock import patch

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from kazma_core import git_identity  # noqa: E402

_EXP_TOO_FAR = "'Expiration time' claim ('exp') is too far in the future"
_IAT_FUTURE = (
    "'Issued at' claim ('iat') must be an Integer representing the time that "
    "the assertion was issued"
)
_EXP_PAST = (
    "'Expiration time' claim ('exp') must be a numeric value representing the "
    "future time at which the assertion expires"
)
_BAD_KEY = "A JSON web token could not be decoded"


class _Resp:
    def __init__(self, status: int, message: str, date: str | None) -> None:
        self.status_code = status
        body: dict[str, Any] = (
            {"token": "ghs_minted"} if status == 201 else {"message": message, "status": str(status)}
        )
        self.text = json.dumps(body)
        self._body = body
        self.headers = {"Date": date} if date is not None else {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeGitHub:
    """``POST /app/installations/{id}/access_tokens``, judged on GitHub's clock."""

    def __init__(self, public_key: Any, server_now: float, *, send_date: bool = True) -> None:
        self.public_key = public_key
        self.server_now = server_now
        self.send_date = send_date
        self.refuse_key = False
        self.posts = 0

    def post(self, url: str, headers: dict[str, str]) -> _Resp:
        self.posts += 1
        assert url.endswith("/access_tokens")
        date = formatdate(self.server_now, usegmt=True) if self.send_date else None
        if self.refuse_key:
            return _Resp(401, _BAD_KEY, date)
        token = headers["Authorization"].split(" ", 1)[1]
        claims = jwt.decode(
            token,
            self.public_key,
            algorithms=["RS256"],
            options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
        )
        if claims["exp"] > self.server_now + 600:
            return _Resp(401, _EXP_TOO_FAR, date)
        if claims["iat"] > self.server_now:
            return _Resp(401, _IAT_FUTURE, date)
        if claims["exp"] <= self.server_now:
            return _Resp(401, _EXP_PAST, date)
        return _Resp(201, "", date)

    # httpx.Client is used as a context manager.
    def __call__(self, *args: Any, **kwargs: Any) -> "_FakeGitHub":
        return self

    def __enter__(self) -> "_FakeGitHub":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture(scope="module")
def app_key() -> tuple[bytes, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return pem, key.public_key()


@pytest.fixture(autouse=True)
def _fresh_mint_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_identity, "_app_token_cache", {"token": None, "expires": 0})
    monkeypatch.setattr(git_identity, "_clock_offset", {"seconds": 0.0})
    monkeypatch.setattr(git_identity, "_mint_backoff", {"until": 0.0, "delay": 0.0})


def _github(
    monkeypatch: pytest.MonkeyPatch,
    app_key: tuple[bytes, Any],
    *,
    local_clock_ahead_s: float,
    send_date: bool = True,
) -> _FakeGitHub:
    """A GitHub on the real clock and a machine clock off by *local_clock_ahead_s*."""
    pem, public_key = app_key
    server_now = time.time()
    fake = _FakeGitHub(public_key, server_now, send_date=send_date)
    monkeypatch.setattr(git_identity, "_now", lambda: server_now + local_clock_ahead_s)
    monkeypatch.setattr(
        git_identity,
        "_read_config",
        lambda: {
            "app_id": "4310451",
            "app_installation_id": "149841589",
            "app_private_key": pem.decode("utf-8"),
        },
    )
    return fake


@pytest.mark.parametrize("ahead_s", [-500, -30, 0, 1, 5, 30, 59])
def test_ordinary_clock_drift_mints_first_time(monkeypatch, app_key, ahead_s):
    """Within a minute fast (or minutes slow) the first request is accepted."""
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=ahead_s)
    with patch("httpx.Client", fake):
        assert git_identity.mint_app_installation_token(force=True) == "ghs_minted"
    assert fake.posts == 1


def test_the_old_claims_fail_on_a_clock_one_second_fast(app_key):
    """Negative control: the fake refuses what the live install sent.

    ``exp = now + 600`` from a clock one second fast is the live refusal; if
    this fake accepted it, the drift test above would prove nothing.
    """
    pem, public_key = app_key
    server_now = time.time()
    local = int(server_now + 1)
    old = jwt.encode(
        {"iat": local - 60, "exp": local + 600, "iss": "4310451"}, pem, algorithm="RS256"
    )
    resp = _FakeGitHub(public_key, server_now).post(
        "https://api.github.com/app/installations/1/access_tokens",
        headers={"Authorization": f"Bearer {old}"},
    )
    assert resp.status_code == 401
    assert _EXP_TOO_FAR in resp.text


@pytest.mark.parametrize("ahead_s", [600, 3 * 3600, -900, -2 * 86400])
def test_a_clock_far_off_is_corrected_from_githubs_date(monkeypatch, app_key, ahead_s):
    """Beyond the margins, the refusal's Date header supplies GitHub's time.

    One refused request, one accepted retry, and the offset is kept: the
    next mint goes out right the first time.
    """
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=ahead_s)
    with patch("httpx.Client", fake):
        assert git_identity.mint_app_installation_token(force=True) == "ghs_minted"
        assert fake.posts == 2
        assert git_identity._clock_offset["seconds"] == pytest.approx(-ahead_s, abs=2)

        assert git_identity.mint_app_installation_token(force=True) == "ghs_minted"
        assert fake.posts == 3


def test_without_the_correction_a_far_clock_is_refused(monkeypatch, app_key):
    """Negative control: the Date-header correction is what rescues the mint."""
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=600)
    monkeypatch.setattr(git_identity, "_learn_clock_offset", lambda _date: False)
    with patch("httpx.Client", fake):
        assert git_identity.mint_app_installation_token(force=True) is None
    assert fake.posts == 1


def test_a_clock_refusal_without_a_date_is_not_retried(monkeypatch, app_key):
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=600, send_date=False)
    with patch("httpx.Client", fake):
        assert git_identity.mint_app_installation_token(force=True) is None
    assert fake.posts == 1
    assert git_identity._clock_offset["seconds"] == 0.0


def test_a_key_refusal_is_not_mistaken_for_a_clock(monkeypatch, app_key):
    """Only the time-claim refusals trigger the retry; a bad key does not."""
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=0)
    fake.refuse_key = True
    with patch("httpx.Client", fake):
        assert git_identity.mint_app_installation_token(force=True) is None
    assert fake.posts == 1


def test_a_refusal_backs_off_instead_of_reposting_every_call(monkeypatch, app_key):
    """The live refusals were re-sent about every 33 s for three and a half hours.

    After a refusal the unforced path returns None (callers fall back to
    their next credential) until the backoff passes; a forced mint -- the
    caller knows its token is dead -- still goes out, and success clears it.
    """
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=0)
    fake.refuse_key = True
    with patch("httpx.Client", fake):
        assert git_identity.get_app_installation_token() is None
        assert git_identity.get_app_installation_token() is None
        assert fake.posts == 1, "a second unforced call inside the backoff must not re-post"

        first_delay = git_identity._mint_backoff["delay"]
        assert git_identity.mint_app_installation_token(force=True) is None
        assert fake.posts == 2
        assert git_identity._mint_backoff["delay"] == min(
            first_delay * 2, git_identity._MINT_BACKOFF_MAX_S
        ), "the backoff doubles while GitHub keeps refusing"

        fake.refuse_key = False
        assert git_identity.mint_app_installation_token(force=True) == "ghs_minted"
        assert git_identity._mint_backoff == {"until": 0.0, "delay": 0.0}
        assert git_identity.get_app_installation_token() == "ghs_minted"
        assert fake.posts == 3, "the cached token answers without a request"


def test_the_backoff_expires(monkeypatch, app_key):
    fake = _github(monkeypatch, app_key, local_clock_ahead_s=0)
    fake.refuse_key = True
    with patch("httpx.Client", fake):
        assert git_identity.get_app_installation_token() is None
        git_identity._mint_backoff["until"] = time.monotonic() - 1
        fake.refuse_key = False
        assert git_identity.get_app_installation_token() == "ghs_minted"
    assert fake.posts == 2
