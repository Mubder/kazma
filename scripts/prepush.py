#!/usr/bin/env python
"""One command before a push: what CI would say, in minutes, on this machine.

Why this exists (2026-11-25): a merge passed 31,438 tests here and CI then
failed, 80 minutes later, on one browser file that was not in a hand-written
list and on a Linux-only race; the fix cost a second 80-minute round. A day
before, CI failed on a Linux job without psycopg. Each is something this
machine can run first.

Five parts, at the same time, below normal priority, each told only when it
fails (the whole output of each is in ``.pytest-tmp/prepush/``):

* ``tests``   the test files near the change (they import, name or read a
  changed file, or are one; from git, against ``origin/main``), here, in
  ``scripts/fast_test.py``;
* ``linux``   the same near files and every gate that reads the source
  (``GATE_PATTERN``) on Linux, Python 3.11 and no psycopg, as CI's Tests job
  has them, in a container (``deploy/prepush/Dockerfile``) built once per
  ``uv.lock``; the source is copied in, not mounted. The gates read files,
  and a file read is what Windows is slow at: 327 files took 25 minutes here
  and 5 there (``--gates-here`` runs them here as well; ``--whole`` runs the
  whole suite there);
* ``browser`` every file of ``tests/e2e``, several at a time, each a pytest
  process of its own (``--near``: only those near the change);
* ``ruff`` and ``bandit`` as ci.yml runs them, from the locked versions;
* ``readme``  ``generate_metrics.py --check-readme``, the JavaScript syntax
  check and ``agents_md.py check``.

Usage::

    python scripts/prepush.py                     # everything
    python scripts/prepush.py --near              # browser files near the change only
    python scripts/prepush.py --whole             # the whole suite on Linux, not only the near files
    python scripts/prepush.py --only tests,linux  # a part
    python scripts/prepush.py --skip browser
    python scripts/prepush.py --list              # what would run, and run nothing

Exit code: 0 when every part that was asked for ran and passed; 1 when one
failed or could not run; 2 when it refused to start (another full suite or
prepush is running, or the checkout's own code is not what Python imports).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "scripts") not in sys.path:
    sys.path.append(str(REPO / "scripts"))

PACKAGES = ("kazma-core", "kazma-gateway", "kazma-ui", "kazma-cli", "kazma-tui", "kazma-skills")
PARTS = ("tests", "linux", "browser", "ruff", "bandit", "readme")
LOG_DIR = REPO / ".pytest-tmp" / "prepush"

#: A test file holding one of these reads the repository's own source.
GATE_PATTERN = re.compile(r'ls-files|_test_sources|rglob\(|glob\("test_')

#: How many at a time, measured on this machine (32 logical cores, 31 GB):
#: docs/agents/testing-2-full-suite.md has the numbers. ``sized`` takes them
#: down when less memory is free: a run must not push the live install, or
#: whatever else is open, into the page file.
TEST_PROCESSES = 8
BROWSER_PROCESSES = 8
LINUX_PROCESSES = 8
LINUX_WHOLE_PROCESSES = 14
LINUX_CPUS = 18
E2E_TEST_TIMEOUT = 600

#: A browser file longer than this (measured seconds) runs as several pytest
#: processes, its tests dealt among them: one file of nine minutes decided how
#: long the whole part took.
SPLIT_BROWSER_SECONDS, SPLIT_BROWSER_WAYS = 200.0, 4

#: Memory, in GB: what one pytest process takes here, one browser file (its
#: server and its Chromium), one pytest process in the container (and the
#: container's files, which are in memory too), and what is left free.
TEST_GB, BROWSER_GB, LINUX_GB, LINUX_FILES_GB, LEFT_FREE_GB = 0.9, 0.8, 0.8, 2.0, 2.0

#: Near tests are cut at this many measured minutes of one process (the
#: nearest first); what is left is said and is CI's.
NEAR_MINUTES = 25.0

IMAGE = "kazma-prepush"
CONTEXTS = ("desktop-linux", "kazma-engine")

#: The run's source and temporary files are in memory: every SQLite commit
#: waits for the disk, and on Docker's overlay that held a container to one
#: of its twelve CPUs (27 files took 423-583 s; with these, 35-275 s).
LINUX_TMPFS = ("/tmp:exec,size=4g,mode=1777", "/work:exec,size=1g,mode=1777")


def sized(parts: list[str], whole: bool, available_gb: float) -> dict[str, int]:
    """How many at a time each part runs in the memory that is free."""
    full = {"tests": TEST_PROCESSES, "browser": BROWSER_PROCESSES,
            "linux": LINUX_WHOLE_PROCESSES if whole else LINUX_PROCESSES}
    each = {"tests": TEST_GB, "browser": BROWSER_GB, "linux": LINUX_GB}
    files = LINUX_FILES_GB if "linux" in parts else 0.0
    need = sum(full[p] * each[p] for p in full if p in parts)
    room = available_gb - LEFT_FREE_GB - files
    share = min(1.0, max(0.0, room / need)) if need else 1.0
    return {p: max(2, int(full[p] * share)) for p in full}


def available_gb() -> float:
    import psutil

    return psutil.virtual_memory().available / 1024**3


# ── the checkout, its interpreter and its environment ────────────────────

def git(*args: str, env: dict[str, str] | None = None, data: bytes | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, input=data, env=env)
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode("utf-8", "replace")


def git_common_dir() -> Path:
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())


def find_python() -> Path:
    """The interpreter that has Kazma's dependencies: ``KAZMA_GATE_PYTHON``,
    this checkout's virtualenv, or the main checkout's (a worktree has none)."""
    named = (os.environ.get("KAZMA_GATE_PYTHON") or "").strip()
    if named:
        return Path(named)
    for root in (REPO, git_common_dir().parent):
        for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
            if (root / rel).is_file():
                return root / rel
    return Path(sys.executable)


def prepare_environment(python: Path) -> None:
    os.environ["KAZMA_GATE_PYTHON"] = str(python)
    os.environ["PYTHONPATH"] = os.pathsep.join(str(REPO / name) for name in PACKAGES)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["KAZMA_DB_BACKEND"] = "sqlite"
    os.environ["KAZMA_PROCESS_PRIORITY"] = "keep"


def imports_this_checkout(python: Path) -> str:
    """Why *python* would test other code than this checkout's, or ``""``.
    The main checkout's virtualenv has it installed editable: without the
    PYTHONPATH a worktree's tests ran the main checkout's code."""
    out = subprocess.run([str(python), "-c", "import kazma_core, kazma_ui; print(kazma_core.__file__); print(kazma_ui.__file__)"],
                         cwd=REPO, capture_output=True, text=True)
    if out.returncode:
        return f"{python} cannot import Kazma: {out.stderr.strip().splitlines()[-1:]}"
    for line in out.stdout.split("\n"):
        if line.strip() and not Path(line.strip()).resolve().is_relative_to(REPO):
            return f"{python} imports {line.strip()}, not this checkout"
    return ""


def lower_priority() -> None:
    """Below a normal program; every process started from here inherits it."""
    with contextlib.suppress(Exception):
        import psutil

        me = psutil.Process()
        me.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)


# ── only one of these at a time ──────────────────────────────────────────

def is_whole_run(cmdline: list[str]) -> str:
    """What *cmdline* is, when it is a run nothing else may start over."""
    names = [part.replace(chr(92), "/").rsplit("/", 1)[-1].lower() for part in cmdline]
    if not names or not names[0].startswith(("python", "coverage", "py.")):
        return ""  # an editor, a shell or a grep that merely names one
    options = [part.split("=", 1)[0] for part in cmdline]
    if "prepush.py" in names:
        return "" if "--list" in options else "another prepush"
    if "fast_test.py" in names and not {"--files-from", "--list", "-h", "--help"} & set(options):
        return "a full suite (fast_test.py)"
    return ""


def other_runs() -> list[str]:
    """The full suites and prepush runs on this machine that are not this one."""
    import psutil

    me = psutil.Process()
    family = {me.pid, *(p.pid for p in me.parents()), *(p.pid for p in me.children(recursive=True))}
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        what = is_whole_run(proc.info.get("cmdline") or [])
        if what and proc.info["pid"] not in family:
            found.append(f"{what}, process {proc.info['pid']}")
    return found


@contextlib.contextmanager
def only_one() -> Iterator[bool]:
    """Hold the machine's prepush lock; ``False`` when another holds it."""
    path = git_common_dir() / "kazma-prepush.lock"
    handle = open(path, "a+b")  # noqa: SIM115 - held for the whole run
    try:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()


# ── what changed, and the tests near it ──────────────────────────────────

def changed_files(base: str) -> list[str]:
    """Files that differ from the merge base of *base*: commits, the working
    tree, files git does not track yet, and files that are gone."""
    start = git("merge-base", base, "HEAD").strip()
    names = git("diff", "--name-only", "-z", "--no-renames", start).split("\0")
    names += git("ls-files", "-z", "--others", "--exclude-standard").split("\0")
    return sorted({name for name in names if name})


def untracked_files() -> list[str]:
    return [n for n in git("ls-files", "-z", "--others", "--exclude-standard").split("\0") if n]


def is_test_file(name: str, roots: Iterable[str]) -> bool:
    path = Path(name)
    return (name.startswith(tuple(f"{r.rstrip('/')}/" for r in roots)) and path.suffix == ".py"
            and (path.name.startswith("test_") or path.name.endswith("_test.py")))


def patterns_for(name: str) -> list[re.Pattern[str]]:
    """What a test that imports, names or reads *name* has in its text."""
    path = Path(name)
    stem, base = path.stem, path.name
    found: list[str] = []
    if path.suffix == ".py":
        parts = list(path.with_suffix("").parts)
        if parts[0] in PACKAGES:
            parts = parts[1:]
        if parts[-1] == "__init__":
            package = r"\.".join(map(re.escape, parts[:-1]))
            return [re.compile(rf"(?:from\s+{package}\s+import|import\s+{package})\b(?!\.)"),
                    re.compile(rf"\b{re.escape('/'.join(parts))}\.py\b")]
        dotted = r"\.".join(map(re.escape, parts))
        parent = r"\.".join(map(re.escape, parts[:-1]))
        found.append(rf"\b{dotted}\b")
        if parent:
            found.append(rf"from\s+{parent}\s+import\s+(?:\([^)]*|[^\n(]*)\b{re.escape(stem)}\b")
        if parts[0] in ("scripts", "tests", "fuzz") or stem.startswith("_"):
            found.append(rf"""["']{re.escape(stem)}["']|\bimport\s+{re.escape(stem)}\b|\bfrom\s+\S*\b{re.escape(stem)}\s+import\b""")
    found.append(rf"(?<![\w.-]){re.escape(base)}(?![\w-])")
    return [re.compile(pattern) for pattern in found]


def near_tests(changed: list[str], texts: dict[str, str]) -> list[str]:
    """The test files of *texts* near *changed*, nearest first.

    A changed test file comes first. Another is as near as the changed files
    it refers to are rarely referred to: a module one test imports says more
    than a file two hundred tests name."""
    score: dict[str, float] = {name: float("inf") for name in changed if name in texts}
    for name in changed:
        if name in texts:
            continue
        patterns = patterns_for(name)
        stem = Path(name).parent.name if Path(name).stem == "__init__" else Path(name).stem
        hits = [test for test, text in texts.items()
                if stem in text and any(pattern.search(text) for pattern in patterns)]
        for test in hits:
            score[test] = score.get(test, 0.0) + 1.0 / len(hits)
    return sorted(score, key=lambda test: (-score[test], test))


def cut_to_budget(files: list[str], seconds: dict[str, float], budget_s: float) -> tuple[list[str], list[str]]:
    """*files*, nearest first, while their measured seconds fit; and the rest."""
    known = sorted(seconds.values())
    median = known[len(known) // 2] if known else 1.0
    kept: list[str] = []
    left: list[str] = []
    total = 0.0
    for name in files:
        total += seconds.get(name, median)
        (kept if total <= budget_s or not kept else left).append(name)
    return kept, left


def read_texts(names: Iterable[str]) -> dict[str, str]:
    return {name: (REPO / name).read_text(encoding="utf-8", errors="replace") for name in names}


# ── the parts ────────────────────────────────────────────────────────────

class Part:
    """One part of the run: what it found, and how long it took."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = False
        self.seconds = 0.0
        self.note = ""
        self.failures = ""
        self.log = LOG_DIR / f"{name}.log"


_processes: set[subprocess.Popen] = set()
_processes_lock = threading.Lock()
_stopping = threading.Event()


def run_logged(cmd: list[str], log: Path, *, stdin: bytes | None = None, timeout: float = 3600) -> tuple[int, str]:
    """Run *cmd* from the checkout, its output to *log*; the code and output."""
    if _stopping.is_set():
        return 130, ""
    with open(log, "ab") as out:
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL)
        with _processes_lock:
            _processes.add(proc)
        try:
            proc.communicate(stdin, timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(30)
            code = 124
        finally:
            with _processes_lock:
                _processes.discard(proc)
    return code, log.read_text(encoding="utf-8", errors="replace")


def stop_everything() -> None:
    _stopping.set()
    with _processes_lock:
        for proc in list(_processes):
            with contextlib.suppress(OSError):
                proc.kill()


#: How failing tests are run once more.
ALONE = ("-q", "-rfE", "-p", "no:cacheprovider", "--timeout=300")


def suite_failures(output: str) -> str:
    """What ``fast_test.py`` says about what failed: its own closing words."""
    for marker in (" failing tests:", "POISON files", "suite ran no tests", "no such test file", "no test files"):
        at = output.find(marker)
        if at >= 0:
            return output[output.rfind("\n", 0, at) + 1:]
    return "\n".join(output.splitlines()[-40:])


def suite_totals(output: str) -> str:
    found = re.findall(r"TOTALS in \d+s: (.*)", output)
    return found[-1] if found else "no totals"


def failed_tests(output: str) -> list[str]:
    """The failing tests ``fast_test.py`` names, without their parameters."""
    return sorted({t.split("[", 1)[0] for t in re.findall(r"^  FAILED (\S+::\S+)", output, re.M)})


def judge(part: Part, code: int, output: str, near: Iterable[str], alone: Callable[[list[str]], tuple[int, str]]) -> None:
    """Whether a run of ``fast_test.py`` passed.

    Failing tests run once more, alone. One that fails again failed. One that
    passes alone lost a race with the others: in a file near the change that
    is a failure (a Linux-only race in a changed test cost a second CI round
    on 2026-11-25); elsewhere it is named, and is not this change's."""
    part.ok = code == 0
    part.note = suite_totals(output)
    if part.ok:
        return
    part.failures = suite_failures(output)
    failed = failed_tests(output)
    if not failed or "POISON" in output or len(failed) > 40:
        return
    again_code, again = alone(failed)
    again_failed = {t.split("[", 1)[0] for t in re.findall(r"^(?:FAILED|ERROR) (\S+::\S+)", again, re.M)}
    if again_code not in (0, 1) or (again_code == 1 and not again_failed):
        return
    raced = [t for t in failed if t not in again_failed]
    near_files = set(near)
    part.ok = not again_failed and not any(t.split("::")[0] in near_files for t in raced)
    if raced:
        part.note += f"; failed beside the others and passed alone: {', '.join(raced)}"
        part.failures = f"passed when run alone (a race): {', '.join(raced)}\n{part.failures}"


def write_list(name: str, files: Iterable[str]) -> Path:
    path = LOG_DIR / name
    path.write_text("".join(f"{f}\n" for f in files), encoding="utf-8", newline="\n")
    return path


def run_tests(part: Part, files: list[str], processes: int, near: Iterable[str] = ()) -> None:
    listing = write_list("tests.txt", files)
    code, output = run_logged([sys.executable, str(REPO / "scripts" / "fast_test.py"), "--files-from", str(listing),
                               "--chunks", str(processes), "--files-per-process", "40",
                               "--durations-json", str(LOG_DIR / "tests-durations.json")], part.log)
    judge(part, code, output, near, lambda ids: run_logged(
        [sys.executable, "-m", "pytest", *ids, *ALONE], part.log.with_name("tests-alone.log")))


def browser_units(name: str, seconds: float, python: str) -> list[list[str]]:
    """The pytest processes one browser file runs as."""
    if seconds <= SPLIT_BROWSER_SECONDS:
        return [[name]]
    done = subprocess.run([python, "-m", "pytest", name, "--collect-only", "-q", "-p", "no:cacheprovider",
                           "-o", "addopts="], cwd=REPO, capture_output=True, text=True, timeout=300)
    ids = [line.strip().replace("\\", "/") for line in done.stdout.splitlines() if "::" in line]
    if done.returncode or len(ids) < 2:
        return [[name]]  # whole: its own run says what is wrong with it
    ways = min(SPLIT_BROWSER_WAYS, len(ids))
    return [ids[n::ways] for n in range(ways)]


def run_browser(part: Part, files: list[str], processes: int) -> None:
    """Each browser file in a pytest process of its own, several at a time.

    One that fails beside the others is run again alone, and both answers are
    said: CI runs a shard's files one after another, so what it does alone is
    what CI will see."""
    import ci_shards
    import fast_test

    seconds = ci_shards.load_table("e2e")
    units: list[tuple[str, list[str], float]] = []
    for name in files:
        each = browser_units(name, seconds.get(name, 30.0), sys.executable)
        units += [(name if len(each) == 1 else f"{name} ({n} of {len(each)})", ids, seconds.get(name, 30.0) / len(each))
                  for n, ids in enumerate(each, 1)]
    units.sort(key=lambda unit: (-unit[2], unit[0]))
    fast_test.THREADS_PER_PROCESS = 2
    took: dict[str, float] = {}
    logs: dict[str, str] = {}

    def one(unit: tuple[str, list[str], float]) -> int:
        name, ids, _ = unit
        if _stopping.is_set():
            return 130
        started = time.monotonic()
        code, log = fast_test.run_pytest([*ids, f"--timeout={E2E_TEST_TIMEOUT}"], 1800.0)
        took[name], logs[name] = time.monotonic() - started, log
        with _processes_lock, open(part.log, "a", encoding="utf-8", newline="\n") as out:
            out.write(f"\n===== {name}: exit {code}, {took[name]:.0f} s =====\n{log}\n")
        return code

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, processes)) as pool:
        codes = list(pool.map(one, units))
    failed = [unit for unit, code in zip(units, codes) if code != 0]
    first = {unit[0]: logs.get(unit[0], "") for unit in failed}
    still = [unit[0] for unit in failed if one(unit) != 0]
    alone = [unit[0] for unit in failed if unit[0] not in still]
    order = [unit[0] for unit in units]
    passed = sum(fast_test._parse_summary(logs.get(n, "")).get("passed", 0) for n in order)
    skipped = sum(fast_test._parse_summary(logs.get(n, "")).get("skipped", 0) for n in order)
    part.ok = not still and passed > 0
    part.note = f"{len(files)} files, {passed} passed, {skipped} skipped"
    if alone:
        part.note += f"; failed beside the others and passed alone: {', '.join(alone)}"
    if still or not passed:
        part.failures = "\n".join(f"--- {n}\n{fast_test._failure_digest(logs[n] or first[n], limit=4000) or logs[n][-2000:]}"
                                  for n in still) or "no browser test ran"
    (LOG_DIR / "browser-durations.json").write_text(json.dumps(took, indent=1, sort_keys=True), encoding="utf-8")


# ── Linux, Python 3.11, no psycopg ───────────────────────────────────────

def _docker_candidates() -> list[str]:
    """PATH's docker, then Docker Desktop's and Kazma's own engine's install folders."""
    out = [shutil.which("docker") or ""]
    if os.name == "nt":
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432")] + [r"C:\Program Files"]
        out += [str(Path(r) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe") for r in roots if r]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out.append(str(Path(local) / "Kazma" / "engine" / "bin" / "docker.exe"))
    return [c for c in dict.fromkeys(out) if c]


_DOCKER: list[str] = []


def docker_exe() -> str:
    """The first docker CLI that starts (2026-11-26: Windows' Application
    Control began refusing the copy Kazma's engine put first on PATH, and the
    Linux part found no Docker). As ``kazma_core.docker_cli`` does, a CLI the
    system refuses is passed over; this script does not import Kazma."""
    if not _DOCKER:
        for candidate in _docker_candidates():
            try:
                subprocess.run([candidate, "--version"], capture_output=True, timeout=15, check=False)
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                continue
            _DOCKER.append(candidate)
            break
        else:
            _DOCKER.append("docker")
    return _DOCKER[0]


def docker_context() -> str:
    """The first Docker that answers: Docker Desktop, else Kazma's own engine
    (the live install's Neo4j and SearXNG run there). Never started here."""
    for context in CONTEXTS:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            done = subprocess.run([docker_exe(), "--context", context, "info", "--format", "{{.OSType}}"],
                                  capture_output=True, text=True, timeout=15)
            if done.returncode == 0 and done.stdout.strip() == "linux":
                return context
    return ""


def locked_requirements() -> str:
    """The Tests job's locked set (ci.yml's install step), with hashes."""
    done = subprocess.run(["uv", "export", "--locked", "--no-emit-project", "--no-header", "--format",
                           "requirements-txt", "--extra", "test", "--group", "build", "--group", "ci-light",
                           "--python", sys.executable, "-q"], cwd=REPO, capture_output=True)
    if done.returncode:
        raise RuntimeError(f"uv export: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode("utf-8").replace("\r\n", "\n")


def image_tag(dockerfile: str, requirements: str) -> str:
    return f"{IMAGE}:{hashlib.sha256((dockerfile + requirements).encode()).hexdigest()[:12]}"


def ensure_image(context: str, log: Path) -> str:
    """The image for this ``uv.lock``, built once; its tag."""
    dockerfile = (REPO / "deploy" / "prepush" / "Dockerfile").read_text(encoding="utf-8").replace("\r\n", "\n")
    requirements = locked_requirements()
    tag = image_tag(dockerfile, requirements)
    docker = [docker_exe(), "--context", context]
    if subprocess.run([*docker, "image", "inspect", tag], capture_output=True).returncode == 0:
        return tag
    print(f"[prepush] linux: building {tag} (once per uv.lock)", flush=True)
    with tempfile.TemporaryDirectory(prefix="kazma-prepush-image-") as folder:
        (Path(folder) / "Dockerfile").write_text(dockerfile, encoding="utf-8", newline="\n")
        (Path(folder) / "requirements.txt").write_text(requirements, encoding="utf-8", newline="\n")
        code, output = run_logged([*docker, "build", "-t", tag, "--label", "io.kazma.prepush=image", folder], log,
                                  timeout=3600)
    if code:
        raise RuntimeError("the image did not build:\n" + "\n".join(output.splitlines()[-25:]))
    return tag


def snapshot_bundle(folder: Path) -> tuple[Path, str]:
    """A git bundle of this checkout as it is now -- the working tree and the
    files git does not track yet -- with its whole history (some tests read
    old commits out of git). Nothing of the checkout changes: the tree is
    written through an index of its own and named by a temporary ref. The
    bundle, and the ref it holds."""
    index = folder / "index"
    shutil.copyfile(git("rev-parse", "--path-format=absolute", "--git-path", "index").strip(), index)
    env = {**os.environ, "GIT_INDEX_FILE": str(index), "GIT_AUTHOR_NAME": "prepush", "GIT_AUTHOR_EMAIL": "prepush@localhost",
           "GIT_COMMITTER_NAME": "prepush", "GIT_COMMITTER_EMAIL": "prepush@localhost"}
    git("add", "-A", env=env)
    commit = git("commit-tree", git("write-tree", env=env).strip(), "-p", "HEAD", "-m", "prepush", env=env).strip()
    ref = f"refs/prepush/{os.getpid()}"
    bundle = folder / "repo.bundle"
    git("update-ref", ref, commit)
    try:
        git("bundle", "create", str(bundle), ref)
    finally:
        git("update-ref", "-d", ref)
    return bundle, ref


def send_file(docker: list[str], name: str, source: Path, target: str) -> str:
    """Copy *source* into the container as *target* (``docker cp`` reaches no
    tmpfs); what went wrong, or nothing."""
    with open(source, "rb") as data:
        sent = subprocess.run([*docker, "exec", "-i", name, "sh", "-c", f"cat > {target}"], stdin=data, capture_output=True)
    return sent.stderr.decode(errors="replace") or "the copy failed" if sent.returncode else ""


def run_linux(part: Part, files: list[str], processes: int, whole: bool = False, near: Iterable[str] = ()) -> None:
    context = docker_context()
    if not context:
        part.failures = f"no Docker answers (contexts {', '.join(CONTEXTS)}); start Docker Desktop, or --skip linux"
        return
    try:
        tag = ensure_image(context, part.log)
    except (RuntimeError, OSError) as exc:
        part.failures = str(exc)
        return
    docker = [docker_exe(), "--context", context]
    name = f"kazma-prepush-{os.getpid()}"
    memory = f"{int(processes * LINUX_GB + LINUX_FILES_GB + 1)}g"
    part.failures = "the container could not be prepared"
    try:
        # --rm and a bounded sleep: whatever happens here, it goes away.
        code, output = run_logged([*docker, "run", "-d", "--rm", "--init", "--name", name, "--label", "io.kazma.prepush=run",
                                   "--memory", memory, "--memory-swap", memory, "--cpus", str(LINUX_CPUS),
                                   "--cpu-shares", "256", "--pids-limit", "4096",
                                   *(word for mount in LINUX_TMPFS for word in ("--tmpfs", mount)),
                                   tag, "sleep", "5400"], part.log)
        if code:
            part.failures = "\n".join(output.splitlines()[-10:])
            return
        with tempfile.TemporaryDirectory(prefix="kazma-prepush-") as folder:
            bundle, ref = snapshot_bundle(Path(folder))
            problem = send_file(docker, name, bundle, "/work/repo.bundle")
        if problem:
            part.failures = problem
            return
        prepare = ("set -e; cd /work; git config --global init.defaultBranch main; git init -q kazma; cd kazma; "
                   f"git fetch -q /work/repo.bundle {ref}; "
                   "git reset -q --hard FETCH_HEAD; rm /work/repo.bundle; mkdir -p .pytest-tmp; "
                   "pip install -q --no-deps --no-build-isolation --root-user-action=ignore -e .; "
                   "! python -c 'import psycopg' 2>/dev/null")
        code, output = run_logged([*docker, "exec", name, "sh", "-c", prepare], part.log, timeout=600)
        if code:
            part.failures = "the container could not be prepared:\n" + "\n".join(output.splitlines()[-15:])
            return
        problem = send_file(docker, name, write_list("linux.txt", files), "/work/kazma/.pytest-tmp/files.txt")
        if problem:
            part.failures = problem
            return
        code, output = run_logged([*docker, "exec", "-w", "/work/kazma", name, "python", "scripts/fast_test.py",
                                   *([] if whole else ["--files-from", ".pytest-tmp/files.txt"]), "--chunks", str(processes),
                                   "--files-per-process", "40"], part.log, timeout=5000)
        part.failures = ""
        judge(part, code, output, near, lambda ids: run_logged(
            [*docker, "exec", "-w", "/work/kazma", name, "python", "-m", "pytest", *ids, *ALONE],
            part.log.with_name("linux-alone.log")))
    finally:
        subprocess.run([*docker, "rm", "-f", name], capture_output=True)


# ── the rest of CI's quick jobs ──────────────────────────────────────────

def ci_commands(job: str, program: str, ci_text: str | None = None) -> list[list[str]]:
    """The gating *program* commands of a ci.yml job, as CI runs them."""
    import shlex

    import yaml

    text = ci_text if ci_text is not None else (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    found = []
    for step in yaml.safe_load(text)["jobs"][job]["steps"]:
        script = str(step.get("run") or "").replace("\\\n", " ")
        for line in script.splitlines():
            words = shlex.split(line, comments=True)
            if words[:1] == [program] and "||" not in words and "--exit-zero" not in words:
                found.append(words)
    return found


#: Tools run as ``python -m <tool>`` instead of the console script uv makes:
#: Windows' Application Control refuses a freshly made ``bandit.exe`` now and
#: then ("os error 4551", 2026-11-26), while the interpreter it runs under starts.
_AS_MODULE = frozenset({"bandit"})


def locked_tool(group: str, words: list[str]) -> list[str]:
    """*words* run from a locked dependency group, in an environment of its
    own: the checkout's virtualenv is not touched."""
    if words and words[0] in _AS_MODULE:
        words = ["python", "-m", *words]
    return ["uv", "run", "--isolated", "--frozen", "--only-group", group, "--python", sys.executable, *words]


def run_commands(part: Part, commands: list[list[str]]) -> None:
    problems = []

    def one(numbered: tuple[int, list[str]]) -> str:
        n, cmd = numbered
        log = part.log.with_name(f"{part.name}-{n}.log")
        log.unlink(missing_ok=True)
        code, output = run_logged(cmd, log, timeout=1200)
        return "" if code == 0 else f"$ {' '.join(cmd)}\n{output[-6000:]}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(commands))) as pool:
        problems = [p for p in pool.map(one, enumerate(commands)) if p]
    part.ok = bool(commands) and not problems
    part.note = f"{len(commands)} commands"
    part.failures = "\n".join(problems) if commands else "ci.yml has no such command any more"


# ── the run ──────────────────────────────────────────────────────────────

def plan_files(base: str, near_minutes: float) -> dict[str, list[str]]:
    import ci_shards
    import fast_test

    roots = fast_test.TEST_DIRS
    changed = changed_files(base)
    names = [n for n in git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
             if n and is_test_file(n, roots) and (REPO / n).is_file()]
    texts = read_texts(names)
    unit = {n: t for n, t in texts.items() if "e2e" not in Path(n).parts}
    e2e = {n: t for n, t in texts.items() if n.startswith("tests/e2e/")}
    near = near_tests(changed, unit)
    if any(n.endswith(".js") for n in changed) and "tests/test_js_suites.py" in unit and "tests/test_js_suites.py" not in near:
        near.append("tests/test_js_suites.py")
    near, left = cut_to_budget(near, ci_shards.load_table("unit"), near_minutes * 60)
    return {"changed": changed, "near": near, "left": left,
            "gates": sorted(n for n, t in unit.items() if GATE_PATTERN.search(t)),
            "browser": sorted(e2e), "browser near": near_tests(changed, e2e)}


def choose_parts(only: str | None, skip: str | None) -> list[str]:
    def parse(text: str) -> list[str]:
        names = [n.strip() for n in text.split(",") if n.strip()]
        unknown = sorted(set(names) - set(PARTS))
        if unknown:
            raise SystemExit(f"[prepush] no such part: {', '.join(unknown)} (parts: {', '.join(PARTS)})")
        return names

    chosen = parse(only) if only else list(PARTS)
    left_out = parse(skip) if skip else []
    return [p for p in PARTS if p in chosen and p not in left_out]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main", help="what the change is measured against")
    ap.add_argument("--near", action="store_true", help="only the browser files near the change")
    ap.add_argument("--only", default=None, metavar="PARTS", help=f"only these parts: {','.join(PARTS)}")
    ap.add_argument("--skip", default=None, metavar="PARTS", help="every part but these")
    ap.add_argument("--whole", action="store_true", help="the whole suite in the Linux container")
    ap.add_argument("--gates-here", action="store_true", help="the source-reading gates here as well as on Linux")
    ap.add_argument("--near-minutes", type=float, default=NEAR_MINUTES,
                    help="measured minutes of near tests, the nearest first (default %(default)s); the rest is CI's")
    ap.add_argument("--processes", type=int, default=None, help="pytest processes of the tests part")
    ap.add_argument("--browsers", type=int, default=None, help="browser files at a time")
    ap.add_argument("--list", action="store_true", help="say what would run, and run nothing")
    args = ap.parse_args(argv)
    parts = choose_parts(args.only, args.skip)

    python = find_python()
    prepare_environment(python)
    if argv is None and Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=REPO)
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(errors="replace")

    with only_one() as alone:
        if not args.list:
            running = other_runs() if alone else ["another prepush"]
            if running:
                print(f"[prepush] refused: {'; '.join(running)} is running. One at a time.")
                return 2
            wrong = imports_this_checkout(python)
            if wrong:
                print(f"[prepush] refused: {wrong}")
                return 2
        return run_parts(args, parts)


def run_parts(args: argparse.Namespace, parts: list[str]) -> int:
    started = time.monotonic()
    lower_priority()
    plan = plan_files(args.base, args.near_minutes)
    browser = plan["browser near"] if args.near else plan["browser"]
    tests = sorted({*plan["near"], *(plan["gates"] if args.gates_here else [])})
    linux = sorted({*plan["near"], *plan["gates"]})
    count = sized(parts, args.whole, available_gb())
    count = {**count, "tests": args.processes or count["tests"], "browser": args.browsers or count["browser"]}
    print(f"[prepush] {len(plan['changed'])} files changed against {args.base}: {len(plan['near'])} test files near them, "
          f"{len(plan['gates'])} source gates, {len(browser)} browser files; parts: {', '.join(parts)}", flush=True)
    print("[prepush] at a time: " + ", ".join(f"{p} {n}" for p, n in count.items() if p in parts), flush=True)
    if plan["left"] and not args.whole:
        print(f"[prepush] {len(plan['left'])} more test files refer to the change and are left to CI "
              f"(--near-minutes {args.near_minutes:g}): {', '.join(plan['left'][:5])}...")
    for name in untracked_files():
        print(f"[prepush] git does not track {name}: the gates that read git do not see it (git add it)")
    if any(Path(n).name == "conftest.py" for n in plan["changed"]):
        print("[prepush] a conftest.py changed: it reaches every test; run scripts/fast_test.py once as well")
    if args.list:
        for title in ("near", "gates", "left"):
            print(f"\n{title}:\n  " + "\n  ".join(plan[title] or ["none"]))
        print("\nbrowser:\n  " + "\n  ".join(browser or ["none"]))
        return 0

    shutil.rmtree(LOG_DIR, ignore_errors=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    work = {
        "tests": lambda p: run_tests(p, tests, count["tests"], plan["near"]),
        "linux": lambda p: run_linux(p, linux, count["linux"], args.whole, plan["near"]),
        "browser": lambda p: run_browser(p, browser, count["browser"]),
        "ruff": lambda p: run_commands(p, [locked_tool("lint", c) for c in ci_commands("lint", "ruff")]),
        "bandit": lambda p: run_commands(p, [locked_tool("scan", c) for c in ci_commands("security", "bandit")]),
        "readme": lambda p: run_commands(p, [[py, "scripts/generate_metrics.py", "--check-readme"],
                                             [py, "scripts/check_js_syntax.py"], [py, "scripts/agents_md.py", "check"]]),
    }
    empty = {"tests": not tests, "browser": not browser}

    def one(name: str) -> Part:
        part = Part(name)
        begun = time.monotonic()
        try:
            if empty.get(name):
                part.ok, part.note = True, "nothing near the change"
            else:
                work[name](part)
        except Exception as exc:  # noqa: BLE001 - a part that breaks is a part that failed, said
            part.ok, part.failures = False, f"{type(exc).__name__}: {exc}"
        part.seconds = time.monotonic() - begun
        print(f"[prepush] {name}: {'ok' if part.ok else 'FAILED'} in {part.seconds:.0f} s ({part.note or 'see below'})",
              flush=True)
        return part

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(parts) or 1) as pool:
            done = list(pool.map(one, parts))
    except KeyboardInterrupt:
        stop_everything()
        raise
    failed = [p for p in done if not p.ok]
    for part in failed:
        print(f"\n[prepush] ===== {part.name} FAILED (all of it: {part.log}) =====\n{part.failures[:12000]}")
    print(f"\n[prepush] {'FAILED: ' + ', '.join(p.name for p in failed) if failed else 'all ok'} "
          f"in {(time.monotonic() - started) / 60:.1f} minutes")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
