<!-- AGENTS.md, piece testing-1-isolation. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
## Testing & Validation

**Answer-quality observations (2026-10-09):**
`observability/answer_quality.py` records at `graph_respond.respond_node`, after
terminal formatting, on a worker thread. A bounded 30-day/10,000-turn SQLite
ledger holds flags and turn/chat identity only; never add prompts, answers,
tool arguments, model reasoning or credentials. Rechecks and plan blocks are
review signals, not accuracy failures (requested JSON/plans may be valid).
Paragraph signals share `answer_format`'s narrow EN/AR predicate and exclude
protected layouts and failed turns. Pauses, cancellations and usage commands
do not pass this terminal node. The quality API is administrator-only; a
storage failure is unavailable, not zero healthy. No external messages are
sent. Gate: `tests/test_answer_quality_monitor.py`.

- **Runtime ownership:** `KazmaAppBuilder` acquires `RuntimeOwnership` before
  services construct and releases only after complete shutdown. Its local
  OS fence is always on. `KAZMA_RUNTIME_HA=1` additionally holds a dedicated
  Postgres session, paired to the complete volume's `.runtime-state-id`.
  Never reconnect that lease or release it while workers still run: session
  loss stops the process. Keep one runtime and qualify CSI storage fencing;
  advisory ownership alone cannot fence a partitioned host's effects.
  Drills: `tests/test_runtime_ownership.py` (marked real Postgres tests),
  deployment contract: `tests/test_kubernetes_runtime_contract.py`.
  **The Kubernetes root filesystem is read-only** (2026-11-11,
  `deploy/kubernetes/runtime.yaml`): every container's security context
  makes its root read-only, and every place Kazma writes is a mount -- the
  `/state` volume (with `KAZMA_ENV_FILE` and `HF_HOME` on it) or an emptyDir
  (`/tmp`, `/home/kazma`, ClamAV's three folders; an init container seeds
  the signatures). The contract test resolves the write locations with
  Kazma's own code in a child under the manifest's environment (every
  zero-argument function of `kazma_core.paths`, the temp folder, the home
  and its caches, the Hugging Face cache, `env_files._install_env_file`, a
  shell tool's home, the skills home, the OCR models) and fails on one
  outside every mount; a planted write, the manifest without its emptyDirs
  and without `KAZMA_ENV_FILE` are the negative controls. A new write
  location outside them needs a mount. Unverified on a cluster.

- **A finished run leaves nothing in the temp folder** (2026-11-14: about
  5 GB from five days of runs). The root conftest's five folders carry their
  process id (`kazma-test-<kind>-<pid>-`), and each run first removes those
  of processes that have ended (`_remove_ended_test_temp`; a folder whose
  process still runs, a soak under pytest, is kept; `KAZMA_TEST_KEEP_TEMP=1`
  keeps all); `scripts/fast_test.py` does the same with its run folders
  (`remove_ended_run_folders`); a helper making a store in a test uses the
  test's `tmp_path`; a soak script removes its scratch folder when the run
  passes (`_scratch` / `_end`, kept and named when it fails); the vault drill
  closes the vault before removing its copy (an open connection left an
  encrypted vault copy per drill on Windows); the git tools' empty hooks
  folder is removed at exit. Gate: `tests/test_test_temp_folders.py` (two real
  runs leave one run's folders; `KAZMA_TEST_KEEP_TEMP=1` as the control), the
  drill's `test_the_drill_leaves_no_copy_of_the_vault`.
- **A test run never touches an install's data** (2026-09-27). The root
  `conftest.py` pins `KAZMA_DATA_DIR` and `KAZMA_USER_HOME` to fresh temp
  folders before any product module is imported and clears the per-store
  path overrides: a live install IS a checkout, and a run inside it (Kazma's
  agent tests its own workspace) wrote test gates, a test chat store and
  test swarm tasks into the live stores. A test that needs a folder of its
  own still sets the variable. Gate: `tests/test_install_data_shield.py`
  (every location `paths.py` resolves, from its source). Since 2026-10-01 it
  pins `KAZMA_SKILLS_HOME` too: the skill folders in the user's home, which
  the live install, running as the same user, reads (§22H).
- **No test reaches the network or loads the real embedder** (2026-10-15,
  `tests/_egress_guard.py`, registered by the root `conftest.py`). Swarm
  pipeline tests loaded bge-m3 from this machine's Hugging Face cache (it
  starved the live server) and built real provider calls. A DNS lookup of
  another host, a connection or UDP send to one (sync sockets and both
  asyncio loops), and the import of the real `sentence_transformers` are
  refused where they are made, and the test FAILS with the call site even
  when the code under test swallowed the refusal. Loopback stays open
  (local servers, the suite's Postgres, Playwright). A test that truly
  needs the real model or a provider is `@pytest.mark.real_model`
  (`KAZMA_TEST_REAL_MODEL=1` runs it), one that needs the internet
  `@pytest.mark.network` (`KAZMA_TEST_NETWORK=1`); `KAZMA_TEST_EGRESS_GUARD=0`
  turns the guard off for debugging. Stub instead:
  `tests/_no_models.keep_models_out` (no embedder, the model package absent,
  self-improvement off, a registry client that refuses like a keyless
  provider) and `finish_turn_memory` (a turn's post-turn memory thread ends
  inside its test), a fake LLM client, `ssrf._resolve_host_ips` answered
  locally. The root conftest sets `KAZMA_EMBEDDER_PREWARM=0` (every app a
  test built loaded the model at boot) and the Hugging Face offline
  switches. Gate: `tests/test_egress_guard.py` (planted tests in a child
  pytest as the negative control).
- **No test reaches the Kazma running on this machine** (2026-11-13). The
  terminal app's tests mount the real app, and its polling went to
  `127.0.0.1:9090`: the live install's log held 33-44 unauthenticated
  requests a dozen times a day, a `PUT /api/settings/single` among them,
  refused only because live asks for a sign-in. The local API asks only the
  configured port (`runtime.local_api.candidate_api_bases`: `server_port`,
  then `KAZMA_BASE_URL`; it also guessed 9090 and 8000, which on a machine
  with two installs reached the other one; the Docker image sets
  `KAZMA_PORT=8000` for commands run inside it). The root conftest gives the
  suite a port nothing listens on, and the egress guard refuses a loopback
  connection to a Kazma outside the run (`KAZMA_TEST_OUTSIDE_PORTS`: 9090
  and the port the environment named before), unless the test bound that
  port itself. Gates: `tests/test_suite_reaches_no_running_kazma.py`,
  `tests/test_egress_guard.py` (a planted test as the negative control),
  `tests/test_agent_turn.py`.
- **A test that runs real containers is marked `real_docker`** (2026-11-13).
  `tests/conftest.py`'s `_isolated_docker_config` points `DOCKER_CONFIG` at
  an empty folder for every other test (which engine a developer's machine
  uses never decides a test); its `default` context is the platform's own
  Docker address, which on a machine whose engine is Kazma's (no Docker
  Desktop) answers nothing: every container test failed there and the
  sandbox's real run skipped without a word. A marked test keeps the
  developer's settings. A test's own `docker run -v` names the folder as the
  engine in force mounts it (`docker_cli.engine_host_path`). The same fixture
  hides the machine's own Kazma engine folder (`docker_cli.kazma_engine_dir`:
  once this PC's setup installed Kazma's docker CLI there, every lookup test
  found it, 2026-11-14); a test that sets its own `LOCALAPPDATA` gets that
  folder. Gate: `tests/test_real_docker_marker.py` (the marked and unmarked
  tests are each other's control).
- **A file a child process publishes is renamed into place and read with
  `tests/_published_files.read_published_text`** (2026-11-14): a child that
  wrote in place was read empty, and on Windows a file just renamed can be
  held for a moment by the scanner (`PermissionError`); both failed only on a
  loaded machine.
- **Every docs diagram parses** with the docs site's own Mermaid
  (`tests/test_docs_mermaid_diagrams_parse.py`, in a blank Chromium page; it
  needs `cd docs && npm install`, as building the site does). The site draws
  diagrams in the browser, so a broken one is only a console error: the
  Commitment Layer page's had never drawn.
