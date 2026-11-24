<!-- AGENTS.md, piece 68-scorecard. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 68. What OpenSSF Scorecard reads, held (2026-11-16)

Kazma's first Scorecard run scored 5.7 (published at scorecard.dev, badge in
both READMEs). Each check Kazma can meet is now met and held by a gate:

- **Token-Permissions**: a workflow is read-only and a job names the writes
  it needs (`release.yml` granted four to the whole workflow);
  `tests/test_workflow_permissions.py` refuses a write at the workflow level.
- **Pinned-Dependencies**: every pip install in CI and the images comes from
  uv.lock with `--require-hashes`, Kazma itself editable with `--no-deps`
  and the locked build backend (`--no-build-isolation`):
  `.github/actions/install-locked` (inputs `extras`, `groups`,
  `only-groups`) in every job, `uv export` in the Dockerfiles (uv from its
  image, pinned by digest). What a job adds beyond an extra is a
  `[dependency-groups]` group (`ci-light`, `build`, `lint`, `scan`,
  `fuzz`): locked, never installed by `uv sync`, never published. CI thus
  tests the versions users get (an update installs the locked ones, §50); a
  new version reaches CI as a uv.lock update. The shipped-wheel job installs
  the locked set, then the wheel with `--no-deps`. Gate:
  `tests/test_installs_pinned_by_hash.py` (Scorecard's own pip rule, every
  workflow, composite action and Dockerfile; the old lines as controls);
  `tests/test_major_versions_held.py` holds the lock below each held major.
- **SAST**: CodeQL runs from `.github/workflows/codeql.yml` (default setup is
  switched off: GitHub refuses both), with default setup's categories
  (`/language:<name>`) so alerts and dismissals carry over.
- **Fuzzing**: `fuzz/fuzz_*.py` are Atheris targets (Linux, Python 3.12+,
  group `fuzz`) over the code that reads outside text: the data fence, the
  secret maskers, the Telegram converter, shell_exec's command reading, the
  Arabic search fold, the MCP sign-in challenge. Each states its invariant in
  `check(data)`; `.github/workflows/fuzz.yml` runs each 45 s on every push
  (300 s weekly) and keeps a breaking input; `tests/test_fuzz_targets.py`
  runs every `check` on seeds and generated inputs on every platform (a
  fence without its sanitiser, a maskerless redactor and a raw converter as
  controls). A new parser of outside text gets a target. Their first run
  found two bugs, both fixed: `documents.arabic.fold_for_search` was not a
  fixed point (a mark after a removed tatweel composed only on a second
  fold; it now folds to a fixed point, `KB_FOLD_VERSION` 2 refolds the
  Knowledge index), and `telegram_format.md_to_tg_html` misnested
  overlapping bold and italic (`***x***` -> `<b><i>x</b></i>`, refused by
  Telegram; `_balance_style_tags` re-nests them).
- **Vulnerabilities**: `osv-scanner.toml` lists exactly the advisories
  `dependency_scanner.REVIEWED_ADVISORIES` reviews as unreachable (GitHub
  id with the package first, PyPI id naming it), and `docs/osv-scanner.toml`
  the docs site's build-only ones, each with the version the lock holds.
  Gate: `tests/test_osv_scanner_ignores.py` (a planted ignore and a stale
  version as controls). Remove an entry when its package moves past it.
- **Coverage** (Codecov, 2026-11-16): `.github/workflows/coverage.yml` runs CI's suites
  again under coverage.py -- the unit suite, the Postgres-marked tests on a real Postgres, the
  browser suites, the recovery drills, the document worker's container profile and the
  Temporal path -- each job uploading under its own Codecov flag, merged into one report.
  `.github/actions/coverage-setup` measures every Python child a job starts (`patch =
  subprocess`, absolute sources: the reload drill's server runs in another folder);
  `.github/actions/coverage-upload` combines and uploads with GitHub's OIDC token
  (`use_oidc`; no Codecov token is stored). They measure and never gate: CI's jobs are the
  gates, and tests that check a child's exact environment see coverage's variable there.
- **Not met, on purpose or not yet**: Code-Review, CI-Tests and
  Branch-Protection count pull requests and their reviews (the owner pushes
  to `main`); Signed-Releases rises as `release.yml` signs new releases;
  Contributors needs other organisations; CII-Best-Practices is 5 at
  "passing" (silver 7.5).

