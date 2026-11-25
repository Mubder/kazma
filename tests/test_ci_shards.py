"""Every test file runs in exactly one CI shard, and one check says so.

The Tests and Playwright jobs are matrix shards since 2026-11-25
(``scripts/ci_shards.py``). Held here: the shards partition what git lists (no
file lost, none twice, the browser files included), a new file lands in one
shard and moves no other, and the aggregate jobs keep the names branch
protection requires and pass only when every shard did. Each rule has its
negative control.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"

#: The aggregate job of each shard matrix, and the check name it reports.
AGGREGATES = {"tests": "Tests", "playwright-smoke": "Playwright smoke"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ci_shards = _load("ci_shards")


def _jobs(text: str | None = None) -> dict:
    return yaml.safe_load(text if text is not None else CI.read_text(encoding="utf-8"))["jobs"]


def _git_listed(*patterns: str) -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", *patterns],
                         cwd=REPO, capture_output=True, check=True).stdout.decode()
    return {name for name in out.split("\0") if name and (REPO / name).is_file()}


def partition_problems(shards: list[list[str]], expected: set[str]) -> list[str]:
    """Why *shards* is not a partition of *expected*."""
    dealt = [name for shard in shards for name in shard]
    problems = [f"in no shard: {name}" for name in sorted(expected - set(dealt))]
    problems += [f"in two shards: {name}" for name in sorted({n for n in dealt if dealt.count(n) > 1})]
    problems += [f"not a test file: {name}" for name in sorted(set(dealt) - expected)]
    return problems


# ── the partition ────────────────────────────────────────────────────────

def _unit_shards_as_ci_runs_them() -> list[list[str]]:
    """What each Tests shard's own command would run, asked of that command."""
    count = len(_jobs()["tests"]["strategy"]["matrix"]["shard"])
    shards = []
    for n in range(1, count + 1):
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "fast_test.py"), "--shard", f"{n}/{count}",
                              "--list"], cwd=REPO, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        shards.append([line for line in out.stdout.splitlines() if line.endswith(".py")])
    return shards


def test_every_unit_test_file_runs_in_exactly_one_shard():
    fast_test = _load("fast_test")
    roots = tuple(f"{d.rstrip('/')}/" for d in fast_test.TEST_DIRS)
    listed = {name for name in _git_listed("*.py")
              if name.startswith(roots) and "e2e" not in Path(name).parts
              and (Path(name).name.startswith("test_") or name.endswith("_test.py"))}
    assert len(listed) > 1500, len(listed)  # the enumeration itself is not blind
    shards = _unit_shards_as_ci_runs_them()
    ignored = set(ci_shards.unit_files()) - listed  # a file git does not list runs here and nowhere else
    assert partition_problems([[n for n in s if n not in ignored] for s in shards], listed) == []
    assert all(shards), "a shard with no file"


def _bash_words(script: str) -> str:
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in script.splitlines())


def browser_files_by_job(jobs: dict) -> dict[str, list[str]]:
    """The tests/e2e files each job's ``run:`` scripts name, comments dropped."""
    found: dict[str, list[str]] = {}
    for key, job in jobs.items():
        for step in job.get("steps") or []:
            names = re.findall(r"tests/e2e/test_\w+\.py", _bash_words(str(step.get("run") or "")))
            if names:
                found.setdefault(key, []).extend(names)
    return found


def test_every_browser_file_runs_in_exactly_one_shard():
    named = browser_files_by_job(_jobs())
    expected = _git_listed("tests/e2e/test_*.py")
    assert len(expected) > 80, len(expected)
    assert set(named) == {"playwright-smoke", "unified-turn-lifecycle"}, sorted(named)
    assert partition_problems([named["playwright-smoke"], named["unified-turn-lifecycle"]], expected) == []
    count = len(_jobs()["playwright-smoke"]["strategy"]["matrix"]["shard"])
    shards = ci_shards.assign(named["playwright-smoke"], count, "e2e")
    assert partition_problems(shards, set(named["playwright-smoke"])) == []
    assert all(shards), "a shard with no file"


def test_control_a_lost_a_repeated_and_an_unknown_file_are_found():
    assert partition_problems([["a", "b"], ["c"]], {"a", "b", "c"}) == []
    assert partition_problems([["a"], ["c"]], {"a", "b", "c"}) == ["in no shard: b"]
    assert partition_problems([["a", "b"], ["b", "c"]], {"a", "b", "c"}) == ["in two shards: b"]
    assert partition_problems([["a", "x"]], {"a"}) == ["not a test file: x"]
    only_explained = {"p": {"steps": [{"run": "files=(\n  # tests/e2e/test_a.py\n  tests/e2e/test_b.py\n)"}]}}
    assert browser_files_by_job(only_explained) == {"p": ["tests/e2e/test_b.py"]}


@pytest.mark.parametrize("kind", ["unit", "e2e"])
def test_a_new_file_lands_in_one_shard_and_moves_no_other(kind):
    table = ci_shards.load_table(kind)
    files = sorted(table)
    before = ci_shards.assign(files, 6, kind)
    new = "tests/test_written_tomorrow.py"
    after = ci_shards.assign([*files, new], 6, kind)
    assert sum(new in shard for shard in after) == 1
    assert [[n for n in s if n != new] for s in after] == before
    assert ci_shards.assign([*files, new], 6, kind) == after  # one answer, every time
    gone = files[0]
    assert [[n for n in s if n != gone] for s in before] == ci_shards.assign(files[1:], 6, kind)


def test_the_browser_runner_runs_its_shard_and_refuses_a_wrong_list(monkeypatch, capsys):
    ran: list[list[str]] = []
    monkeypatch.setattr(ci_shards.subprocess, "call", lambda cmd, cwd: ran.append(cmd) or 0)
    files = sorted(_git_listed("tests/e2e/test_*.py"))
    for n in (1, 2, 3):
        assert ci_shards.main(["e2e", "--shard", f"{n}/3", *files]) == 0
    dealt = [[word for word in cmd if word.startswith("tests/e2e/")] for cmd in ran]
    assert partition_problems(dealt, set(files)) == []
    ran.clear()
    assert ci_shards.main(["e2e", "--shard", "1/3", *files, files[0]]) == 2
    assert ci_shards.main(["e2e", "--shard", "1/3", *files, "tests/e2e/test_gone.py"]) == 2
    assert ran == []
    with pytest.raises(SystemExit):
        ci_shards.main(["e2e", "--shard", "4/3", *files])


def test_a_failing_browser_shard_fails(tmp_path, monkeypatch):
    """A real pytest: its exit code is the shard's, and what it took is written."""
    suite = tmp_path / "tests" / "e2e"
    suite.mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (suite / "test_fails.py").write_text("def test_fails():\n    assert False\n", encoding="utf-8")
    (suite / "test_passes.py").write_text("def test_passes():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(ci_shards, "REPO", tmp_path)
    took = tmp_path / "took.json"
    assert ci_shards.run_e2e((1, 1), ["tests/e2e/test_passes.py", "tests/e2e/test_fails.py"], took) == 1
    assert set(json.loads(took.read_text())["e2e"]) == {"tests/e2e/test_passes.py", "tests/e2e/test_fails.py"}
    assert ci_shards.run_e2e((1, 1), ["tests/e2e/test_passes.py"], None) == 0


# ── the shards' balance ──────────────────────────────────────────────────

@pytest.mark.parametrize("kind,job", [("unit", "tests"), ("e2e", "playwright-smoke")])
def test_no_shard_is_much_longer_than_the_others(kind, job):
    """The slowest shard is the wait. Refresh the table when this fails:
    ``python scripts/ci_shards.py update <a green CI run's id>``."""
    table = ci_shards.load_table(kind)
    files = ci_shards.unit_files() if kind == "unit" else browser_files_by_job(_jobs())[job]
    median = sorted(table.values())[len(table) // 2]
    count = len(_jobs()[job]["strategy"]["matrix"]["shard"])
    loads = [sum(table.get(n, median) for n in shard) for shard in ci_shards.assign(files, count, kind)]
    assert max(loads) <= max(1.35 * sum(loads) / count, max(table.values())), [round(x) for x in loads]


# ── the aggregate jobs ───────────────────────────────────────────────────

def aggregate_problems(jobs: dict) -> list[str]:
    problems: list[str] = []
    for shards, name in AGGREGATES.items():
        holders = [key for key, job in jobs.items() if job.get("name") == name]
        if len(holders) != 1 or holders[0] == shards:
            problems.append(f"{name!r} is reported by {holders}")
            continue
        job = jobs[holders[0]]
        if shards not in (job.get("needs") or []):
            problems.append(f"{name!r} does not need {shards!r}")
        if str(job.get("if") or "").strip("${} ") != "always()":
            problems.append(f"{name!r} is skipped when a shard fails: a skipped check can count as passed")
        steps = job.get("steps") or []
        if len(steps) != 1 or steps[0].get("env", {}).get("RESULTS") != "${{ join(needs.*.result, ' ') }}":
            problems.append(f"{name!r} does not read every result")
        if any(step.get("continue-on-error") or step.get("if") for step in steps) or job.get("continue-on-error"):
            problems.append(f"{name!r} can pass without its gate")
        matrix = (jobs[shards].get("strategy") or {}).get("matrix") or {}
        numbers = matrix.get("shard") or []
        if list(matrix) != ["shard"] or numbers != list(range(1, len(numbers) + 1)) or len(numbers) < 2:
            problems.append(f"{shards!r} has shards {numbers}")
        if jobs[shards].get("continue-on-error") or (jobs[shards].get("strategy") or {}).get("fail-fast") is not False:
            problems.append(f"{shards!r} must run every shard to its end, and let none fail quietly")
        runs = [str(s.get("run") or "") for s in jobs[shards].get("steps") or []]
        command = [r for r in runs if "--shard" in r]
        if len(command) != 1 or "--shard ${{ matrix.shard }}/${{ strategy.job-total }}" not in command[0]:
            problems.append(f"{shards!r} does not run its own shard of all of them")
    return problems


def test_the_aggregates_keep_the_required_names_and_need_every_shard():
    jobs = _jobs()
    assert aggregate_problems(jobs) == []
    assert "readme-metrics" in jobs["tests-passed"]["needs"]


@pytest.mark.parametrize("old,new", [
    ("    needs: [tests, readme-metrics]\n", "    needs: [readme-metrics]\n"),
    ("\n    if: always()\n", "\n"),
    ("\n    if: always()\n", "\n    if: success()\n"),
    ("RESULTS: ${{ join(needs.*.result, ' ') }}", "RESULTS: success"),
    ("        shard: [1, 2, 3, 4, 5, 6]\n", "        shard: [1, 2, 3, 4, 6]\n"),
    ("--shard ${{ matrix.shard }}/${{ strategy.job-total }}", "--shard ${{ matrix.shard }}/5"),
    ("    name: Tests, shard ${{ matrix.shard }}\n", "    name: Tests\n"),
])
def test_control_an_aggregate_that_would_pass_anyway_is_found(old, new):
    text = CI.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert old in text
    assert aggregate_problems(_jobs(text.replace(old, new))) != []


def _bash() -> str | None:
    for name in ("bash", r"C:\Program Files\Git\bin\bash.exe"):
        found = shutil.which(name)
        if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
            return found
    return None


@pytest.mark.parametrize("results,passes", [
    ("success", True), ("success success", True), ("success failure", False), ("cancelled success", False),
    ("skipped", False), ("", False),
])
def test_the_aggregates_gate_itself(results, passes):
    bash = _bash()
    if bash is None:
        pytest.skip("no bash on this machine (CI has one)")
    for key in ("tests-passed", "playwright-smoke-passed"):
        script = _jobs()[key]["steps"][0]["run"]
        out = subprocess.run([bash, "-e", "-c", script], env={"RESULTS": results, "PATH": "/usr/bin:/bin"},
                             capture_output=True, timeout=60)
        assert (out.returncode == 0) is passes, (key, results, out.stdout)


# ── the table ────────────────────────────────────────────────────────────

def test_update_merges_a_runs_measurements(tmp_path, monkeypatch):
    table = tmp_path / "table.json"
    table.write_text(json.dumps({"unit": {"tests/test_a.py": 1.0, "tests/test_gone.py": 9.0}, "e2e": {}}))
    monkeypatch.setattr(ci_shards, "TABLE", table)
    monkeypatch.setattr(ci_shards, "unit_files", lambda: ["tests/test_a.py", "tests/test_b.py"])
    monkeypatch.setattr(ci_shards, "e2e_files", lambda: ["tests/e2e/test_c.py"])

    def download(cmd, cwd):
        folder = Path(cmd[cmd.index("--dir") + 1])
        for n, body in enumerate(({"unit": {"tests/test_b.py": 2.5}}, {"e2e": {"tests/e2e/test_c.py": 30}})):
            (folder / f"test-durations-{n}").mkdir()
            (folder / f"test-durations-{n}" / "test-durations.json").write_text(json.dumps(body))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ci_shards.subprocess, "run", download)
    assert ci_shards.update(123) == 0
    data = json.loads(table.read_text(encoding="utf-8"))
    assert data == {"unit": {"tests/test_a.py": 1.0, "tests/test_b.py": 2.5}, "e2e": {"tests/e2e/test_c.py": 30.0},
                    "measured": "CI run 123"}


def test_durations_are_read_from_a_real_report(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (suite / "test_slow.py").write_text(
        "import time\ndef test_a():\n    time.sleep(0.3)\ndef test_b():\n    time.sleep(0.3)\n", encoding="utf-8")
    report = tmp_path / "junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}",
                    *ci_shards.JUNIT_OPTIONS], cwd=suite, check=True, capture_output=True, timeout=120)
    seconds = ci_shards.durations_of(report)
    assert list(seconds) == ["test_slow.py"] and 0.5 < seconds["test_slow.py"] < 20


def test_only_a_run_number_is_passed_to_gh(monkeypatch):
    asked: list[int] = []
    monkeypatch.setattr(ci_shards, "update", lambda run_id: asked.append(run_id) or 0)
    assert ci_shards.main(["update", "45712858239"]) == 0
    for wrong in ("--repo=other/one", "latest", "1; rm"):
        with pytest.raises(SystemExit):
            ci_shards.main(["update", wrong])
    assert asked == [45712858239]


def test_a_path_outside_the_repository_and_the_temp_folders_is_refused(tmp_path, monkeypatch):
    assert ci_shards.contained(tmp_path / "x.json") == (tmp_path / "x.json").resolve()
    assert ci_shards.contained(REPO / ".pytest-tmp" / "x.json")
    outside = Path(REPO.anchor) / "elsewhere" / "x.json"
    for wrong in (outside, REPO / ".." / ".." / ".." / ".." / ".." / ".." / "x.json"):
        with pytest.raises(SystemExit):
            ci_shards.contained(wrong)
    with pytest.raises(SystemExit):
        ci_shards.write_durations(outside, "unit", {})
    assert not outside.exists()
    monkeypatch.setenv("RUNNER_TEMP", str(outside.parent))  # GitHub's own temp folder
    assert ci_shards.contained(outside) == outside.resolve()
