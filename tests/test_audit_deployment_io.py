"""Behavioral audit regressions using synthetic files, HTTP streams and tokens."""
from __future__ import annotations

import base64
import subprocess
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_gateway.adapters import callback_store, telegram
from kazma_gateway.routers.workspace import create_workspace_select_router
from kazma_gateway.routers.workspaces import create_workspaces_router
from kazma_core.backup import rclone_policy
from kazma_core.provider_probe import probe_chat_completion


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for index in range(0, len(self.body), 3):
            self.read += 3
            yield self.body[index:index + 3]

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_compressed_download_is_refused_before_reading():
    from kazma_gateway.adapters.downloads import bounded_download

    stream = CountingStream(b"compressed-input")

    def handle(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="Compressed"):
            await bounded_download(client, "https://example.org/proof", 8)
    assert stream.read == 0
    assert stream.closed


@pytest.mark.parametrize("voice", [False, True])
@pytest.mark.parametrize("declared", [None, "1", "invalid", "999"])
@pytest.mark.asyncio
async def test_telegram_stops_stream_and_closes_on_size_limit(monkeypatch, voice, declared):
    monkeypatch.setattr(telegram, "MAX_VOICE_BYTES", 8)
    monkeypatch.setattr(telegram, "MAX_MEDIA_BYTES", 8)
    stream = CountingStream(b"x" * 300)
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/proof.ogg"}})
        headers = {"content-length": declared} if declared is not None else {}
        return httpx.Response(200, headers=headers, stream=stream)

    adapter = telegram.TelegramAdapter(token="synthetic-bot")
    async with httpx.AsyncClient(base_url="https://api.telegram.org/botsynthetic-bot",
                                 transport=httpx.MockTransport(handle)) as client:
        adapter._http = client
        download = adapter.download_voice_file if voice else adapter._download_media_file
        assert await download("proof") is None
    assert stream.closed
    assert stream.read <= 9
    assert [request.method for request in requests] == ["GET", "GET"]


@pytest.mark.parametrize("voice", [False, True])
@pytest.mark.asyncio
async def test_telegram_accepts_exact_limit_and_never_logs_error_body(monkeypatch, voice, caplog):
    monkeypatch.setattr(telegram, "MAX_VOICE_BYTES", 8)
    monkeypatch.setattr(telegram, "MAX_MEDIA_BYTES", 8)
    secret = "synthetic-bot-credential"
    responses = [
        httpx.Response(200, stream=CountingStream(b"12345678")),
        httpx.Response(403, text=secret),
    ]

    def handle(request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/proof.ogg"}})
        return responses.pop(0)

    adapter = telegram.TelegramAdapter(token=secret)
    async with httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{secret}",
                                 transport=httpx.MockTransport(handle)) as client:
        adapter._http = client
        download = adapter.download_voice_file if voice else adapter._download_media_file
        assert await download("proof") == b"12345678"
        assert await download("proof") is None
    assert secret not in caplog.text


@pytest.mark.parametrize("operation", ["select", "create", "switch"])
@pytest.mark.parametrize("configured_root", [False, True])
def test_workspace_refuses_before_any_store_mutation(tmp_path, monkeypatch, operation, configured_root):
    from kazma_core import stores

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_WORKSPACE_ROOT", str(allowed) if configured_root else "")
    store = MagicMock()
    store.get_workspace.return_value = {"id": "old-outside", "root_path": str(outside)}
    monkeypatch.setattr(stores, "get_workspace_store", lambda: store)
    app = FastAPI()
    app.include_router(create_workspace_select_router())
    app.include_router(create_workspaces_router())
    requests = {
        "select": ("/api/workspace/select", {"path": str(outside)}),
        "create": ("/api/workspaces/create", {"path": str(outside / "new"), "name": "proof"}),
        "switch": ("/api/workspaces/switch", {"workspace_id": "old-outside"}),
    }
    url, body = requests[operation]
    response = TestClient(app).post(url, json=body)
    assert response.status_code == 403, response.text
    store.set_active_workspace.assert_not_called()
    store.create_workspace.assert_not_called()
    assert not (outside / "new").exists()


def test_workspace_policy_accepts_inside_and_refuses_symlink_escape(tmp_path, monkeypatch):
    from kazma_core.workspace import root_policy

    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_WORKSPACE_ROOT", str(allowed))
    assert root_policy.validate_root(allowed / "project") == (allowed / "project").resolve()
    link = allowed / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(PermissionError):
        root_policy.validate_root(link)


@pytest.mark.parametrize("remote", ["--config=evil", "-remote:folder", ":s3:bucket", "local-folder", "drive:\nflag"])
def test_rclone_option_and_backend_confusion_refused(remote):
    with pytest.raises(ValueError):
        rclone_policy.validate_remote(remote)


@pytest.mark.parametrize("remote", ["drive:path with spaces/ملفات", "team-backup:/reports/2026", "my remote:bucket/a:b"])
def test_rclone_legitimate_remote_paths_preserved(remote):
    assert rclone_policy.validate_remote(remote) == remote


def test_callback_failures_do_not_log_token_or_exception_payload(monkeypatch, caplog):
    token = "synthetic-callback-proof"
    monkeypatch.setattr(callback_store.secrets, "token_hex", lambda _: token)
    monkeypatch.setattr(callback_store, "_lru_cache", {})

    def fail():
        raise RuntimeError(token)

    monkeypatch.setattr(callback_store, "_get_connection", fail)
    caplog.set_level("DEBUG")
    assert callback_store.encode_callback_data("x" * 70) == f"cb:{token}"
    callback_store._lru_cache.clear()
    assert callback_store.decode_callback_data(f"cb:{token}") == f"cb:{token}"
    assert token not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_provider_probe_preserves_status_without_echoing_remote_text(monkeypatch):
    def handle(request):
        return httpx.Response(403, text="private-internal-file-content token=synthetic-secret")

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result = await probe_chat_completion("http://127.0.0.1:11434/v1", "synthetic-key", "proof")
    assert result["ok"] is False
    assert "403" in result["error"]
    assert "private-internal" not in str(result)
    assert "synthetic-secret" not in str(result)


@pytest.mark.asyncio
async def test_git_authentication_and_refresh_never_put_tokens_in_argv(tmp_path, monkeypatch, caplog):
    from kazma_core import git_identity
    from kazma_skills.native.git_github_manager import tools

    original, fresh = "synthetic-first-token", "synthetic-refreshed-token"
    monkeypatch.setattr(tools, "_get_workspace", lambda: tmp_path)
    monkeypatch.setattr(git_identity, "get_app_installation_token", lambda: original)
    monkeypatch.setattr(git_identity, "invalidate_app_token_cache", lambda: None)
    monkeypatch.setattr(git_identity, "mint_app_installation_token", lambda **_: fresh)
    monkeypatch.setenv("KAZMA_VAULT_KEY", "synthetic-vault-secret")
    calls = []
    attempts = 0

    async def run(argv, **kwargs):
        nonlocal attempts
        calls.append((list(argv), dict(kwargs.get("env") or {})))
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/example/proof.git\n", "")
        if "push" in argv:
            attempts += 1
            return subprocess.CompletedProcess(argv, 1 if attempts == 1 else 0,
                                               "", "bad credentials" if attempts == 1 else "updated")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tools, "run_off_loop", run)
    result = await tools.git_push(branch="main")
    pushes = [env for argv, env in calls if "push" in argv]
    assert len(pushes) == 2
    for env, token in zip(pushes, (original, fresh)):
        assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: Basic {encoded}"
        assert "KAZMA_VAULT_KEY" not in env
        assert all(token not in str(argv) and encoded not in str(argv) for argv, _ in calls)
        assert token not in result + caplog.text
