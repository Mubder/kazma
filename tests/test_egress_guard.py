"""The suite reaches no network and loads no real model (tests/_egress_guard.py).

On 2026-10-15 swarm pipeline tests loaded the real bge-m3 embedder from this
machine's Hugging Face cache and built real model-provider calls. The guard
refuses both for every test and fails the test that tried, even when the code
under test swallowed the refusal. Shown here:

* what counts as this machine (loopback, unspecified, its own name);
* loopback stays open -- a local server, the suite's real Postgres and
  Playwright live there;
* the negative control: planted tests that reach for the network or the real
  embedder (run only in a child pytest, under a probe variable) each fail
  with the guard's report, a planted ``real_model`` test is skipped unless
  ``KAZMA_TEST_REAL_MODEL=1``, and with the guard off the embedder probe
  passes -- it is the guard that fails it.

Nothing here leaves the machine: the planted network calls are refused
before a packet is sent, and the planted embedder is a stand-in package
written to a temp folder, so the probe is the same on CI (where the real
package is not installed) and here (where the model is cached).
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests import _egress_guard as guard

REPO = Path(__file__).resolve().parents[1]
HERE = "tests/test_egress_guard.py"
_PROBE = "KAZMA_EGRESS_GUARD_PROBE"


def _probing() -> bool:
    return os.environ.get(_PROBE) == "1"


def _child_pytest(*node_ids: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, _PROBE: "1", "KAZMA_TEST_PREIMPORT": "0", **env_extra}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs", *node_ids],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )


# ── what is this machine ──────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.9.10", "::1", "[::1]", "localhost",
                                  "LOCALHOST", "api.localhost", "0.0.0.0", "::", "::ffff:127.0.0.1",
                                  None, socket.gethostname()])
def test_this_machine_is_local(host):
    assert guard.is_local_address(host)


@pytest.mark.parametrize("host", ["8.8.8.8", "192.0.2.1", "2001:db8::1", "api.openai.com",
                                  "huggingface.co", "example.invalid"])
def test_another_host_is_not(host):
    assert not guard.is_local_address(host)


def test_a_literal_address_resolves_without_dns():
    assert guard.resolves_offline("192.0.2.1") and guard.resolves_offline("localhost")
    assert not guard.resolves_offline("api.openai.com")


def test_the_guard_is_registered_for_every_testpath(request):
    assert request.config.pluginmanager.has_plugin("tests._egress_guard")
    assert socket.getaddrinfo is guard._guarded_getaddrinfo
    assert socket.socket.connect is guard._guarded_connect


# ── loopback stays open ───────────────────────────────────────────────────


def test_a_loopback_server_is_reachable():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        conn.sendall(b"hi")
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("localhost", port), timeout=5) as client:
            assert client.recv(2) == b"hi"
    finally:
        thread.join(5)
        server.close()


@pytest.mark.parametrize("loop_kind", ["default", "selector"])
def test_an_asyncio_loopback_connection_is_allowed(loop_kind):
    async def run() -> bytes:
        async def handle(reader, writer):
            writer.write(b"ok")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        data = await reader.read(2)
        writer.close()
        server.close()
        await server.wait_closed()
        return data

    factory = asyncio.SelectorEventLoop if loop_kind == "selector" else None
    assert asyncio.run(run(), loop_factory=factory) == b"ok"


# ── planted tests: run only in the child pytests below ───────────────────


def test_planted_dns_lookup_swallowed():
    """A product path that swallows the network error: the test still fails."""
    if not _probing():
        return
    try:
        socket.getaddrinfo("api.openai.com", 443)
    except OSError:
        pass


def test_planted_connection_to_another_host():
    if not _probing():
        return
    try:
        socket.create_connection(("192.0.2.1", 9), timeout=0.5).close()
    except OSError:
        pass


@pytest.mark.parametrize("loop_kind", ["default", "selector"])
def test_planted_asyncio_connection_in_a_thread_and_a_loop(loop_kind):
    """Both loop kinds (Windows' Proactor connects through ConnectEx, never
    socket.connect), and a lookup in a worker thread."""
    if not _probing():
        return

    async def run() -> None:
        try:
            await asyncio.wait_for(asyncio.open_connection("192.0.2.1", 9), 1)
        except (OSError, TimeoutError):
            pass
        try:
            await asyncio.to_thread(socket.gethostbyname, "huggingface.co")
        except OSError:
            pass

    factory = asyncio.SelectorEventLoop if loop_kind == "selector" else None
    asyncio.run(run(), loop_factory=factory)


def _plant_stand_in_model_package(tmp_path: Path, monkeypatch) -> None:
    """A ``sentence_transformers`` package in a temp folder, found first:
    what the real one is to the guard, on any machine."""
    pkg = tmp_path / "sentence_transformers"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "class SentenceTransformer:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "    def encode(self, text, convert_to_numpy=False):\n"
        "        return [0.5, 0.5]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)


def test_planted_real_embedder(tmp_path, monkeypatch):
    """The product's local embedder: it answers as an unavailable embedder
    does (no vector), and the test fails all the same -- also on a second
    embedder whose breaker an earlier refusal could have opened."""
    if not _probing():
        return
    from kazma_core.memory.embedder import LocalSentenceTransformerEmbedder

    _plant_stand_in_model_package(tmp_path, monkeypatch)
    emb = LocalSentenceTransformerEmbedder("BAAI/bge-m3", allow_download=True)
    assert emb.encode("hello") == []
    emb.breaker.failure("opened by an earlier refusal")
    assert emb.encode("again") == []


def test_planted_import_of_the_model_package(tmp_path, monkeypatch):
    """Any path to the model package, not only the embedder's."""
    if not _probing():
        return
    _plant_stand_in_model_package(tmp_path, monkeypatch)
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        pass


@pytest.mark.real_model
def test_planted_marked_real_model(tmp_path, monkeypatch):
    """Marked: skipped by default, and allowed the model when enabled."""
    if not _probing():
        return
    from kazma_core.memory.embedder import LocalSentenceTransformerEmbedder

    _plant_stand_in_model_package(tmp_path, monkeypatch)
    assert LocalSentenceTransformerEmbedder("BAAI/bge-m3").encode("hello") == [0.5, 0.5]


# ── the negative control ──────────────────────────────────────────────────

_REACHING = (
    "test_planted_dns_lookup_swallowed",
    "test_planted_connection_to_another_host",
    "test_planted_asyncio_connection_in_a_thread_and_a_loop[default]",
    "test_planted_asyncio_connection_in_a_thread_and_a_loop[selector]",
    "test_planted_real_embedder",
    "test_planted_import_of_the_model_package",
)


def test_the_guard_fails_each_planted_test():
    proc = _child_pytest(*(f"{HERE}::{name}" for name in _REACHING),
                         f"{HERE}::test_planted_marked_real_model")
    out = proc.stdout
    assert proc.returncode == 1, out[-3000:]
    assert "6 failed, 1 skipped" in out, out[-3000:]
    for name in _REACHING:
        assert f"FAILED {HERE}::{name}" in out, (name, out[-3000:])
    assert "a DNS lookup of 'api.openai.com'" in out
    assert "a connection to 192.0.2.1:9" in out
    assert "a DNS lookup of 'huggingface.co'" in out
    assert "the real embedder (BAAI/bge-m3)" in out
    assert "the real embedder (import sentence_transformers)" in out
    assert "KAZMA_TEST_REAL_MODEL=1" in out  # the skip names the switch


def test_a_marked_test_runs_with_the_model_when_enabled():
    proc = _child_pytest(f"{HERE}::test_planted_marked_real_model", KAZMA_TEST_REAL_MODEL="1")
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "1 passed" in proc.stdout, proc.stdout[-3000:]


def test_without_the_guard_the_planted_model_probes_pass():
    """Negative control: it is the guard that fails them. (Only the model
    probes run unguarded: their package is the local stand-in, so nothing
    leaves the machine and nothing large loads.) Unguarded, the embedder
    gets the stand-in's vector, so the probe's own ``== []`` fails --
    proof that the guarded run answered without reaching the model."""
    proc = _child_pytest(f"{HERE}::test_planted_real_embedder",
                         f"{HERE}::test_planted_import_of_the_model_package",
                         KAZMA_TEST_EGRESS_GUARD="0")
    out = proc.stdout
    assert "1 failed, 1 passed" in out, out[-3000:]
    assert f"FAILED {HERE}::test_planted_real_embedder" in out, out[-3000:]
    assert "assert [0.5, 0.5] == []" in out, out[-3000:]
    assert "reached outside the machine" not in out, out[-3000:]
