<!-- AGENTS.md, piece testing-2-full-suite. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Fast FULL suite (use this — ~10 min, not 40):**
  `python scripts/fast_test.py`
  Crash-tolerant chunked runner: file-chunks run as independent serial pytest
  processes; crashed/empty chunks are retried per-file; poison files are
  reported. One chunk per CPU, at most 8 (`DEFAULT_MAX_CHUNKS`: 32 chunks
  each loading torch crashed 12 of them, 2026-09-25); CI passes
  `--chunks 4` and no budget (a fixed 1500 s was below what ~400 files need
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
