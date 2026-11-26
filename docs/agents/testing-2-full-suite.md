<!-- AGENTS.md, piece testing-2-full-suite. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Before every push: `python scripts/prepush.py`** (2026-11-25, the owner:
  no time spent "on waiting on every small changes"; a merge had passed 31,438
  tests here and failed CI 80 minutes later on one browser file nobody ran and
  a Linux-only race). What CI would say, here, in about 15 minutes, told only
  when it fails (whole outputs in `.pytest-tmp/prepush/`):
  - the tests **near** the change, from git against `origin/main`: changed test
    files, and those that import, name or read a changed file (Windows);
  - **Linux**, Python 3.11, no psycopg, CI's locked set: the same near files
    and every gate that reads the source (`GATE_PATTERN`), in a container of
    Docker Desktop's (`deploy/prepush/Dockerfile`, built once per `uv.lock`;
    the working tree copied in with its history; tmpfs for source and temp);
  - **every browser file**, 8 at a time (`--near`: only the near ones). A file
    that fails beside the others runs once more alone and both answers are
    said: alone is how CI runs it. A unit test that does so lost a race: near
    the change it fails, elsewhere it is named;
  - CI's own ruff and bandit commands (read from `ci.yml`, run from the locked
    versions), `--check-readme`, the JavaScript parse, `agents_md.py check`.

  `--only` / `--skip` a part, `--whole` the whole suite on Linux, `--gates-here`
  the gates on Windows too, `--list` the plan. It refuses to start over another
  full suite or prepush, and when Python would import another checkout's code;
  runs below normal priority; takes fewer processes when less memory is free;
  asks no Docker, WSL or live install to change. Gate: `tests/test_prepush.py`.
- **How long a change takes now.** A small change: prepush, push, CI (about 25
  minutes), deploy. A whole stage: the same, and the full suite here once
  (below; `prepush.py --whole` is the suite on CI's platform, the Windows run
  is for what only Windows shows). Still the owner's rules: one full suite at a
  time, no files edited under it, nothing marked or skipped to get a pass, a
  red check on `main` fixed at once.
- **CI's shards** (`scripts/ci_shards.py`, gate `tests/test_ci_shards.py`).
  `Tests` is six shards of `fast_test.py --shard K/6 --chunks 4`, `Playwright
  smoke` four of `ci_shards.py e2e`; on one runner they took 52-82 and 34
  minutes. A file's shard is a function of its path and the committed table
  (`scripts/ci_test_durations.json`; a hash of the path when it is not there),
  so every file is in exactly one, and a new file moves no other. A new browser
  file goes at the end of the list in `ci.yml`. The aggregate jobs keep the two
  names branch protection requires and fail when a shard failed, was cancelled
  or skipped. Each shard uploads what its files took: when the balance gate
  fails, `python scripts/ci_shards.py update <green run id>`.
- **Measured on this machine** (32 logical cores, 31 GB, 2026-11-25/26; the
  live install and other sessions running). More processes do not make Windows
  faster: the CPUs sat at 42% on average under 14 of them, and what was slow
  was SQLite commits, file reads and process starts. The whole suite in the Linux
  container, its files in memory: 1,395 s with 8 processes, 928 s with 14
  (31,234 passed), against 49 minutes on Windows idle with 8. Near tests and
  gates, about 330 files: 1,514 s on Windows, 504 s there. `fast_test.py` stays at 8
  processes: on a sixth of the suite 14 took 307 s and 8 took 356 s (one file,
  `tests/test_guard_integration.py`, is 304 s of it), and a WHOLE run at 14 was
  worse: after 37 minutes at least three processes had run out of their 900 s or lost a
  test to its 120 s, and were being run again. Browser files, all 101: 6 at a
  time 785 s, 12 at a time 604 s with one test lost to timing, 8 beside the
  other parts 940 s; `tests/e2e/test_pages_load_clean.py` alone is 525 s (it
  waits 8 s on each page that holds a stream open), so prepush deals its tests
  to four processes. A CI unit shard on 4 CPUs: 483 s; before, 3,038-4,197 s.
- **The FULL suite (a whole stage, once; ~49 minutes here, idle):**
  `python scripts/fast_test.py`
  Crash-tolerant chunked runner: file-chunks run as independent serial pytest
  processes; crashed/empty chunks are retried per-file; poison files are
  reported. One chunk per CPU, at most 8 (`DEFAULT_MAX_CHUNKS`: 32 chunks
  each loading torch crashed 12 of them, 2026-09-25); CI passes
  `--chunks 4` (per shard) and no budget (a fixed 1500 s was below what ~400 files need
  on CI's runner: every chunk ran out of time in November 2026 and was
  re-run whole, 60-75 minutes a run). Locally, pass NO `--chunks`. **A
  pytest process runs at most `--files-per-process` files** (2026-11-23;
  default `DEFAULT_FILES_PER_PROCESS`, 60; CI's Tests job and the coverage
  run pass it): a chunk runs its files as consecutive pytest processes
  (`split_into_pieces`: even, in the chunk's order), one after another
  (`run_chunk_in_pieces`), so as many processes run at once as before; each
  has its own budget (its own files, 10 s each, floor 900 s; an explicit
  `--chunk-timeout` is every process's), its own tally, its own line in the
  log as it ends ("chunk 00, process 3/8") and its own recovery -- a process
  that crashes or runs out of time costs the chunk's other processes
  nothing. `0` is one process per chunk, the runner before (the control in
  `tests/test_fast_test_runner.py`, real processes). Why: CI's runner (4
  vCPUs, 16 GB, ~470 files a process) was ended from outside 30-40 minutes
  in, run after run; every chunk process grew for the whole run (3.8, 3.6,
  3.5 and 2.8 GB at 40 minutes, 14.6 of 16 GB used; 5.1 GB at 52 on an
  earlier run), and `MALLOC_ARENA_MAX=2` changed nothing: what a test
  leaves reachable stays until its process ends. Measured on chunk 00 of
  the 4-chunk layout (471 files, Windows, private bytes, one test thread):
  one process 5.53 GB at its end; at most 120 files a process 1.43-2.34 GB;
  at most 60, 0.77-1.72 GB; the same 7,033 passed and 54 skipped all three
  ways, 2,953 / 2,962 / 2,966 s (about 6 s of start-up for each extra
  process). The worst process at any size holds `tests/test_settings_scope.py`
  (about 710 MB kept, building the app several times) and
  `tests/test_static_gates.py` (about 400 MB) -- the next thing to shrink.
  **Every
  pytest run is one killable unit** (2026-09-30: a `--chunks 4` run took
  1 h 22 min): a chunk's budget scales with its files (10 s each, floor 900 s)
  unless given; a timeout kills the whole tree -- a kill-on-close Job Object on
  Windows, where the venv's `python.exe` is a launcher and killing it left the
  interpreter running tests while CPython's post-timeout `communicate()` waited
  with no timeout; a process group on POSIX -- and the drain after it is
  bounded; a finished run's leftovers are reaped (Windows); and a chunk that
  ran out of TIME is re-run as parallel pieces, not one process per file,
  serially (`tests/test_fast_test_runner.py`, real processes, with the old
  direct-child kill as the negative control). Every pytest process it starts first lowers itself below a
  normal program (`pytest_command`: Windows CPU class below normal and
  memory priority low; `nice` elsewhere; `--foreground` opts out), and gets
  its share of math-library threads (CPUs / chunks, `OMP_NUM_THREADS` & co.
  unless already set): the live install runs on the machine the suite runs
  on, and two minutes into a 4-chunk run its event loop froze for 18.5 s
  (2026-09-29). The DISK priority is left alone, measured: low I/O priority
  took 476 SQLite-heavy tests from 80 s to 147-199 s, and a full run with
  it timed out every chunk; Windows' background mode made `import torch`
  take 244 s instead of 4.4 (`tests/test_fast_test_runner.py`). It PRINTS the per-chunk FAILURES tracebacks (deep-audit
  2026-08-19 — they used to be captured and discarded, leaving CI-only
  failures undiagnosable) and treats pytest exit 5 ("no tests collected",
  i.e. module-level importorskip like the Playwright e2e suite on a
  `.[test]`-only install) as a benign skip, not POISON. Per-file exit 0/1
  without a terminal tally is unmeasured: pytest-timeout exits 1 without that
  summary. Both isolation retries require a parsed tally (exit 5 remains an
  explicit no-tests skip), otherwise the file is POISON. A timed-out/crashed
  chunk's parsed tally is not added before recovery: pytest may print its
  summary and hang during shutdown. Count the recovered files once, retaining
  observed failure identities so a clean retry never erases them. The original
  double-count boundary is covered in `tests/test_fast_test_runner.py`.
  Both isolation retries require a parsed tally (exit 5 remains an
  explicit no-tests skip), otherwise the file is POISON. The original boundary
  is a negative control in `tests/test_fast_test_runner.py`. Do NOT use
  pytest-xdist here — worker segfaults (native lib) make
  it silently drop ~half the suite. The serial monolithic run intermittently
  segfaults and takes 20+ min.
  **Quiet hours** (2026-11-17, the owner: "My pc is whining because of the
  test suite"): `--cores N` holds a run to N cores (0: all), and a schedule
  kept beside the checkout, `.kazma/fast_test.json` (git ignores it), holds
  every run to its cores at the hours it names, in its own time zone
  (`scheduled_cores`); the owner's: 8 cores from 01:00 to 13:00 Kuwait time,
  all cores the rest of the day. The cap is the runner's CPU affinity, which
  every process it starts inherits; a held run's chunk budget grows with it.
  Gates in `tests/test_fast_test_runner.py` (a real grandchild's cores, the
  uncapped run as the control).
