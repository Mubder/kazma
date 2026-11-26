# CodeQL triage, 2026-11-26

| | |
|---|---|
| Alerts | 135 open on `Mubder/kazma` on 2026-11-26 (created 2026-11-16 to 2026-11-25): 133 CodeQL, 2 OpenSSF Scorecard |
| Analysis read | CodeQL 2.27.6, Python analysis `2256312367` of `main` at `049545f78`, with its code flows (SARIF) |
| Rules | AGENTS.md sections 66 (code scanning), 26 D and F (masking, `safe_error`), 32, 67, 69 |
| Tests | `tests/test_codeql_triage_2026_11_26.py`, `tests/test_route_errors_carry_no_exception_text.py`, `tests/test_sealed_zip.py` |

Every alert was read against the code, and against the path CodeQL gives
for it: the source it started from and each step to the line it names. The
table at the end has each number, its verdict and why. Nothing was
dismissed on GitHub: that is the owner's.

## Summary

| Verdict | Alerts | What happens on GitHub |
|---|---|---|
| Fixed in the code | 11 | closes at the next analysis of `main` |
| False positive, its source removed | 88 | closes at the next analysis of `main` (see below) |
| False positive, hardened | 1 | closes at the next analysis of `main` |
| False positive | 33 | stays open: the owner dismisses it, with the reason in the table |
| Owner's decision | 2 | Scorecard: repository settings, not code |

**No log line among the 101 logging alerts prints a secret.** Each one
prints an account id, a space id, a key's id, a count, a role, a model's or
a secret's *name*, a path, or a refusal sentence that is already masked.

What was real:

- **A route answered an exception's text, and the gate did not see it**
  (#554). `people_api._refused` answered `str(exc)` of a `PeopleRefused`.
  The section 26 F gate (`tests/test_route_errors_carry_no_exception_text.py`)
  only recognised an exception parameter by a name ending in `Error` or
  `Exception`. It now knows every exception class the packages define, by
  their bases, to a fixpoint. Its negative control is the old suffix rule
  on the old shape. The People sentences are Kazma's own, so nothing
  leaked in practice, but a path in one went out unredacted.
- **A memory backup's path was built from the request's text** (#519, #654,
  #655). It was validated first (admin only, no separators, `relative_to` the
  backups folder), but the name check took Unicode digits as a timestamp.
  `memory.backup.memory_backup_path` now rebuilds the name from its number,
  as `universal._backup_dir_name` already does for the universal backups
  (section 66).
- **A test escaped only the dots of a catalog key** before using it in a
  pattern (#630, test code).

## The classes

### Clear-text logging of sensitive data (101)

CodeQL calls a value a secret by the *name* it is bound to: a variable,
attribute, parameter or function whose name matches its heuristics (secret,
password, api key, token...). For 88 of the 101 alerts, the only source in
the path is one line: `config_store.py:1641`, `secret = vault.retrieve(name)`.
That was the vault fallback in `ConfigStore._resolve_vault_value`, the
function every setting is read through. Because of that one name, every
value any module read from the settings store was a secret to CodeQL: a
space id, a sender list, a limit. Each log line downstream became an alert,
and each new Spaces feature added more.

The fix is at the source. The fallback returns the vault's answer without
naming it. A secret is still named where it is read: by its key
(`cfg.get("x.api_key")` is a source to CodeQL by its literal key) or by the
variable that holds it. `tests/test_codeql_triage_2026_11_26.py` reads every
function on `ConfigStore.get`'s value path with CodeQL's own name heuristics
and fails if one binds a value to a secret's name. The old line is its
control. These 88 close at the next analysis. If one does not (another
source CodeQL did not list), the table's reason is the dismissal.

The other 13 come from sources that are names, not values. The table says
which for each:

- vault entry names (`space_secret_name()`, `secret_scope()`, X's credential
  keys);
- an API token's role (`_api_token_entry()`);
- the manifest key `"secrets"`, a count;
- a test's variable named `secret`;
- the Postgres app password, which reaches only a refusal sentence
  `_word_refusal` already words or masks.

Every log record also passes the log writer's mask
(`logging_config._LogQueueHandler.prepare`: URL passwords and every token
shape, section 66). CodeQL does not model that mask.

### Path injection (21)

There are no traversal gaps.

- **Admin-only folder choices (12).** The backup folder's Test and Browse,
  and the workspace's select and create. `/api/settings` is admin only, and
  the workspace writes are in `_ADMIN_WRITE_PREFIXES`. Choosing a folder of
  this computer is the feature. Device and network paths are refused on
  their text before anything resolves them.
- **The path policy itself (6).** `is_kazma_store`, `in_another_spaces_folder`,
  `path_under_root`, `check_path_access` and `tool_path_or_reason` resolve
  the path they are asked about: that resolution is the check. The flows
  come from the IDE's LSP request (`IdeService.resolve` is the containment
  check) and the checkpoint restore, which writes only a file its checkpoint
  holds and `check_path_access` allows for writing.
- **The memory restore (3).** Fixed (above).

### Stack-trace exposure (7)

None sent a traceback. Six answered a Kazma refusal through
`validation_error(exc)`, which redacts `str(exc)`. One (#554) sent
`str(exc)` raw. The rule is now stronger: a route answers one of Kazma's
own refusals with `errors.refusal_message`. That reads the `message` field
the refusal class keeps (the sentence Kazma wrote when it refused), redacted
and cut, and never the exception's text, which a subclass, a wrapper or a
cause can change.

- The classes that keep the field: `PeopleRefused`, `LinkRefused`,
  `OwnKeyRefused`, `SpaceDataRefused`, `SpaceMoveRefused`, `ImportRefused`.
- The gate: every class handed to `refusal_message` keeps `self.message`,
  itself or through a base, read from the source. A planted class without
  the field is the control.
- X's API-access save no longer raises for a malformed token.
  `_token_problem` returns Kazma's sentence.

### URL redirection (1)

#509 is not an open redirect. `x_api._own_base` is `email_api._request_base`:
`KAZMA_PUBLIC_URL`, else `KAZMA_HOST` and the port, never the Host or
X-Forwarded-Host header (audit H6). A test sends both headers and gets the
install's own address back. The tainted part was X's error code in the
query string, already reduced to `[a-z_]`. It is now one of Kazma's
constants: `access_denied`, `invalid_scope`, `invalid_request` (each worded
on Settings -> X in English and Arabic), else `refused`.

### Weak cryptographic algorithm (1)

#530 is correct as written. WinZip AES (AE-2) is counter mode with a
little-endian counter from 1. `cryptography`'s CTR counts big-endian, so
`_Counter` builds the counter blocks and encrypts them with ECB. ECB never
touches the data. Each entry draws a fresh salt and so its own key, so no
two entries share a keystream. A new test seals two identical entries and
checks they differ. Its control, one salt for both, shows identical bodies.
The existing control shows a big-endian counter writes a zip no tool opens.

### Overly large range (1)

#507 is deliberate. `logging_config._UNSAFE_IN_A_LINE` lists the C0
controls but the tab (`\x0a-\x1f`), DEL, the C1 controls and the line
separators: every character `str.splitlines` breaks a line at, so no log
line can be forged (section 69).

### Incomplete string escaping, JavaScript (1)

#630 is fixed: `tests/js/test_memory_tables_failed_read.js` escapes every
pattern character of a catalog key, the backslash first.

### Scorecard (2)

#506 (Code-Review) and #481 (Branch-Protection) are about how `main` is
changed, not about code.

- Code-Review scores 0: no approved changesets among the last 30.
- Branch-Protection scores 3. Scorecard's warnings: the protection does not
  apply to administrators, "up-to-date branches" is off, and pull requests
  are not required on `main`.

Kazma has one operator who commits straight to `main`. Each step Scorecard
asks for (required reviews, protection that binds the admin, required pull
requests) would stop that operator's own pushes, or need a second reviewer.
The choice is between dismissing both as "won't fix" (single operator) and
moving to pull requests. This triage changes no repository setting.

## What the owner does

1. After the fixes reach `main`, the next CodeQL analysis closes 100 alerts
   (88 + 11 + 1).
2. Dismiss the 33 marked "False positive" with the reason in the table
   (GitHub: "False positive").
3. Decide #481 and #506.

## Every alert

| # | Rule | Where | Verdict | What it is, and why |
|---|---|---|---|---|
| 602 | clear-text logging | `cli/spaces.py:72` | FP, source removed | prints a precondition note: the chat-app sender ids the owner listed |
| 553 | clear-text logging | `cli/spaces.py:115` | FP, source removed | prints the space ids that got a key of their own |
| 609 | clear-text logging | `cli/spaces.py:166` | FP, source removed | prints a space's name, id, key id and counts (`key_report`: ids, never key material) |
| 610 | clear-text logging | `cli/spaces.py:190` | FP, source removed | prints the space ids whose key will change |
| 611 | clear-text logging | `cli/spaces.py:205` | FP, source removed | prints a space id and `KeyChangeRefused`'s sentence |
| 612 | clear-text logging | `cli/spaces.py:207` | FP, source removed | prints a space id, its new key's id and a count |
| 521 | clear-text logging | `core/backup/restic_repo.py:787` | FP, source removed | the local restic repository's folder (a local path; the log writer masks any URL password) |
| 534 | clear-text logging | `core/backup/restic_repo.py:817` | FP, source removed | the backup folder's restic repository path |
| 648 | clear-text logging | `core/backup/universal.py:949` | FP, source removed | a folder the universal backup could not read |
| 548 | clear-text logging | `core/data_folder_move.py:563` | False positive | data-folder move: the service name and Postgres's sign-in refusal as `postgres_pool._word_refusal` words it (a sentence, or Postgres's text through `mask_urls_in_text`); the source is the app password in the DSN, which is never printed |
| 650 | clear-text logging | `core/documents/ingestion.py:3649` | FP, source removed | documents ingestion: a description of the failed row and the exception's type name |
| 562 | clear-text logging | `core/memory/forget.py:1905` | FP, source removed | memory forget: an entry id and its owner (a space id) |
| 579 | clear-text logging | `core/runtime/live_llm.py:333` | FP, source removed | a model's name |
| 622 | clear-text logging | `core/security/second_factor.py:292` | FP, source removed | an account id (whose second factor); never the TOTP secret or a code |
| 623 | clear-text logging | `core/security/second_factor.py:300` | FP, source removed | an account id |
| 624 | clear-text logging | `core/security/second_factor.py:305` | FP, source removed | an account id |
| 625 | clear-text logging | `core/security/second_factor.py:383` | FP, source removed | an account id |
| 626 | clear-text logging | `core/security/second_factor.py:396` | FP, source removed | an account id |
| 627 | clear-text logging | `core/security/second_factor.py:406` | FP, source removed | the account id of who reset it |
| 628 | clear-text logging | `core/security/second_factor.py:478` | FP, source removed | an account id and a count |
| 629 | clear-text logging | `core/security/second_factor.py:502` | FP, source removed | an account id and how many recovery codes are left (a count) |
| 549 | clear-text logging | `core/security/vault.py:583` | False positive | a space id beside a key's id (`k` + 16 hex, never the key); sources: `secret_scope()` and X's credential key names, both names |
| 642 | clear-text logging | `core/security/vault.py:713` | FP, source removed | a count and a space id |
| 643 | clear-text logging | `core/security/vault.py:774` | FP, source removed | a space id and its new key's id |
| 614 | clear-text logging | `core/security/vault.py:902` | FP, source removed | what the write-ahead-log checkpoint followed (a sentence naming a space) |
| 615 | clear-text logging | `core/security/vault.py:906` | FP, source removed | the same sentence |
| 593 | clear-text logging | `core/security/vault.py:1027` | FP, source removed | a tenant id (the secret's name and category beside it are names) |
| 517 | clear-text logging | `core/security/vault.py:1307` | False positive | a secret's name, not its value (sources: X's credential key names) |
| 518 | clear-text logging | `core/security/vault.py:1692` | False positive | a secret's name (sources: vault entry names such as `space_secret_name()`) |
| 528 | clear-text logging | `core/security/vault.py:1711` | False positive | a secret's name |
| 594 | clear-text logging | `core/security/vault.py:1711` | FP, source removed | a tenant id |
| 644 | clear-text logging | `core/security/vault.py:1749` | FP, source removed | a space id and a row count |
| 529 | clear-text logging | `core/security/web_sessions.py:185` | FP, source removed | the user name of a new web session (an id; the session is named by its label, section 66) |
| 597 | clear-text logging | `core/spaces/chat_apps.py:435` | FP, source removed | an account id |
| 598 | clear-text logging | `core/spaces/chat_apps.py:461` | FP, source removed | an account id |
| 599 | clear-text logging | `core/spaces/chat_apps.py:471` | FP, source removed | an account id |
| 600 | clear-text logging | `core/spaces/chat_links.py:451` | FP, source removed | an account id (the link code itself is never logged) |
| 601 | clear-text logging | `core/spaces/chat_links.py:608` | FP, source removed | an account id and a chat-app user id |
| 555 | clear-text logging | `core/spaces/daily_limits.py:171` | FP, source removed | a stored daily limit that is not a limit (the number or text the admin saved) |
| 576 | clear-text logging | `core/spaces/daily_limits.py:243` | FP, source removed | an account id |
| 577 | clear-text logging | `core/spaces/daily_limits.py:257` | FP, source removed | an account id |
| 578 | clear-text logging | `core/spaces/daily_limits.py:270` | FP, source removed | an account id, tokens and dollars spent |
| 649 | clear-text logging | `core/spaces/file_moves.py:1159` | FP, source removed | a space id |
| 618 | clear-text logging | `core/spaces/invitations.py:126` | FP, source removed | the account id that made an invitation (the token is never logged) |
| 619 | clear-text logging | `core/spaces/invitations.py:208` | FP, source removed | the id of the account an invitation made |
| 591 | clear-text logging | `core/spaces/own_keys.py:277` | FP, source removed | an account id and a provider name (never the key) |
| 592 | clear-text logging | `core/spaces/own_keys.py:296` | FP, source removed | an account id and a provider name |
| 580 | clear-text logging | `core/spaces/people.py:382` | FP, source removed | an account id |
| 581 | clear-text logging | `core/spaces/people.py:382` | FP, source removed | the account id that ended the sessions |
| 563 | clear-text logging | `core/spaces/people.py:499` | FP, source removed | the account id that made the space |
| 564 | clear-text logging | `core/spaces/people.py:635` | FP, source removed | an account id |
| 565 | clear-text logging | `core/spaces/people.py:635` | FP, source removed | the account id that added the member |
| 566 | clear-text logging | `core/spaces/people.py:651` | FP, source removed | an account id |
| 567 | clear-text logging | `core/spaces/people.py:679` | FP, source removed | the account id that removed the member |
| 568 | clear-text logging | `core/spaces/people.py:716` | FP, source removed | an account id and a space id |
| 569 | clear-text logging | `core/spaces/people.py:766` | FP, source removed | an account id and a space id |
| 570 | clear-text logging | `core/spaces/people.py:818` | FP, source removed | an account id |
| 571 | clear-text logging | `core/spaces/people.py:818` | FP, source removed | a space id |
| 620 | clear-text logging | `core/spaces/people.py:911` | FP, source removed | the account id that made the account |
| 523 | clear-text logging | `core/spaces/people.py:937` | FP, source removed | the account id whose password was reset |
| 524 | clear-text logging | `core/spaces/people.py:937` | FP, source removed | the account id that reset the password (never the password) |
| 621 | clear-text logging | `core/spaces/people.py:985` | FP, source removed | an account id |
| 572 | clear-text logging | `core/spaces/people.py:986` | FP, source removed | an account id |
| 573 | clear-text logging | `core/spaces/people.py:986` | FP, source removed | the account id that deleted the account |
| 558 | clear-text logging | `core/spaces/people.py:1027` | FP, source removed | an account id |
| 559 | clear-text logging | `core/spaces/people.py:1027` | FP, source removed | the account id that set the limit |
| 582 | clear-text logging | `core/spaces/people.py:1044` | FP, source removed | an account id |
| 583 | clear-text logging | `core/spaces/people.py:1077` | FP, source removed | an account id |
| 584 | clear-text logging | `core/spaces/people.py:1094` | FP, source removed | an account id |
| 585 | clear-text logging | `core/spaces/people.py:1150` | FP, source removed | an account id |
| 586 | clear-text logging | `core/spaces/people.py:1150` | FP, source removed | an account id |
| 631 | clear-text logging | `core/spaces/people.py:1181` | FP, source removed | an account id |
| 632 | clear-text logging | `core/spaces/people.py:1227` | FP, source removed | an account id |
| 588 | clear-text logging | `core/spaces/people.py:1249` | FP, source removed | an account id |
| 589 | clear-text logging | `core/spaces/people.py:1283` | FP, source removed | an account id |
| 527 | clear-text logging | `core/spaces/space_data.py:856` | FP, source removed | the account id that erased the space |
| 552 | clear-text logging | `core/spaces/space_keys.py:74` | FP, source removed | a space id |
| 607 | clear-text logging | `core/spaces/space_keys.py:152` | FP, source removed | a space id |
| 608 | clear-text logging | `core/spaces/space_keys.py:154` | FP, source removed | a space id |
| 633 | clear-text logging | `core/spaces/space_keys.py:154` | FP, source removed | the account id that changed the key |
| 634 | clear-text logging | `core/spaces/space_move.py:970` | FP, source removed | a space id |
| 635 | clear-text logging | `core/spaces/space_move.py:970` | FP, source removed | the account id that made the bundle |
| 636 | clear-text logging | `core/spaces/space_move.py:971` | False positive | how many secrets the bundle holds (an int); CodeQL reads the manifest key `"secrets"` as a secret. The bundle's manifest format names it |
| 645 | clear-text logging | `core/spaces/space_move.py:1843` | FP, source removed | a space id |
| 637 | clear-text logging | `core/spaces/space_move.py:1848` | FP, source removed | a space id |
| 646 | clear-text logging | `core/spaces/space_move.py:1975` | FP, source removed | a space id |
| 639 | clear-text logging | `core/spaces/space_move.py:1980` | FP, source removed | a space id |
| 647 | clear-text logging | `core/spaces/space_move.py:1985` | FP, source removed | a space id |
| 640 | clear-text logging | `core/spaces/space_move.py:2093` | FP, source removed | the owner's account id |
| 641 | clear-text logging | `core/spaces/space_move.py:2093` | FP, source removed | the account id that moved the space in |
| 510 | clear-text logging | `core/swarm/shared_approvals.py:474` | FP, source removed | who pressed an approval button (a name or id) |
| 512 | clear-text logging | `skills/native/email_manager/credentials.py:100` | False positive | the vault entry name of a mail account's password (`space_secret_name()`), not its value |
| 513 | clear-text logging | `skills/native/email_manager/credentials.py:139` | False positive | the vault entry name |
| 514 | clear-text logging | `skills/native/email_manager/credentials.py:177` | False positive | the vault entry names |
| 616 | clear-text logging | `ui/app.py:371` | FP, source removed | the boot refusal sentence: switch names and the names of the accounts that exist |
| 617 | clear-text logging | `ui/app.py:372` | FP, source removed | the same sentence on stderr |
| 596 | clear-text logging | `ui/auth.py:1527` | False positive | an API token's role (`viewer`, `operator`, `admin`); sources: `_api_token_entry()` and `_api_token_role()`, which CodeQL reads as tokens by their names |
| 511 | clear-text logging | `ui/auth.py:1534` | False positive | the same role |
| 516 | clear-text logging | `ui/session_manager.py:821` | False positive | a chat session id (not the web sign-in cookie, section 66); the only source is a test's variable named `secret` (`tests/test_attached_text_fenced.py:110`) |
| 515 | clear-text logging | `ui/session_spool.py:123` | False positive | a chat session id; the same test source |
| 590 | clear-text logging | `scripts/scan_old_sessions.py:388` | FP, source removed | a provider's name (a maintenance script) |
| 539 | path injection | `core/backup/folder_copy.py:250` | False positive | Settings -> Backup folder, Test: admin only (`platform_rbac`, `/api/settings`). Any folder of this computer is the feature; `path_problem` refuses device paths, relative paths and the data folder, before anything opens it |
| 540 | path injection | `core/backup/folder_copy.py:252` | False positive | the same test (the folder's drive) |
| 541 | path injection | `core/backup/folder_copy.py:255` | False positive | the same test (making the chosen folder) |
| 542 | path injection | `core/backup/folder_copy.py:261` | False positive | the same test (its probe file, `.kazma-write-test-<random>`) |
| 543 | path injection | `core/backup/folder_copy.py:262` | False positive | the same test (reading the probe back) |
| 544 | path injection | `core/backup/folder_copy.py:268` | False positive | the same test (removing the probe) |
| 545 | path injection | `core/backup/folder_copy.py:274` | False positive | the same test (the free room) |
| 546 | path injection | `core/backup/folder_copy.py:307` | False positive | Settings -> Backup folder, Browse: admin only; a network path is refused before it is listed, the rest is the folder picker |
| 547 | path injection | `core/backup/folder_copy.py:311` | False positive | the same browse (listing the folder) |
| 654 | path injection | `core/db/sqlite_snapshot.py:36` | Fixed | memory restore: the copy is now made from `memory_backup_path`, the name rebuilt from its timestamp (it was validated before: admin only, no separators, `relative_to` the backups folder) |
| 655 | path injection | `core/db/sqlite_snapshot.py:36` | Fixed | the same restore |
| 519 | path injection | `core/memory/backup.py:196` | Fixed | the same restore (`_is_memory_backup`) |
| 520 | path injection | `core/store_registry.py:1285` | False positive | `store_registry.is_kazma_store` resolving the path it is asked about: the predicate that refuses Kazma's own stores to every tool is the check itself |
| 656 | path injection | `core/store_registry.py:1298` | False positive | the same predicate |
| 657 | path injection | `core/store_registry.py:1788` | False positive | `in_another_spaces_folder`, the same predicate's space rule |
| 531 | path injection | `core/stores/workspaces.py:308` | False positive | Workspace select / create: admin only (`platform_rbac._ADMIN_WRITE_PREFIXES`), a network path refused on its text, `root_policy.validate_root`; the folder chosen becomes the workspace by design |
| 651 | path injection | `core/workspace/binding.py:238` | False positive | `binding.tool_path_or_reason`, where every tool path is resolved before the policy decides; network and drive-relative paths refused on the text first. Sources: the IDE's LSP request and the checkpoint restore, which writes only a file the checkpoint holds and `check_path_access` allows |
| 574 | path injection | `core/workspace/folder_identity.py:41` | False positive | the backup folder test's same-disk check (`_identity`): admin only, the folder the admin chose |
| 575 | path injection | `core/workspace/folder_identity.py:58` | False positive | the same check |
| 653 | path injection | `core/workspace/path_grants.py:55` | False positive | `path_grants.path_under_root`: the containment check itself |
| 652 | path injection | `core/workspace/path_policy.py:437` | False positive | `path_policy._space_disk_refusal` / `check_path_access`: the path policy itself |
| 603 | stack-trace exposure | `ui/chat_links_api.py:163` | Fixed | chat links: `LinkRefused` answered through `errors.refusal_message` (its `message` field) |
| 604 | stack-trace exposure | `ui/chat_links_api.py:178` | Fixed | the same, unlinking |
| 658 | stack-trace exposure | `ui/history_import_api.py:152` | Fixed | history import: `ImportRefused`'s `message` field |
| 595 | stack-trace exposure | `ui/own_keys_api.py:48` | Fixed | a person's own keys: `OwnKeyRefused`'s `message` field |
| 554 | stack-trace exposure | `ui/people_api.py:70` | Fixed | People: answered `str(exc)` raw, unseen by the section 26 F gate (it knew an exception parameter only by a name ending in Error); now `refusal_message`, and the gate knows every exception class the packages define |
| 533 | stack-trace exposure | `ui/spaces_api.py:71` | Fixed | Spaces: `SpaceDataRefused` / `SpaceMoveRefused` / `PeopleRefused` by their field |
| 508 | stack-trace exposure | `ui/x_api.py:731` | Fixed | X API access: the token check returns Kazma's sentence (`_token_problem`); no exception is raised or answered |
| 509 | URL redirection | `ui/x_api.py:781` | False positive, hardened | `_own_base` is `KAZMA_PUBLIC_URL` (else `KAZMA_HOST` and the port), never a Host or X-Forwarded-Host header, so the redirect cannot leave the install. The tainted part was X's error code inside the query string (`[a-z_]` only). It is now one of Kazma's words (`_sign_in_error_word`: access_denied, invalid_scope, invalid_request, else refused), each worded on the page in EN and AR |
| 530 | weak crypto | `core/security/sealed_zip.py:71` | False positive | WinZip AES (AE-2) counter mode: ECB encrypts only the little-endian counter blocks under each entry's own key (a fresh salt per entry), which is CTR. `cryptography`'s CTR counts big-endian, which no tool opens (`tests/test_sealed_zip.py`'s control). New test: no two entries share a keystream |
| 507 | overly large range | `core/logging_config.py:497` | False positive | `_UNSAFE_IN_A_LINE`: the C0 controls but the tab (`\x0a-\x1f`), DEL, the C1 controls and the line separators, chosen character by character so a log line cannot be forged (section 69) |
| 630 | incomplete sanitization (JS) | `tests/js/test_memory_tables_failed_read.js:70` | Fixed | a test built a pattern from a catalog key escaping only the dots; it escapes every pattern character now (test code, no product effect) |
| 506 | Scorecard Code-Review | (repository) | Owner's decision | Scorecard Code-Review: 0 of 30 changes approved. Kazma has one operator who commits to `main`; a second reviewer, or approving one's own pull requests, is the owner's choice. Nothing in code |
| 481 | Scorecard Branch-Protection | (repository) | Owner's decision | Scorecard Branch-Protection: `main` is not fully protected (reviews, status checks, admins included). The same choice: protection that requires a review blocks the single operator's pushes. Repository settings are not changed by this triage |
