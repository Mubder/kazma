"""The worker image is named by the code it holds, and the broker serves it.

Until 2026-10-15 the document worker image was built by hand under any tag,
and the broker served whatever tag it was given. Now
``kazma_core/documents/worker_image.py`` derives the tag from everything the
build reads, ``scripts/document_worker_image.py build`` builds and labels it,
and the broker asks for that tag -- serving the newest built image meanwhile,
and stopping to switch once the right one appears. Its exit codes tell the
guard what happened, and on Windows it stops cleanly when the guard asks.
Each test carries a negative control. Real Docker: the broker qualification
suite and ``test_document_broker_guard_docker.py``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.documents import worker_broker as broker_module
from kazma_core.documents import worker_image
from kazma_core.documents.worker_broker import BrokerSettings, _Broker

REPO = Path(__file__).resolve().parents[1]
TOKEN = "t" * 40
IMAGE_ID = "sha256:" + "a" * 64


# ── the tag is the build's inputs ───────────────────────────────────────


def _copy_build_inputs(dest: Path) -> Path:
    for path in worker_image._build_inputs(REPO):
        target = dest / path.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return dest


def test_the_same_code_gets_the_same_tag_whatever_its_line_endings(tmp_path):
    a = _copy_build_inputs(tmp_path / "a")
    b = _copy_build_inputs(tmp_path / "b")
    module = b / "kazma-core" / "kazma_core" / "documents" / "worker_image.py"
    text = module.read_bytes().replace(b"\r\n", b"\n")
    module.write_bytes(text.replace(b"\n", b"\r\n"))
    assert worker_image.build_fingerprint(a) == worker_image.build_fingerprint(b)
    tag = worker_image.expected_tag(a)
    assert re.fullmatch(r"kazma-document-worker:src-[0-9a-f]{16}", tag)


@pytest.mark.parametrize("change", ["code", "dockerfile", "requirements", "font", "new_file"])
def test_any_change_to_what_the_image_holds_changes_the_tag(tmp_path, change):
    root = _copy_build_inputs(tmp_path / "r")
    before = worker_image.build_fingerprint(root)
    if change == "code":
        target = root / "kazma-core" / "kazma_core" / "errors.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    elif change == "dockerfile":
        target = root / "deploy" / "document-worker" / "Dockerfile"
        target.write_text(target.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    elif change == "requirements":
        target = root / "deploy" / "document-worker" / "requirements.txt"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif change == "font":
        font = next((root / "kazma-core" / "kazma_core").rglob("*.ttf"))
        font.write_bytes(font.read_bytes() + b"\0")
    else:
        (root / "kazma-core" / "kazma_core" / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    assert worker_image.build_fingerprint(root) != before


def test_what_the_build_leaves_out_does_not_change_the_tag(tmp_path):
    root = _copy_build_inputs(tmp_path / "r")
    before = worker_image.build_fingerprint(root)
    cache = root / "kazma-core" / "kazma_core" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "x.cpython-311.pyc").write_bytes(b"\0compiled")
    (root / "kazma-core" / "kazma_core" / "stale.pyc").write_bytes(b"\0compiled")
    (root / "kazma-ui").mkdir()
    (root / "kazma-ui" / "page.html").write_text("not in the image", encoding="utf-8")
    assert worker_image.build_fingerprint(root) == before


def _dockerignore_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:[^/]+/)*"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out)


def _build_context(root: Path, patterns: list[str]) -> set[str]:
    """Files ``docker build`` sends under *patterns* (Docker's rules: a later
    pattern wins, ``!`` re-includes, a pattern matching a parent folder
    matches what is in it)."""
    rules = [(p.startswith("!"), _dockerignore_regex(p.lstrip("!"))) for p in patterns]
    sent: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        prefixes = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
        included = True
        for negated, regex in rules:
            if any(regex.fullmatch(prefix) for prefix in prefixes):
                included = negated
        if included:
            sent.add(rel)
    return sent


def _ignore_patterns() -> list[str]:
    text = (REPO / "deploy" / "document-worker" / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def test_the_tag_covers_exactly_what_the_build_sends(tmp_path):
    """The fingerprint walks what the .dockerignore lets into the build
    context, no more and no less (the Dockerfile and the .dockerignore are
    read by the build without being sent; they are in the tag too)."""
    root = _copy_build_inputs(tmp_path / "r")
    (root / "kazma-core" / "kazma_core" / "__pycache__").mkdir(exist_ok=True)
    (root / "kazma-core" / "kazma_core" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    (root / "kazma-ui").mkdir()
    (root / "kazma-ui" / "x.py").write_text("x", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "t.py").write_text("x", encoding="utf-8")
    walked = {p.relative_to(root).as_posix() for p in worker_image._build_inputs(root)}
    walked -= {"deploy/document-worker/Dockerfile", "deploy/document-worker/Dockerfile.dockerignore"}
    assert _build_context(root, _ignore_patterns()) == walked
    # Negative control: a .dockerignore that also dropped the fonts would
    # send less than the tag claims -- the comparison sees it.
    assert _build_context(root, [*_ignore_patterns(), "**/*.ttf"]) != walked


def test_the_broker_mirrors_the_image_labels() -> None:
    assert broker_module.IMAGE_SOURCE_LABEL == worker_image.SOURCE_LABEL
    assert broker_module.IMAGE_DOCUMENTS_LABEL == worker_image.DOCUMENTS_LABEL


def test_worker_image_is_standard_library_only() -> None:
    """The guard loads it by path and never imports the app."""
    import ast

    tree = ast.parse(Path(worker_image.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# ── the broker resolves the image ───────────────────────────────────────


class _FakeDocker:
    """Answers the broker's docker calls from a table of local images."""

    def __init__(self, images: dict[str, dict[str, str]], *, engine: bool = True) -> None:
        self.images = images
        self.engine = engine
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, timeout: float = 30.0):
        self.calls.append(args)
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "version":
            if not self.engine:
                return SimpleNamespace(returncode=1, stdout="", stderr="error during connect: pipe")
            return SimpleNamespace(returncode=0, stdout="29.8.2\n", stderr="")
        if args[:2] == ("image", "inspect"):
            ref = args[-1]
            if ref not in self.images:
                return SimpleNamespace(returncode=1, stdout="", stderr="No such image")
            labels = self.images[ref]
            return SimpleNamespace(returncode=0, stdout=f"{IMAGE_ID}|{json.dumps(labels)}\n", stderr="")
        if args[:2] == ("image", "ls"):
            built = [ref for ref, labels in self.images.items() if broker_module.IMAGE_SOURCE_LABEL in labels]
            return SimpleNamespace(returncode=0, stdout="\n".join(reversed(built)) + "\n", stderr="")
        return ok


def _broker(fake: _FakeDocker, *, fallback: bool, image: str = "kazma-document-worker:src-new") -> _Broker:
    broker = _Broker(BrokerSettings(docker="docker", image=image, token=TOKEN, fallback_to_newest_built=fallback))
    broker._docker = fake  # type: ignore[method-assign]
    return broker


def test_the_broker_serves_the_newest_build_while_the_one_asked_for_is_missing():
    old = {broker_module.IMAGE_SOURCE_LABEL: "1" * 64, broker_module.IMAGE_DOCUMENTS_LABEL: "2" * 64}
    fake = _FakeDocker({"kazma-document-worker:src-old": old, "kazma-document-worker:handmade": {}})
    broker = _broker(fake, fallback=True)
    assert broker.resolve_image() == IMAGE_ID
    status = broker.status()
    assert status["image_ref"] == "kazma-document-worker:src-old" and status["image_fallback"] is True
    assert status["image"] == "kazma-document-worker:src-new"
    assert status["image_source"] == "1" * 64
    # Negative control: without the fallback the broker had nothing to serve.
    with pytest.raises(broker_module._NoWorkerImageError):
        _broker(fake, fallback=False).resolve_image()


def test_the_image_asked_for_wins_when_it_is_built():
    labels = {broker_module.IMAGE_SOURCE_LABEL: "3" * 64}
    fake = _FakeDocker({"kazma-document-worker:src-new": labels,
                        "kazma-document-worker:src-old": {broker_module.IMAGE_SOURCE_LABEL: "1" * 64}})
    broker = _broker(fake, fallback=True)
    broker.resolve_image()
    assert broker.image_ref == "kazma-document-worker:src-new" and broker.image_fallback is False


def test_no_image_at_all_and_no_engine_are_told_apart():
    with pytest.raises(broker_module._NoWorkerImageError):
        _broker(_FakeDocker({}), fallback=True).resolve_image()
    with pytest.raises(broker_module._DockerEngineUnavailableError):
        _broker(_FakeDocker({}, engine=False), fallback=True).check_engine()


@pytest.mark.parametrize("images, engine, code", [
    ({}, False, broker_module.EXIT_NO_ENGINE),
    ({}, True, broker_module.EXIT_NO_IMAGE),
])
def test_the_exit_code_says_why_the_broker_stopped(tmp_path, monkeypatch, images, engine, code):
    token = tmp_path / "token"
    token.write_text(TOKEN, encoding="utf-8")
    fake = _FakeDocker(images, engine=engine)
    monkeypatch.setattr(_Broker, "_docker", lambda self, *a, **k: fake(*a, **k))
    monkeypatch.setattr(broker_module.signal, "signal", lambda *a: None)
    rc = broker_module.main(["--image", "kazma-document-worker:src-x", "--token-file", str(token),
                             "--docker", "docker", "--fallback-to-newest-built"])
    assert rc == code
    # Negative control: before 2026-10-15 both were "cannot start" (2), and
    # the guard could not tell a missing build from Docker being down.
    assert rc != broker_module.EXIT_CANNOT_START


def test_a_missing_token_file_is_a_clean_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_module.signal, "signal", lambda *a: None)
    rc = broker_module.main(["--image", "x", "--token-file", str(tmp_path / "missing"), "--docker", "docker"])
    assert rc == broker_module.EXIT_CANNOT_START


class _Server:
    def __init__(self) -> None:
        self.stopped = False

    def shutdown(self) -> None:
        self.stopped = True


class _Checks:
    """The watcher's wait: no time passes; each call is one check, *n* in all."""

    def __init__(self, n: int) -> None:
        self.n = n

    def wait(self, _seconds: float) -> bool:
        self.n -= 1
        return self.n < 0


def test_an_older_image_stops_the_broker_once_the_right_one_is_built(monkeypatch):
    fake = _FakeDocker({"kazma-document-worker:src-old": {broker_module.IMAGE_SOURCE_LABEL: "1" * 64}})
    broker = _broker(fake, fallback=True)
    broker.resolve_image()
    fake.images["kazma-document-worker:src-new"] = {broker_module.IMAGE_SOURCE_LABEL: "9" * 64}
    busy = {"active": True}
    monkeypatch.setattr(broker, "idle", lambda: not busy["active"])
    # Negative control first: a job runs through every check -- never stopped.
    server, outcome = _Server(), {"code": 0}
    broker_module._watch_for_requested_image(broker, server, outcome, _Checks(5))
    assert not server.stopped and outcome["code"] == 0
    busy["active"] = False
    broker_module._watch_for_requested_image(broker, server, outcome, _Checks(5))
    assert server.stopped and outcome["code"] == broker_module.EXIT_NEWER_IMAGE


def test_an_older_image_is_kept_while_the_right_one_is_not_built(monkeypatch):
    fake = _FakeDocker({"kazma-document-worker:src-old": {broker_module.IMAGE_SOURCE_LABEL: "1" * 64}})
    broker = _broker(fake, fallback=True)
    broker.resolve_image()
    monkeypatch.setattr(broker, "idle", lambda: True)
    server, outcome = _Server(), {"code": 0}
    broker_module._watch_for_requested_image(broker, server, outcome, _Checks(5))
    assert not server.stopped


_STOP_SCRIPT = textwrap.dedent('''
    import importlib.util, socketserver, sys, time
    spec = importlib.util.spec_from_file_location("b", sys.argv[1])
    b = importlib.util.module_from_spec(spec); sys.modules["b"] = b; spec.loader.exec_module(b)
    server = socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)
    if sys.argv[3] == "handled":
        b._stop_on_signals(server)
    print("listening", flush=True)
    server.serve_forever(poll_interval=0.1)
    open(sys.argv[2], "w").write("graceful")
''')


@pytest.mark.skipif(os.name != "nt", reason="CTRL_BREAK is the guard's stop on Windows")
@pytest.mark.parametrize("handled", [True, False])
def test_the_guard_s_stop_ends_the_broker_cleanly_on_windows(tmp_path, handled):
    """The guard stops its children with CTRL_BREAK; Python raises it as
    SIGBREAK, whose default ends the process on the spot -- job containers
    left behind. The broker handles it (negative control: unhandled)."""
    script = tmp_path / "stop.py"
    script.write_text(_STOP_SCRIPT, encoding="utf-8")
    done = tmp_path / "done"
    proc = subprocess.Popen(
        [sys.executable, str(script), broker_module.__file__, str(done), "handled" if handled else "default"],
        stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "listening"
    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
    code = proc.wait(timeout=30)
    if handled:
        assert code == 0 and done.read_text(encoding="utf-8") == "graceful"
    else:
        assert code != 0 and not done.exists()


# ── the build script ────────────────────────────────────────────────────


def test_the_build_script_tags_and_labels_the_build(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("_dwi", REPO / "scripts" / "document_worker_image.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    built: dict[str, object] = {}

    def call(argv, cwd=None):
        built["argv"], built["cwd"] = argv, cwd
        return 0

    source = worker_image.build_fingerprint(REPO)
    tag = worker_image.tag_for(source)
    state = {"after": False}

    def image(docker, ref):
        if ref == tag and state["after"]:
            return {"id": IMAGE_ID, "created": "now", "labels": {worker_image.SOURCE_LABEL: source}}
        return None

    def call_then_built(argv, cwd=None):
        state["after"] = True
        return call(argv, cwd)

    monkeypatch.setattr(module, "find_docker_cli", lambda: "docker")
    monkeypatch.setattr(module, "_image", image)
    monkeypatch.setattr(module.subprocess, "call", call_then_built)
    assert module.main(["build"]) == 0
    argv = built["argv"]
    assert argv[argv.index("-t") + 1] == tag
    assert f"{worker_image.SOURCE_LABEL}={source}" in argv
    assert any(str(a).startswith(worker_image.DOCUMENTS_LABEL + "=") for a in argv)
    assert Path(str(built["cwd"])) == REPO
    # Built already: nothing is rebuilt.
    built.clear()
    assert module.main(["build"]) == 0 and not built
