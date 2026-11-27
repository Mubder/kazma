"""A process's start is read and compared in one place (2026-11-27).

On Linux psutil's ``create_time()`` adds the boot time from ``/proc/stat``,
read again on every call, and the boot time moves with every step of the
wall clock: on Docker Desktop's WSL2 VM it moved a whole second about every
30 s while the PC was loaded. The Knowledge ingest then read a running
crawl's own owner as gone and took it over (prepush's Linux part,
``tests/test_kb_api_contract.py``). Every owner record that compared a
recorded start with a fresh reading had the same fault, and so did the
guard's orphan check.

Records now carry a stamp: on Linux the boot id and the start's ticks after
boot, which no clock step moves. Here each site is run with the clock
stepped, the helper's rules are shown (Linux through a planted ``/proc``, so
they run on every platform, and through the real one where there is one),
the guard's copy is held to the helper's, and the product source is
enumerated so that no module reads a start, the boot time or
``/proc/<pid>/stat`` by itself again.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import psutil
import pytest
from kazma_core import process_start

REPO = Path(__file__).resolve().parents[1]
HELPER = "kazma-core/kazma_core/process_start.py"
GUARD = "scripts/service/kazma_guard.py"
#: The product before the stamps and the helper (origin/main, 2026-11-27).
BEFORE = "cc01b8194f3dedc5c564160b16aaf4056bae1e2a"


@pytest.fixture
def step_the_clock(monkeypatch):
    """``step(seconds)``: every reading of a start from then on is that much
    later, as /proc/stat's boot time made it. This process read its own start
    before, as a running server has."""
    process_start.own_start()
    process_start.own_stamp()
    real = psutil.Process.create_time

    def step(seconds: float) -> None:
        monkeypatch.setattr(psutil.Process, "create_time", lambda self: real(self) + seconds)

    return step


@pytest.fixture
def linux_rule(monkeypatch):
    """The rule where psutil moves the start with the boot time."""
    monkeypatch.setattr(process_start, "_START_KEPT_BY_KERNEL", False)


@pytest.fixture
def fake_proc(monkeypatch, linux_rule):
    """A planted Linux ``/proc``: one boot id and the start ticks of each pid
    (``ticks[pid]``); this process gets its stamp from it."""
    ticks = {os.getpid(): 4_200}
    monkeypatch.setattr(process_start, "_LINUX_PROC", True)
    monkeypatch.setattr(process_start, "_BOOT_ID", ["boot-a"])
    monkeypatch.setattr(process_start, "_start_ticks", lambda pid: ticks.get(pid))
    monkeypatch.setattr(process_start, "_OWN_STAMP", None)
    return ticks


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


def as_another_process(monkeypatch) -> None:
    """The helper's view from another process of this host: its own pid is
    not this one's (a record of this process is read as another's)."""
    real = os.getpid()
    monkeypatch.setattr(process_start, "os", types.SimpleNamespace(getpid=lambda: real + 1))


# -- the helper's rules -------------------------------------------------------


def test_this_process_reads_its_start_once(step_the_clock):
    before = process_start.own_start()
    step_the_clock(1.0)
    assert process_start.own_start() == before


def test_this_process_is_itself_after_a_clock_step(step_the_clock):
    identity = process_start.own_identity()
    step_the_clock(3600.0)
    assert process_start.identity_is_this_process(identity)
    assert process_start.is_start_of(psutil.Process(), *identity[1:])
    pid, started = identity[:2]
    assert process_start.is_this_process(pid, f"{started:.3f}"), "the 3-decimal text records keep"


def test_an_earlier_process_with_this_pid_is_not_this_one():
    """Within 2 ms for a record without a stamp: a container's pid 1 after a
    restart has this pid and a start a moment earlier."""
    pid, started = process_start.own_identity()[:2]
    for earlier in (started - 1.0, started - 0.01, started + 0.5):
        assert not process_start.is_this_process(pid, earlier)
    assert not process_start.is_this_process(pid + 1, started)


def test_by_the_stamp_an_earlier_process_with_this_pid_is_not_this_one(fake_proc):
    """With stamps, exactly: the same pid and the same start time, but the
    start ticks of another process (or another boot), is not this one."""
    pid, started, stamp = process_start.own_identity()
    assert stamp == f"linux:boot-a:{fake_proc[pid]}"
    assert process_start.is_this_process(pid, started, stamp)
    assert not process_start.is_this_process(pid, started, f"linux:boot-a:{fake_proc[pid] - 1}")
    assert not process_start.is_this_process(pid, started, f"linux:boot-b:{fake_proc[pid]}")


@pytest.mark.parametrize("recorded", [None, "", "soon", True, float("nan"), float("inf"), [1.0]])
def test_a_start_that_is_not_a_number_names_no_process(recorded):
    pid = os.getpid()
    assert not process_start.is_this_process(pid, recorded)
    assert not process_start.is_start_of(psutil.Process(pid), recorded)
    assert not process_start.identity_is_this_process([pid, recorded])


@pytest.mark.parametrize("identity", [None, 7, "x", [], [1], [1, 2, 3, 4], {"pid": 1}])
def test_a_record_that_is_not_an_identity_is_not_this_process(identity):
    assert not process_start.identity_is_this_process(identity)


def test_another_process_is_matched_by_its_stamp_however_far_the_clock_moved(
        fake_proc, step_the_clock, other_process):
    from kazma_core.web_acquire import crawl_jobs

    process, started = other_process
    fake_proc[process.pid] = 9_001
    record = [process.pid, started, f"linux:boot-a:{fake_proc[process.pid]}"]
    step_the_clock(3600.0)
    assert process_start.is_start_of(process, *record[1:])
    assert crawl_jobs._alive(record)
    fake_proc[process.pid] = 9_002  # the pid now belongs to a later process
    assert not process_start.is_start_of(process, *record[1:])
    assert not crawl_jobs._alive(record)
    fake_proc[process.pid] = 9_001
    assert not process_start.is_start_of(process, started, "linux:boot-b:9001"), "another boot"


def test_a_record_written_late_still_names_its_writer(fake_proc, step_the_clock, monkeypatch):
    """The review's case: this process read its start, the clock moved 11 s,
    then it wrote a record. Another process reading it finds its writer by the
    stamp; by the start alone (the record without it) it would not."""
    step_the_clock(process_start.CLOCK_STEP_TOLERANCE_S + 1.0)
    pid, started, stamp = process_start.own_identity()
    as_another_process(monkeypatch)
    writer = psutil.Process(pid)
    assert process_start.is_start_of(writer, started, stamp)
    assert not process_start.is_start_of(writer, started), "control: the start alone"


def test_a_record_without_a_stamp_is_matched_by_its_start(linux_rule, step_the_clock, other_process):
    """Rows written before the stamps (and macOS, the BSDs) keep the tolerance."""
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


def test_where_the_kernel_keeps_the_start_it_is_matched_within_2_ms(monkeypatch, step_the_clock, other_process):
    monkeypatch.setattr(process_start, "_START_KEPT_BY_KERNEL", True)
    process, started = other_process
    assert process_start.is_start_of(process, f"{started:.3f}")
    step_the_clock(1.0)
    assert not process_start.is_start_of(process, started)


def test_where_nothing_settles_it_the_verdict_says_so(linux_rule, step_the_clock, other_process, monkeypatch):
    """Without a Linux stamp, a start further off than the tolerance proves
    nothing where psutil moves it with the clock (None); on Windows it does
    (False). Within the tolerance it is the process (True)."""
    process, started = other_process
    assert process_start.start_verdict(process, started) is True
    step_the_clock(process_start.CLOCK_STEP_TOLERANCE_S + 1.0)
    assert process_start.start_verdict(process, started) is None
    assert process_start.is_start_of(process, started) is False
    monkeypatch.setattr(process_start, "_START_KEPT_BY_KERNEL", True)
    assert process_start.start_verdict(process, started) is False


def test_without_a_linux_stamp_this_process_is_known_by_a_run_id(monkeypatch, linux_rule, step_the_clock):
    """macOS and the BSDs: no reading stays put, so this process is told
    apart by an id of its own, and its records carry a start read when they
    are written (another process compares a reading made close to it)."""
    monkeypatch.setattr(process_start, "_LINUX_PROC", False)
    monkeypatch.setattr(process_start, "_OWN_STAMP", None)
    pid, started, stamp = process_start.own_identity()
    assert stamp.startswith("run:") and process_start.own_stamp() == stamp
    step_the_clock(60.0)
    assert abs(process_start.own_identity()[1] - (started + 60.0)) < 5.0
    assert process_start.is_this_process(pid, started, stamp)
    assert not process_start.is_this_process(pid, started, "run:" + "0" * 32)


def test_a_fork_reads_its_own_start(monkeypatch):
    """The cache is this pid's: a forked child (a new pid) reads its own."""
    monkeypatch.setattr(psutil.Process, "create_time", lambda self: 1234.5)
    monkeypatch.setattr(process_start, "_OWN", (-1, 1.0))
    assert process_start.own_start() == 1234.5


def test_field_22_is_counted_from_the_last_parenthesis():
    fields = " ".join(str(n) for n in range(4, 22))
    line = f"123 (a (b) c) S {fields} 424242 19 20\n".encode()
    assert process_start._ticks_from_stat(line) == 424242


def test_the_real_proc_gives_this_process_its_stamp(other_process):
    """Linux: ``/proc`` answers and the stamp of a process reads the same
    twice; elsewhere there is no Linux stamp."""
    process, _started = other_process
    on_linux = sys.platform.startswith("linux")
    assert process_start.stamp_of(os.getpid()) == (process_start.own_stamp() if on_linux else None)
    first = process_start.stamp_of(process.pid)
    assert first == process_start.stamp_of(process.pid)
    assert (first is not None and first.startswith("linux:")) == on_linux


# -- every owner record, with the clock stepped --------------------------------


def test_a_crawl_owner_is_not_gone_after_a_clock_step(step_the_clock):
    from kazma_core.web_acquire import crawl_jobs

    owner = crawl_jobs._owner()
    row = {"owner": owner, "claim": "c" * 32, "updated_at": time.time()}
    step_the_clock(3600.0)
    with crawl_jobs._ACTIVE_LOCK:
        crawl_jobs._ACTIVE.add(row["claim"])
    try:
        assert not crawl_jobs._owner_gone(row, time.time())
    finally:
        with crawl_jobs._ACTIVE_LOCK:
            crawl_jobs._ACTIVE.discard(row["claim"])
    assert crawl_jobs._owner_gone(row, time.time()), "control: a claim this process gave up"
    assert crawl_jobs._alive([owner["pid"], owner["started"], owner.get("stamp")])


def test_the_old_crawl_journal_took_its_own_crawl_over(monkeypatch):
    """Negative control on the product as it was: the crawl journal before the
    helper, run against the same stepped clock, reads this process's live
    crawl as gone."""
    source = subprocess.run(["git", "show", f"{BEFORE}:kazma-core/kazma_core/web_acquire/crawl_jobs.py"],
                            cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    old = types.ModuleType("_crawl_jobs_before_process_start")
    monkeypatch.setitem(sys.modules, old.__name__, old)
    exec(compile(source, "<crawl_jobs before>", "exec"), old.__dict__)  # noqa: S102 -- the repo's own file
    monkeypatch.setattr(psutil.Process, "create_time", lambda self: 1_000.0)
    row = {"owner": old._owner(), "claim": "c" * 32, "updated_at": time.time()}
    old._ACTIVE.add(row["claim"])
    assert not old._owner_gone(row, time.time()), "before the step it is its own"
    monkeypatch.setattr(psutil.Process, "create_time", lambda self: 1_001.0)  # btime stepped
    assert old._owner_gone(row, time.time())


def test_research_and_storage_owners_stay_alive_after_a_clock_step(step_the_clock):
    """``research_session._process_owner`` and the research storage
    reservation record ``[pid, start(, stamp)]``; both are read by
    ``crawl_jobs._alive``."""
    from kazma_core.tools import research_session
    from kazma_core.web_acquire import crawl_jobs

    owner = research_session._process_owner()
    step_the_clock(3600.0)
    assert crawl_jobs._alive(owner)
    assert crawl_jobs._alive(process_start.own_identity())


async def test_a_resumed_session_reads_its_owner_off_the_loop(monkeypatch):
    """The owner reads /proc (Linux) or psutil (macOS): built in the thread
    that claims the session, never on the loop (AGENTS §35)."""
    import asyncio

    from kazma_core.tools import research_session as rs

    where: list[str] = []

    def owner() -> list:
        try:
            asyncio.get_running_loop()
            where.append("loop")
        except RuntimeError:
            where.append("thread")
        return [os.getpid(), 1.0]

    monkeypatch.setattr(rs, "_process_owner", owner)
    monkeypatch.setattr(rs, "_claim_session", lambda sid, held, root: rs.ResearchSession(
        id=sid, topic="", meta={"process_owner": held}))
    monkeypatch.setattr(rs, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(rs, "get_session", lambda sid: None)
    sess = await rs.start_deep_research.__wrapped__("t", resume_session_id="rs_off_the_loop")
    assert where == ["thread"] and sess.meta["process_owner"] == [os.getpid(), 1.0]


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
        step_the_clock(3600.0)
        assert "t" * 32 in origin_state._row(table, origin)["active"]
    finally:
        with origin_state._HELD_LOCK:
            origin_state._HELD.discard("t" * 32)
    assert "t" * 32 not in origin_state._row(table, origin)["active"], "control: a lease given up"


def test_a_document_intake_owner_is_alive_after_a_clock_step(tmp_path, monkeypatch, fake_proc, step_the_clock,
                                                             other_process):
    from kazma_core.documents import admission

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    admission._process_identity.cache_clear()
    try:
        install, host, _pid, _started = admission._process_identity()
        assert install, "an install id in the test's own data folder"
        process, child_started = other_process
        fake_proc[process.pid] = 5_150

        def owner(started: float, stamp: str | None) -> str:
            return json.dumps({"install": install, "host": host, "pid": process.pid,
                               "started": f"{started:.3f}", **({"stamp": stamp} if stamp else {})})

        mine = admission.current_owner()
        assert json.loads(mine)["stamp"] == process_start.own_stamp()
        step_the_clock(3600.0)
        assert admission.owner_state(mine) == "self"
        assert admission.owner_state(owner(child_started, "linux:boot-a:5150")) == "alive"
        assert admission.owner_state(owner(child_started, "linux:boot-a:5149")) == "dead", "control: reused"
        assert admission.owner_state(owner(child_started, None)) == "unknown", (
            "the start alone, 1 h off, proves nothing here: the intake's lease decides")
    finally:
        admission._process_identity.cache_clear()


def test_an_intake_this_process_holds_is_self_by_its_stamp_first(tmp_path, monkeypatch, fake_proc):
    """An earlier process with this pid and the same start text is not this
    one: the stamp is asked before the start."""
    from kazma_core.documents import admission

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    admission._process_identity.cache_clear()
    try:
        mine = json.loads(admission.current_owner())
        assert admission.owner_state(json.dumps(mine)) == "self"
        earlier = {**mine, "stamp": f"linux:boot-a:{fake_proc[os.getpid()] - 1}"}
        assert admission.owner_state(json.dumps(earlier)) == "dead"
    finally:
        admission._process_identity.cache_clear()


def test_a_live_intake_is_not_read_dead_after_the_clock_moved_where_no_stamp_settles_it(
        tmp_path, monkeypatch, linux_rule, step_the_clock):
    """macOS and the BSDs (and a Linux without /proc): the intake owner is a
    fence token fixed for the process, its start read once. Another process
    reading it after more clock drift than the tolerance cannot tell: the
    owner is "unknown" and its lease decides, never "dead" (taken over)."""
    from kazma_core.documents import admission

    monkeypatch.setattr(process_start, "_LINUX_PROC", False)
    monkeypatch.setattr(process_start, "_OWN_STAMP", None)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    admission._process_identity.cache_clear()
    try:
        mine = admission.current_owner()
        assert admission.current_owner() == mine, "the fence token is fixed for the process"
        step_the_clock(process_start.CLOCK_STEP_TOLERANCE_S + 1.0)
        as_another_process(monkeypatch)
        assert admission.owner_state(mine) == "unknown"
        recorded = json.loads(mine)
        assert not process_start.is_start_of(psutil.Process(recorded["pid"]), recorded["started"],
                                             recorded["stamp"]), "control: read as another process, it is gone"
    finally:
        admission._process_identity.cache_clear()


def test_a_migration_folder_in_use_stays_in_use_after_a_clock_step(tmp_path, fake_proc, step_the_clock,
                                                                    other_process):
    from kazma_core.migration import bundle

    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    bundle.mark_working_folder(mine)
    assert json.loads((mine / bundle.IN_USE_MARKER).read_text(encoding="utf-8"))["stamp"] == process_start.own_stamp()
    process, started = other_process
    fake_proc[process.pid] = 6_060
    theirs.mkdir()

    def mark(stamp: str | None) -> None:
        (theirs / bundle.IN_USE_MARKER).write_text(json.dumps(
            {"host": socket.gethostname(), "pid": process.pid, "started": f"{started:.3f}",
             **({"stamp": stamp} if stamp else {})}), encoding="utf-8")

    step_the_clock(3600.0)
    assert bundle._working_folder_in_use(mine)
    mark("linux:boot-a:6060")
    assert bundle._working_folder_in_use(theirs)
    mark("linux:boot-a:6061")
    assert not bundle._working_folder_in_use(theirs), "control: a reused pid"


# -- the guard keeps the same rule ---------------------------------------------


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("_process_start_guard", REPO / GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self, level: str, event: str, **fields: object) -> None:
        self.events.append(event)


def test_the_guards_stamp_is_the_helpers(guard, other_process):
    process, _started = other_process
    for pid in (os.getpid(), process.pid):
        assert guard._process_start_stamp(pid) == process_start.stamp_of(pid)


def test_the_guard_reaps_its_orphan_after_the_clock_moved(guard, monkeypatch):
    """A server recorded with its stamp, the clock 30 s on since: still the
    guard's child. Another stamp: a reused pid, never killed."""
    monkeypatch.setattr(guard, "_process_started_at", lambda pid: 1030.0)
    monkeypatch.setattr(guard, "_process_start_stamp", lambda pid: "linux:boot-a:777")
    record = {"child_created": 1000.0, "child_stamp": "linux:boot-a:777", "child_stamp_pid": 4242}
    log = _Log()
    assert guard._recorded_child_is_ours(4242, record, log) and "orphan.pid_reused" not in log.events
    assert not guard._recorded_child_is_ours(4242, {**record, "child_stamp": "linux:boot-a:778"}, log)
    assert "orphan.pid_reused" in log.events
    log = _Log()
    assert not guard._recorded_child_is_ours(4242, {"child_created": 1000.0}, log), (
        "control: by the creation time alone, 30 s of drift reads as a reused pid")
    assert "orphan.pid_reused" in log.events


def test_the_guard_records_its_childs_stamp(guard, monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(guard, "_process_started_at", lambda pid: 1000.0)
    monkeypatch.setattr(guard, "_process_start_stamp", lambda pid: f"linux:boot-a:{pid}")
    guard._record_child(4242)
    state = guard._read_state()
    assert state["child_created"] == 1000.0 and state["child_stamp"] == "linux:boot-a:4242"
    assert state["child_stamp_pid"] == 4242


def _rolled_back_and_forward(guard, monkeypatch, tmp_path):
    """A guard of this version recorded child 4242; a guard from before the
    stamps (a rollback) then started child 5151 and merged only its pid and
    time into the state, as it does; this version runs again."""
    monkeypatch.setattr(guard, "_state_path", lambda: tmp_path / "state.json")
    stamps = {4242: "linux:boot-a:777", 5151: "linux:boot-a:888"}
    monkeypatch.setattr(guard, "_process_start_stamp", lambda pid: stamps.get(pid))
    monkeypatch.setattr(guard, "_process_started_at", lambda pid: {4242: 1000.0, 5151: 2000.0}.get(pid))
    guard._record_child(4242)
    guard._update_state(child_pid=5151, child_created=2000.0, guard_pid=1)  # the older guard's write
    return guard._read_state()


def test_a_stamp_left_by_an_earlier_child_does_not_stop_the_reap(guard, monkeypatch, tmp_path):
    state = _rolled_back_and_forward(guard, monkeypatch, tmp_path)
    assert state["child_stamp"] == "linux:boot-a:777" and state["child_pid"] == 5151
    log = _Log()
    assert guard._recorded_child_is_ours(5151, state, log), "the time settles it: the stamp is 4242's"
    assert "orphan.pid_reused" not in log.events


def test_the_unbound_stamp_called_the_new_child_a_reused_pid(guard, monkeypatch, tmp_path):
    """Negative control on the guard as it was (bc241508d): the stale stamp
    read the older guard's live child as a reused pid, and it was not reaped."""
    state = _rolled_back_and_forward(guard, monkeypatch, tmp_path)
    source = subprocess.run(["git", "show", f"bc241508dc879f591adc7088079e62c22d5d7a67:{GUARD}"], cwd=REPO,
                            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    old = types.ModuleType("_process_start_guard_before")
    old.__file__ = str(REPO / GUARD)
    monkeypatch.setitem(sys.modules, old.__name__, old)
    exec(compile(source, "<kazma_guard before>", "exec"), old.__dict__)  # noqa: S102 -- the repo's own file
    monkeypatch.setattr(old, "_process_start_stamp", guard._process_start_stamp)
    monkeypatch.setattr(old, "_process_started_at", guard._process_started_at)
    log = _Log()
    assert not old._recorded_child_is_ours(5151, state, log)
    assert "orphan.pid_reused" in log.events


# -- the gate: nothing else reads a start -------------------------------------

#: A path to the start of a process: /proc/stat (the boot time) or
#: /proc/<pid>/stat (the start ticks).
_PROC_STAT = re.compile(r"^/proc/[^/]+/stat$|^/proc/stat$")
_ALL_KINDS = {"create_time", "boot_time", "attrs", "info", "btime", "/proc stat", "ps start"}
#: Files allowed some kinds, in which functions (None: anywhere), and why.
ALLOWED: dict[str, tuple[set[str], set[str] | None, str]] = {
    HELPER: (_ALL_KINDS, None, "the helper itself"),
    GUARD: ({"btime", "/proc stat"}, {"_process_started_at", "_process_start_stamp"},
            "loaded by path with the standard library only: its time for display and old state files, "
            "and its own copy of the Linux stamp, held to the helper's above"),
}


def _text(node: ast.AST) -> str | None:
    """A string literal's text (an f-string's placeholders as ``{}``)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        value = node.value
        return value.decode("latin-1") if isinstance(value, bytes) else value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    return None


def _texts(node: ast.AST) -> list[str]:
    return [text for inner in ast.walk(node) if (text := _text(inner)) is not None]


def _builds_proc_stat(node: ast.AST) -> bool:
    """``Path('/proc') / pid / 'stat'``, ``os.path.join('/proc', pid, 'stat')``:
    a /proc path and a ``stat`` part in one expression."""
    texts = _texts(node)
    return any(t.startswith("/proc") for t in texts) and any(t.strip("/") == "stat" for t in texts)


def _is_a_process(func: ast.AST) -> bool:
    """``psutil.Process(...).as_dict`` or ``proc.as_dict`` / ``p.as_dict``: a
    receiver that reads as a process (rows, events and models have their own
    ``as_dict``)."""
    receiver = getattr(func, "value", None)
    if isinstance(receiver, ast.Call):
        return _call_name(receiver) == "Process"
    name = getattr(receiver, "id", "") or getattr(receiver, "attr", "")
    return name in ("p", "child", "parent") or "proc" in name.lower()


def _call_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")


def start_reads(source: str) -> list[tuple[int, str, str]]:
    """Every read of a process start in *source*: (line, kind, the function it is in)."""
    tree = ast.parse(source)
    functions = [(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def within(line: int) -> str:
        holding = [f for f in functions if f[0] <= line <= f[1]]
        return max(holding)[2] if holding else ""

    found: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in ("create_time", "boot_time"):
                found.add((node.lineno, name))
            elif name == "getattr" and len(node.args) > 1 and _text(node.args[1]) in ("create_time", "boot_time"):
                found.add((node.lineno, "attrs"))
            elif name == "as_dict" and not node.args and not node.keywords and _is_a_process(node.func):
                found.add((node.lineno, "attrs"))  # every attribute, the start among them
            elif name in ("as_dict", "process_iter") and "create_time" in _texts(node):
                found.add((node.lineno, "attrs"))
            elif (name == "get" and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "info"
                  and node.args and _text(node.args[0]) == "create_time"):
                found.add((node.lineno, "info"))
            elif name in ("join", "Path", "PurePath", "PosixPath") and _builds_proc_stat(node):
                found.add((node.lineno, "/proc stat"))
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
              and node.value.attr == "info" and _text(node.slice) == "create_time"):
            found.add((node.lineno, "info"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _builds_proc_stat(node):
            found.add((node.lineno, "/proc stat"))
        elif (isinstance(node, ast.Attribute) and node.attr in ("st_ctime", "st_mtime")
              and any(t.startswith("/proc") for t in _texts(node.value))):
            found.add((node.lineno, "/proc stat"))  # the /proc/<pid> directory's time
        text = _text(node) if isinstance(node, (ast.Constant, ast.JoinedStr)) else None
        if text is not None:
            if text.startswith("btime"):
                found.add((node.lineno, "btime"))
            if _PROC_STAT.match(text):
                found.add((node.lineno, "/proc stat"))
            if re.search(r"\b(lstart|etimes|etime)=", text):
                found.add((node.lineno, "ps start"))  # ps -o lstart= and friends
    return sorted((line, kind, within(line)) for line, kind in found)


def violations(name: str, source: str) -> list[str]:
    """What the gate refuses in file *name*: every read not allowed there."""
    kinds, functions, _why = ALLOWED.get(name, (set(), None, ""))
    return [f"{name}:{line} {kind} in {function or 'the module'}" for line, kind, function in start_reads(source)
            if kind not in kinds or (functions is not None and function not in functions)]


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
        path = REPO / name
        if path.exists():
            found += violations(name, path.read_text(encoding="utf-8"))
    assert not found, (
        "a module reads a process's start itself; on Linux a reading moves with the wall clock. "
        "Use kazma_core.process_start (own_identity, is_this_process, is_start_of): " + ", ".join(found))


def test_the_gate_reads_the_real_files_and_sees_the_helper_and_the_guard():
    """Negative control: the enumeration holds the helper, the guard and the
    sites it replaced, and the scanner finds what the two allowed files read,
    in the guard only inside its two functions."""
    names = set(product_sources())
    assert {HELPER, GUARD} <= names, "stage the helper: the gate reads git-listed files"
    assert {"kazma-core/kazma_core/web_acquire/crawl_jobs.py", "kazma-core/kazma_core/documents/admission.py",
            "kazma-core/kazma_core/migration/bundle.py", "kazma-core/kazma_core/web_acquire/origin_state.py",
            "kazma-core/kazma_core/tools/research_session.py",
            "kazma-core/kazma_core/web_acquire/artifact_storage.py"} <= names
    helper = {kind for _line, kind, _fn in start_reads((REPO / HELPER).read_text(encoding="utf-8"))}
    assert {"create_time", "/proc stat"} <= helper
    guard = start_reads((REPO / GUARD).read_text(encoding="utf-8"))
    assert {kind for _line, kind, _fn in guard} == {"btime", "/proc stat"}
    assert {fn for _line, _kind, fn in guard} == {"_process_started_at", "_process_start_stamp"}


def test_a_btime_read_elsewhere_in_the_guard_is_refused():
    """Negative control for the guard's allowance: the same read planted in
    another function of the real guard source fails the gate."""
    source = (REPO / GUARD).read_text(encoding="utf-8")
    assert violations(GUARD, source) == []
    planted = source + (
        "\n\ndef _uptime_check(pid):\n"
        "    return [l for l in Path('/proc/stat').read_text().splitlines() if l.startswith('btime ')]\n")
    found = violations(GUARD, planted)
    assert len(found) == 2 and all("in _uptime_check" in f for f in found), found
    assert violations("kazma-core/kazma_core/elsewhere.py", source), "the guard's reads, anywhere else"


@pytest.mark.parametrize("snippet, kind", [
    ("ok = owner['started'] == psutil.Process().create_time()", "create_time"),
    ("started = f'{psutil.Process(os.getpid()).create_time():.3f}'", "create_time"),
    ("seen.add((child.pid, child.create_time()))", "create_time"),
    ("booted = psutil.boot_time()", "boot_time"),
    ("info = p.as_dict(attrs=['pid', 'create_time'])", "attrs"),
    ("info = p.as_dict()", "attrs"),
    ("info = psutil.Process(pid).as_dict()", "attrs"),
    ("procs = list(psutil.process_iter(['pid', 'create_time']))", "attrs"),
    ("started = p.info['create_time']", "info"),
    ("started = p.info.get('create_time')", "info"),
    ("started = getattr(p, 'create_time')()", "attrs"),
    ("boot = [line for line in lines if line.startswith(b'btime ')]", "btime"),
    ("text = Path('/proc/stat').read_text()", "/proc stat"),
    ("text = Path(f'/proc/{pid}/stat').read_text()", "/proc stat"),
    ("text = open('/proc/self/stat').read()", "/proc stat"),
    ("text = (Path('/proc') / str(pid) / 'stat').read_text()", "/proc stat"),
    ("text = Path('/proc', str(pid), 'stat').read_text()", "/proc stat"),
    ("text = open(os.path.join('/proc', str(pid), 'stat')).read()", "/proc stat"),
    ("born = os.stat(f'/proc/{pid}').st_ctime", "/proc stat"),
    ("out = run(['ps', '-o', 'lstart=', '-p', str(pid)])", "ps start"),
    ("out = run(['ps', '-o', 'etimes=', '-p', str(pid)])", "ps start"),
])
def test_the_scanner_sees_each_shape(snippet, kind):
    """Negative control: each shape of a start read, planted."""
    assert kind in {found for _line, found, _fn in start_reads(snippet)}


@pytest.mark.parametrize("snippet", [
    "when = message.get('create_time')",
    "when = raw.info.get('title')",
    "boot = _boot_time(row[0])",
    "info = p.as_dict(attrs=['pid', 'name'])",
    "row = event.as_dict()",
    "query = {'start_time': since, 'url': f'{base}?start_time={since}'}",
    "text = Path('/proc/self/status').read_text()",
    "group = Path('/proc/self/cgroup').read_text()",
    "text = (Path('/proc') / 'self' / 'status').read_text()",
    "ns = os.readlink('/proc/self/ns/net')",
    "boot = Path('/proc/sys/kernel/random/boot_id').read_text()",
    "size = os.stat(path).st_mtime",
    "out = run(['ps', '-o', 'comm=', '-p', str(pid)])",
])
def test_the_scanner_leaves_what_is_not_a_start(snippet):
    """A chat export's ``create_time`` field, Postgres's start, other
    ``/proc`` files and other ``ps`` columns are not a process's start."""
    assert start_reads(snippet) == []
