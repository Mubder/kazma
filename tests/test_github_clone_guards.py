"""Clone guards (audit H14/H15/L1): slug traversal, host allowlist, redaction.

Pure-function tests for the helpers shared by the Web clone router
(``POST /api/github/repos/clone``) and the gateway ``/ide repo clone``
command.
"""

from __future__ import annotations

import pytest

from kazma_gateway.agent_handler.commands import safe_repo_dir_name
from kazma_gateway.routers.github import _clone_host_allowed, _redact_token


class TestSafeRepoDirName:
    @pytest.mark.parametrize(
        ("slug", "expected"),
        [
            ("owner/repo", "repo"),
            ("owner/repo.git", "repo.git"),
            ("a/b/c", "c"),  # last segment wins
            ("a//b", "b"),  # empty middle segments are irrelevant
        ],
    )
    def test_accepts_normal_slugs(self, slug: str, expected: str) -> None:
        assert safe_repo_dir_name(slug) == expected

    @pytest.mark.parametrize(
        "slug",
        [
            "",
            "/",
            "owner/",
            "owner/.",
            "owner/..",
            "../..",
            "a/b/../../..",
            "owner/repo extra",
            "owner/repo;rm",
            "https://github.com/o/r?x=1",
        ],
    )
    def test_rejects_unsafe_slugs(self, slug: str) -> None:
        assert safe_repo_dir_name(slug) is None

    def test_rejects_dotdot_from_multi_segment(self) -> None:
        """A crafted multi-segment slug must not yield a traversal target."""
        assert safe_repo_dir_name("a/b/../../..") is None


class TestCloneHostAllowlist:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/owner/repo",
            "https://github.com/owner/repo.git",
            "https://GITHUB.COM/owner/repo",
            "https://gist.github.com/owner/abc",
            "git@github.com:owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
        ],
    )
    def test_allows_github_family(self, url: str) -> None:
        assert _clone_host_allowed(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/owner/repo",  # no plaintext http
            "https://gitlab.com/owner/repo",
            "file:///etc/passwd",
            "ftp://github.com/x",
            "https://192.168.1.10/owner/repo",
            "not-a-url",
            "",
        ],
    )
    def test_refuses_non_allowlisted(self, url: str) -> None:
        assert _clone_host_allowed(url) is False

    def test_env_override_extends_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAZMA_CLONE_HOSTS", "ghe.internal.example, alt.example")
        assert _clone_host_allowed("https://ghe.internal.example/o/r") is True
        assert _clone_host_allowed("ssh://git@alt.example/o/r") is True
        # Override does NOT open the floodgates.
        assert _clone_host_allowed("https://evil.example/o/r") is False


class TestRedactToken:
    def test_removes_token_substring(self) -> None:
        err = "remote: Invalid username or password for ghp_abc123"
        out = _redact_token(err, "ghp_abc123")
        assert "ghp_abc123" not in out
        assert "[redacted]" in out

    def test_handles_none_and_missing_secret(self) -> None:
        assert _redact_token(None, None) == ""
        assert _redact_token("plain failure", None) == "plain failure"
        assert _redact_token("plain failure", "") == "plain failure"

    def test_stderr_shape_survives(self) -> None:
        err = "fatal: repository 'x-access-token:ghp_secret@github.com/' not found"
        out = _redact_token(err, "ghp_secret")[:300]
        assert "ghp_secret" not in out
        assert out.startswith("fatal:")
