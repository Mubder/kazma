"""``kazma uninstall`` (2026-11-14, REMAINING 2.5).

A machine of tmp folders and a recorded command runner: the plan, what runs,
what is kept, and what happens to data -- kept by default, moved aside with
``--remove-data``, deleted only with ``--purge``. Each rule has its control.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_cli import uninstall as un


class Runner:
    """Answers ``schtasks /Query`` as told (the service task's XML names the
    folder it starts, in UTF-16 as schtasks writes it); records every other
    command."""

    def __init__(self, tasks: tuple[str, ...] = ("KazmaAgent", "KazmaFreshclam"), folder: str | None = None) -> None:
        self.tasks = tasks
        self.folder = folder
        self.ran: list[list[str]] = []

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[Any]:
        argv = list(argv)
        if argv[:2] == ["schtasks", "/Query"]:
            there = argv[3] in self.tasks
            if "/XML" in argv:
                xml = ('<?xml version="1.0" encoding="UTF-16"?><Task><Actions><Exec><Command>python.exe</Command>'
                       f"<WorkingDirectory>{self.folder}</WorkingDirectory></Exec></Actions></Task>")
                return subprocess.CompletedProcess(argv, 0 if there else 1, xml.encode("utf-16") if there else b"", b"")
            return subprocess.CompletedProcess(argv, 0 if there else 1, "", "")
        self.ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")


def _machine(tmp_path: Path, *, os_name: str = "windows", admin: bool = True, record: dict | None = None,
             runner: Runner | None = None) -> un.Machine:
    root = tmp_path / "kazma"
    (root / "scripts" / "service").mkdir(parents=True)
    (root / ".venv").mkdir()
    (root / ".venv" / "python.exe").write_text("x", encoding="utf-8")
    data = root / "kazma-data"
    data.mkdir()
    (data / "memory_state.db").write_text("memories", encoding="utf-8")
    home = tmp_path / "user" / ".kazma"
    home.mkdir(parents=True)
    (home / "install.log").write_text("log", encoding="utf-8")
    runner = runner or Runner()
    if runner.folder is None:
        runner.folder = str(root)
    if os_name == "linux":
        unit = tmp_path / "user" / ".config" / "systemd" / "user" / "kazma.service"
        unit.parent.mkdir(parents=True)
        guard = root / "scripts" / "service" / "kazma_guard.py"
        unit.write_text(f"[Service]\nExecStart={root / '.venv' / 'bin' / 'python'} {guard}\n", encoding="utf-8")
    # A program that outlives the uninstall (cmd.exe's delayed delete): recorded,
    # never started -- on Linux CI there is no cmd.exe, and the real one would
    # delete this test's folder behind it.
    def spawn(argv, **_kw):
        runner.ran.append(["spawn", *argv])

    return un.Machine(os_name=os_name, root=root, data=data, home=home, user_home=tmp_path / "user",
                      python="python", run=runner, spawn=spawn, admin=admin, record=record or {},
                      now=lambda: 1763100000.0)


def test_another_installs_service_and_the_computers_shared_pieces_are_left_alone(tmp_path) -> None:
    """Live 2026-11-14: a dry run from a second checkout found the KazmaAgent
    task -- the live install's -- and planned to end and delete it. The task's
    name is the same for every install; the folder it starts says whose it is."""
    other = str(tmp_path / "someone-else" / "kazma")
    machine = _machine(tmp_path, runner=Runner(folder=other), record={"container_engine": {"installed": True}})
    plan = un.build_plan(machine)
    kept = [i for i in plan if i.how == "keep"]
    assert any("KazmaAgent" in i.what and "another Kazma" in i.why for i in kept)
    assert any("container engine" in i.what for i in kept)
    removed = [i.what for i in plan if i.how == "remove"]
    assert not [w for w in removed if w.startswith(("Kazma's service", "Stop the guard", "ClamAV"))], removed
    assert un._run_plan(plan, machine, say=lambda _s: None) == un.EXIT_DONE
    assert not any(a[:1] == ["schtasks"] for a in machine.run.ran)
    # This folder's own guard is still stopped (it is this install's).
    assert machine.run.ran[0][2:4] == ["--pause", "--stop"]


def test_the_folder_is_read_from_the_service_the_way_each_system_keeps_it(tmp_path) -> None:
    machine = _machine(tmp_path)
    assert un._same_folder(un._service_folder(machine), machine.root)
    linux = _machine(tmp_path / "l", os_name="linux")
    assert un._same_folder(un._service_folder(linux), linux.root)
    none = _machine(tmp_path / "n", runner=Runner(tasks=()))
    assert un._service_folder(none) is None


@pytest.fixture(autouse=True)
def _no_containers(monkeypatch):
    from kazma_core import docker_cli

    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda *a, **k: None)


def test_the_default_plan_removes_kazma_and_keeps_every_byte_of_data(tmp_path) -> None:
    machine = _machine(tmp_path, record={"postgres": {"service": "postgresql-x64-18"}})
    plan = un.build_plan(machine)
    removed = [i.what for i in plan if i.how == "remove"]
    assert any("Stop Kazma" in w for w in removed)
    assert any("KazmaAgent" in w for w in removed) and any("service" in w for w in removed)
    assert any("ClamAV" in w for w in removed) and any("Python environment" in w for w in removed)
    assert not [i for i in plan if i.how in ("move", "delete")]
    kept = {i.what for i in plan if i.how == "keep"}
    assert any("Kazma's data" in k for k in kept) and any("PostgreSQL" in k for k in kept)
    assert any(i.why.startswith("other programs may use it; to remove: winget uninstall") for i in plan)

    code = un._run_plan(plan, machine, say=lambda _s: None)
    assert code == un.EXIT_DONE
    assert (machine.data / "memory_state.db").read_text(encoding="utf-8") == "memories"
    assert (machine.home / "install.log").is_file()
    ran = machine.run.ran
    assert ran[0][1].endswith("kazma_guard.py") and ran[0][2:4] == ["--pause", "--stop"]
    assert ["schtasks", "/End", "/TN", "KazmaAgent"] in ran
    assert any(a[-1] == "--uninstall" and a[1].endswith("install_service.py") for a in ran)
    assert ["schtasks", "/Delete", "/TN", "KazmaFreshclam", "/F"] in ran


def test_remove_data_moves_it_aside_and_purge_alone_deletes(tmp_path) -> None:
    machine = _machine(tmp_path)
    plan = un.build_plan(machine, remove_data=True)
    assert {i.how for i in plan if "Kazma's data" in i.what or "Kazma's home" in i.what} == {"move"}
    assert un._run_plan(plan, machine, say=lambda _s: None) == un.EXIT_DONE
    aside = tmp_path / "user" / "Kazma-uninstalled-" / "x"
    moved = next((tmp_path / "user").glob("Kazma-uninstalled-*"))
    assert (moved / "kazma-data" / "memory_state.db").read_text(encoding="utf-8") == "memories"
    assert (moved / ".kazma" / "install.log").is_file() and not machine.data.exists()
    assert not aside.exists()

    # --purge: deleted for good (the control: only with it is anything deleted).
    machine2 = _machine(tmp_path / "second")
    plan2 = un.build_plan(machine2, remove_data=True, purge=True)
    assert {i.how for i in plan2 if "Kazma's data" in i.what} == {"delete"}
    assert un._run_plan(plan2, machine2, say=lambda _s: None) == un.EXIT_DONE
    assert not machine2.data.exists() and not list((tmp_path / "second" / "user").glob("Kazma-uninstalled-*"))


def test_without_an_administrator_the_admin_steps_are_refused_and_said(tmp_path) -> None:
    machine = _machine(tmp_path, admin=False)
    said: list[str] = []
    assert un._run_plan(un.build_plan(machine), machine, say=said.append) == un.EXIT_NOT_DONE
    assert any("NOT DONE" in s and "administrator" in s for s in said)
    assert not any(a[:2] == ["schtasks", "/Delete"] for a in machine.run.ran)


def test_nothing_runs_before_the_plan_is_accepted(tmp_path) -> None:
    machine = _machine(tmp_path)
    said: list[str] = []
    assert un.main([], machine=machine, ask=lambda _q: "no", say=said.append) == un.EXIT_CANCELLED
    assert machine.run.ran == [] and "Stopped: nothing was changed." in said
    assert un.main(["--dry-run"], machine=machine, say=said.append) == un.EXIT_DONE and machine.run.ran == []
    assert un.main(["--purge"], machine=machine, say=said.append) == un.EXIT_CANCELLED
    # Purging asks for the word that says so.
    assert un.main(["--remove-data", "--purge"], machine=machine, ask=lambda _q: "uninstall",
                   say=said.append) == un.EXIT_CANCELLED
    assert machine.data.exists()
    assert un.main([], machine=machine, ask=lambda _q: "uninstall", say=said.append) == un.EXIT_DONE


def test_a_task_that_is_not_there_is_not_in_the_plan(tmp_path) -> None:
    machine = _machine(tmp_path, runner=Runner(tasks=()))
    removed = " ".join(i.what for i in un.build_plan(machine) if i.how == "remove")
    assert "KazmaAgent" not in removed and "ClamAV" not in removed


def test_linux_removes_the_user_unit_and_the_environment_now(tmp_path) -> None:
    machine = _machine(tmp_path, os_name="linux")
    plan = un.build_plan(machine)
    env = next(i for i in plan if "Python environment" in i.what)
    assert env.action == "remove_tree"
    assert un._run_plan(plan, machine, say=lambda _s: None) == un.EXIT_DONE
    assert not (machine.root / ".venv").exists()
    assert any("sudo apt remove" in i.why for i in plan)


def test_containers_setup_made_are_removed_their_volumes_only_with_remove_data(tmp_path, monkeypatch) -> None:
    from kazma_core import container_services as cs
    from kazma_core import docker_cli

    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda *a, **k: "docker")
    monkeypatch.setattr(cs, "_image_state",
                        lambda svc, root, docker, run=None: cs.ImageState(
                            svc, "current" if svc.part == "private_search" else "absent",
                            volumes=("kazma_searxng_cache",)))
    machine = _machine(tmp_path)
    plan = un.build_plan(machine)
    (item,) = [i for i in plan if i.how == "remove" and "rm" in i.argv]
    assert item.argv[1:3] == ("compose", "-f") and "rm" in item.argv
    assert not [i for i in plan if i.argv[1:3] == ("volume", "rm")], "its data is kept unless asked"
    with_data = un.build_plan(machine, remove_data=True)
    removes = [i for i in with_data if i.argv and ("rm" in i.argv)]
    assert removes[0].argv[1:3] == ("compose", "-f")
    assert removes[1].argv == ("docker", "volume", "rm", "kazma_searxng_cache"), "its named volumes, after it"


def test_the_named_volumes_come_from_dockers_own_answer(tmp_path) -> None:
    """compose rm --volumes removes anonymous volumes only: Neo4j's data is a
    named volume, read from the container's mounts (the old flag as control)."""
    import json
    import subprocess

    from kazma_core import container_services as cs

    svc = next(s for s in cs.SERVICES if s.part == "graph_neo4j")
    root = tmp_path
    (root / svc.compose_file).parent.mkdir(parents=True, exist_ok=True)
    lines = ["services:", "  kazma-neo4j:", "    image: neo4j:5.26@sha256:" + "a" * 64, ""]
    (root / svc.compose_file).write_text(chr(10).join(lines), encoding="utf-8")
    pinned = cs._pinned_image(svc, root)
    row = {"Config": {"Image": pinned, "Labels": {
        "com.docker.compose.project": cs._compose_project(svc, root),
        "com.docker.compose.service": svc.service,
        "com.docker.compose.project.config_files": str(root / svc.compose_file)}},
        "Mounts": [{"Type": "volume", "Name": "kazma-graph_kazma-neo4j-data"},
                   {"Type": "bind", "Source": "/x"}]}

    def run(argv, **_kw):
        return subprocess.CompletedProcess(argv, 0, json.dumps([row]), "")

    state = cs._image_state(svc, root, "docker", run=run)
    assert state.state == "current" and state.volumes == ("kazma-graph_kazma-neo4j-data",)


def test_kazmas_engine_is_removed_through_its_own_removal(tmp_path, monkeypatch) -> None:
    from kazma_core import container_engine_setup

    calls: list[Any] = []
    monkeypatch.setattr(container_engine_setup, "remove", lambda run, *a, **k: calls.append(run) or (False, "busy"))
    machine = _machine(tmp_path, record={"container_engine": {"installed": True}})
    plan = un.build_plan(machine)
    said: list[str] = []
    assert un._run_plan(plan, machine, say=said.append) == un.EXIT_NOT_DONE
    assert calls and any("container engine" in s and "busy" in s for s in said)


def test_the_cli_names_the_command() -> None:
    from pathlib import Path as _P

    main = (_P(un.__file__).with_name("main.py")).read_text(encoding="utf-8")
    assert 'elif cmd == "uninstall":' in main and "uninstall  Take Kazma off this computer" in main


# -- Kazma's database (REMAINING 2.5): backed up, then dropped, only when asked --


class _DbRun:
    """psql and pg_dump as the uninstall calls them; the dump writes bytes."""

    def __init__(self, dump_code: int = 0) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.dump_code = dump_code

    def __call__(self, argv, **kw):
        import subprocess

        self.calls.append((list(argv), kw))
        if any(str(a).endswith(("pg_dump", "pg_dump.exe")) for a in argv):
            if self.dump_code == 0:
                kw["stdout"].write(b"PGDMP-fake")
            return subprocess.CompletedProcess(argv, self.dump_code, None, b"pg_dump: connection refused")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _db_machine(tmp_path, run):
    machine = _machine(tmp_path)
    machine.record = {"postgres": {"os": "windows", "manager": "winget", "service": "postgresql-x64-18",
                                   "port": 5432, "bin_dir": "C:/PG/18/bin", "major": 18}}
    machine.run = run
    machine.home.mkdir(parents=True, exist_ok=True)
    (machine.home / "postgres_superuser.pass").write_text("s3cret-superuser", encoding="utf-8")
    return machine


def test_the_database_goes_only_when_asked_and_before_the_data_moves(tmp_path) -> None:
    machine = _db_machine(tmp_path, _DbRun())
    assert not [i for i in un.build_plan(machine) if i.action == "database"], "kept unless asked"
    plan = un.build_plan(machine, remove_data=True)
    actions = [i.action for i in plan if i.how != "keep"]
    assert "database" in actions and actions.index("database") < actions.index("move")


def test_the_database_is_backed_up_then_dropped(tmp_path) -> None:
    run = _DbRun()
    machine = _db_machine(tmp_path, run)
    ok, said = un._database(machine)
    assert ok, said
    dump_argv, dump_kw = run.calls[0]
    assert dump_argv[0].endswith("pg_dump.exe") and "-Fc" in dump_argv and dump_argv[-1] == "kazma"
    dumps = list(machine.data.glob("kazma-database-*.dump"))
    assert len(dumps) == 1 and dumps[0].read_bytes() == b"PGDMP-fake", "the backup lands in the data folder"
    drop_argv, drop_kw = run.calls[1]
    assert "DROP DATABASE IF EXISTS kazma;" in drop_kw["input"] and "DROP ROLE IF EXISTS kazma_app;" in drop_kw["input"]
    for argv, kw in run.calls:
        assert "s3cret-superuser" not in " ".join(argv), "the password never on a command line"
        assert kw["env"]["PGPASSWORD"] == "s3cret-superuser"


def test_a_failed_backup_drops_nothing(tmp_path) -> None:
    run = _DbRun(dump_code=1)
    machine = _db_machine(tmp_path, run)
    ok, said = un._database(machine)
    assert not ok and "nothing was dropped" in said
    assert len(run.calls) == 1, "no DROP after a failed backup"
    assert not list(machine.data.glob("kazma-database-*.dump"))
