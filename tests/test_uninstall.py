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
    return un.Machine(os_name=os_name, root=root, data=data, home=home, user_home=tmp_path / "user",
                      python="python", run=runner, admin=admin, record=record or {},
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
                        lambda svc, root, docker, run=None: cs.ImageState(svc, "current" if svc.part == "private_search"
                                                                          else "absent"))
    machine = _machine(tmp_path)
    (item,) = [i for i in un.build_plan(machine) if i.how == "remove" and "rm" in i.argv]
    assert item.argv[1:3] == ("compose", "-f") and "rm" in item.argv and "--volumes" not in item.argv
    (item2,) = [i for i in un.build_plan(machine, remove_data=True) if i.argv and "rm" in i.argv]
    assert "--volumes" in item2.argv


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
