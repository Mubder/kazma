<!-- AGENTS.md, piece 66-code-scanning. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 66. Code scanning (GitHub CodeQL, 2026-11-14)

GitHub's CodeQL default setup (Python, JavaScript, C#, Actions; on since
2026-11-13, every push and weekly) reported 388 alerts. Each was judged
against the code: the real ones are fixed, each class with a gate; the rest
are dismissed on GitHub with the reason (most: Kazma's own checks, which
CodeQL does not know -- `safe_error`, `remote_path_reason`, the session hash).
A new alert is judged the same way; never dismiss one without reading the
code it names.

- **Every JavaScript file parses** (`scripts/check_js_syntax.py`, CI's
  js-check): every tracked `.js`/`.mjs`/`.cjs`, a `.js` checked as a script
  and as a module, each from a copy (`node --check` on a `.js` leaves it to
  node's module detection, which accepted a file valid as neither).
  `loadtests/k6_swarm.js` began with Python imports: k6 could not run it, and
  CodeQL's "could not process" warning was on every analysis. Gate:
  `tests/test_every_js_file_parses.py`.
- **A web session is named by `web_sessions.session_label`** (the start of
  its hash: the `id` Settings lists), never by its cookie, which is the
  sign-in credential. `web_actor.actor_for_session` is the one builder (the
  Memory page's trail, the approve route, Time Travel's audit, `web_actor`);
  four kept the cookie's first eight characters in kazma.log and the audit
  tables. Gate: `tests/test_session_label_never_the_cookie.py`.
- **No log line carries a password inside an address.** The log writer
  masks every message and traceback before it is queued
  (`logging_config._LogQueueHandler.prepare`, `url_credentials.mask_urls_in_text`):
  model discovery and the model registry logged provider addresses as they
  were. `kazma doctor`'s rows (`doctor._line`) and the update log
  (`update._without_url_passwords`, a local copy: the updater runs while
  Kazma's package may not import) mask too, and the settings store's change
  log masks a list's or mapping's nested secrets by name (`_redact_for_log`,
  `config_mask.mask_deep`). Gates: `tests/test_logs_never_carry_url_passwords.py`,
  `tests/test_update_log_masks_url_passwords.py`.
- **A network path is refused before anything resolves it** (the 2026-11-10
  rule, §62) in the places it had missed: `IdeService.resolve`, the
  Workspace file list (`/api/workspace/files`: only "/" was stripped, so a
  backslash path became the join), the extra folders
  (`path_grants.set_durable_roots`, `_extra_root_problem`; one kept from
  before is skipped by `list_durable_roots`), the store check before an
  approval card (`control_plane_store_targeted`), a skill installed from a
  folder (`install_from_any`) and the workspace-roots policy
  (`normalize_workspace_roots`). Gate:
  `tests/test_remote_paths_refused_before_touching.py`.
- **No answer carries an exception's own text** (§26 F, the rule and its
  gate): 17 routes and 5 near misses CodeQL named (py/stack-trace-exposure),
  and the helpers they answer through, now answer `described_error` or
  `safe_error`; Kazma's own refusals go through `validation_error`, except
  where its redaction would hide the folder the owner typed
  (`put_extra_roots`, declared).
- **Patterns that read untrusted text stay linear** (possessive quantifiers,
  bounded classes; Python 3.11.5 is the floor): the memory graph's version
  test (`ego_anchor._VERSION_RE`, exponential: a subject of "0.0", thirty
  letters and "!" took minutes), the forged-fence filter
  (`prompt_fence._FENCE_TAG_RE`, quadratic over every fetched page; an
  opener with no ">" is redacted too), the provider address's version test
  and its "/v1/v1" trim (`normalize_provider_url`), the attachment-name
  scan (`extract_active_attachments`) and the problem report's e-mail
  scrubber (`problem_report._EMAIL`), and the credential maskers
  (`errors._REDACT_SECRETS` and the phrase patterns: a long run of letters
  used to take minutes on the URL-userinfo alternative). Gate: `tests/test_regexes_stay_linear.py`
  (the old patterns' answers on random inputs, their growth as the control)
  and `tests/test_credential_phrases_masked.py`.
  Measure before changing a flagged pattern: the schedule parser, the bare
  host and file-path patterns were linear on their real input.
- **Smaller ones:** maps keyed by server frames in the turn view are
  prototype-free (`Object.create(null)`; a turn id of "__proto__" answered
  `Object.prototype`, `tests/js/test_turn_view.js`); ids the web pages make
  come from the browser's cryptographic source on plain-http pages too
  (`window.kazmaRandomId`, `tests/js/test_random_ids.js`); the Kuwaiti
  tokenizer's emoji class is the emoji blocks (a copied range took Arabic
  presentation forms, CJK and Hangul, `tests/test_tokenizers.py`); a new
  restic passphrase file is restricted before it is written
  (`tests/test_restic_password_file_restricted.py`); Kazma Watch's report ids
  draw every letter evenly (`newId`, rejection sampling); two address checks
  compare host names exactly (`tests/test_url_host_checks.py`); the test
  servers find a free port on loopback, and the document broker's port probe
  skips an address with no host. Second pass (2026-11-16): a backup's name
  from a request is rebuilt from its number before a path is made
  (`universal._backup_dir_name`: the old match let a trailing newline
  through), and the `*_KEY` env-name rule is two plain checks instead of an
  overlapping pattern (`tests/test_codeql_followups_2026_11_16.py`, the old
  check and pattern as controls).
- **Third pass (2026-11-26, 135 open; every one judged in
  `docs/audits/CODEQL_TRIAGE_2026-11-26.md`).** No log line printed a
  secret. CodeQL calls a value a secret by the name it is bound to:
  - **Every key's read path names nothing a secret.**
    `ConfigStore._resolve_vault_value`'s vault fallback bound its answer to
    `secret`, so every setting any module read was a secret to CodeQL: 88
    log lines of ids and counts. The fallback returns it unnamed; a secret
    is named where it is read, by its key. Gate:
    `tests/test_codeql_triage_2026_11_26.py` reads `ConfigStore.get`'s value
    path with CodeQL's own name heuristics, the old line as the control.
  - **A route answers a Kazma refusal by its field.**
    `errors.refusal_message` reads the `message` the refusal keeps (the
    sentence Kazma wrote), redacted and cut, never `str(exc)`: `PeopleRefused`,
    `LinkRefused`, `OwnKeyRefused`, `SpaceDataRefused`, `SpaceMoveRefused`,
    `ImportRefused`. `people_api` answered `str(exc)` raw, unseen by section
    26 F's gate, which knew an exception parameter only by a name ending in
    Error; it knows every exception class the packages define now (the
    suffix rule as the control). The same file gates that every class handed
    to `refusal_message` keeps the field.
  - **Smaller ones.** A memory backup a request names is rebuilt from its
    timestamp (`memory.backup.memory_backup_path`; Unicode digits passed
    `isdigit()`). X's sign-in refusal goes back to the page as one of
    Kazma's words (`x_api._sign_in_error_word`), and its token check returns
    a sentence instead of raising. A sealed zip's entries never share a
    keystream: its ECB makes WinZip's counter blocks, each entry under its
    own salt (`tests/test_sealed_zip.py`).
  - **Judged false positives** (the owner dismisses them, with the triage's
    reason): admin-only folder choices, the path policy's own resolution,
    secret *names* and token roles read as secrets, the log-line control
    class (section 69).

