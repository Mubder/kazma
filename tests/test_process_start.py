"""A process's start time is read and compared in one place (2026-11-27).

On Linux psutil's ``create_time()`` adds the boot time from ``/proc/stat``,
read again on every call, and the boot time moves with every step of the
wall clock: on Docker Desktop's WSL2 VM it moved a whole second about every
30 s while the PC was loaded. The Knowledge ingest then read a running
crawl's own owner as gone and took it over (prepush's Linux part,
``tests/test_kb_api_contract.py``). Every owner record that compared a
recorded start with a fresh reading had the same fault. Here each of them is
run with the clock stepped, the helper's rules are shown, and the product
source is enumerated so that no module reads a start time by itself again.
"""
from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from kazma_core import process_start

REPO = Path(__file__).resolve().parents[1]
HELPER = "kazma-core/kazma_core/process_start.py"


@pytest.fixture
def step_the_clock(monkeypatch):
    """``step(seconds)``: every reading of a start from then on is that much
    later, as /proc/stat's boot time made it. This process read its own start
    before, as a running server has."""
    process_start.own_start()
    real = psutil.Process.create_time

    def step(seconds: float) -> None:
        monkeypatch.setattr(psutil.Process, "create_time", lambda self: real(self) + seconds)

    return step


@pytest.fixture
def linux_rule(monkeypatch):
    """The rule of a platform whose boot time follows the wall clock."""
    monkeypatch.setattr(process_start, "_START_KEPT_BY_KERNEL", False)


@pytest.fixture
def other_process():
    """A live process of this host that is not this one, and its start."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        process = psutil.Process(child.pid)
        yield process, process.create_time()
    finally:
        child.kill()
        child.wait(timeout=30)


# -- the helper's rules -------------------------------------------------------


def test_this_process_reads_its_start_once(step_the_clock):
    before = process_start.own_start()
    step_the_clock(1.0)
    assert process_start.own_start() == before
    assert process_start.own_identity() == [os.getpid(), before]


def test_this_process_is_itself_after_a_clock_step(step_the_clock):
    pid, started = process_start.own_identity()
    step_the_clock(1.0)
    assert process_start.is_this_process(pid, started)
    assert process_start.is_start_of(psutil.Process(pid), started)
    assert process_start.is_this_process(pid, f"{started:.3f}"), "the 3-decimal text records keep"


def test_an_earlier_process_with_this_pid_is_not_this_one():
    """No tolerance for this process: a container's pid 1 after a restart has
    this pid and a start a moment earlier."""
    pid, started = process_start.own_identity()
    for earlier in (started - 1.0, started - 0.01, started + 0.5):
        assert not process_start.is_this_process(pid, earlier)
    assert not process_start.is_this_process(pid + 1, started)


@pytest.mark.parametrize("recorded", [None, "", "soon", True, float("nan"), float("inf"), [1.0]])
def test_a_start_that_is_not_a_number_names_no_process(recorded):
    pid = os.getpid()
    assert not process_start.is_this_process(pid, recorded)
    assert not process_start.is_start_of(psutil.Process(pid), recorded)


def test_another_process_survives_a_boot_time_step(linux_rule, step_the_clock, other_process):
    process, started = other_process
    step_the_clock(1.0)
    assert process_start.is_start_of(process, started)
    assert process_start.is_start_of(process, f"{started:.3f}")


def test_a_reused_pid_is_still_told_apart(linux_rule, step_the_clock, other_process):
    """Negative control for the tolerance: a start further off than it is
    another process's."""
    process, started = other_process
    step_the_clock(process_start.CLOCK_STEP_TOLERANCE_S + 1.0)
    assert not process_start.is_start_of(process, started)


def test_where_the_kernel_keeps_the_start_only_the_text_is_slack(monkeypatch, step_the_clock, other_process):
    monkeypatch.setattr(process_start, "_START_KEPT_BY_KERNEL", True)
    process, started = other_process
    assert process_start.is_start_of(process, f"{started:.3f}")
    step_the_clock(1.0)
    assert not process_start.is_start_of(process, started)


def test_a_fork_reads_its_own_start(monkeypatch):
    """The cache is this pid's: a forked child (a new pid) reads its own."""
    monkeypatch.setattr(process_start, "_OWN", (-1, 1.0))
    assert process_start.own_start() == psutil.Process().create_time()


def test_the_old_comparison_called_this_process_another(step_the_clock):
    """Negative control: the rule before -- a fresh reading compared exactly
    -- says this live process is not itself once the boot time stepped."""
    recorded = psutil.Process().create_time()
    step_the_clock(1.0)
    assert psutil.Process().create_time() != recorded


# -- every owner record, with the clock stepped --------------------------------


def test_a_crawl_owner_is_not_gone_after_a_clock_step(step_the_clock):
    from kazma_core.web_acquire import crawl_jobs

    owner = crawl_jobs._owner()
    row = {"owner": owner, "claim": "c" * 32, "updated_at": time.time()}
    step_the_clock(1.0)
    with crawl_jobs._ACTIVE_LOCK:
        crawl_jobs._ACTIVE.add(row["claim"])
    try:
        assert not crawl_jobs._owner_gone(row, time.time())
    finally:
        with crawl_jobs._ACTIVE_LOCK:
            crawl_jobs._ACTIVE.discard(row["claim"])
    assert crawl_jobs._owner_gone(row, time.time()), "control: a claim this process gave up"
    assert crawl_jobs._alive([owner["pid"], owner["started"]])


def test_research_and_storage_owners_stay_alive_after_a_clock_step(step_the_clock):
    """``research_session._process_owner`` and the research storage
    reservation record ``[pid, start]``; both are read by ``crawl_jobs._alive``."""
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import crawl_jobs

    owner = research_session._process_owner()
    step_the_clock(1.0)
    assert crawl_jobs._alive(owner)
    assert crawl_jobs._alive(process_start.own_identity())


def test_another_processs_crawl_owner_is_alive_after_a_clock_step(linux_rule, step_the_clock, other_process):
    from kazma_core.web_acquire import crawl_jobs

    process, started = other_process
    step_the_clock(1.0)
    assert crawl_jobs._alive([process.pid, started])


def test_an_origin_lease_this_process_holds_survives_a_clock_step(step_the_clock):
    from kazma_core.web_acquire import origin_state

    origin = ["https", "lease.example.test", 443]
    table: dict = {}
    origin_state._row(table, origin)["active"]["t" * 32] = origin_state._own_identity()
    with origin_state._HELD_LOCK:
        origin_state._HELD.add("t" * 32)
    try:
        step_the_clock(1.0)
        assert "t" * 32 in origin_state._row(table, origin)["active"]
    finally:
        with origin_state._HELD_LOCK:
            origin_state._HELD.discard("t" * 32)
    assert "t" * 32 not in origin_state._row(table, origin)["active"], "control: a lease given up"


def test_a_document_intake_owner_is_alive_after_a_clock_step(tmp_path, monkeypatch, linux_rule, step_the_clock,
                                                             other_process):
    from kazma_core.documents import admission

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    admission._process_identity.cache_clear()
    try:
        install, host, _pid, _started = admission._process_identity()
        assert install, "an install id in the test's own data folder"
        process, child_started = other_process
        theirs = json.dumps({"install": install, "host": host, "pid": process.pid,
                             "started": f"{child_started:.3f}"})
        step_the_clock(1.0)
        assert admission.owner_state(admission.current_owner()) == "self"
        assert admission.owner_state(theirs) == "alive"
        gone = json.dumps({"install": install, "host": host, "pid": process.pid,
                           "started": f"{child_started - 60:.3f}"})
        assert admission.owner_state(gone) == "dead", "control: a reused pid"
    finally:
        admission._process_identity.cache_clear()


def test_a_migration_folder_in_use_stays_in_use_after_a_clock_step(tmp_path, linux_rule, step_the_clock,
                                                                    other_process):
    from kazma_core.migration import bundle

    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    bundle.mark_working_folder(mine)
    process, started = other_process
    theirs.mkdir()
    (theirs / bundle.IN_USE_MARKER).write_text(json.dumps(
        {"host": socket.gethostname(), "pid": process.pid, "started": f"{started:.3f}"}), encoding="utf-8")
    step_the_clock(1.0)
    assert bundle._working_folder_in_use(mine)
    assert bundle._working_folder_in_use(theirs)
    (theirs / bundle.IN_USE_MARKER).write_text(json.dumps(
        {"host": socket.gethostname(), "pid": process.pid, "started": f"{started - 60:.3f}"}), encoding="utf-8")
    assert not bundle._working_folder_in_use(theirs), "control: a reused pid"


# -- the gate: nothing else reads a start time ----------------------------------


def start_readers(source: str) -> list[int]:
    """Lines of every ``create_time()`` call in *source*."""
    return sorted(node.lineno for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "create_time")


def product_sources() -> list[str]:
    """Every git-listed Python file that ships or runs Kazma: not tests, not docs."""
    out = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=REPO, capture_output=True,
                         check=True).stdout.decode("utf-8")
    skip = {"tests", "docs", "fuzz"}
    return sorted(name for name in out.split("\0")
                  if name and not skip & set(Path(name).parts) and Path(name).name != "conftest.py")


def test_no_module_but_the_helper_reads_a_process_start():
    found = []
    for name in product_sources():
        if name == HELPER:
            continue
        path = REPO / name
        if path.exists():
            found += [f"{name}:{line}" for line in start_readers(path.read_text(encoding="utf-8"))]
    assert not found, (
        "a module reads a process's start itself; on Linux a second reading moves with the wall "
        "clock. Use kazma_core.process_start (own_start, is_this_process, is_start_of): " + ", ".join(found))


def test_the_gate_reads_the_real_files_and_sees_the_helper():
    """Negative control: the enumeration holds the helper and the sites it
    replaced, and the scanner finds the helper's own reading."""
    names = set(product_sources())
    assert HELPER in names, "stage the helper: the gate reads git-listed files"
    assert {"kazma-core/kazma_core/web_acquire/crawl_jobs.py", "kazma-core/kazma_core/documents/admission.py",
            "kazma-core/kazma_core/migration/bundle.py", "kazma-core/kazma_core/web_acquire/origin_state.py",
            "kazma-core/kazma_core/tools/research_session.py",
            "kazma-core/kazma_core/web_acquire/artifact_storage.py"} <= names
    assert start_readers((REPO / HELPER).read_text(encoding="utf-8"))


@pytest.mark.parametrize("snippet", [
    "ok = owner['started'] == psutil.Process().create_time()",
    "started = f'{psutil.Process(os.getpid()).create_time():.3f}'",
    "def alive(p, s):\n    return p.create_time() == s",
    "seen.add((child.pid, child.create_time()))",
])
def test_the_scanner_sees_each_old_shape(snippet):
    """Negative control: each shape the product used before 2026-11-27."""
    assert start_readers(snippet) == [snippet.count("\n") + 1]


def test_the_scanner_leaves_a_key_named_create_time():
    """A chat export's ``create_time`` field is not a process's start."""
    assert start_readers("when = message.get('create_time')") == []
