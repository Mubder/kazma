"""The S3 write probe, which is the check that would have caught 2026-08-29.

The offsite repository spent a day accepting every read and refusing every
write, and each health check that only listed said it was fine. ``s3:`` repos
used to skip the probe entirely -- ``remote_writable`` returned True for
anything that was not ``rclone:`` -- so the migration off Drive would have
carried the same blind spot to the new destination.

These tests stand up a local HTTP server that recomputes the SigV4 signature
the way S3 does. That matters: signing code that is never exercised fails as
a 403 against the real bucket and gets blamed on the bucket policy.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from kazma_core.backup import restic_repo as rr

ACCESS = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
REGION = "auto"


def _make_server(secret: str, *, delete_status: int = 204):
    """An S3 stand-in that verifies signatures instead of trusting them."""
    seen: list[tuple[str, str, bool]] = []

    class Handler(BaseHTTPRequestHandler):
        def _verify(self) -> bool:
            body = b""
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                body = self.rfile.read(length)

            auth = self.headers.get("Authorization", "")
            amzdate = self.headers.get("x-amz-date", "")
            datestamp = amzdate[:8]
            payload_hash = self.headers.get("x-amz-content-sha256", "")
            if payload_hash != hashlib.sha256(body).hexdigest():
                seen.append((self.command, self.path, False))
                return False

            signed = auth.split("SignedHeaders=")[1].split(",")[0]
            canonical_headers = "".join(
                f"{h}:{self.headers.get(h)}\n" for h in signed.split(";")
            )
            canonical_request = "\n".join(
                [self.command, self.path, "", canonical_headers, signed, payload_hash]
            )
            scope = f"{datestamp}/{REGION}/s3/aws4_request"
            to_sign = "\n".join(
                [
                    "AWS4-HMAC-SHA256",
                    amzdate,
                    scope,
                    hashlib.sha256(canonical_request.encode()).hexdigest(),
                ]
            )

            def _sign(key: bytes, msg: str) -> bytes:
                return hmac.new(key, msg.encode(), hashlib.sha256).digest()

            key = _sign(
                _sign(_sign(_sign(f"AWS4{secret}".encode(), datestamp), REGION), "s3"),
                "aws4_request",
            )
            expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
            ok = expected == auth.split("Signature=")[1].strip()
            seen.append((self.command, self.path, ok))
            return ok

        def do_PUT(self) -> None:  # noqa: N802
            self.send_response(200 if self._verify() else 403)
            self.end_headers()

        def do_DELETE(self) -> None:  # noqa: N802
            self.send_response(delete_status if self._verify() else 403)
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1], seen


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", ACCESS)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    rr._write_probe_cache.clear()
    yield
    rr._write_probe_cache.clear()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "s3:https://acct.r2.cloudflarestorage.com/kazma-backup",
            ("https://acct.r2.cloudflarestorage.com", "kazma-backup", ""),
        ),
        (
            "s3:s3.us-west-002.backblazeb2.com/bucket/prefix/deep",
            ("https://s3.us-west-002.backblazeb2.com", "bucket", "prefix/deep"),
        ),
        ("s3:http://localhost:9000/bucket", ("http://localhost:9000", "bucket", "")),
        ("s3:nohost", None),
    ],
)
def test_parses_the_restic_s3_url_forms(url, expected):
    assert rr._parse_s3_repo(url) == expected


def test_signature_is_accepted_and_probe_uses_the_locks_prefix(s3_env):
    srv, port, seen = _make_server(SECRET)
    try:
        ok, detail = rr._s3_writable(f"s3:http://127.0.0.1:{port}/kazma-backup")
    finally:
        srv.shutdown()

    assert ok, detail
    assert [s[0] for s in seen] == ["PUT", "DELETE"]
    assert all(s[2] for s in seen), "server rejected our signature"
    # The probe must live under locks/, the one prefix an append-only key is
    # allowed to delete from.
    assert "/kazma-backup/locks/.kazma-write-probe-" in seen[0][1]
    # And it must clean up after itself, at the same key it wrote.
    assert seen[0][1] == seen[1][1]


def test_a_refused_write_is_reported_as_read_only(s3_env, monkeypatch):
    """The exact failure shape that went undetected for a day."""
    srv, port, _ = _make_server("a-different-secret")
    try:
        ok, detail = rr._s3_writable(f"s3:http://127.0.0.1:{port}/kazma-backup")
    finally:
        srv.shutdown()

    assert not ok
    assert "READ-ONLY" in detail
    assert "NOT" in detail  # says plainly that snapshots are not being written


def test_append_only_key_that_cannot_clear_locks_is_caught(s3_env):
    """Writes fine, cannot delete: restic wedges on stale locks days later."""
    srv, port, _ = _make_server(SECRET, delete_status=403)
    try:
        ok, detail = rr._s3_writable(f"s3:http://127.0.0.1:{port}/kazma-backup")
    finally:
        srv.shutdown()

    assert not ok
    assert "locks/" in detail
    assert "DeleteObject" in detail


def test_missing_credentials_fail_closed(s3_env, monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    ok, detail = rr._s3_writable("s3:https://example.invalid/bucket")
    assert not ok
    assert "AWS_ACCESS_KEY_ID" in detail


def test_unreachable_endpoint_fails_closed(s3_env):
    """No answer is not the same as a yes."""
    # Port 1 is reserved and nothing listens on it.
    ok, detail = rr._s3_writable("s3:http://127.0.0.1:1/bucket")
    assert not ok
    assert detail


def test_s3_repo_is_probed_rather_than_assumed_writable(s3_env, monkeypatch):
    """Regression guard: s3: used to short-circuit to (True, '') unprobed."""
    calls = []

    def fake(repo):
        calls.append(repo)
        return False, "probed"

    monkeypatch.setattr(rr, "_s3_writable", fake)
    ok, detail = rr.remote_writable("s3:https://example.invalid/bucket")
    assert calls, "an s3: repository must be probed, not assumed writable"
    assert not ok and detail == "probed"


def test_local_repository_is_still_trusted(tmp_path, s3_env):
    ok, detail = rr.remote_writable(str(tmp_path / "repo"))
    assert ok and detail == ""


def test_probe_result_is_cached_then_forced(s3_env, monkeypatch):
    calls = []

    def fake(repo):
        calls.append(repo)
        return True, ""

    monkeypatch.setattr(rr, "_s3_writable", fake)
    repo = "s3:https://example.invalid/bucket"
    rr.remote_writable(repo)
    rr.remote_writable(repo)
    assert len(calls) == 1, "second call inside the TTL should be cached"
    rr.remote_writable(repo, force=True)
    assert len(calls) == 2, "force=True must re-probe"


def test_probe_never_raises_into_the_backup_path(s3_env, monkeypatch):
    def boom(repo):
        raise RuntimeError("network stack on fire")

    monkeypatch.setattr(rr, "_s3_writable", boom)
    ok, detail = rr.remote_writable("s3:https://example.invalid/bucket")
    assert not ok
    assert "write probe would not run" in detail
