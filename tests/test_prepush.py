"""scripts/prepush.py runs what CI would, and refuses to start when it may not.

The parts that decide what runs: the tests near a change, the gates that read
the source, the commands ci.yml gates on, the snapshot a container gets, and
the refusals. Each has the case it must not get wrong beside the case it must.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("prepush_under_test", REPO / "scripts" / "prepush.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


prepush = _load()


# ── the tests near a change ──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "from kazma_core.memory.recall import recall",
    "import kazma_core.memory.recall as r",
    "from kazma_core.memory import forget, recall",
    "from kazma_core.memory import (\n    forget,\n    recall,\n)",
    'monkeypatch.setattr("kazma_core.memory.recall.x", 1)',
    'SRC = ROOT / "kazma-core" / "kazma_core" / "memory" / "recall.py"',
    'SRC = "kazma-core/kazma_core/memory/recall.py"',
])
def test_a_test_that_imports_names_or_reads_a_changed_module_is_near_it(text):
    assert prepush.near_tests(["kazma-core/kazma_core/memory/recall.py"], {"tests/test_a.py": text}) == ["tests/test_a.py"]


@pytest.mark.parametrize("text", [
    "from kazma_core.memory.recall_records import x",
    "from kazma_core.memory import forget\nrecall = 1",
    "from kazma_core.swarm.recall_x import y",
    "# what a user may recall",
    'SRC = "kazma_core/memory/query_recall.py"',
])
def test_control_a_test_that_does_not_is_not_near_it(text):
    assert prepush.near_tests(["kazma-core/kazma_core/memory/recall.py"], {"tests/test_a.py": text}) == []


@pytest.mark.parametrize("changed,text", [
    ("kazma-core/kazma_core/memory/__init__.py", "from kazma_core.memory import recall"),
    ("kazma-ui/kazma_ui/static/js/chat.js", 'JS = STATIC / "js" / "chat.js"'),
    (".github/workflows/ci.yml", 'CI = REPO / ".github" / "workflows" / "ci.yml"'),
    ("scripts/fast_test.py", "import fast_test"),
    ("scripts/ci_shards.py", 'ci_shards = _load("ci_shards")'),
    ("tests/_no_models.py", "from tests._no_models import keep_models_out"),
    ("tests/e2e/_unified_turn_harness.py", "from tests.e2e._unified_turn_harness import boot"),
    ("uv.lock", 'LOCK = REPO / "uv.lock"'),
    ("kazma-ui/kazma_ui/gone.py", "from kazma_ui.gone import x"),  # a deleted module's tests too
])
def test_a_package_a_script_a_helper_and_a_file_that_is_not_python(changed, text):
    assert prepush.near_tests([changed], {"tests/test_a.py": text}) == ["tests/test_a.py"]


def test_control_names_that_only_look_alike():
    texts = {"tests/test_a.py": 'x = "unified_chat.js"', "tests/test_b.py": "from kazma_core.memory.recall import x"}
    assert prepush.near_tests(["kazma-ui/kazma_ui/static/js/chat.js"], texts) == []
    assert prepush.near_tests(["kazma-core/kazma_core/memory/__init__.py"], texts) == []


def test_the_nearest_come_first_and_the_budget_says_what_it_left():
    texts = {"tests/test_own.py": "", "tests/test_few.py": "kazma_core.rare",
             **{f"tests/test_many_{i}.py": "kazma_core.common" for i in range(5)}}
    near = prepush.near_tests(["tests/test_own.py", "kazma-core/kazma_core/rare.py", "kazma-core/kazma_core/common.py"], texts)
    assert near[:2] == ["tests/test_own.py", "tests/test_few.py"] and len(near) == 7
    kept, left = prepush.cut_to_budget(near, dict.fromkeys(near, 10.0), 35)
    assert (len(kept), len(left)) == (3, 4) and kept + left == near
    assert prepush.cut_to_budget(near[:1], {near[0]: 999.0}, 35) == (near[:1], [])  # never nothing


def test_every_gate_that_lists_the_source_is_found_and_the_plan_holds_them():
    plan = prepush.plan_files("HEAD", 40)
    assert {"tests/test_ci_shards.py", "tests/test_static_gates.py", "tests/test_debt_ratchet.py"} <= set(plan["gates"])
    assert len(plan["gates"]) > 100
    assert set(plan["browser"]) == {p.relative_to(REPO).as_posix() for p in (REPO / "tests" / "e2e").glob("test_*.py")}
    assert not any("e2e" in n for n in plan["gates"] + plan["near"])


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args], cwd=repo, check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("secret.env\n")
    for name in ("kept.py", "edited.py", "removed.py"):
        (repo / name).write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    (repo / "committed.py").write_text("y = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "two")
    (repo / "edited.py").write_text("x = 2\n")
    (repo / "removed.py").unlink()
    (repo / "new.py").write_text("z = 1\n")
    (repo / "secret.env").write_text("KEY=demo\n")
    return repo


def test_the_change_is_the_commits_the_working_tree_and_what_git_does_not_track(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(prepush, "REPO", repo)
    assert prepush.changed_files("HEAD~1") == ["committed.py", "edited.py", "new.py", "removed.py"]
    assert prepush.untracked_files() == ["new.py"]


def test_the_container_gets_the_tree_as_it_is_with_its_history_and_nothing_ignored(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(prepush, "REPO", repo)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, check=True).stdout
    bundle, ref = prepush.snapshot_bundle(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    for args in (["fetch", "-q", str(bundle), ref], ["reset", "-q", "--hard", "FETCH_HEAD"]):
        subprocess.run(["git", *args], cwd=clone, check=True, capture_output=True)
    files = {p.name for p in clone.iterdir() if p.is_file()}
    assert files == {".gitignore", "kept.py", "edited.py", "committed.py", "new.py"}
    assert (clone / "edited.py").read_text().strip() == "x = 2"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=clone, capture_output=True, text=True, check=True).stdout
    assert log.split() == ["prepush", "two", "one"]
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, check=True).stdout
    refs = subprocess.run(["git", "for-each-ref"], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert after == before and "prepush" not in refs


# ── what CI gates on ─────────────────────────────────────────────────────

def test_ruff_and_bandit_run_as_ci_gates_them():
    ruff = prepush.ci_commands("lint", "ruff")
    assert len(ruff) == 1 and "--select" in ruff[0] and "--exit-zero" not in ruff[0]
    bandit = prepush.ci_commands("security", "bandit")
    assert [c for c in bandit if "-lll" in c] and [c for c in bandit if "B613" in c]
    assert all({"tests", "scripts"} <= set(c) and "json" not in c for c in bandit)
    # Control: a command that cannot fail is not a gate, and a job without one is said.
    text = "jobs:\n  lint:\n    steps:\n      - run: ruff check a --exit-zero\n      - run: ruff check b || true\n"
    assert prepush.ci_commands("lint", "ruff", text) == []
    part = prepush.Part("ruff")
    prepush.run_commands(part, [])
    assert not part.ok and part.failures


def test_the_container_has_the_tests_jobs_set():
    """The same extras and groups as ci.yml's Tests shards, and no psycopg."""
    import yaml

    steps = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]["tests"]["steps"]
    install = next(s["with"] for s in steps if s.get("uses") == "./.github/actions/install-locked")
    source = (REPO / "scripts" / "prepush.py").read_text(encoding="utf-8")
    for extra in str(install["extras"]).split():
        assert f'"--extra", "{extra}"' in source
    for group in str(install["groups"]).split():
        assert f'"--group", "{group}"' in source
    assert "postgres" not in str(install["extras"]).split()
    dockerfile = (REPO / "deploy" / "prepush" / "Dockerfile").read_text(encoding="utf-8")
    apt = next(s["run"] for s in steps if "apt-get install" in str(s.get("run")))
    for package in ("fonts-noto-core", "tesseract-ocr-ara", "tesseract-ocr-eng", "ripgrep", "libreoffice-writer"):
        assert package in apt and package in dockerfile, package
    assert "FROM python:3.11" in dockerfile and "USER runner" in dockerfile


def test_one_image_per_lock_and_dockerfile():
    a = prepush.image_tag("FROM x", "one==1")
    assert a == prepush.image_tag("FROM x", "one==1") and a.startswith("kazma-prepush:")
    assert len({a, prepush.image_tag("FROM x", "one==2"), prepush.image_tag("FROM y", "one==1")}) == 3


def test_docker_desktop_first_and_no_docker_is_a_failure_not_a_skip(monkeypatch, tmp_path):
    asked: list[str] = []

    def docker(answers):
        def run(cmd, **kw):
            asked.append(cmd[2])
            ok = cmd[2] in answers
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, "linux\n" if ok else "", "")
        return run

    monkeypatch.setattr(prepush, "docker_exe", lambda: "docker")
    # The calls as the Windows CLI makes them (the wsl.exe route is docker_cli's, tested there).
    monkeypatch.setattr(prepush, "docker_route", lambda context: (lambda args: ["docker", "--context", context, *args]))
    monkeypatch.setattr(prepush.subprocess, "run", docker({"desktop-linux", "kazma-engine"}))
    assert prepush.docker_context() == "desktop-linux" and asked == ["desktop-linux"]
    monkeypatch.setattr(prepush.subprocess, "run", docker({"kazma-engine"}))
    assert prepush.docker_context() == "kazma-engine"
    monkeypatch.setattr(prepush.subprocess, "run", docker(set()))
    part = prepush.Part("linux")
    prepush.run_linux(part, ["tests/test_a.py"], 2)
    assert not part.ok and "no Docker answers" in part.failures


# ── the refusals ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmdline,whole", [
    (["python.exe", "scripts/fast_test.py"], True),
    (["C:/x/.venv/Scripts/python.exe", r"G:\repo\scripts\fast_test.py", "--chunks", "8"], True),
    (["python", "scripts/fast_test.py", "--shard", "1/6"], True),
    (["python", "scripts/prepush.py"], True),
    (["python", "scripts/fast_test.py", "--files-from", "near.txt"], False),
    (["python", "scripts/prepush.py", "--list"], False),
    (["python", "-m", "pytest", "tests/test_fast_test_runner.py"], False),
    (["notepad.exe", "scripts/fast_test.py"], False),
    (["bash", "-c", "python scripts/fast_test.py"], False),  # the shell; its python is found itself
    ([], False),
])
def test_what_counts_as_a_run_nothing_may_start_over(cmdline, whole):
    assert bool(prepush.is_whole_run(cmdline)) is whole


def test_a_running_suite_is_found_and_this_runs_own_is_not(tmp_path):
    stand_in = tmp_path / "fast_test.py"
    stand_in.write_text("import time\nprint('up', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    mine = subprocess.Popen([sys.executable, str(stand_in)], stdout=subprocess.PIPE)
    try:
        mine.stdout.readline()
        assert not any(str(mine.pid) in line for line in prepush.other_runs())  # this process's own child
        probe = subprocess.run([sys.executable, "-c", (
            "import importlib.util,sys;"
            f"s=importlib.util.spec_from_file_location('p',{str(REPO / 'scripts' / 'prepush.py')!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.other_runs())")],
            capture_output=True, text=True, timeout=120, cwd=REPO)
        assert str(mine.pid) in probe.stdout, probe.stdout + probe.stderr
    finally:
        mine.kill()
        mine.wait(30)


def test_the_lock_is_held_once(tmp_path, monkeypatch):
    monkeypatch.setattr(prepush, "git_common_dir", lambda: tmp_path)
    with prepush.only_one() as first:
        assert first is True
        out = subprocess.run([sys.executable, "-c", (
            "import importlib.util,pathlib;"
            f"s=importlib.util.spec_from_file_location('p',{str(REPO / 'scripts' / 'prepush.py')!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            f"m.git_common_dir=lambda: pathlib.Path({str(tmp_path)!r})\n"
            "with m.only_one() as got: print(got)")], capture_output=True, text=True, timeout=120, cwd=REPO)
        assert out.stdout.strip() == "False", out.stderr
    with prepush.only_one() as again:
        assert again is True


def test_another_checkouts_code_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(str(REPO / name) for name in prepush.PACKAGES))
    assert prepush.imports_this_checkout(Path(sys.executable)) == ""
    for name in ("kazma_core", "kazma_ui"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    assert "not this checkout" in prepush.imports_this_checkout(Path(sys.executable))


def test_less_free_memory_is_fewer_at_a_time():
    every = list(prepush.PARTS)
    full = prepush.sized(every, False, 64)
    assert full == {"tests": prepush.TEST_PROCESSES, "browser": prepush.BROWSER_PROCESSES, "linux": prepush.LINUX_PROCESSES}
    half = prepush.sized(every, False, 14)
    assert all(2 <= half[p] < full[p] for p in full)
    assert prepush.sized(every, False, 1) == {"tests": 2, "browser": 2, "linux": 2}
    assert prepush.sized(["tests"], False, 14)["tests"] == prepush.TEST_PROCESSES  # the others' share is its own
    assert prepush.sized(["linux"], True, 64)["linux"] == prepush.LINUX_WHOLE_PROCESSES


def test_the_gates_run_on_linux_with_the_near_files_and_here_when_asked(monkeypatch, tmp_path):
    seen: dict[str, list[str]] = {}

    def note(name):
        def run(part, files, *_):
            seen[name] = files
            part.ok = True
        return run

    runs = {name: note(name) for name in ("tests", "linux", "browser")}
    assert _run_main(monkeypatch, tmp_path, **runs) == 0
    assert seen == {"tests": ["tests/test_a.py"], "linux": ["tests/test_a.py", "tests/test_b.py"],
                    "browser": ["tests/e2e/test_c.py"]}


# ── a part that fails is said ────────────────────────────────────────────

def test_parts_are_chosen_by_name():
    assert prepush.choose_parts(None, None) == list(prepush.PARTS)
    assert prepush.choose_parts("linux,tests", None) == ["tests", "linux"]
    assert "browser" not in prepush.choose_parts(None, "browser")
    with pytest.raises(SystemExit):
        prepush.choose_parts("browsers", None)


def test_the_failing_tests_and_their_tracebacks_are_what_is_said():
    output = ("[fast-test] chunk 00: OK 3p/1f\n" * 50 + "\n[fast-test] TOTALS in 9s: 1 failed, 3 passed\n\n"
              "[fast-test] 1 failing tests:\n  FAILED tests/test_a.py::test_x\n\n[fast-test] failure tracebacks\nassert 1 == 2\n")
    said = prepush.suite_failures(output)
    assert said.startswith("[fast-test] 1 failing tests:") and "assert 1 == 2" in said and "chunk 00" not in said
    assert prepush.suite_totals(output) == "1 failed, 3 passed"
    assert "suite ran no tests" in prepush.suite_failures("x\n[fast-test] suite ran no tests (passed=0)\n")


def _run_main(monkeypatch, tmp_path, **parts) -> int:
    monkeypatch.setattr(prepush, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(prepush, "lower_priority", lambda: None)
    monkeypatch.setattr(prepush, "plan_files", lambda base, minutes: {
        "changed": ["a"], "near": ["tests/test_a.py"], "left": [], "gates": ["tests/test_b.py"],
        "browser": ["tests/e2e/test_c.py"], "browser near": []})
    monkeypatch.setattr(prepush, "untracked_files", list)
    for name, run in parts.items():
        monkeypatch.setattr(prepush, f"run_{name}", run)
    import argparse

    args = argparse.Namespace(base="x", near=False, near_minutes=1.0, whole=False, gates_here=False, list=False,
                              processes=None, browsers=None)
    return prepush.run_parts(args, [p for p in prepush.PARTS if p in parts])


def test_one_failed_part_fails_the_run_and_only_it_is_told(monkeypatch, tmp_path, capsys):
    def passes(part, *_):
        part.ok, part.note = True, "3 passed"

    def fails(part, *_):
        part.failures = "FAILED tests/test_a.py::test_x"

    def breaks(part, *_):
        raise RuntimeError("the runner broke")

    assert _run_main(monkeypatch, tmp_path, tests=passes, linux=passes, browser=passes) == 0
    assert "all ok" in capsys.readouterr().out
    assert _run_main(monkeypatch, tmp_path, tests=passes, linux=fails, browser=passes) == 1
    out = capsys.readouterr().out
    assert "linux FAILED" in out and "test_a.py::test_x" in out and "===== tests" not in out
    assert _run_main(monkeypatch, tmp_path, tests=passes, linux=passes, browser=breaks) == 1
    assert "the runner broke" in capsys.readouterr().out


def test_a_browser_file_that_fails_alone_fails_and_one_that_does_not_is_named(monkeypatch, tmp_path):
    """Real pytest processes, in a checkout of their own."""
    import fast_test

    suite = tmp_path / "tests" / "e2e"
    suite.mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (suite / "test_passes.py").write_text("def test_p():\n    pass\n", encoding="utf-8")
    (suite / "test_once.py").write_text(
        "import pathlib\ndef test_o():\n    m = pathlib.Path(__file__).with_name('seen')\n"
        "    first = not m.exists()\n    m.touch()\n    assert not first\n", encoding="utf-8")
    (suite / "test_fails.py").write_text("def test_f():\n    assert 'planted' == 'x'\n", encoding="utf-8")
    monkeypatch.setattr(fast_test, "REPO", tmp_path)
    monkeypatch.setattr(prepush, "LOG_DIR", tmp_path)
    part = prepush.Part("browser")
    part.log = tmp_path / "browser.log"
    prepush.run_browser(part, ["tests/e2e/test_passes.py", "tests/e2e/test_once.py"], 2)
    assert part.ok and "passed alone: tests/e2e/test_once.py" in part.note
    part = prepush.Part("browser")
    part.log = tmp_path / "browser.log"
    prepush.run_browser(part, ["tests/e2e/test_passes.py", "tests/e2e/test_fails.py"], 2)
    assert not part.ok and "planted" in part.failures and "test_passes" not in part.failures


def test_a_long_browser_file_runs_as_several_processes_every_test_once(monkeypatch, tmp_path):
    import fast_test

    suite = tmp_path / "tests" / "e2e"
    suite.mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (suite / "test_long.py").write_text("".join(f"def test_{i}():\n    assert {i} != 5\n" for i in range(10)), encoding="utf-8")
    monkeypatch.setattr(fast_test, "REPO", tmp_path)
    monkeypatch.setattr(prepush, "REPO", tmp_path)
    monkeypatch.setattr(prepush, "LOG_DIR", tmp_path)
    long = "tests/e2e/test_long.py"
    assert prepush.browser_units(long, 10.0, sys.executable) == [[long]]
    units = prepush.browser_units(long, 900.0, sys.executable)
    assert len(units) == prepush.SPLIT_BROWSER_WAYS
    assert sorted(test for unit in units for test in unit) == [f"{long}::test_{i}" for i in range(10)]
    ci_shards = sys.modules["ci_shards"]
    monkeypatch.setattr(ci_shards, "load_table", lambda kind: {long: 900.0})
    part = prepush.Part("browser")
    part.log = tmp_path / "browser.log"
    prepush.run_browser(part, [long], 4)
    assert not part.ok and "test_5" in part.failures and "1 files, 9 passed" in part.note
    # Control: a file that cannot be collected runs whole, and fails there.
    (suite / "test_long.py").write_text("import nothing_like_it\n", encoding="utf-8")
    assert prepush.browser_units(long, 900.0, sys.executable) == [[long]]


# ── a race is named, and near the change it fails ────────────────────────

_RED = "x\n[fast-test] TOTALS in 9s: 2 failed, 3 passed\n\n[fast-test] 2 failing tests:\n  FAILED tests/test_a.py::test_x[one two]\n  FAILED tests/test_b.py::test_y\n"


def _judged(near, alone_code, alone_output):
    part = prepush.Part("linux")
    asked: list[list[str]] = []

    def alone(ids):
        asked.append(ids)
        return alone_code, alone_output

    prepush.judge(part, 1, _RED, near, alone)
    assert asked == [["tests/test_a.py::test_x", "tests/test_b.py::test_y"]]
    return part


def test_a_test_that_fails_alone_too_failed():
    part = _judged([], 1, "FAILED tests/test_b.py::test_y - assert\n1 failed, 1 passed in 1s\n")
    assert not part.ok and "failing tests" in part.failures and "passed alone: tests/test_a.py::test_x" in part.note


def test_a_race_far_from_the_change_is_named_and_near_it_fails():
    far = _judged(["tests/test_other.py"], 0, "2 passed in 1s\n")
    assert far.ok and "passed alone: tests/test_a.py::test_x, tests/test_b.py::test_y" in far.note
    near = _judged(["tests/test_a.py"], 0, "2 passed in 1s\n")
    assert not near.ok and "a race" in near.failures


def test_control_a_rerun_that_says_nothing_changes_nothing():
    for code, output in ((2, ""), (124, ""), (1, "no summary"), (4, "ERROR: not found")):
        part = _judged([], code, output)
        assert not part.ok and "passed alone" not in part.note
    part = prepush.Part("linux")
    prepush.judge(part, 1, _RED + "[fast-test] POISON files\n  POISON tests/test_c.py\n", [], lambda ids: (0, ""))
    assert not part.ok  # a file that crashed has no test to run again
    part = prepush.Part("linux")
    prepush.judge(part, 0, "[fast-test] TOTALS in 1s: 3 passed\n", [], None)
    assert part.ok and part.failures == ""


# ── a program Windows refuses (2026-11-26) ───────────────────────────────


def _cli_module_with(monkeypatch, tmp_path, refused: set[str]) -> list[Path]:
    """prepush's docker_cli (``kazma_core/docker_cli.py`` loaded by path),
    with two CLIs on disk and a stand-in start that Windows refuses for *refused*."""
    module = prepush._docker_cli_module()
    clis = []
    for name in ("first", "second"):
        exe = tmp_path / name / "docker.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"")
        clis.append(exe)
    started: list[str] = []

    def start(candidate):
        started.append(Path(candidate).parent.name)
        if Path(candidate).parent.name in refused:
            exc = OSError(22, "An Application Control policy has blocked this file")
            exc.winerror = 4551
            raise exc

    monkeypatch.delenv("KAZMA_DOCKER_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(module, "_candidates", lambda: clis)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module, "_on_windows", lambda: True)
    monkeypatch.setattr(module, "_checks_files", lambda: False)
    monkeypatch.setattr(module, "_start", start)
    monkeypatch.setattr(module, "_started", {})
    monkeypatch.setattr(module, "_reported", set())
    monkeypatch.setattr(prepush, "_DOCKER", [])
    clis.append(started)  # the starts, for the assertions
    return clis


def test_a_docker_cli_the_system_refuses_is_passed_over(monkeypatch, tmp_path):
    """prepush takes Kazma's own lookup (the audit's F6/F7, 2026-11-26): its
    copy passed over any OSError and never read ``winerror``."""
    first, second, started = _cli_module_with(monkeypatch, tmp_path, {"first"})
    assert prepush.docker_exe() == str(second)
    assert prepush.docker_exe() == str(second) and started == ["first", "second"]


def test_control_the_first_docker_that_starts_is_kept(monkeypatch, tmp_path):
    first, _second, _started = _cli_module_with(monkeypatch, tmp_path, set())
    assert prepush.docker_exe() == str(first)


def test_bandit_runs_as_a_module_and_other_tools_as_named():
    assert prepush.locked_tool("scan", ["bandit", "-r", "x"])[-5:] == ["python", "-m", "bandit", "-r", "x"]
    assert prepush.locked_tool("lint", ["ruff", "check"])[-2:] == ["ruff", "check"]


def test_the_locked_export_never_carries_colour(monkeypatch):
    """A shell that forces colour put an ANSI code into the exported
    requirements, and the Linux image refused it (2026-11-27)."""
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"pytest==8.3.5 \\n    --hash=sha256:00\n", stderr=b"")

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(prepush.subprocess, "run", run)
    out = prepush.locked_requirements()
    argv = seen["argv"]
    assert argv[:2] == ["uv", "export"]
    # The call before named no colour setting: index() raised there (the control).
    assert argv[argv.index("--color") + 1] == "never"
    assert "\x1b[" not in out
