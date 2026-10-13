"""F02 container profile: protocol, fixed policy, attestation, fail-closed client.

The real-Docker qualification of the same code is
``tests/test_document_worker_container_docker.py``; everything here runs
without Docker and pins the boundary's rules.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import io
import json
import os
import socket
import struct
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from kazma_core.documents import worker_broker as broker_module
from kazma_core.documents import worker_container
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import DocumentParseError, DocumentSandboxError
from kazma_core.documents.worker_broker import (
    BrokerLimits,
    BrokerSettings,
    _Broker,
    _check_attestation,
    _container_argv,
)
from kazma_core.documents.worker_bundle import (
    INPUT_MAGIC,
    OUTPUT_MAGIC,
    RUN_ROOT,
    BundleError,
    read_bundle,
    validate_input_name,
    validate_output_name,
    write_bundle,
)

TOKEN = "t" * 48


def _in_json(path: Path, text: str) -> bool:
    """Whether *path* appears in JSON *text* (backslashes are escaped there)."""

    return json.dumps(str(path))[1:-1] in text
IMAGE_ID = "sha256:" + "a" * 64
RUN_ID = "0" * 32


# ── bundle protocol ─────────────────────────────────────────────────────


def _raw_bundle(
    records: list[dict[str, Any]],
    payloads: list[bytes],
    *,
    magic: bytes = OUTPUT_MAGIC,
    metadata: dict[str, Any] | None = None,
    trailing: bytes = b"",
    header_override: bytes | None = None,
) -> bytes:
    header = dict(metadata or {})
    header["files"] = records
    encoded = header_override if header_override is not None else json.dumps(header).encode()
    return magic + struct.pack(">I", len(encoded)) + encoded + b"".join(payloads) + trailing


def _record(name: str, data: bytes, **override: Any) -> dict[str, Any]:
    record = {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    record.update(override)
    return record


def _read_output(stream: bytes, destination: Path, *, max_bytes: int = 1 << 20) -> dict[str, Path]:
    files, _ = read_bundle(
        io.BytesIO(stream),
        OUTPUT_MAGIC,
        destination,
        validate_name=validate_output_name,
        max_total_bytes=max_bytes,
        max_file_bytes=max_bytes,
        allowed_metadata=frozenset({"exit_code"}),
    )
    return files


def test_bundle_round_trip_is_exact(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.7 bytes \x00\xff" * 100)
    stream = io.BytesIO()
    write_bundle(
        stream,
        INPUT_MAGIC,
        [("work/request.json", b'{"a":1}'), ("in/source.pdf", source)],
        validate_name=validate_input_name,
        metadata={"worker": "parser"},
    )
    out = tmp_path / "staged"
    out.mkdir()
    files, metadata = read_bundle(
        io.BytesIO(stream.getvalue()),
        INPUT_MAGIC,
        out,
        validate_name=validate_input_name,
        max_total_bytes=1 << 20,
        max_file_bytes=1 << 20,
        allowed_metadata=frozenset({"worker"}),
    )
    assert metadata == {"worker": "parser"}
    assert files["in/source.pdf"].read_bytes() == source.read_bytes()
    assert files["work/request.json"].read_bytes() == b'{"a":1}'


def test_a_well_formed_output_stream_is_accepted(tmp_path: Path) -> None:
    # Negative control for every refusal below: the same builder, valid.
    result, output = b'{"ok":true}', b"%PDF-1.7"
    stream = _raw_bundle(
        [_record("result.json", result), _record("output.pdf", output)],
        [result, output],
        metadata={"exit_code": 0},
    )
    files = _read_output(stream, tmp_path)
    assert sorted(files) == ["output.pdf", "result.json"]


@pytest.mark.parametrize(
    ("case", "build"),
    [
        ("parent traversal", lambda d: _raw_bundle([_record("../escape", d)], [d])),
        ("absolute path", lambda d: _raw_bundle([_record("/tmp/escape", d)], [d])),
        ("input name on output side", lambda d: _raw_bundle([_record("work/request.json", d)], [d])),
        ("uppercase output", lambda d: _raw_bundle([_record("Output.PDF", d)], [d])),
        ("nested output", lambda d: _raw_bundle([_record("output.pdf/../../x", d)], [d])),
        ("duplicate", lambda d: _raw_bundle([_record("result.json", d)] * 2, [d, d])),
        ("short body", lambda d: _raw_bundle([_record("result.json", d, size=len(d) + 10)], [d])),
        ("digest", lambda d: _raw_bundle([_record("result.json", d, sha256="0" * 64)], [d])),
        ("trailing bytes", lambda d: _raw_bundle([_record("result.json", d)], [d], trailing=b"x")),
        ("over limit", lambda d: _raw_bundle([_record("result.json", d, size=1 << 30)], [d])),
        ("negative size", lambda d: _raw_bundle([_record("result.json", d, size=-1)], [d])),
        ("bool size", lambda d: _raw_bundle([_record("result.json", d, size=True)], [d])),
        ("header not json", lambda d: _raw_bundle([], [], header_override=b"{nope")),
        ("unknown metadata", lambda d: _raw_bundle([_record("result.json", d)], [d], metadata={"cmd": "x"})),
        ("wrong magic", lambda d: _raw_bundle([_record("result.json", d)], [d], magic=INPUT_MAGIC)),
        ("extra record key", lambda d: _raw_bundle([_record("result.json", d, mode=0o777)], [d])),
    ],
)
def test_a_hostile_output_stream_is_refused_and_writes_nothing_outside(
    tmp_path: Path, case: str, build: Any
) -> None:
    # A dedicated base: the root conftest keeps its own stores in tmp_path.
    base = tmp_path / "case"
    destination = base / "run"
    destination.mkdir(parents=True)
    with pytest.raises(BundleError):
        _read_output(build(b'{"ok":true}'), destination, max_bytes=1 << 20)
    outside = {p for p in base.rglob("*") if destination not in (p, *p.parents)}
    assert outside == set(), case


def test_the_root_check_backs_up_name_validation(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    destination.mkdir()
    data = b"x"
    stream = _raw_bundle([_record("../escape", data)], [data])
    with pytest.raises(BundleError, match="escapes"):
        read_bundle(
            io.BytesIO(stream), OUTPUT_MAGIC, destination, validate_name=lambda name: name,
            max_total_bytes=10, max_file_bytes=10,
        )
    assert not (tmp_path / "escape").exists()


def test_a_file_that_changes_while_sent_fails_the_send(tmp_path: Path) -> None:
    from kazma_core.documents.worker_bundle import prepare_bundle

    source = tmp_path / "doc.pdf"
    source.write_bytes(b"original")
    prepared = prepare_bundle(INPUT_MAGIC, [("in/doc.pdf", source)], validate_name=validate_input_name)
    source.write_bytes(b"replaced")
    with pytest.raises(BundleError, match="changed"):
        prepared.write(io.BytesIO())


@pytest.mark.parametrize(
    "name", ["in/../x", "in//x", "in/.hidden", "work/assets/../request.json", "in/a/b", "x.pdf", "in/a\\b"]
)
def test_input_names_are_an_allowlist(name: str) -> None:
    with pytest.raises(BundleError):
        validate_input_name(name)


# ── container-side requests carry no host path ──────────────────────────


def test_parser_request_names_only_staged_paths(tmp_path: Path) -> None:
    source = tmp_path / "store" / "blobs" / "ab" / "doc.PDF"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF")
    request = {
        "protocol_version": 1,
        "source_path": str(source),
        "config": {"storage_root": str(tmp_path / "store"), "max_pages": 5},
    }
    body, inputs = worker_container.parser_container_request(request, source)
    original, staged = json.dumps(request), json.dumps(body)
    assert _in_json(tmp_path, original)  # negative control: the local profile's request
    assert not _in_json(tmp_path, staged)
    assert body["source_path"] == f"{RUN_ROOT}/in/source.PDF"
    assert body["config"]["storage_root"] == f"{RUN_ROOT}/storage"
    assert inputs == [("in/source.PDF", source)]
    assert request["source_path"] == str(source)  # the caller's request is untouched


def test_operation_requests_stage_sources_and_assets(tmp_path: Path) -> None:
    sources = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for path in sources:
        path.write_bytes(b"%PDF")
    worker, body, inputs = worker_container.operation_container_request(
        "kazma_core.documents.mutation_worker",
        {"sources": [{"path": str(p), "sha256": "x"} for p in sources]},
        tmp_path,
    )
    assert worker == "mutation"
    assert [item["path"] for item in body["sources"]] == [
        f"{RUN_ROOT}/in/source-0.pdf", f"{RUN_ROOT}/in/source-1.pdf"
    ]
    assert [name for name, _ in inputs] == ["in/source-0.pdf", "in/source-1.pdf"]
    run_dir = tmp_path / "run"
    (run_dir / "assets").mkdir(parents=True)
    (run_dir / "assets" / "logo.png").write_bytes(b"png")
    worker, body, inputs = worker_container.operation_container_request(
        "kazma_core.documents.renderer_worker",
        {"source_path": str(sources[0]), "approved_assets": [{"name": "logo.png", "sha256": "y"}]},
        run_dir,
    )
    assert worker == "renderer"
    assert not _in_json(tmp_path, json.dumps(body))
    assert [name for name, _ in inputs] == ["in/source.pdf", "work/assets/logo.png"]
    with pytest.raises(DocumentSandboxError) as unknown:
        worker_container.operation_container_request("kazma_core.documents.parser_worker", {}, run_dir)
    assert unknown.value.code == "worker_input_unsupported"


def test_an_unstageable_extension_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "doc.p df"
    with pytest.raises(DocumentSandboxError) as caught:
        worker_container.parser_container_request({"config": {"storage_root": "x"}}, source)
    assert caught.value.code == "worker_input_unsupported"


# ── the one container policy ────────────────────────────────────────────

_REQUIRED_PAIRS = {
    ("--network", "none"),
    ("--pull", "never"),
    ("--cap-drop", "ALL"),
    ("--security-opt", "no-new-privileges=true"),
    ("--user", "10001:10001"),
    ("--ipc", "none"),
    ("--cgroupns", "private"),
    ("--log-driver", "none"),
    ("--entrypoint", "python"),
}
_FORBIDDEN = ("-v", "--volume", "--mount", "--privileged", "--cap-add", "--device", "-e",
              "--env", "--env-file", "--volumes-from", "--pid", "--userns", "-p", "--publish")


def _policy_violations(argv: list[str], limits: BrokerLimits) -> list[str]:
    problems = []
    pairs = set(zip(argv, argv[1:]))
    problems += [f"missing {pair}" for pair in _REQUIRED_PAIRS - pairs]
    for flag in ("--read-only", "--init"):
        if flag not in argv:
            problems.append(f"missing {flag}")
    problems += [f"forbidden {flag}" for flag in _FORBIDDEN if flag in argv]
    problems += [f"forbidden {a}" for a in argv if a.startswith(("--security-opt=seccomp", "seccomp="))]
    if ("--memory", f"{limits.memory_mb}m") not in pairs or ("--memory-swap", f"{limits.memory_mb}m") not in pairs:
        problems.append("memory/swap not pinned")
    if ("--pids-limit", str(limits.pids)) not in pairs:
        problems.append("pids not pinned")
    tmpfs = [b for a, b in pairs if a == "--tmpfs"]
    if not tmpfs or not {"nosuid", "nodev", "noexec", f"size={limits.tmpfs_mb}m"} <= set(tmpfs[0].split(":", 1)[1].split(",")):
        problems.append("tmpfs not bounded/hardened")
    return problems


def _settings(**overrides: Any) -> BrokerSettings:
    values: dict[str, Any] = {"docker": "docker-not-invoked", "image": "kazma-document-worker:test", "token": TOKEN}
    values.update(overrides)
    return BrokerSettings(**values)


def test_container_argv_is_the_fixed_policy() -> None:
    settings = _settings()
    argv = _container_argv(settings, IMAGE_ID, RUN_ID, "parser")
    assert _policy_violations(argv, settings.limits) == []
    image_at = argv.index(IMAGE_ID)
    assert argv[image_at + 1:] == ["-I", "-m", "kazma_core.documents.worker_entry", "parser"]
    assert "--qualification" not in argv
    assert _container_argv(_settings(qualification=True), IMAGE_ID, RUN_ID, "probe")[-1] == "--qualification"
    # Negative control: the checker sees a mount, a capability and host network.
    for extra in (["-v", "/:/host"], ["--cap-add", "SYS_ADMIN"], ["--privileged"]):
        assert _policy_violations([*argv, *extra], settings.limits)
    tampered = [("host" if a == "none" else a) for a in argv]
    assert _policy_violations(tampered, settings.limits)


@pytest.mark.parametrize("worker", ["shell", "python", "../parser", ""])
def test_container_argv_refuses_unknown_workers(worker: str) -> None:
    with pytest.raises(ValueError):
        _container_argv(_settings(), IMAGE_ID, RUN_ID, worker)


@pytest.mark.parametrize("run_id", ["x", "0" * 31, "../" + "0" * 29, "A" * 32])
def test_container_argv_refuses_malformed_run_ids(run_id: str) -> None:
    with pytest.raises(ValueError):
        _container_argv(_settings(), IMAGE_ID, run_id, "parser")


@pytest.mark.parametrize(
    "overrides",
    [{"token": "short"}, {"name": "Bad Name"}, {"image": "--privileged"},
     {"limits": BrokerLimits(memory_mb=512, tmpfs_mb=512)}, {"limits": BrokerLimits(pids=0)}],
)
def test_broker_settings_refuse_weak_configuration(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _Broker(_settings(**overrides))


# ── attestation ─────────────────────────────────────────────────────────


def _good_facts(limits: BrokerLimits) -> dict[str, Any]:
    zero = "0000000000000000"
    return {
        "uid": 10001,
        "gid": 10001,
        "status": {"CapEff": zero, "CapPrm": zero, "CapAmb": zero, "NoNewPrivs": "1", "Seccomp": "2"},
        "interfaces": ["lo"],
        "external_connect_errno": 101,
        "dns_resolved": False,
        "sensitive_paths_visible": [],
        "tmp_write_errno": None,
        "write_errno": {"/": 30, "/opt": 30, "/usr": 30, "/etc": 30, "/var": 30},
        "mounts": [
            {"mount_point": "/", "options": "ro,relatime", "fstype": "overlay"},
            {"mount_point": "/proc", "options": "rw,nosuid", "fstype": "proc"},
            {"mount_point": "/dev", "options": "rw,nosuid", "fstype": "tmpfs"},
            {"mount_point": "/sys/fs/cgroup", "options": "ro,nosuid", "fstype": "cgroup2"},
            {"mount_point": "/tmp", "options": "rw,nosuid,nodev,noexec,relatime", "fstype": "tmpfs"},
            {"mount_point": "/etc/hosts", "options": "rw,relatime", "fstype": "ext4"},
            {"mount_point": "/sbin/docker-init", "options": "ro,relatime", "fstype": "ext4"},
        ],
        "cgroup": {
            "memory.max": str(limits.memory_mb * 1024 * 1024),
            "memory.swap.max": "0",
            "pids.max": str(limits.pids),
            "cpu.max": f"{limits.cpu_quota} 100000",
        },
        "tmp_bytes": limits.tmpfs_mb * 1024 * 1024,
    }


def test_attestation_accepts_the_policy() -> None:
    limits = BrokerLimits()
    assert _check_attestation(_good_facts(limits), limits) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda f: f.update(uid=0), "root"),
        (lambda f: f["status"].update(CapEff="00000000a80425fb"), "CapEff"),
        (lambda f: f["status"].update(NoNewPrivs="0"), "no_new_privs"),
        (lambda f: f["status"].update(Seccomp="0"), "seccomp"),
        (lambda f: f.update(interfaces=["eth0", "lo"]), "interfaces"),
        (lambda f: f.update(external_connect_errno=None), "outside connection"),
        (lambda f: f.update(dns_resolved=True), "DNS"),
        (lambda f: f.update(sensitive_paths_visible=["/var/run/docker.sock"]), "host paths"),
        (lambda f: f["write_errno"].update({"/opt": None}), "/opt is writable"),
        (lambda f: f["mounts"].append({"mount_point": "/data", "options": "rw", "fstype": "ext4"}), "unexpected mount"),
        (lambda f: f["mounts"][0].update(options="rw,relatime"), "read-only"),
        (lambda f: f["mounts"][4].update(options="rw,nosuid,nodev"), "noexec"),
        (lambda f: f["cgroup"].update({"memory.max": "max"}), "memory.max"),
        (lambda f: f["cgroup"].update({"pids.max": "max"}), "pids.max"),
        (lambda f: f["cgroup"].update({"memory.swap.max": "max"}), "memory.swap.max"),
        (lambda f: f.update(tmp_bytes=10), "/tmp size"),
        (lambda f: f.update(tmp_write_errno=30), "scratch"),
    ],
)
def test_attestation_names_each_violation(mutate: Any, expected: str) -> None:
    limits = BrokerLimits()
    facts = _good_facts(limits)
    mutate(facts)
    problems = _check_attestation(facts, limits)
    assert any(expected in problem for problem in problems), problems


def test_broker_and_bundle_framing_agree(tmp_path: Path) -> None:
    assert broker_module._INPUT_MAGIC == INPUT_MAGIC
    assert broker_module._OUTPUT_MAGIC == OUTPUT_MAGIC
    payload = broker_module._bundle_bytes(
        INPUT_MAGIC, [("work/request.json", b'{"action":"attest"}')], {"worker": "probe"}
    )
    files, metadata = read_bundle(
        io.BytesIO(payload), INPUT_MAGIC, tmp_path, validate_name=validate_input_name,
        max_total_bytes=1024, max_file_bytes=1024, allowed_metadata=frozenset({"worker"}),
    )
    assert metadata == {"worker": "probe"} and files["work/request.json"].read_bytes()
    out = io.BytesIO()
    write_bundle(out, OUTPUT_MAGIC, [("result.json", b'{"ok":true,"facts":{}}')],
                 validate_name=validate_output_name, metadata={"exit_code": 0})
    assert broker_module._bundle_result(out.getvalue()) == {"ok": True, "facts": {}}


# ── broker request handling (no Docker) ─────────────────────────────────


def _request_bytes(header: dict[str, Any]) -> bytes:
    encoded = json.dumps(header).encode()
    return broker_module.REQUEST_MAGIC + struct.pack(">I", len(encoded)) + encoded


def _read_frame(sock: socket.socket) -> tuple[bytes, bytes]:
    def exact(size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            assert chunk, "broker closed early"
            data += chunk
        return data

    head = exact(5)
    return head[:1], exact(struct.unpack(">I", head[1:])[0])


def _run_request(broker: _Broker, header: dict[str, Any]) -> dict[str, Any]:
    server, client = socket.socketpair()
    worker = threading.Thread(target=broker.handle, args=(server,))
    worker.start()
    try:
        client.sendall(_request_bytes(header))
        kind, payload = _read_frame(client)
        assert kind == b"S"
        return json.loads(payload)
    finally:
        worker.join(timeout=10)
        client.close()
        server.close()


def _valid_run(**overrides: Any) -> dict[str, Any]:
    header = {"protocol": 1, "token": TOKEN, "op": "run", "worker": "parser",
              "run_id": RUN_ID, "timeout_seconds": 30, "input_size": 100}
    header.update(overrides)
    return header


@pytest.mark.parametrize(
    ("header", "message"),
    [
        (_valid_run(token="x" * 48), "authentication"),
        (_valid_run(token=None), "authentication"),
        (_valid_run(worker="shell"), "unknown worker"),
        (_valid_run(worker="probe"), "probe"),
        (_valid_run(run_id="../etc"), "run id"),
        (_valid_run(input_size=10**12), "input size"),
        (_valid_run(input_size=0), "input size"),
        (_valid_run(timeout_seconds=-1), "deadline"),
        ({**_valid_run(), "image": "alpine"}, "fields"),
        ({**_valid_run(), "env": {"A": "B"}}, "fields"),
        (_valid_run(op="exec"), "operation"),
        (_valid_run(protocol=2), "protocol"),
    ],
)
def test_broker_refuses_bad_requests_before_docker(
    monkeypatch: pytest.MonkeyPatch, header: dict[str, Any], message: str
) -> None:
    broker = _Broker(_settings())
    monkeypatch.setattr(broker, "_docker", lambda *a, **k: pytest.fail("docker was called"))
    status = _run_request(broker, header)
    assert status["status"] == "rejected"
    assert message in status["message"]


def test_a_valid_request_to_an_unattested_broker_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = _Broker(_settings())
    monkeypatch.setattr(broker, "_docker", lambda *a, **k: pytest.fail("docker was called"))
    assert _run_request(broker, _valid_run())["status"] == "unavailable"
    status = _run_request(broker, {"protocol": 1, "token": TOKEN, "op": "status"})
    assert status["ready"] is False and status["attestation"]["verified"] is False


# ── client against a stub broker on loopback ────────────────────────────


@functools.lru_cache(maxsize=1)
def _host_report_json() -> str:
    """This host's own engine report, built by the image probe's code.

    Computed with the process profile in force, whatever the calling test set.
    """

    from kazma_core.documents.worker_entry import _capabilities

    saved = os.environ.pop("KAZMA_DOCUMENT_WORKER_ISOLATION", None)
    try:
        return json.dumps(_capabilities())
    finally:
        if saved is not None:
            os.environ["KAZMA_DOCUMENT_WORKER_ISOLATION"] = saved


def _host_report() -> dict[str, Any]:
    return json.loads(_host_report_json())


class _StubBroker(_Broker):
    """The real connection handling with a scripted container."""

    def __init__(self, settings: BrokerSettings, script: dict[str, Any]) -> None:
        super().__init__(settings)
        self.image_id = IMAGE_ID
        self.attestation = {"verified": True, "problems": [], "at": "now", "cleanup": "verified"}
        self.capabilities = script.get("capabilities", _host_report())
        self.capability_error = None if self.capabilities is not None else "no report"
        self.script = script
        self.received = b""
        self.cancelled_seen = threading.Event()

    def execute(self, *, worker, run_id, timeout_seconds, input_size, read_input, emit,  # type: ignore[override]
                cancelled, on_admitted=lambda: None):
        on_admitted()
        data = b""
        while len(data) < input_size:
            chunk = read_input(input_size - len(data))
            if not chunk:
                break
            data += chunk
        self.received = data
        for chunk in self.script.get("chunks", []):
            try:
                emit(chunk)
            except OSError:
                self.cancelled_seen.set()
                break
        if self.script.get("wait_for_cancel"):
            for _ in range(200):
                if cancelled():
                    self.cancelled_seen.set()
                    break
                threading.Event().wait(0.01)
        return {"status": self.script.get("status", "completed"), "exit_code": self.script.get("exit_code", 0),
                "stderr": "", "cleanup": "verified", "run_id": run_id, "output_bytes": 0,
                "duration_seconds": 0.0, "image_id": IMAGE_ID, "oom_killed": False, "stderr_truncated": False}


@pytest.fixture
def stub_broker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    servers = []

    def start(script: dict[str, Any], *, token: str = TOKEN) -> _StubBroker:
        broker = _StubBroker(_settings(), script)
        server = broker_module._make_server("tcp:127.0.0.1:0", broker, allow_non_loopback=False)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        servers.append((server, thread))
        token_file = tmp_path / f"token-{len(servers)}"
        token_file.write_text(token, encoding="utf-8")
        host, port = server.server_address[:2]
        monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "container")
        monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", f"tcp://{host}:{port}")
        monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", str(token_file))
        monkeypatch.setattr(worker_container, "_status_cache", None)
        return broker

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _output(files: list[tuple[str, bytes]], exit_code: int = 0) -> bytes:
    stream = io.BytesIO()
    write_bundle(stream, OUTPUT_MAGIC, files, validate_name=lambda n: n, metadata={"exit_code": exit_code})
    return stream.getvalue()


def _run(tmp_path: Path, **kwargs: Any):
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.7 inert")
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    request = {"protocol_version": 1, "source_path": str(source), "config": {"storage_root": str(tmp_path)}}
    body, inputs = worker_container.parser_container_request(request, source)
    return run_dir, worker_container.run_container_worker(
        worker="parser", request=body, inputs=inputs, run_dir=run_dir,
        timeout_seconds=30, max_output_bytes=kwargs.pop("max_output_bytes", 1 << 20), **kwargs,
    )


def test_client_streams_a_staged_bundle_and_materializes_the_result(stub_broker, tmp_path: Path) -> None:
    broker = stub_broker({"chunks": [_output([("result.json", b'{"ok":true}')], exit_code=0)]})
    run_dir, result = _run(tmp_path)
    assert result.returncode == 0 and result.resource_limits_enforced
    assert not result.timed_out and not result.output_limit_exceeded
    assert (run_dir / "result.json").read_bytes() == b'{"ok":true}'
    assert not (run_dir / ".container-output.bundle").exists()
    staged = tmp_path / "staged"
    staged.mkdir()
    files, metadata = read_bundle(
        io.BytesIO(broker.received), INPUT_MAGIC, staged, validate_name=validate_input_name,
        max_total_bytes=1 << 20, max_file_bytes=1 << 20, allowed_metadata=frozenset({"worker"}),
    )
    assert metadata == {"worker": "parser"}
    assert files["in/source.pdf"].read_bytes() == b"%PDF-1.7 inert"
    request = json.loads(files["work/request.json"].read_text(encoding="utf-8"))
    assert not _in_json(tmp_path, json.dumps(request))


@pytest.mark.parametrize(
    "files",
    [
        [("result.json", b"{}"), ("../escape", b"x")],
        [("result.json", b"{}"), ("output.pdf", b"a"), ("output.docx", b"b")],
        [("output.pdf", b"a")],
    ],
)
def test_client_refuses_a_hostile_result_stream(stub_broker, tmp_path: Path, files) -> None:
    stub_broker({"chunks": [_output(files)]})
    with pytest.raises(DocumentSandboxError) as caught:
        _run(tmp_path)
    assert caught.value.code == "worker_output_invalid"
    assert not caught.value.retryable
    assert not (tmp_path / "escape").exists()


def test_client_cap_closes_the_connection(stub_broker, tmp_path: Path) -> None:
    broker = stub_broker({"chunks": [b"x" * 600_000] * 4, "wait_for_cancel": True})
    run_dir, result = _run(tmp_path, max_output_bytes=1_000_000)
    assert result.output_limit_exceeded
    assert broker.cancelled_seen.wait(5)
    assert not (run_dir / "result.json").exists()


@pytest.mark.parametrize(
    ("status", "attribute", "code"),
    [
        ("timeout", "timed_out", None),
        ("output_limit", "output_limit_exceeded", None),
        ("memory_limit", None, "parser_oom"),
        ("busy", None, "worker_isolation_busy"),
        ("cancelled", None, "worker_isolation_unavailable"),
        ("error", None, "worker_isolation_unavailable"),
    ],
)
def test_broker_outcomes_map_to_typed_results(stub_broker, tmp_path: Path, status, attribute, code) -> None:
    stub_broker({"status": status})
    if attribute:
        _, result = _run(tmp_path)
        assert getattr(result, attribute)
    else:
        with pytest.raises(DocumentSandboxError) as caught:
            _run(tmp_path)
        assert caught.value.code == code
        assert caught.value.retryable is (code in {"parser_oom", "worker_isolation_busy", "worker_isolation_unavailable"})


def test_a_wrong_token_is_refused_by_the_broker(stub_broker, tmp_path: Path) -> None:
    stub_broker({}, token="w" * 48)
    with pytest.raises(DocumentSandboxError) as caught:
        _run(tmp_path)
    assert caught.value.code == "worker_isolation_unavailable"


def test_broker_status_reaches_the_isolation_report(stub_broker) -> None:
    stub_broker({})
    report = worker_container.isolation_report()
    assert report["mode"] == "container" and report["ready"] is True
    assert report["filesystem_isolation"] and report["network_isolation"]
    assert report["broker"]["attested"] is True and report["broker"]["image_id"] == IMAGE_ID


# ── selection is explicit and fails closed ──────────────────────────────


def _service(tmp_path: Path):
    from kazma_core.documents.service import DocumentService

    return DocumentService(config=replace(DocumentConfig(storage_root=tmp_path / "store"), ocr_enabled=False))


def _forbid_local_worker(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from kazma_core.documents import operations as ops_module
    from kazma_core.documents import service as svc_module

    calls: list[Any] = []

    def record(request):
        calls.append(request)
        raise AssertionError("the local process worker ran")

    monkeypatch.setattr(svc_module, "run_isolated_subprocess", record)
    monkeypatch.setattr(ops_module, "run_isolated_subprocess", record)
    return calls


def _blank_pdf(path: Path) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.mark.parametrize("setting", ["containr", "docker", "none", "jail"])
def test_an_invalid_isolation_setting_refuses_every_run(tmp_path: Path, monkeypatch, setting: str) -> None:
    calls = _forbid_local_worker(monkeypatch)
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", setting)
    source = _blank_pdf(tmp_path / "blank.pdf")
    with pytest.raises(DocumentParseError) as caught:
        _service(tmp_path).inspect_path(source)
    assert caught.value.code == "worker_isolation_invalid" and not caught.value.retryable
    assert calls == []
    assert worker_container.isolation_report()["ready"] is False


def test_container_profile_never_falls_back_to_the_local_worker(tmp_path: Path, monkeypatch) -> None:
    calls = _forbid_local_worker(monkeypatch)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "container")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", f"tcp://127.0.0.1:{port}")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", str(token_file))
    source = _blank_pdf(tmp_path / "blank.pdf")
    service = _service(tmp_path)
    with pytest.raises(DocumentParseError) as caught:
        service.inspect_path(source)
    assert caught.value.code == "worker_isolation_unavailable" and caught.value.retryable
    from kazma_core.documents.operations import OperationScope

    merged = service.operations.pdf_operation(
        "pdf:info", (source,), scope=OperationScope("t", "w", "a"), output_name=None, export_dir=None,
    )
    assert not merged.ok and merged.code == "worker_isolation_unavailable"
    assert calls == []
    monkeypatch.delenv("KAZMA_DOCUMENT_BROKER_URL")
    with pytest.raises(DocumentParseError) as unconfigured:
        service.inspect_path(source)
    assert unconfigured.value.code == "worker_isolation_unavailable"
    assert calls == []


def test_process_profile_still_uses_the_local_worker(tmp_path: Path, monkeypatch) -> None:
    # Negative control for the two tests above: the same calls, default setting.
    calls = _forbid_local_worker(monkeypatch)
    monkeypatch.delenv("KAZMA_DOCUMENT_WORKER_ISOLATION", raising=False)
    source = _blank_pdf(tmp_path / "blank.pdf")
    with pytest.raises(AssertionError, match="local process worker"):
        _service(tmp_path).inspect_path(source)
    assert len(calls) == 1
    report = worker_container.isolation_report()
    assert report["mode"] == "process"
    assert report["filesystem_isolation"] is False and report["network_isolation"] is False
    assert report["hostile_document_profile"] == "unqualified"


def test_broker_cli_defaults_are_the_policy_defaults() -> None:
    args = broker_module._parse_args(["--image", "x", "--token-file", "t"])
    limits = BrokerLimits()
    assert (args.memory_mb, args.cpus, args.pids, args.tmpfs_mb, args.concurrency) == (
        limits.memory_mb, limits.cpus, limits.pids, limits.tmpfs_mb, limits.concurrency
    )
    assert args.max_input_mb * 1024 * 1024 == limits.max_input_bytes
    assert args.listen == "tcp:127.0.0.1:8765" and not args.qualification


def test_a_worker_that_dies_without_a_stream_is_no_result(stub_broker, tmp_path: Path) -> None:
    # A crash before any output is "no result" -- what the local profile says
    # for the same crash -- not a malformed stream.
    stub_broker({"chunks": [], "exit_code": 139})
    run_dir, result = _run(tmp_path)
    assert result.returncode == 139 and not result.timed_out
    assert not (run_dir / "result.json").exists()


def test_the_service_reports_a_crashed_container_like_a_crashed_process(stub_broker, tmp_path: Path) -> None:
    stub_broker({"chunks": [], "exit_code": 139})
    source = _blank_pdf(tmp_path / "blank.pdf")
    with pytest.raises(DocumentParseError) as caught:
        _service(tmp_path).inspect_path(source)
    assert caught.value.code == "parser_result_missing" and caught.value.retryable



# ── engine readiness comes from the worker image ────────────────────────


def _report(**changes: Any) -> dict[str, Any]:
    """The host's report with rows changed: {kind: {id: {field: value} | None}}."""

    report = copy.deepcopy(_host_report())
    for kind, rows in changes.items():
        key = "parser_id" if kind == "parsers" else "renderer_id"
        for item_id, update in rows.items():
            if update is None:
                report[kind] = [row for row in report[kind] if row[key] != item_id]
            else:
                next(row for row in report[kind] if row[key] == item_id).update(update)
    return report


def test_parser_readiness_comes_from_the_image(stub_broker, monkeypatch) -> None:
    from kazma_core.documents.registry import get_parser_registry

    stub_broker({"capabilities": _report(parsers={
        "text": {"readiness": "unavailable", "reason": "no text parser in this image"},
        "pdf": None,
    })})
    registry = get_parser_registry()
    text_parser = registry.capability_for_extension(".txt")
    assert text_parser is not None and not text_parser.available
    assert text_parser.reason == "no text parser in this image"
    assert "not in the document worker image" in (registry.capability("pdf").reason or "")
    assert registry.capability("docx").available
    # Negative control: the same lookups with the local profile probe this host.
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "process")
    local = get_parser_registry()
    assert local.capability_for_extension(".txt").available
    assert local.capability("pdf").available


def test_renderer_mutator_and_ocr_readiness_come_from_the_image(stub_broker, monkeypatch) -> None:
    from kazma_core.documents.mutation import get_mutation_registry
    from kazma_core.documents.ocr import get_ocr_health
    from kazma_core.documents.renderers import get_renderer_registry

    report = _report(
        renderers={"markdown": {"readiness": "unavailable", "reason": "markdown missing in image"}},
        mutators={"pypdf": None},
    )
    report["ocr"].update(readiness="ready", languages=["eng"], reason=None)
    stub_broker({"capabilities": report})
    markdown = get_renderer_registry().resolve("generate:markdown")
    assert not markdown.available and markdown.reason == "markdown missing in image"
    merge = get_mutation_registry().resolve("pdf:merge")
    assert not merge.available and "not in the document worker image" in (merge.reason or "")
    assert get_ocr_health(("eng",)).languages == ("eng",)
    missing = get_ocr_health(("eng", "ara"))
    assert missing.readiness.value == "unavailable"
    assert "missing in the worker image: ara" in (missing.reason or "")
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "process")
    assert get_renderer_registry().resolve("generate:markdown").available  # negative control


def test_an_operation_the_image_cannot_run_is_refused_with_its_reason(stub_broker, tmp_path: Path) -> None:
    stub_broker({"capabilities": _report(mutators={"pypdf": None})})
    from kazma_core.documents.operations import OperationScope

    source = _blank_pdf(tmp_path / "blank.pdf")
    result = _service(tmp_path).operations.pdf_operation(
        "pdf:info", (source,), scope=OperationScope("t", "w", "a"), output_name=None, export_dir=None,
    )
    assert not result.ok and result.code == "document_engine_unavailable"
    assert "not in the document worker image" in result.message


def test_without_a_ready_broker_every_engine_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.documents.ocr import get_ocr_health
    from kazma_core.documents.registry import get_parser_registry
    from kazma_core.documents.renderers import get_renderer_registry

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "container")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", f"tcp://127.0.0.1:{port}")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(worker_container, "_status_cache", None)
    parsers = get_parser_registry().capabilities()
    assert parsers and not any(item.available for item in parsers)
    assert all("not ready" in (item.reason or "") for item in parsers)
    assert not any(item.available for item in get_renderer_registry().capabilities())
    assert get_ocr_health().readiness.value == "unavailable"
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "containr")
    parser = get_parser_registry().capability("pdf")
    assert not parser.available and "KAZMA_DOCUMENT_WORKER_ISOLATION" in (parser.reason or "")


def test_a_service_built_while_the_broker_was_down_catches_up(stub_broker, tmp_path: Path, monkeypatch) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token_file = tmp_path / "early-token"
    token_file.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "container")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", f"tcp://127.0.0.1:{port}")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(worker_container, "_status_cache", None)
    service = _service(tmp_path)
    assert not service.registry.capability("pdf").available
    stub_broker({})  # the broker comes up; the status cache is reset
    assert service.registry.capability("pdf").available
    assert service.operations.renderers.resolve("generate:markdown").available


def _inspect_output(source: Path) -> bytes:
    result = {
        "protocol_version": 1, "ok": True, "code": "ok", "message": "Document inspected",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "sniff": {"mime_type": "application/pdf", "extension": ".pdf", "container": None},
    }
    return _output([("result.json", json.dumps(result).encode())])


def _host_without_engines(monkeypatch) -> None:
    from kazma_core.documents.registry import ParserReadiness, ParserRegistry

    original = ParserRegistry._probe

    def unavailable(plugin):
        return replace(original(plugin), readiness=ParserReadiness.UNAVAILABLE,
                       reason="engine missing on this host")

    monkeypatch.setattr(ParserRegistry, "_probe", staticmethod(unavailable))


def test_inspection_trusts_the_image_not_the_host(stub_broker, tmp_path: Path, monkeypatch) -> None:
    source = _blank_pdf(tmp_path / "blank.pdf")
    _host_without_engines(monkeypatch)
    stub_broker({"chunks": [_inspect_output(source)]})
    assert _service(tmp_path).inspect_path(source).mime_type == "application/pdf"


def test_the_local_profile_still_refuses_an_engine_the_host_lacks(tmp_path: Path, monkeypatch) -> None:
    # Negative control for the test above: the original host-side refusal.
    monkeypatch.delenv("KAZMA_DOCUMENT_WORKER_ISOLATION", raising=False)
    source = _blank_pdf(tmp_path / "blank.pdf")
    _host_without_engines(monkeypatch)
    with pytest.raises(DocumentParseError) as caught:
        _service(tmp_path).inspect_path(source)
    assert caught.value.code == "document_parser_unavailable"


def test_readiness_flags_an_image_built_from_other_code(stub_broker, tmp_path: Path, monkeypatch) -> None:
    from kazma_core.documents.ingestion import DocumentIngestionService

    stub_broker({})
    assert worker_container.isolation_report()["image_code_matches"] is True
    stale = _report()
    stale["documents_code_sha256"] = "0" * 64
    stub_broker({"capabilities": stale})
    report = worker_container.isolation_report()
    assert report["ready"] is True and report["image_code_matches"] is False
    service = DocumentIngestionService(config=replace(
        DocumentConfig(storage_root=tmp_path / "ingest"), ocr_enabled=False,
        security_malware_scan="off",
    ))
    try:
        readiness = service.readiness()
    finally:
        service.close()
    assert "document_worker_image_outdated" in readiness["degraded_reasons"]
    assert readiness["worker_isolation"]["image_code_matches"] is False


def test_a_broker_without_a_capability_report_is_not_ready(stub_broker) -> None:
    stub_broker({"capabilities": None})
    report = worker_container.isolation_report()
    assert report["ready"] is False and report["engines_reported"] is False


# ── the install's strict execution profile requires containers ──────────


@pytest.fixture
def strict_profile():
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.deployment_policy import CONTAINER_REQUIRED_KEY

    store = get_config_store()
    store.set(CONTAINER_REQUIRED_KEY, True)
    try:
        yield
    finally:
        store.delete(CONTAINER_REQUIRED_KEY)


def test_container_required_refuses_the_local_worker(strict_profile, tmp_path: Path, monkeypatch) -> None:
    from kazma_core.documents.operations import OperationScope

    calls = _forbid_local_worker(monkeypatch)
    monkeypatch.delenv("KAZMA_DOCUMENT_WORKER_ISOLATION", raising=False)
    source = _blank_pdf(tmp_path / "blank.pdf")
    service = _service(tmp_path)
    with pytest.raises(DocumentParseError) as caught:
        service.inspect_path(source)
    assert caught.value.code == "worker_isolation_required" and not caught.value.retryable
    result = service.operations.pdf_operation(
        "pdf:info", (source,), scope=OperationScope("t", "w", "a"), output_name=None, export_dir=None,
    )
    assert not result.ok and result.code == "worker_isolation_required"
    assert calls == []
    report = worker_container.isolation_report()
    assert report["mode"] == "process" and report["ready"] is False
    assert "container_required" in report["reason"]


def test_an_unreadable_execution_policy_refuses_the_local_worker(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    calls = _forbid_local_worker(monkeypatch)
    monkeypatch.delenv("KAZMA_DOCUMENT_WORKER_ISOLATION", raising=False)

    def unreadable() -> bool:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("kazma_core.safety.deployment_policy.container_required", unreadable)
    with pytest.raises(DocumentParseError) as caught:
        _service(tmp_path).inspect_path(_blank_pdf(tmp_path / "blank.pdf"))
    assert caught.value.code == "document_policy_unavailable" and caught.value.retryable
    assert calls == []


def test_container_required_admits_the_container_profile(strict_profile, stub_broker, tmp_path: Path) -> None:
    source = _blank_pdf(tmp_path / "blank.pdf")
    stub_broker({"chunks": [_inspect_output(source)]})
    assert _service(tmp_path).inspect_path(source).mime_type == "application/pdf"
    assert worker_container.isolation_report()["ready"] is True
