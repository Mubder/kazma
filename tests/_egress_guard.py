"""No test reaches the network or loads the real embedder (2026-10-15).

A pytest plugin, registered by the root ``conftest.py`` for every testpath.

Found on 2026-10-15: unstubbed swarm pipeline tests reached self-improvement
and memory recall, loaded the real bge-m3 embedder from this machine's
Hugging Face cache (gigabytes, seconds of CPU -- it starved the live server
that runs on the same machine) and called a real model provider over the
network, which answered 401. A test like that passes or fails with the
machine it runs on: on CI the cache is empty and the provider unreachable.

Two closed doors, for every test:

* **The network.** A DNS lookup of any name but this machine's own and the
  loopback names, and a connection (or UDP send) to any address but loopback
  or this machine's own, is refused: ``socket.getaddrinfo`` /
  ``gethostbyname*``, ``socket.socket.connect`` / ``connect_ex`` /
  ``sendto`` and both asyncio loops' ``sock_connect`` (Windows' Proactor
  connects through ``ConnectEx``, never ``socket.connect``). A loopback
  server, a Postgres container on localhost and Playwright on localhost stay
  allowed. A process a test starts is outside this process: its network is
  not seen here (a child pytest loads this plugin again).
* **The real embedder.** The product's local embedder
  (``memory.embedder.LocalSentenceTransformerEmbedder``) asked to encode or
  load with no model of its own and no stand-in ``sentence_transformers`` in
  ``sys.modules`` is refused -- it answers as an unavailable embedder does
  (no vector) -- and so is any import of the real ``sentence_transformers``,
  which the product does only to load a model (an ``ImportError``, which the
  embedder already degrades on). The first door is checked on every call,
  so the embedder's breaker, open after an earlier test's refusal, cannot
  hide a later test that reaches for the model; and it is the same here,
  where the model is cached, and on CI, where the package is not installed.

The refusal raises where the call is made, so nothing leaves the machine or
loads, and the test FAILS with the call site whatever the code under test did
with the error -- product code that swallows a network error must not make a
test that reached for the network pass. A test that truly needs the real
model or a real provider is marked ``@pytest.mark.real_model`` and skipped
unless ``KAZMA_TEST_REAL_MODEL=1``; one that truly needs the internet (a
live GitHub download) is ``@pytest.mark.network``, skipped unless
``KAZMA_TEST_NETWORK=1``. An enabled marked test runs with both doors open.
``KAZMA_TEST_EGRESS_GUARD=0`` turns the guard off for debugging.
``tests/test_egress_guard.py`` holds it, with the planted tests as its
negative control.
"""

from __future__ import annotations

import asyncio.proactor_events
import asyncio.selector_events
import importlib.abc
import ipaddress
import os
import socket
import sys
import threading
import traceback
from typing import Any

import pytest

GUARD_ENV = "KAZMA_TEST_EGRESS_GUARD"
REAL_MODEL_ENV = "KAZMA_TEST_REAL_MODEL"
REAL_MODEL_MARK = "real_model"
NETWORK_ENV = "KAZMA_TEST_NETWORK"
NETWORK_MARK = "network"
#: Each marker that opens the doors, the variable that enables its tests, and
#: what it is for.
MARKS = {
    REAL_MODEL_MARK: (REAL_MODEL_ENV, "loads the real embedder or reaches a real model provider"),
    NETWORK_MARK: (NETWORK_ENV, "reaches a real host on the internet"),
}
#: Packages imported only to load a real model.
REAL_MODEL_PACKAGES = frozenset({"sentence_transformers"})

#: The checkout this plugin belongs to (frames outside it are not Kazma's).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace("\\", "/").lower()

_lock = threading.Lock()
_hits: list[str] = []
_collection_hits: list[str] = []
#: The running test carries a marker whose tests are enabled (MARKS).
_allowed = False


class EgressRefusedError(ConnectionRefusedError):
    """A connection a test made to a host other than this machine."""


class DnsRefusedError(socket.gaierror):
    """A name a test asked the network to resolve."""


class RealModelRefusedError(ImportError):
    """The real embedder, loaded by a test."""


def guard_on() -> bool:
    return not _allowed and os.environ.get(GUARD_ENV) != "0"


# ── which hosts are this machine ───────────────────────────────────────────

_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_real_getaddrinfo = socket.getaddrinfo
_own: tuple[frozenset[str], frozenset[str]] | None = None


def _own_names_and_addresses() -> tuple[frozenset[str], frozenset[str]]:
    """This machine's host name and the addresses it resolves to locally."""
    global _own
    if _own is None:
        name = socket.gethostname().lower()
        addresses: set[str] = set()
        try:
            for info in _real_getaddrinfo(name, None):
                addresses.add(str(info[4][0]).split("%", 1)[0])
        except OSError:
            pass
        _own = (frozenset({name, name.split(".", 1)[0]}), frozenset(addresses))
    return _own


def _host_text(host: Any) -> str:
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    return str(host).strip().strip("[]").lower().split("%", 1)[0]


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def is_local_address(host: Any) -> bool:
    """A host a connection to stays on this machine."""
    if host is None:
        return True
    text = _host_text(host)
    if not text or text in _LOCAL_NAMES or text.endswith(".localhost"):
        return True
    ip = _ip(text)
    if ip is not None:
        mapped = getattr(ip, "ipv4_mapped", None)
        if ip.is_loopback or ip.is_unspecified or (mapped is not None and mapped.is_loopback):
            return True
        return text in _own_names_and_addresses()[1]
    return text in _own_names_and_addresses()[0]


def resolves_offline(host: Any) -> bool:
    """A name whose lookup asks no DNS server: a literal address, a loopback
    name, this machine's own name."""
    if host is None:
        return True
    text = _host_text(host)
    return _ip(text) is not None or is_local_address(text)


# ── what reached out ──────────────────────────────────────────────────────


def _call_site() -> str:
    """Where it was made: the innermost Kazma frame, the outermost one (the
    entry point -- a worker thread's stack starts there) and the test's."""
    here = __file__.replace("\\", "/")
    product: list[str] = []
    test = ""
    for frame in reversed(traceback.extract_stack()):
        path = frame.filename.replace("\\", "/")
        if path == here or not path.lower().startswith(_REPO) or "site-packages" in path:
            continue
        rel = path[len(_REPO):].lstrip("/")
        where = f"{rel}:{frame.lineno} {frame.name}"
        if "/tests/" in path or "_tests/" in path:
            test = where
        else:
            product.append(where)
    parts = product[:1] + product[-1:] if len(product) > 1 else product
    if test:
        parts.append(f"under {test}")
    return " <- ".join(parts) or "outside Kazma's code"


def note(what: str) -> None:
    with _lock:
        _hits.append(f"{what} (from {_call_site()})")


def _take() -> list[str]:
    with _lock:
        taken = list(_hits)
        _hits.clear()
    return taken


def _refuse_connection(address: Any) -> None:
    host = address[0] if isinstance(address, tuple) and address else None
    if host is None or not guard_on() or is_local_address(host):
        return
    port = address[1] if len(address) > 1 else ""
    note(f"a connection to {_host_text(host)}:{port}")
    raise EgressRefusedError(f"tests may not connect to {host}:{port} -- the network is closed to "
                        "the suite (tests/_egress_guard.py)")


def _refuse_lookup(host: Any) -> None:
    if not guard_on() or resolves_offline(host):
        return
    note(f"a DNS lookup of {_host_text(host)!r}")
    raise DnsRefusedError(socket.EAI_NONAME, f"tests may not resolve {host!r} -- the network is "
                     "closed to the suite (tests/_egress_guard.py)")


# ── the doors ─────────────────────────────────────────────────────────────


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    _refuse_lookup(host)
    return _real_getaddrinfo(host, port, *args, **kwargs)


_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex


def _guarded_gethostbyname(host):
    _refuse_lookup(host)
    return _real_gethostbyname(host)


def _guarded_gethostbyname_ex(host):
    _refuse_lookup(host)
    return _real_gethostbyname_ex(host)


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto


def _guarded_connect(self, address):
    _refuse_connection(address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    _refuse_connection(address)
    return _real_connect_ex(self, address)


def _guarded_sendto(self, data, *args):
    if args:
        _refuse_connection(args[-1])
    return _real_sendto(self, data, *args)


def _guard_sock_connect(cls: type) -> None:
    real = cls.sock_connect

    async def sock_connect(self, sock, address):
        _refuse_connection(address)
        return await real(self, sock, address)

    sock_connect.__wrapped__ = real  # type: ignore[attr-defined]
    cls.sock_connect = sock_connect


class _RealModelLoader(importlib.abc.Loader):
    """The real loader, refusing to run while the guard is on."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def create_module(self, spec):
        return self._real.create_module(spec)

    def exec_module(self, module) -> None:
        name = module.__name__
        if guard_on():
            note(f"the real embedder (import {name})")
            raise RealModelRefusedError(f"tests may not load the real model ({name}) -- stub the "
                                        "embedder, or mark the test real_model (tests/_egress_guard.py)")
        self._real.exec_module(module)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._real, item)


class _RealModelFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname not in REAL_MODEL_PACKAGES:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                if spec.loader is not None:
                    spec.loader = _RealModelLoader(spec.loader)
                return spec
        return None


def _stand_in_model_package() -> bool:
    """A test put its own ``sentence_transformers`` in ``sys.modules``: a stub
    module, or ``None`` (the usual "not installed"). The real package, loaded
    through the guard's finder, is not one."""
    if "sentence_transformers" not in sys.modules:
        return False
    mod = sys.modules["sentence_transformers"]
    return mod is None or not isinstance(
        getattr(getattr(mod, "__spec__", None), "loader", None), _RealModelLoader)


def _reaches_the_real_model(embedder: Any) -> bool:
    if not guard_on() or getattr(embedder, "_model", None) is not None:
        return False
    if _stand_in_model_package():
        return False
    note(f"the real embedder ({getattr(embedder, '_model_name', '?')})")
    return True


def _guard_local_embedder() -> None:
    """Wrap the product's local embedder: refuse its model, every call."""
    try:
        from kazma_core.memory import embedder as embedder_module
    except ImportError:  # a broken import is tests/test_imports.py's to report
        return
    local = embedder_module.LocalSentenceTransformerEmbedder
    real_encode, real_ensure = local.encode, local._ensure_model

    def encode(self, text):
        # Only when this call would reach the guarded loader: a test that
        # stubbed _ensure_model (on the instance, or the class) decides itself.
        if (getattr(type(self), "_ensure_model", None) is _ensure_model
                and "_ensure_model" not in vars(self) and _reaches_the_real_model(self)):
            return []
        return real_encode(self, text)

    def _ensure_model(self):
        if _reaches_the_real_model(self):
            return None
        return real_ensure(self)

    encode.__wrapped__ = real_encode  # type: ignore[attr-defined]
    _ensure_model.__wrapped__ = real_ensure  # type: ignore[attr-defined]
    setattr(local, "encode", encode)  # noqa: B010 -- a session-long door, never undone
    setattr(local, "_ensure_model", _ensure_model)  # noqa: B010


_installed = False


def install() -> None:
    """Close the doors for this process (idempotent)."""
    global _installed
    if _installed:
        return
    _installed = True
    _guard_local_embedder()
    socket.getaddrinfo = _guarded_getaddrinfo
    socket.gethostbyname = _guarded_gethostbyname
    socket.gethostbyname_ex = _guarded_gethostbyname_ex
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.socket.sendto = _guarded_sendto
    _guard_sock_connect(asyncio.selector_events.BaseSelectorEventLoop)
    _guard_sock_connect(asyncio.proactor_events.BaseProactorEventLoop)
    sys.meta_path.insert(0, _RealModelFinder())


# ── pytest hooks ──────────────────────────────────────────────────────────


def _report(hits: list[str]) -> str:
    return (
        "This test reached outside the machine or loaded the real model -- the "
        "suite must not depend on the network, a provider or a model cache "
        "(tests/_egress_guard.py). Stub it (a fake LLM client, a fake "
        "embedder: tests/_no_models.py), or mark the test real_model or "
        "network.\n  " + "\n  ".join(dict.fromkeys(hits))
    )


def _enabled_mark(item) -> bool:
    """The test carries a marker whose tests are enabled in this run."""
    return any(item.get_closest_marker(mark) is not None and os.environ.get(env) == "1"
               for mark, (env, _why) in MARKS.items())


def pytest_configure(config) -> None:
    for mark, (env, why) in MARKS.items():
        config.addinivalue_line("markers", f"{mark}: {why}; skipped unless {env}=1 "
                                           "(tests/_egress_guard.py)")
    install()


def pytest_collection_modifyitems(config, items) -> None:
    for item in items:
        for mark, (env, why) in MARKS.items():
            if item.get_closest_marker(mark) is not None and os.environ.get(env) != "1":
                item.add_marker(pytest.mark.skip(reason=f"{why}: set {env}=1 to run it"))


def pytest_collection_finish(session) -> None:
    _collection_hits.extend(_take())


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item) -> None:
    global _allowed
    _take()
    _allowed = _enabled_mark(item)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    hits = _take()
    if hits:
        outcome.force_exception(pytest.fail.Exception(_report(hits), pytrace=False))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    global _allowed
    outcome = yield
    hits = _take()
    _allowed = False
    if hits and outcome.excinfo is None:
        outcome.force_exception(pytest.fail.Exception(_report(hits), pytrace=False))


def pytest_sessionfinish(session, exitstatus) -> None:
    _collection_hits.extend(_take())
    if _collection_hits:
        session.exitstatus = int(pytest.ExitCode.TESTS_FAILED)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if not _collection_hits:
        return
    terminalreporter.section("network or real model outside a test", red=True)
    for hit in dict.fromkeys(_collection_hits):
        terminalreporter.write_line(f"ERROR conftest.py::egress_outside_a_test - {hit}")
