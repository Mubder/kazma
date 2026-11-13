"""Moving Kazma's containers from Docker Desktop to its own engine (Stage 3).

``kazma setup engine --move-from-desktop`` (``kazma_core/engine_move.py``,
AGENTS.md section 63). The owner's PC was moved by hand on 2026-11-12 with a
one-off tool; these tests hold the command made from it. Every docker call
goes to ``tests/_fake_docker_engines.py``, a fake CLI with two engines run as
a real program: the move's real argv, its real pipes between two docker
processes and real tar streams are exercised, and volumes keep owners,
modes, symlinks and hard links.

Held here, each with its negative control:

* only the containers Kazma's record names move; another project's
  container on Docker Desktop is never touched;
* Kazma is held before any container is stopped, and every container is
  stopped before its volume is read (never a copy while it runs);
* the copy is checked entry by entry: a copy that changed one byte is
  refused and everything is put back (the move without the check completes
  on the bad copy);
* a failure leaves Kazma on Docker Desktop as it was: this run's objects
  removed from the engine, the originals' restart policy back and running,
  ``containers.engine`` still ``desktop``;
* after the switch, Kazma not seen ready puts everything back too, keeping
  the engine's copies (Kazma may have written to them);
* a run that died half way is rolled back exactly by the next run, which
  then moves;
* Kazma's database on Docker Desktop, a volume another project shares, a
  name already on the engine: refused before anything changes;
* ``kazma setup engine`` alone on a Docker Desktop install writes Docker
  Desktop down before it records the engine (recorded, the engine would be
  Kazma's choice at its next start, with none of its containers).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

from kazma_core import container_engine_setup as ces
from kazma_core import engine_move as em
from kazma_core.docker_cli import ENGINE_KEY
from kazma_core.guard_dependencies import Endpoint

FAKE = Path(__file__).with_name("_fake_docker_engines.py")
DESKTOP, ENGINE = em.DESKTOP, em.ENGINE
SECRET = "Neo4j-Secret-Kept-7"


# ── the two engines ──────────────────────────────────────────────────────


def _volume_tar(path: Path, members: list[tuple[str, str, dict]]) -> None:
    """A volume's content: ``(name, kind, fields)`` with kind file/dir/sym/link."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, kind, fields in members:
            info = tarfile.TarInfo(name)
            info.uid, info.gid = fields.get("uid", 0), fields.get("gid", 0)
            info.mode = fields.get("mode", 0o644)
            data = fields.get("data", b"")
            if kind == "dir":
                info.type = tarfile.DIRTYPE
            elif kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, fields["to"]
            elif kind == "link":
                info.type, info.linkname = tarfile.LNKTYPE, fields["to"]
            else:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if kind == "file" else None)


def _image(image_id: str, tag: str, layers: list[str]) -> dict:
    return {"Id": image_id, "RepoTags": [tag], "RepoDigests": [f"{tag.split(':')[0]}@sha256:{image_id[-8:] * 8}"],
            "RootFS": {"Type": "layers", "Layers": layers}}


def _row(name: str, image: dict, *, ports=(), mounts=(), env=(), labels=None, networks=None, health=True,
         running=True, diff=(), files=None, restart="unless-stopped") -> dict:
    cid = hashlib.sha256(name.encode()).hexdigest()
    bindings: dict[str, list] = {}
    for ip, host, port in ports:
        bindings.setdefault(port, []).append({"HostIp": ip, "HostPort": host})
    return {
        "Id": cid, "Name": "/" + name, "Image": image["Id"],
        "Config": {"Image": image["RepoTags"][0], "Env": list(env), "Labels": labels or {},
                   "Cmd": ["serve"], "Entrypoint": ["/entry.sh", "--quiet"], "WorkingDir": "/app", "User": "",
                   "Hostname": cid[:12], "Tty": False, "OpenStdin": False,
                   "Healthcheck": {"Test": ["CMD-SHELL", "wget -qO- http://localhost/ || exit 1"],
                                   "Interval": 15_000_000_000, "Retries": 10} if health else None},
        "HostConfig": {"RestartPolicy": {"Name": restart, "MaximumRetryCount": 0}, "PortBindings": bindings,
                       "NetworkMode": next(iter(networks)) if networks else "bridge"},
        "Mounts": list(mounts),
        "NetworkSettings": {"Networks": networks or {"bridge": {"Aliases": None}}},
        "State": {"Running": running, "Status": "running" if running else "exited"},
        "_diff": list(diff), "_files": files or {},
    }


class Engines:
    """The fake's state: write the two engines, read them back."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, ctx: str, data: dict) -> None:
        (self.root / f"{ctx}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")

    def read(self, ctx: str) -> dict:
        return json.loads((self.root / f"{ctx}.json").read_text(encoding="utf-8"))

    def faults(self, **faults) -> None:
        (self.root / "faults.json").write_text(json.dumps(faults), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        folder = self.root / "calls"
        if not folder.is_dir():
            return []
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(folder.iterdir())]

    def mark(self, text: str) -> None:
        import time

        folder = self.root / "calls"
        folder.mkdir(exist_ok=True)
        (folder / f"{time.time_ns():020d}-{os.getpid()}.json").write_text(json.dumps(["MARK", text]),
                                                                          encoding="utf-8")

    def volume(self, ctx: str, name: str) -> list[tuple]:
        path = self.root / f"{ctx}.volumes" / f"{name}.tar"
        with path.open("rb") as fh:
            return em._manifest_of(fh)


ANON = "a" * 64


@pytest.fixture
def engines(tmp_path, monkeypatch):
    state = tmp_path / "docker"
    state.mkdir()
    box = Engines(state)
    searxng = _image("sha256:" + "5" * 64, "docker.io/searxng/searxng:latest", ["l-s1", "l-s2"])
    neo4j = _image("sha256:" + "4" * 64, "neo4j:5", ["l-n1", "l-n2", "l-n3"])
    shipx = _image("sha256:" + "6" * 64, "postgres:16", ["l-p1"])
    compose = {"com.docker.compose.project": "kazma", "com.docker.compose.service": "searxng"}
    rows = {
        "kazma-searxng": _row(
            "kazma-searxng", searxng, ports=[("127.0.0.1", "8088", "8080/tcp")],
            mounts=[{"Type": "bind", "Source": r"G:\GitHubRepos\kazma\deploy\searxng", "Destination": "/etc/searxng",
                     "RW": True},
                    {"Type": "volume", "Name": ANON, "Driver": "local", "Destination": "/var/cache/searxng",
                     "RW": True}],
            env=["SEARXNG_SECRET=searx-secret-1", "SEARXNG_BASE_URL=http://127.0.0.1:8088/"], labels=compose,
            networks={"kazma_default": {"Aliases": ["kazma-searxng", "searxng"]}}),
        "kazma-neo4j": _row(
            "kazma-neo4j", neo4j, ports=[("127.0.0.1", "7474", "7474/tcp"), ("127.0.0.1", "7687", "7687/tcp")],
            mounts=[{"Type": "volume", "Name": "kazma-neo4j-data", "Driver": "local", "Destination": "/data",
                     "RW": True}],
            env=[f"NEO4J_AUTH=neo4j/{SECRET}"],
            diff=["C /etc", "A /etc/kazma-extra.conf", "A /tmp/cache", "C /var", "C /var/lib", "C /var/lib/neo4j",
                  "C /var/lib/neo4j/conf", "C /var/lib/neo4j/conf/neo4j.conf"],
            files={"/etc/kazma-extra.conf": "extra=1", "/var/lib/neo4j/conf/neo4j.conf": "dbms.memory=2g"}),
        "shipx-db": _row("shipx-db", shipx, ports=[("0.0.0.0", "5600", "5432/tcp")],
                         mounts=[{"Type": "volume", "Name": "shipx-pg", "Driver": "local",
                                  "Destination": "/var/lib/postgresql/data", "RW": True}],
                         env=["POSTGRES_PASSWORD=shipx"]),
    }
    box.write(DESKTOP, {
        "up": True, "containers": rows,
        "images": {i["Id"]: i for i in (searxng, neo4j, shipx)},
        "networks": {"kazma_default": {"Name": "kazma_default", "Driver": "bridge",
                                       "Labels": {"com.docker.compose.network": "default"}}},
        "volumes": {name: {"Name": name, "Driver": "local", "Labels": labels, "Options": {}} for name, labels in (
            ("kazma-neo4j-data", {"com.docker.compose.volume": "kazma-neo4j-data"}), (ANON, {}), ("shipx-pg", {}))},
    })
    box.write(ENGINE, {"up": True, "containers": {}, "images": {}, "networks": {}, "volumes": {}})
    _volume_tar(state / f"{DESKTOP}.volumes" / "kazma-neo4j-data.tar", [
        ("databases", "dir", {"mode": 0o750, "uid": 7474, "gid": 7474}),
        ("databases/neo4j.store", "file", {"data": b"graph store " * 500, "mode": 0o640, "uid": 7474, "gid": 7474}),
        ("databases/store.link", "link", {"to": "databases/neo4j.store", "uid": 7474, "gid": 7474}),
        ("current", "sym", {"to": "databases"}),
        (".hidden", "file", {"data": b"dot file", "mode": 0o600}),
    ])
    _volume_tar(state / f"{DESKTOP}.volumes" / f"{ANON}.tar", [("cache.bin", "file", {"data": b"cache"})])
    _volume_tar(state / f"{DESKTOP}.volumes" / "shipx-pg.tar", [("PG_VERSION", "file", {"data": b"16"})])
    (state / "registry.json").write_text(json.dumps({"python:3.12-slim": _image(
        "sha256:" + "7" * 64, "python:3.12-slim", ["l-py"])}), encoding="utf-8")
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    # Both engines' Docker contexts exist (the setting refuses an engine that is not here).
    config = tmp_path / "docker-config"
    monkeypatch.setenv("DOCKER_CONFIG", str(config))
    for name in (DESKTOP, ENGINE):
        folder = config / "contexts" / "meta" / hashlib.sha256(name.encode()).hexdigest()
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"Name": name}), encoding="utf-8")
    monkeypatch.setattr("kazma_core.guard_dependencies._endpoints",
                        lambda: ([Endpoint("graph", 7687), Endpoint("search", 8088)], set()))
    monkeypatch.setattr(ces, "check", lambda run=None: ces.EngineCheck(True, "Kazma's engine, Docker 29.9.3", [],
                                                                       "29.9.3"))
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    store.delete(ENGINE_KEY)
    yield box
    store.delete(ENGINE_KEY)


def _docker() -> em.Docker:
    return em.Docker([sys.executable, "-I", "-S", str(FAKE)], env={**os.environ})


class _Hold:
    """The guard's stop and start: marks in the fake's call log."""

    def __init__(self, box: Engines, *, running: bool = True, resume_result: str = "up",
                 record: Path | None = None, ready: bool = False) -> None:
        self.box, self.running, self.resume_result, self.record = box, running, resume_result, record
        self.held, self._ready, self.leftover = False, ready, False

    def refusal(self):
        return None

    def stop(self) -> bool:
        if self.held or not self.running:
            return True
        self.box.mark("hold")
        self.held = True
        return True

    def resume(self) -> str:
        if not self.held:
            return "not_held"
        self.box.mark("resume")
        self.held = False
        return self.resume_result

    def adopt_leftover(self) -> bool:
        if self.leftover:
            self.held = self.running = True
        return self.leftover

    def ready(self) -> bool:
        return self._ready

    def record_path(self):
        return self.record


def _options(**kw):
    base = {"yes": True, "check": False, "dry_run": False, "remove": False, "force": False,
            "move_from_desktop": True}
    base.update(kw)
    return argparse.Namespace(**base)


def _move(box: Engines, tmp_path: Path, *, hold: _Hold | None = None, answering=lambda port: True) -> int:
    from kazma_cli import setup

    hold = hold or _Hold(box)
    return setup._move_from_desktop(
        _options(), platform=_Windows(), docker=_docker(), hold=hold, answering=answering,
        journal=em.Journal(tmp_path / "setup_record.json"))


class _Windows:
    os = "windows"
    wsl = False
    manager = "winget"


def _index(calls: list[list[str]], match) -> int:
    for i, call in enumerate(calls):
        if match(call):
            return i
    return -1


def _changing(calls: list[list[str]]) -> list[list[str]]:
    """Calls that change an engine."""
    verbs = {"stop", "update", "create", "start", "rm", "load", "pull", "tag", "cp"}
    return [c for c in calls if c[0] != "MARK" and (c[1] in verbs or c[1:3] in (["volume", "create"],
            ["volume", "rm"], ["network", "create"], ["network", "rm"], ["network", "connect"])
            or (c[1] == "run" and "-xf" in c))]


def _stored() -> str:
    from kazma_core.config_store import get_config_store

    return str(get_config_store().get(ENGINE_KEY) or "")


# ── the move ─────────────────────────────────────────────────────────────


def test_kazmas_containers_move_whole_and_another_projects_is_never_touched(engines, tmp_path, capsys):
    record = tmp_path / "guard_dependencies.json"
    record.write_text(json.dumps({"version": 1, "engine": "", "containers": [{"name": "kazma-neo4j"}]}),
                      encoding="utf-8")
    assert _move(engines, tmp_path, hold=_Hold(engines, record=record)) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    desktop, engine = engines.read(DESKTOP), engines.read(ENGINE)
    for name in ("kazma-searxng", "kazma-neo4j"):
        before, after = desktop["containers"][name], engine["containers"][name]
        # Kept on Docker Desktop, stopped, never started there again by itself.
        assert before["State"]["Running"] is False
        assert before["HostConfig"]["RestartPolicy"]["Name"] == "no"
        # Made again from its own settings, running on the engine.
        assert after["State"]["Running"] is True and after["State"]["Health"]["Status"] == "healthy"
        assert after["Config"]["Env"] == before["Config"]["Env"]
        assert after["Config"]["Labels"] == before["Config"]["Labels"]
        assert after["HostConfig"]["PortBindings"] == before["HostConfig"]["PortBindings"]
        assert after["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped", "its own policy, not the move's no"
        assert after["Config"]["Entrypoint"] + after["Config"]["Cmd"] == ["/entry.sh", "--quiet", "serve"]
        assert after["Config"]["Healthcheck"]["Test"] == before["Config"]["Healthcheck"]["Test"]
        assert after["Image"] == before["Image"], "the same image, layer for layer"
    searx = engine["containers"]["kazma-searxng"]
    assert {"Type": "bind", "Source": "/g/GitHubRepos/kazma/deploy/searxng", "Destination": "/etc/searxng",
            "RW": True} in searx["Mounts"], "a Windows folder as Kazma's engine mounts it"
    assert searx["NetworkSettings"]["Networks"] == {"kazma_default": {"Aliases": ["searxng"]}}
    assert engine["networks"]["kazma_default"]["Labels"] == {"com.docker.compose.network": "default"}
    # Every volume the same entry by entry: owners, modes, symlinks, hard links.
    for volume in ("kazma-neo4j-data", ANON):
        assert engines.volume(ENGINE, volume) == engines.volume(DESKTOP, volume)
    assert engine["volumes"]["kazma-neo4j-data"]["Labels"] == {"com.docker.compose.volume": "kazma-neo4j-data"}
    # Files added or changed inside the container went with it; runtime ones did not.
    assert engine["containers"]["kazma-neo4j"]["_files"] == {
        "/etc/kazma-extra.conf": "extra=1", "/var/lib/neo4j/conf/neo4j.conf": "dbms.memory=2g"}
    # Said once per container: Neo4j's start lists every file of its folder (248 lines on the drill VM).
    assert out.count("carried what was added or changed inside kazma-neo4j") == 1
    # Another project's container: not one call names it, it runs as it did.
    assert desktop["containers"]["shipx-db"]["State"]["Running"] is True
    assert "shipx-db" not in engine["containers"] and "shipx-pg" not in engine["volumes"]
    assert not any("shipx" in " ".join(c) for c in engines.calls())
    # The switch, the guard told, the journal closed; the sandbox image pulled.
    assert _stored() == "kazma"
    assert json.loads(record.read_text(encoding="utf-8"))["engine"] == "kazma"
    assert em.Journal(tmp_path / "setup_record.json").state == "done"
    assert "python:3.12-slim" in json.dumps(engine["images"])
    assert SECRET not in out, "the containers' passwords are never printed"
    assert "stay stopped and kept" in out


def test_kazma_is_held_before_any_container_stops_and_each_stops_before_its_volume_is_read(engines, tmp_path):
    assert _move(engines, tmp_path) == 0
    calls = engines.calls()
    held = _index(calls, lambda c: c == ["MARK", "hold"])
    first_stop = _index(calls, lambda c: c[:2] == [DESKTOP, "stop"])
    assert -1 < held < first_stop, "nothing of Kazma's stops while Kazma runs"
    for volume, name in (("kazma-neo4j-data", "kazma-neo4j"), (ANON, "kazma-searxng")):
        stopped = _index(calls, lambda c, n=name: c[:2] == [DESKTOP, "stop"] and c[-1] == n)
        read = _index(calls, lambda c, v=volume: c[:2] == [DESKTOP, "run"] and f"{v}:/v:ro" in c)
        assert -1 < stopped < read, f"{volume} read only after {name} stopped"
    resumed = _index(calls, lambda c: c == ["MARK", "resume"])
    assert resumed > _index(calls, lambda c: c[:2] == [ENGINE, "start"]), "Kazma starts after its containers"
    # The images were put on the engine before Kazma was held: the hold is the copy only.
    assert -1 < _index(calls, lambda c: c[:2] == [ENGINE, "load"]) < held


def test_a_container_that_keeps_running_is_never_copied(engines, tmp_path, capsys):
    engines.faults(keeps_running=["kazma-neo4j"])
    assert _move(engines, tmp_path) == 1
    calls = engines.calls()
    assert not any(c[:2] == [DESKTOP, "run"] and "kazma-neo4j-data:/v:ro" in c for c in calls)
    assert "still runs on Docker Desktop" in capsys.readouterr().out


def test_control_a_mover_that_trusts_the_stop_copies_a_running_container(engines, tmp_path, monkeypatch):
    engines.faults(keeps_running=["kazma-neo4j"])
    monkeypatch.setattr(em, "_seen_stopped", lambda docker, name: True)
    _move(engines, tmp_path)
    assert any(c[:2] == [DESKTOP, "run"] and "kazma-neo4j-data:/v:ro" in c for c in engines.calls())


def _back_on_desktop(box: Engines) -> None:
    desktop = box.read(DESKTOP)
    for name in ("kazma-searxng", "kazma-neo4j"):
        row = desktop["containers"][name]
        assert row["State"]["Running"] is True, f"{name} runs on Docker Desktop again"
        assert row["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped", "its own restart policy back"
    assert desktop["containers"]["shipx-db"]["State"]["Running"] is True
    assert _stored() == "desktop"


def test_a_copy_that_changed_a_byte_is_refused_and_everything_is_put_back(engines, tmp_path, capsys):
    engines.faults(corrupt=[ENGINE])
    assert _move(engines, tmp_path) == 1
    out = capsys.readouterr().out
    assert "is not the same" in out
    engine = engines.read(ENGINE)
    assert engine["containers"] == {} and engine["volumes"] == {} and engine["networks"] == {}, \
        "this run's objects removed from the engine (they held only the copy)"
    _back_on_desktop(engines)
    assert em.Journal(tmp_path / "setup_record.json").state == "rolled_back"
    assert ["MARK", "resume"] in engines.calls()


def test_control_without_the_check_the_bad_copy_is_switched_to(engines, tmp_path, monkeypatch):
    engines.faults(corrupt=[ENGINE])
    monkeypatch.setattr(em, "_manifest_difference", lambda a, b: "")
    assert _move(engines, tmp_path) == 0
    assert _stored() == "kazma"
    assert engines.volume(ENGINE, "kazma-neo4j-data") != engines.volume(DESKTOP, "kazma-neo4j-data")


def test_kazma_not_seen_ready_after_the_switch_puts_it_back_and_keeps_the_engines_copies(engines, tmp_path, capsys):
    record = tmp_path / "guard_dependencies.json"
    record.write_text(json.dumps({"version": 1, "engine": "", "containers": []}), encoding="utf-8")
    hold = _Hold(engines, resume_result="slow", record=record)
    assert _move(engines, tmp_path, hold=hold) == 1
    _back_on_desktop(engines)
    engine = engines.read(ENGINE)
    for name in ("kazma-searxng", "kazma-neo4j"):
        assert engine["containers"][name]["State"]["Running"] is False, "stopped on the engine"
        assert engine["containers"][name]["HostConfig"]["RestartPolicy"]["Name"] == "no"
    assert "kazma-neo4j-data" in engine["volumes"], "kept: Kazma may have written to it"
    assert json.loads(record.read_text(encoding="utf-8"))["engine"] == "desktop"
    assert "stopped and kept" in capsys.readouterr().out


def test_a_container_that_never_answers_puts_everything_back(engines, tmp_path):
    assert _move(engines, tmp_path, answering=lambda port: port != 7687) == 1
    _back_on_desktop(engines)
    assert engines.read(ENGINE)["containers"] == {}


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """A container either answers at once in the fake or never does."""
    monkeypatch.setattr(em, "START_WAIT_S", 0.0)


def test_an_image_docker_save_cannot_write_is_pulled_by_its_digest(engines, tmp_path):
    # Docker Desktop's containerd store cannot save some multi-platform images.
    desktop = engines.read(DESKTOP)
    registry = json.loads((engines.root / "registry.json").read_text(encoding="utf-8"))
    for image in desktop["images"].values():
        for digest in image["RepoDigests"]:
            registry[digest] = image
    (engines.root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    engines.faults(fail={f"{DESKTOP} save": "content digest not found"})
    assert _move(engines, tmp_path) == 0
    engine = engines.read(ENGINE)
    assert engine["containers"]["kazma-neo4j"]["Image"] == desktop["containers"]["kazma-neo4j"]["Image"]
    assert any(c[:2] == [ENGINE, "pull"] and "@sha256:" in c[2] for c in engines.calls())


def test_an_image_that_cannot_reach_the_engine_stops_the_move_before_anything(engines, tmp_path, capsys):
    engines.faults(fail={f"{DESKTOP} save": "content digest not found"})  # and no registry holds it
    assert _move(engines, tmp_path) == 1
    assert "could not be put on Kazma's engine" in capsys.readouterr().out
    assert not any(c[:2] == [DESKTOP, "stop"] for c in engines.calls()), "nothing of Kazma's stopped"
    assert not any(c == ["MARK", "hold"] for c in engines.calls())


# ── refused before anything changes ──────────────────────────────────────


def _nothing_changed(box: Engines) -> None:
    assert _changing(box.calls()) == []
    assert not any(c == ["MARK", "hold"] for c in box.calls())


def test_kazmas_database_on_docker_desktop_is_refused_with_its_own_move(engines, tmp_path, monkeypatch, capsys):
    desktop = engines.read(DESKTOP)
    desktop["containers"]["kazma-db-win"] = _row("kazma-db-win", desktop["images"]["sha256:" + "6" * 64],
                                                 ports=[("127.0.0.1", "5433", "5432/tcp")])
    engines.write(DESKTOP, desktop)
    monkeypatch.setattr("kazma_core.guard_dependencies._endpoints", lambda: (
        [Endpoint("database", 5433), Endpoint("graph", 7687), Endpoint("search", 8088)], set()))
    assert _move(engines, tmp_path) == 2
    out = capsys.readouterr().out
    assert "kazma setup postgres --move-from-container kazma-db-win" in out
    _nothing_changed(engines)


def test_a_volume_another_project_shares_is_refused(engines, tmp_path, capsys):
    desktop = engines.read(DESKTOP)
    desktop["containers"]["shipx-db"]["Mounts"].append(
        {"Type": "volume", "Name": "kazma-neo4j-data", "Driver": "local", "Destination": "/x", "RW": True})
    engines.write(DESKTOP, desktop)
    assert _move(engines, tmp_path) == 2
    assert "also used by shipx-db" in capsys.readouterr().out
    _nothing_changed(engines)


def test_a_name_already_on_the_engine_is_refused(engines, tmp_path, capsys):
    engine = engines.read(ENGINE)
    engine["volumes"]["kazma-neo4j-data"] = {"Name": "kazma-neo4j-data", "Driver": "local", "Labels": {},
                                             "Options": {}}
    engines.write(ENGINE, engine)
    assert _move(engines, tmp_path) == 2
    assert "never overwritten" in capsys.readouterr().out
    _nothing_changed(engines)


def test_docker_desktop_down_is_refused(engines, tmp_path, capsys):
    desktop = engines.read(DESKTOP)
    desktop["up"] = False
    engines.write(DESKTOP, desktop)
    assert _move(engines, tmp_path) == 2
    assert "Docker Desktop does not answer" in capsys.readouterr().out
    _nothing_changed(engines)


def test_an_install_already_on_its_own_engine_is_not_moved(engines, tmp_path, capsys):
    em._write_engine("kazma")
    assert _move(engines, tmp_path) == 2
    assert "nothing to move" in capsys.readouterr().out
    _nothing_changed(engines)


def test_a_dry_run_changes_nothing(engines, tmp_path, capsys):
    from kazma_cli import setup

    code = setup._move_from_desktop(_options(dry_run=True), platform=_Windows(), docker=_docker(),
                                    hold=_Hold(engines), answering=lambda p: True,
                                    journal=em.Journal(tmp_path / "setup_record.json"))
    assert code == 0
    out = capsys.readouterr().out
    assert "kazma-neo4j (graph)" in out and "kazma-searxng (search)" in out and "shipx" not in out
    _nothing_changed(engines)
    assert _stored() == ""


# ── a run that died half way ─────────────────────────────────────────────


class _Died(BaseException):
    """The process ending (killed, the power gone): nothing after it runs."""


def _dies_at_the_switch(monkeypatch) -> None:
    real = em._write_engine

    def write(value: str) -> None:
        if value == "kazma":
            raise _Died
        real(value)

    monkeypatch.setattr(em, "_write_engine", write)


def test_a_run_that_died_half_way_is_rolled_back_then_moved(engines, tmp_path, monkeypatch):
    real = em._write_engine
    _dies_at_the_switch(monkeypatch)
    with pytest.raises(_Died):
        _move(engines, tmp_path)
    journal = em.Journal(tmp_path / "setup_record.json")
    assert journal.state == "moving" and "kazma-neo4j" in journal.created("containers")
    assert engines.read(DESKTOP)["containers"]["kazma-neo4j"]["State"]["Running"] is False
    monkeypatch.setattr(em, "_write_engine", real)
    again = _Hold(engines)
    again.leftover = True  # the dead run's pause: Kazma is still stopped
    assert _move(engines, tmp_path, hold=again) == 0
    engine = engines.read(ENGINE)
    assert sorted(engine["containers"]) == ["kazma-neo4j", "kazma-searxng"]
    assert engines.volume(ENGINE, "kazma-neo4j-data") == engines.volume(DESKTOP, "kazma-neo4j-data")
    assert _stored() == "kazma" and not again.held
    assert engines.calls().count(["MARK", "hold"]) == 1, "the second run took the dead run's pause over"


def test_control_without_the_journal_nothing_puts_docker_desktop_back(engines, tmp_path, monkeypatch):
    _dies_at_the_switch(monkeypatch)
    with pytest.raises(_Died):
        _move(engines, tmp_path)
    (tmp_path / "setup_record.json").unlink()  # no journal: nothing says what the dead run changed
    steps = em.MoveSteps(hold=_Hold(engines), say=print, answering=lambda p: True)
    assert em.recover(_docker(), em.Journal(tmp_path / "setup_record.json"), steps) == "none"
    stuck = engines.read(DESKTOP)["containers"]["kazma-neo4j"]
    assert stuck["State"]["Running"] is False and stuck["HostConfig"]["RestartPolicy"]["Name"] == "no"


def test_ctrl_c_half_way_puts_everything_back_before_it_stops(engines, tmp_path, monkeypatch):
    def interrupted(value: str) -> None:
        if value == "kazma":
            raise KeyboardInterrupt
        em_real(value)

    em_real = em._write_engine
    monkeypatch.setattr(em, "_write_engine", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _move(engines, tmp_path)
    _back_on_desktop(engines)
    assert engines.read(ENGINE)["containers"] == {}
    assert em.Journal(tmp_path / "setup_record.json").state == "rolled_back"


# ── pieces ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("source", "fixed", "expected"), [
    (r"G:\GitHubRepos\kazma\deploy\searxng", True, "/g/GitHubRepos/kazma/deploy/searxng"),
    ("C:/Users/x", True, "/c/Users/x"),
    (r"D:\\", True, "/d"),
    ("/run/desktop/mnt/host/c/Users/x", True, "/c/Users/x"),
    ("/host_mnt/e/data", True, "/e/data"),
    (r"E:\usb", False, None),
    (r"\\server\share\x", True, None),
    ("/var/run/docker.sock", True, None),
])
def test_a_bind_source_as_kazmas_engine_mounts_it(source, fixed, expected):
    assert em._engine_bind_source(source, fixed=lambda drive: fixed) == expected


def _archive(order: list[tuple[str, str]]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, kind in order:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = 4
                tar.addfile(info, io.BytesIO(b"data"))
            else:
                info.type, info.linkname = tarfile.LNKTYPE, kind
                tar.addfile(info)
    buf.seek(0)
    return buf


def test_a_hard_link_pair_is_the_same_whichever_comes_first():
    one = em._manifest_of(_archive([("./a", "file"), ("./b", "./a")]))
    two = em._manifest_of(_archive([("./b", "file"), ("./a", "./b")]))
    assert one == two


def test_setup_engine_alone_keeps_a_docker_desktop_install_on_docker_desktop(engines, tmp_path, monkeypatch, capsys):
    from kazma_cli import setup
    from kazma_core import container_engine

    monkeypatch.setattr("kazma_core.postgres_service._record_path", lambda: tmp_path / "setup_record.json")
    monkeypatch.setattr(ces, "check", lambda run=None: ces.EngineCheck(False, "no", ["task"], ""))
    monkeypatch.setattr(ces, "preflight", lambda run=None: "")

    def record(action, **kw):
        if action == "engine:record":
            (tmp_path / "setup_record.json").write_text(json.dumps({"container_engine": {"kind": "kazma"}}),
                                                        encoding="utf-8")
        return True, ""

    monkeypatch.setattr(ces, "run_action", record)
    code = setup._setup_engine(_options(move_from_desktop=False), platform=_Windows(), admin=True)
    assert code == 0
    assert container_engine.engine_setting() == "desktop", "its next start looks where its containers are"
    assert "--move-from-desktop" in capsys.readouterr().out


def test_control_without_writing_desktop_down_the_record_switches_kazma(engines, tmp_path, monkeypatch):
    from kazma_cli import setup
    from kazma_core import container_engine

    monkeypatch.setattr("kazma_core.postgres_service._record_path", lambda: tmp_path / "setup_record.json")
    monkeypatch.setattr(ces, "check", lambda run=None: ces.EngineCheck(False, "no", ["task"], ""))
    monkeypatch.setattr(ces, "preflight", lambda run=None: "")
    monkeypatch.setattr(ces, "run_action", lambda action, **kw: (
        (tmp_path / "setup_record.json").write_text(json.dumps({"container_engine": {"kind": "kazma"}}),
                                                    encoding="utf-8"), (True, ""))[1])
    monkeypatch.setattr(setup, "_keep_desktop_choice", lambda: False)
    assert setup._setup_engine(_options(move_from_desktop=False), platform=_Windows(), admin=True) == 0
    assert container_engine.engine_setting() == "kazma", "the empty engine would have been Kazma's choice"


def _unpacked_install(tmp_path: Path) -> Path:
    """A source folder unpacked from a zip: the guard script, and no .git."""
    root = tmp_path / "kazma"
    (root / "scripts" / "service").mkdir(parents=True)
    guard = Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"
    (root / "scripts" / "service" / "kazma_guard.py").write_text(guard.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "kazma-cli" / "kazma_cli").mkdir(parents=True)
    return root


def test_the_hold_finds_the_guard_of_an_install_with_no_git(tmp_path, monkeypatch):
    """The drill VM's install was unpacked from a zip (2026-11-13): looking for
    the guard by ``.git`` alone, the move said "no guard supervises it" over a
    running guard and refused."""
    from kazma_cli import setup
    from kazma_cli import update

    root = _unpacked_install(tmp_path)
    assert setup._checkout_root(root / "kazma-cli" / "kazma_cli" / "setup.py") == root
    monkeypatch.setattr(update, "_find_git_root", lambda: None)
    monkeypatch.setattr(update, "_is_server_running", lambda port: True)
    monkeypatch.setattr(setup, "_checkout_root", lambda start=None: root)
    assert setup._MoveHold(setup._ENGINE_MOVE_REASON).guard is not None


def test_control_found_by_git_alone_a_zip_install_has_no_guard(tmp_path, monkeypatch):
    from kazma_cli import setup
    from kazma_cli import update

    _unpacked_install(tmp_path)
    monkeypatch.setattr(update, "_find_git_root", lambda: None)
    monkeypatch.setattr(update, "_is_server_running", lambda port: True)
    monkeypatch.setattr(setup, "_checkout_root", lambda start=None: None)
    hold = setup._MoveHold(setup._ENGINE_MOVE_REASON)
    assert hold.guard is None
    assert "no guard supervises it" in (hold.refusal() or "")


class _Probed(tuple):
    answered = True
    needs_setup: tuple[str, ...] = ()


@pytest.mark.parametrize("healthy, needs, ready", [
    (True, (), True),
    (False, ("llm_provider",), True),   # up, waiting for a provider key: up, as the guard's resume says
    (False, (), False),
])
def test_a_kazma_waiting_for_its_setup_counts_as_up_after_a_move(healthy, needs, ready, monkeypatch):
    from kazma_cli import setup
    from kazma_cli import update

    answer = _Probed((healthy, "detail"))
    answer.needs_setup = needs

    class _Guard:
        def health_url(self, of_running_guard=False):
            return "http://127.0.0.1:9/health/ready"

        def probe(self, url, timeout):
            return answer

    monkeypatch.setattr(update, "_load_guard", lambda root: _Guard())
    monkeypatch.setattr(update, "_is_server_running", lambda port: True)
    assert setup._MoveHold(setup._ENGINE_MOVE_REASON).ready() is ready
