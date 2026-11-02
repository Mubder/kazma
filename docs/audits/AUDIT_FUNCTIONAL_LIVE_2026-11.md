# Functional audit of the live install, 2026-11

| | |
|---|---|
| Brief | `docs/plans/FUNCTIONAL_AUDIT_PROMPT_2026-11-01.md` (the owner's authorization, limits, yardstick and phases) |
| Window | 2026-11-01 22:40 UTC to 2026-11-02 (Phases 0-7 without a pause) |
| Live build | `eb0df553` when the audit began; `73eee4b6` from 2026-11-02 (the FA-001..003 hotfix, deployed during the audit) |
| Dev HEAD | `dae47783` when the audit began |
| Install | `C:\Users\balfa\kazma`, Windows 11, Postgres backend, loopback 9090 behind Cloudflare Tunnel |
| Remediation | `docs/plans/FUNCTIONAL_AUDIT_2026-11_REMEDIATION.md` |
| Evidence | `docs/audits/evidence/functional_2026-11/` (redacted text); screenshots in `C:\Users\balfa\kazma-audit-evidence\2026-11\` (not in the repository) |

## 1. Executive summary

**Verdict: Kazma works with issues.** No Critical finding. 18 High.

The ordinary day works in both languages:
- web chat;
- approvals and their refusals;
- reminders;
- memory remember and forget;
- Time Travel;
- the swarm;
- Knowledge search;
- mail and calendar reads;
- the ops signals.

The High findings sit where the product fails quietly or recovers badly:
- the install's own secrets were readable by read-tier tools;
- a database outage, a reload during a crawl, a restore and a migration each fail recovery while reporting success;
- privacy controls have side doors;
- two Settings controls change nothing;
- the live install runs below the dependency floors of the code it serves.

**Fixed during the audit (live since `73eee4b6`):**
- FA-001: read-tier tools read `.env`, `restic.pass` and the vault key.
- FA-002: the sandboxed `python_exec` saw the whole workspace, `.env` included.
- FA-003: copies of Kazma's stores were readable as ordinary files.

All three have class gates and negative controls. The full suite passed (18,260) before the deploy.

**Owner actions that cannot wait:**
- Re-create the Postgres, Neo4j and SearXNG containers bound to `127.0.0.1` (FA-218). Postgres answers on the LAN with a superuser login.
- Run the one update command and rebuild the document image (FA-258):
  - 26 packages are below this build's floors;
  - 10 packages carry 81 published advisories;
  - documents run on an outdated image.

### Findings by severity

| Severity | Count | Of which fixed during the audit |
|---|---|---|
| Critical | 0 | 0 |
| High | 18 | 3 |
| Medium | 50 | 0 |
| Low | 60 | 0 |
| **Total** | **128** | **3** |

Source: 4 from the live pass's first wave (FA-001..004), 54 from the offline code hunts (FA-101..154, `evidence/functional_2026-11/offline_findings.md`), 70 from the live candidates (FA-2NN = candidate C<NN>, `evidence/functional_2026-11/findings_draft.md`).

### Verdict per area

| Area | Verdict | Why (findings) |
|---|---|---|
| A1 Chat turns (web) | Works with issues | First token misses its budget (FA-277); the bubble names the wrong model (FA-210); a cut stream is saved as complete (FA-213); a reload ends a stream with a false ending (FA-262) |
| A2 Models and providers | Works with issues | One streaming 429 ends the turn (FA-214); a malformed provider body is called a step limit (FA-211). Slow, 5xx, 401 and truncated tool calls handled honestly |
| A3 Approvals and safety | Works with issues | Cards, Deny, always-ask, grants and `/yolo off` all correct; FA-001..003 (fixed); a graph built with HITL off stays ungated (FA-147) |
| A4 Chat apps | Not tested | Desktop control of Telegram and Slack was declined; one approval card sampled on the owner's own Telegram turn (card 1 s after the call) |
| A5 Web UI, TUI, CLI | Partial | Pages load and navigate; Settings -> Tools does nothing (FA-250); CLI text faults (FA-224, FA-225); no full browser tour of every page this time |
| A6 Memory | Works with issues | Remember and forget work in EN and AR; Arabic "remember" loses the user's provenance (FA-228); self-improvement writes lessons from failed and kept-out turns (FA-212); recall degrades under concurrency (FA-276) |
| A7 Knowledge Library | Works with issues | Crawl, search and delete work; a crawl cut by a reload never resumes (FA-263); English footer on Arabic answers (FA-229) |
| A8 Documents | Works with issues | Intake, conversion, policy and malware refusal work; Arabic pages misread by Kazma's own reader (FA-004); 6.5-6.9 s clamscan per upload (FA-233); stale signatures (FA-232) |
| A9 Research and web acquisition | Works with issues | Runs complete in EN and AR; an Arabic question gets an English report (FA-238); admission and storage disagree (FA-266) |
| A10 Swarm | Works with issues | EN and AR answers in 3 s, cancel correct; API shape and cost issues only (FA-236, FA-239) |
| A11 Scheduled | Works with issues | A reminder fired 12.1 s late (budget 60 s) and was delivered; refusals correct; error envelopes (FA-241) |
| A12 X | Read only | Status, queue, preview and refusals pass; no public action by the brief's limit |
| A13 Mail and calendar | Works with issues | Reads, create and delete on the owner's own calendar behind cards; Gmail listing ~0.6 s per message (FA-242); free-slot search ignores multi-day and all-day events (FA-112) |
| A14 IDE, workspace, GitHub | Partial | Reads, search, LSP, git reads and every path refusal pass; writes not run (the live workspace is the install folder) |
| A15 MCP | Works with issues | List, presets and Test work; Test hides the cause (FA-245); testing an unsaved server pages a false alert (FA-256) |
| A16 Time Travel | Passed | Steps, fork, restore, refusals, expiry and `/replay` in EN and AR (fork privacy is FA-246, filed under memory) |
| A17 Run supervision | Works with issues | Listing, refusals, Diagnose, Dismiss and runbooks correct; a policy refusal paged as a failure with Retry (FA-247) |
| A18 Voice, vision, skills | Broken (TTS) | STT works in EN and AR (0.45-0.59 s); TTS refused by Microsoft (FA-248); Skills Validate (FA-249); vision turns 321-333 s |
| A19 Settings, backup, restore, migration | Broken (restore, migration) | Settings export, restore and undo correct; the documented restore is unreadable (FA-268); an export from the wrong folder passes verify (FA-252); an import revives revoked grants and sessions (FA-255) |
| A20 Ops signals | Works with issues | Start card, health, digest and weekly ledger produced; the guard kills Kazma during a short database outage (FA-259); digest send time drifts (FA-208) |

## 2. Budgets

| Measure | Budget | Measured | Result |
|---|---|---|---|
| First visible token, simple web turn | p50 ≤3 s, p95 ≤8 s | All 67 audit turns: p50 5.3 s, p95 20.1 s. Trivial turns: 2.1-2.6 s. 4 concurrent trivial chats: p50 3.1 s, max 4.4 s | **Missed** (FA-277) |
| Approval card on a chat app | ≤5 s | One Telegram sample: 1 s. Web calendar cards: 4.1 s and 5.1 s (1 s poll) | Met on the sample; one web card at 5.1 s |
| Page load after sidebar navigation | ≤1.5 s | Page polls at 10 concurrent clients: p95 within 1.5 s except the session list, 2.2 s | **Missed for the session list** (FA-278) |
| Reminder fire lateness | ≤60 s | 12.1 s | Met |
| Event-loop stalls, normal load | none ≥2 s | No stall dump during 6,764 requests at 10-60 clients (103 before, 103 after); liveness max 1.66 s | Met |
| Backup RPO | ≤6 h local, offsite included | Schedule 6 h; offsite last 2026-11-01 23:21 (344 MB) | Met by schedule; the documented restore of it does not work (FA-268) and its nested SQLite copies are not consistent (FA-267) |
| Outage ride-through | ≥10 min of Postgres down, no restart | Killed by the guard during a 2-minute outage; down 15 min 10 s in a second drill | **Missed** (FA-259) |
| Graceful reload (extra) | inside the guard's 60 s grace | 58.2-60 s with work running; one drill killed at 60 s and paged | **Missed in effect** (FA-261) |

## 3. Findings table

Status: **Fixed** = fixed and deployed during the audit; **Owner** = needs an owner action or decision first; otherwise open. Full text: **D** = `findings_draft.md` (candidate C<NN>), **O** = `offline_findings.md`, **R** = section 4 of this report.

| ID | Sev | Area | Finding | Status | Text |
|---|---|---|---|---|---|
| FA-001 | High | A3 | Read-tier tools read the install's `.env`, `restic.pass` and the vault key with no card | Fixed | R |
| FA-002 | High | A3 | The Docker sandbox of `python_exec` mounted the whole workspace; a snippet read `.env` | Fixed | R |
| FA-003 | High | A3 | Copies of Kazma's stores (`*.db.pre_relabel`, `.bak-*`) were readable as ordinary files | Fixed | R |
| FA-004 | High | A8 | On a PDF page holding any Arabic, Kazma's reader repeats lines and glues table cells ("Q1120") | Open | R |
| FA-101 | High | A20 | `kazma update` reports success over a conflicting stash pop | Open | R |
| FA-102 | Medium | A6 | A rewind with a locked memory DB journals nothing; /undo and /edit still say memory was cleared | Open | O |
| FA-103 | Medium | A19 | `get_vault()` caches a failed or in-progress start as "no vault" | Open | O |
| FA-104 | Medium | A13 | Mail and calendar sign-ins ignore a failed vault write | Open | O |
| FA-105 | Medium | A4 | Discord send returns "sent" after the 429 retries run out | Open | O |
| FA-106 | Medium | A2 | `resilient_chat` keeps old failover clients after a provider change | Open | O |
| FA-107 | Medium | A2 | A failed model discovery is shown as a count of stale models | Open | O |
| FA-108 | Low | A1 | "Reply persisted" is logged when the save failed | Open | O |
| FA-109 | Low | A10 | Orphan recovery counts a task whose write was refused | Open | O |
| FA-110 | Low | A2 | A failed agent rebind after a key save is logged at DEBUG | Open | O |
| FA-111 | Low | A1 | The "already read this turn" note is shared across chats | Open | O |
| FA-112 | High | A13 | `find_free_slots` ignores multi-day and all-day events and the operator's zone | Open | R |
| FA-113 | Medium | A10 | `dispatch_swarm` named "auto" routes to the first worker by name | Open | O |
| FA-114 | Medium | A5 | The log-level hint says it applies now; it does not | Open | O |
| FA-115 | Medium | A9 | The proxy hint omits the acquisition policy | Open | O |
| FA-116 | Medium | A19 | The backup hint says 24 h, vectors and a Postgres dump | Open | O |
| FA-117 | Medium | A3 | The outbound allowlist does not cover `send_message`; the dispatcher manifest misstates its target | Open | O |
| FA-118 | Medium | A1 | The model's product knowledge has stale lines and an incomplete always-ask list | Open | O |
| FA-119 | Low | A11 | Scheduled time-zone hints say the opposite of the lookup order | Open | O |
| FA-120 | Low | A3 | The `python_exec` description promises limits the code does not apply | Open | O |
| FA-121 | Low | A1 | The built-in default system prompt is unreachable when kazma.yaml sets one | Open | O |
| FA-122 | Low | A7 | The Knowledge auto-inject hint says "every prompt" | Open | O |
| FA-123 | Low | A6 | The memory-mirror and vault hints describe behaviour the code does not have | Open | O |
| FA-124 | Medium | A3 | `arabic_translate` (read tier, no card) sends text to MyMemory and returns it unfenced | Open | O |
| FA-125 | Medium | A5 | Settings Non-Stop "Per-Tool Timeout" is a dead control | Open | O |
| FA-126 | Medium | A3 | `config_save`'s protected list misses keys that redirect secrets or memory | Open | O |
| FA-127 | Low | A4 | `gateway.rate_limits` is read once at boot | Open | O |
| FA-128 | Medium | A4 | Chat apps preview links in model-written replies | Open | O |
| FA-129 | Medium | A19 | `GET /api/settings/proxy` returns the decrypted proxy password | Open | O |
| FA-130 | Low | A14 | The GitHub OAuth result page puts its message into HTML unescaped | Open | O |
| FA-131 | Low | A20 | `/health/ready` returns raw exception text without sign-in | Open | O |
| FA-132 | Low | A4 | The Telegram webhook logs a WARNING per unauthenticated POST | Open | O |
| FA-133 | Low | A5 | OIDC `state` is one global key compared with `!=` | Open | O |
| FA-134 | Low | A8 | Upload size limits apply after the body is spooled to disk | Open | O |
| FA-135 | Medium | A20 | Every log line is written and flushed on the event loop | Open | O |
| FA-136 | Medium | A1 | A watching WebSocket that stops reading freezes its thread's stream | Open | O |
| FA-137 | Medium | A20 | Three cancel-then-unbounded-await sites the §26E gate misses | Open | O |
| FA-138 | Medium | A20 | After a fatal loop error the server does not exit | Open | O |
| FA-139 | Low | A3 | Small synchronous file I/O inside async tools | Open | O |
| FA-140 | Medium | A4 | Telegram chunks are sized in code points, not UTF-16 units | Open | O |
| FA-141 | Low | A20 | Four append-only stores have no retention | Open | O |
| FA-142 | Low | A19 | `settings.db` free pages and a large `knowledge.db` WAL are never reclaimed | Open | O |
| FA-143 | Low | A20 | `guard.log` never rotates | Open | O |
| FA-144 | Low | A19 | The universal backup's manifest is written in place | Open | O |
| FA-145 | Low | A20 | Two process-lifetime maps are never pruned | Open | O |
| FA-146 | Low | A1 | Uploaded attachments are stored per upload with no dedupe or retention | Open | O |
| FA-147 | Medium | A3 | A graph built while HITL was off stays ungated after HITL is turned on (§7) | Open | O |
| FA-148 | Low | A1 | `/abort` leaves an armed long task and its YOLO window (§25B) | Open | O |
| FA-149 | Low | A6 | Macro sleep and reconsolidation wait from boot (§15B) | Open | O |
| FA-150 | Low | A6 | `_distinct_tenants` reads SQLite on the event loop | Open | O |
| FA-151 | Low | docs | §2 says graph state never holds a chat id; §16B puts it there | Open | O |
| FA-152 | Low | docs | Stale names, counts and paths in AGENTS.md §1-§26 (grouped) | Open | O |
| FA-153 | Medium | A4 | `/swarm config disable` bypasses the chat-app admin gate | Open | O |
| FA-154 | Low | docs | Stale names in AGENTS.md §27 onward (grouped) | Open | O |
| FA-202 | Low | A8 | The document worker logs ERROR on every claim during a pool outage (104 in 5 min) | Open | D C2 |
| FA-203 | Low | A20 | Stall dumps are never pruned (103 since 2026-08-30) | Open | D C3 |
| FA-204 | Low | A19 | Old-format document-store backup folders are never pruned | Open | D C4 |
| FA-205 | Low | A8 | An orphan `document_blobs` row GC does not collect | Open | D C5 |
| FA-206 | Medium | A19 | `/api/settings` returns the S3 access key id unmasked | Open | D C6 |
| FA-208 | Low | A20 | The daily digest's send time drifts through the day | Open | D C8 |
| FA-209 | Medium | repo | The release workflow runs third-party actions by mutable tag | Open | D C9 |
| FA-210 | Medium | A1 | The live bubble names the active model, not the one that answered | Open | D C10 |
| FA-211 | Medium | A2 | A provider body that is not JSON, or has no choices, is reported as the tool-round limit | Open | D C11 |
| FA-212 | High | A6 | Self-improvement writes a lesson into every later prompt after failed and kept-out turns | Open | R |
| FA-213 | Medium | A2 | A stream cut without `[DONE]` is saved as a complete answer | Open | D C13 |
| FA-214 | High | A2 | One streaming 429 ends the turn with no backoff or retry | Open | R |
| FA-215 | Medium | A19 | The offsite copy is a plain zip holding `.env` beside the vault | Owner | D C15 |
| FA-217 | Low | A3 | A shell command naming a user's bare `*.db` is refused as a Kazma store | Open | D C17 |
| FA-218 | High | infra | Postgres (superuser login), Neo4j and SearXNG are published on every interface | Owner | R |
| FA-219 | Low | A5 | Unauthenticated `/api/auth/status` reveals the trusted-proxy list | Open | D C19 |
| FA-222 | Medium | A5 | The app's catch-all handler returns the exception's type and text | Open | D C22 |
| FA-224 | Low | A5 | `kazma acquisition status`, a diagnostic, tries to write | Open | D C24 |
| FA-225 | Low | A5 | `kazma doctor` fails on an em dash under cp1252; `--version` missing | Open | D C25 |
| FA-226 | Low | A3 | The model claims a gate stopped `rm -rf /` when it declined itself | Open | D C26 |
| FA-227 | Low | A1 | A `context_compacted` notice appears on a language switch | Open | D C27 |
| FA-228 | Medium | A6 | An Arabic "remember this" is stored as `agent_asserted`, the English one as `user_explicit` | Open | D C28 |
| FA-229 | Low | A7 | The Knowledge footer is English on Arabic answers | Open | D C29 |
| FA-230 | Medium | A8 | A year at the end of an Arabic sentence in an LTR paragraph moves to the front when read back | Open | D C30 |
| FA-231 | Low | A8 | The malware name is shown only with a dot | Open | D C31 |
| FA-232 | Medium | A8 | Malware signatures 41 days old, no alert | Open | D C32 |
| FA-233 | Medium | A8 | Each upload waits 6.5-6.9 s for `clamscan` | Open | D C33 |
| FA-234 | Low | A8 | Document errors use three envelopes | Open | D C34 |
| FA-235 | Low | A9 | The research rubric is structural only | Open | D C35 |
| FA-236 | Low | A10 | Dispatch to an unknown worker answers 200 | Open | D C36 |
| FA-238 | Medium | A9 | An Arabic research question gets an English report | Open | D C38 |
| FA-239 | Low | A10 | A one-sentence swarm answer costs ~24,900 prompt tokens | Open | D C39 |
| FA-240 | Low | A9 | "Sources loaded N/N" counts one more than acquired | Open | D C40 |
| FA-241 | Low | A11 | An empty prompt answers 409; the 422 echoes the whole input | Open | D C41 |
| FA-242 | Medium | A13 | Listing Gmail costs ~0.6 s per message (a TLS connection per request) | Open | D C42 |
| FA-243 | Low | A13 | The model repeats `email_list` calls with the same results | Open | D C43 |
| FA-244 | Low | A14 | The IDE diff shows blank lines | Open | D C44 |
| FA-245 | Medium | A15 | The MCP Test hides the cause of a failure | Open | D C45 |
| FA-246 | High | A6/A16 | A fork of a chat kept out of memory is remembered | Open | R |
| FA-247 | Medium | A17 | A document refused by policy is paged as a failed run with Retry | Open | D C47 |
| FA-248 | High | A18 | Read aloud and voice-note replies fail: the default TTS is refused by Microsoft, silently on chat apps | Open | R |
| FA-249 | Medium | A18 | Skills Validate resolves against the CWD and scores a missing folder 70/100 | Open | D C49 (C23) |
| FA-250 | High | A5 | Settings -> Tools' switch changes nothing | Open | R |
| FA-251 | Low | A19 | A settings restore writes keys no rule names | Open | D C51 |
| FA-252 | High | A19 | An export run outside the install exports an empty install, and `verify` passes it | Open | R |
| FA-253 | Low | A3 | A commitment-denied call leaves no step in the turn | Open | D C53 |
| FA-254 | Medium | A19 | Memory repair copies travel in bundles; one is missing from the erasure horizon | Open | D C54 |
| FA-255 | High | A19 | An import revives revoked "Allow tool" grants and signed-out web sessions | Open | R |
| FA-256 | Medium | A15 | Testing an unsaved HTTP MCP server pages a false alert | Open | D C56 |
| FA-257 | Low | A20 | Two metric families say less than their names | Open | D C57 |
| FA-258 | High | A20 | Live runs 26 packages below this build's floors, 10 with advisories, and an outdated document image | Owner | R |
| FA-259 | High | A20 | A two-minute Postgres outage gets Kazma killed; one drill kept it down 15 min | Open | R |
| FA-260 | Low | A9/A17 | Research and Diagnose model calls carry no turn, so their cost belongs to nothing | Open | D C60 (C37) |
| FA-261 | Medium | A20 | A reload with work running uses 58-60 s of the 60 s grace | Open | D C61 |
| FA-262 | Medium | A1 | A reload ends a running stream with a false ending | Open | D C62 |
| FA-263 | High | A7 | A crawl cut by a graceful reload never resumes | Open | R |
| FA-264 | Medium | A8 | A reload spends one of a document job's attempts | Open | D C64 |
| FA-265 | Low | A20 | Shutdown waits 15 s on an open approval stream, with an ERROR | Open | D C65 |
| FA-266 | Medium | A9 | Research admits 4 runs while its storage holds 3 | Open | D C66 |
| FA-267 | Medium | A19 | The universal backup copies nested live SQLite stores raw, without their WAL | Open | D C67 |
| FA-268 | High | A19 | The documented restic restore yields a layout Kazma does not read, and reports success | Open | R |
| FA-269 | Low | A19 | The disaster-recovery doc names the wrong `restic.pass` path | Open | D C69 |
| FA-270 | Low | A19 | A restored tree's ACL; WAL/SHM files left behind | Open | D C70 |
| FA-271 | Medium | security | The GitHub App private key sits as plaintext in Postgres beside its vault copy | Owner | D C71 |
| FA-272 | Low | security | `.env` and `restic.pass` are readable by a sandbox group | Owner | D C72 |
| FA-274 | Low | A1 | A malformed body to `POST /api/chat/sessions` answers 503 | Open | D C74 (C20) |
| FA-275 | Low | A6 | A memory GET answers 200 with `not_found` | Open | D C75 |
| FA-276 | Medium | A6 | Recall's refused embedding slot is reported as a missed deadline | Open | D C76 |
| FA-277 | Medium | A1 | First token misses its budget (p50 5.3 s, p95 20.1 s) | Owner | D C77 |
| FA-278 | Low | A1 | The chat session list serializes under concurrent reads | Open | D C78 |

Not findings or folded: C1 is part of FA-258; C7 (the owner's retention setting of 2); C16 became FA-003; C20 is FA-274; C21 was dropped (a WebSocket close handshake); C23 is part of FA-249; C37 is part of FA-260; C73 was not used.

## 4. The High findings

### FA-001 (fixed) Read-tier tools read the install's secrets

- **Seen live, twice:** with the workspace at the install folder (the live default), `file_read(".env")` and `file_search("KAZMA_VAULT_KEY")` returned the vault key, `KAZMA_SECRET`, the DSN and `restic.pass`, with no card. `generate_pdf(markdown_path=".env")` would print `.env` into a PDF.
- **Cause:** the path policy's rule 0, `store_registry.is_kazma_store`, knew SQLite stores only, so a secret file was an ordinary workspace file. The same class had six other doors:
  - the skill-folder bypass in `tool_scope.resolve_in_scope` and `file_read._resolve_for_read`;
  - the search walk (`_searchable_text`);
  - ripgrep's `--hidden`;
  - `document_generator`'s `_P(markdown_path)`, which the raw-path gate missed through an import alias;
  - `document_platform._resolve_workspace_file`;
  - `send_message._file_attachment`.
- **Fix:** `cdbe9483` adds `is_kazma_secret_file` (named secret files and their copies) beside `is_kazma_store` at every door, and the refusal names what the file is.
- **Gates:**
  - `tests/test_kazma_secret_files_refused.py` exercises every door;
  - `tests/test_tool_paths.py` now resolves import aliases.
- **Verified live on `73eee4b6`.**

### FA-002 (fixed) The sandbox saw `.env`

- **Seen live:** `code_exec._run_docker_jail` mounted the whole workspace read-only. An approved `python_exec` read `/workspace/.env` (2,395 bytes).
- **Fix:** `_workspace_mounts` hides secret files under `/dev/null` and Kazma folders under tmpfs, and fails closed when a mount cannot be built. `path_policy.sandbox_workspace_target` maps the paths.
- **Gate:** `tests/test_sandbox_hides_kazma_files.py`. Its negative control (the old mount) reads 37 bytes of a planted `.env`.
- **Verified live after the deploy:** `.env` reads 0 bytes inside the sandbox, `README.md` reads 20,391.

### FA-003 (fixed) Copies of stores were ordinary files

- **Seen live:** `memory_state.db.pre_relabel` and `research_sessions.db.bak-2026-08-14-flood` were readable through the file tools.
- **Fix:** `_DB_COPY_NAME` (a store name with a copy suffix) plus `_has_sqlite_header` for any other name under the data dir.
- **Gate:** `test_a_copy_of_a_store_is_a_store`.

### FA-004 Kazma misreads its own Arabic PDFs

- **Seen live:** a DOCX Kazma made, converted to PDF by Kazma and uploaded back (document `20585eee`, job `2a29f21e`), read wrong in three ways:
  - the title line twice;
  - a cut word ("The audit codew");
  - the borderless table as "QuarterValue / Q1120 / Q2135".
- **Minimal repro:** two English lines and a row "Q1" / "120" read correctly. Add one Arabic word anywhere on the page, and the first line repeats and the row reads "Q1120". Dev `main` reads the same.
- **Cause:** on a page with right-to-left glyphs, `pdf_layout.extract_pymupdf_page_text` (`pdf_layout.py:335`) re-reads every block from its glyphs, with two faults:
  - `pdf_rtl.py:379` takes a glyph whose baseline is up to half a font size above the block, so the line above joins the block;
  - `pdf_rtl.py:310`, the left-to-right early return, joins glyphs with no word gaps.
- **Class:** every left-to-right block on a page with any Arabic, in every PDF Kazma reads: uploads, research PDFs, and the read-back of its own bilingual reports, which the agent trusts.
- **Fix:**
  - take glyphs whose baseline lies inside the block;
  - apply the word-gap rule on left-to-right lines too.
- **Gate:**
  - the minimal PDF reads the same English text with and without the Arabic word;
  - the DOCX -> PDF -> read round trip gives "Q1 | 120".
  - Negative control: the current `text_in_rect`.

### FA-101 `kazma update` reports success over a conflicting stash pop

- **Cause:** `kazma-cli/kazma_cli/update.py:1997` drops the result of `_restore_stash`, which returns False on a conflict. `_postflight_ok` checks only that HEAD equals `origin/main` and that the CLI imports. Line 2033 returns `ok and back`.
- **Effect:** the update says it succeeded, while:
  - `kazma.yaml` holds conflict markers;
  - the owner's edits sit in an unannounced stash.
- **Live exposure:** the live install commits its local `kazma.yaml` delta, and an incoming `kazma.yaml` change is exactly the conflicting case.
- **Fix:**
  - on a failed pop, put the tree back, name the stash and return a distinct status;
  - `_postflight_ok` refuses unmerged paths (`git diff --diff-filter=U`).
- **Gate:** the repro as a test, plus `update.py` in `tests/test_failure_results_are_used.py`.

### FA-112 The free-slot search answers "free" over booked time

- **Cause:** every backend uses a working day of `date + "T09:00:00+00:00"` plus 8 h and filters events by `s.date() == day.date()`:
  - `calendar/backends/sandbox.py:89-114`;
  - `google_calendar.py:167-194`;
  - `outlook_calendar.py:209-239`.

  So the search ignores:
  - an event that started on an earlier day;
  - an all-day event;
  - the operator's zone (09:00 UTC is 12:00 in Kuwait).

  The tool takes one date, while its manifest promises a range.
- **Repro:**
  - sandbox: an event from 11-02 to 11-05 still yields 10 free slots on 11-03;
  - Google shapes: an all-day event plus an overnight event yields a first free slot at 09:00Z.
- **Fix:** one overlap finder in `backends/base.py`, in the operator's zone, that parses all-day events. Every backend uses it.
- **Gate:** the three cases on every backend. Negative control: the current per-backend loop.

### FA-212 Self-improvement writes lessons from failed and kept-out turns

- **Seen live:** after every chat turn the refiner writes a lesson into the supervisor's Soul, which is part of every later prompt. That includes:
  - a failed turn whose refiner also failed: a template line, `"[SelfImprovement] 1/1 stages failed."` (`skills/self_improvement.py:327`);
  - a chat kept out of memory;
  - whatever the turn's own model said: a fault-server echo became a lesson.
- **Effect:**
  - The Soul keeps 12 blocks, so six failed or test turns evicted half of the owner's live lessons.
  - During a provider outage every failed turn pushes a contentless line into every prompt.
  - Each re-cap also adds a separator, so older blocks carry growing runs of blank lines.
- **Fix:**
  - no lesson from a turn that failed (`turn_failed`), from a chat the forget ledger keeps out, or from a fallback template;
  - the cap keeps the blocks as they are.
- **Gate:** each of the three inputs through the real post-turn path yields no Soul change; a successful turn yields one. Negative control: the current refiner.

### FA-214 One streaming 429 ends the turn

- **Seen live:** the fault server's `fault-429` got one streaming request, and the turn failed in under 2 s. The reply said the call "was retried automatically but kept failing".
- **Cause:** the 429 branch of the stream path (`llm_provider.py`, near line 1683) raises `LLMError(transient=True, kind="rate_limit_exhausted")` with no backoff and ignores `Retry-After`. The supervisor (`graph_supervisor.py:1607`) skips its retry for that kind, which was meant for a 429 the non-streaming path had already retried. Live has failover off, so nothing else catches it.
- **Effect:** every web and chat-app turn streams, so a provider's routine rate-limit answer fails the user's turn.
- **Fix:** the stream path retries a 429 with the non-streaming path's backoff, honouring `Retry-After`, before raising.
- **Gate:** a scripted provider answering 429 then 200 to streaming requests completes the turn. Negative control: the current branch.

### FA-218 Postgres answers on the LAN with a superuser login (owner action)

- **Seen live:** `kazma-db-win` is published on `0.0.0.0:5433`, and a TCP connect to the laptop's LAN address succeeds. The Windows rule "Docker Desktop Backend" allows any port on the Private and Public profiles.
- **Why it matters:**
  - `pg_hba` accepts any address with a password;
  - the `kazma` login is a SUPERUSER, so it can run `COPY ... TO PROGRAM`, which is command execution;
  - its password is 15 characters of two classes.

  Anyone on a network the laptop joins (a cafe counts as Public) can try passwords against the login that reads every chat and setting.
- **Siblings:**
  - Neo4j publishes 7474/7687 (auth on).
  - SearXNG publishes 8088 and answers anyone's searches.
- **Origin:** the shipped compose file binds `127.0.0.1`; these containers came from a hand-run `docker run`.
- **Fix (owner):** re-create the three containers with `127.0.0.1:` port bindings and the same volumes, after a checked dump. Optionally, a non-superuser login for Kazma.
- **Kazma-side gate:** the hardening report and boot check whether the database port answers on a non-loopback address of the machine, and whether the login is a superuser.

### FA-246 A fork of a kept-out chat is remembered

- **Seen live:** two forks of a chat marked "Don't remember this chat" (`fork-de87f21241e9`, `fork-29ecf1ac443f`) had their turns written to memory (three episodes). The kept-out chat itself wrote none.
- **Cause:** `thread_rewind.py:385-447` `fork_thread` writes the new chat's row and checkpoint and never asks the forget ledger to cover the new id.
- **Fix:** a fork of a kept-out chat is kept out (a whole-chat ledger row under the fork's keys, written before its first turn).
- **Gate:** fork a kept-out chat and run a turn through the real route; recall and the episode table find nothing. Negative control: the current fork.

### FA-248 Spoken replies fail

- **Seen live:** "Read aloud" fails, and voice-note replies on chat apps fail silently.
- **Cause:** the default provider, edge-tts 7.2.8, is refused by Microsoft with a 403; 7.3.1 works. The package is declared neither in `pyproject.toml` nor in the lock, so no floor and no update reaches it. `tts.py:303,364` imports it, the failure is logged at `:273`, and Telegram logs it at DEBUG.
- **Fix:**
  - declare `edge-tts>=7.3.1`;
  - log a voice-reply failure at WARNING with the cause.
- **Gate:** every third-party import a default provider reaches is declared with a floor. Negative control: the current undeclared import.

### FA-250 Settings -> Tools' switch changes nothing

- **Seen live:** a tool switched off is still offered to the model and still runs.
- **Cause:** `tools.<name>.enabled` is written at `settings_manager.py:1016-1018` and read only by the listing at `:1008`. `tool_registry.py` (445-466, 513-538) checks only the skill switch.
- **Fix:** the registry reads the tool switch where it reads the skill switch: left out of the definitions and refused on execute.
- **Gate:**
  - every key a Settings control writes has a reader outside its page;
  - the switched-off tool is absent and refused through the real registry.

  Negative control: the current registry.

### FA-252 An export from the wrong folder passes `verify`

- **Seen offline, on scratch folders:** `kazma migrate export` run outside the install exports a new, empty install, and `verify` passes it.
- **Cause:** `paths.data_dir()` creates `<cwd>/kazma-data` (`paths.py:138-149`). The exporter never checks that it found an install, and `verify` only warns on an empty bundle.
- **Fix:**
  - the exporter refuses a data dir with no stores;
  - `verify` refuses a bundle with no vault and no settings.
- **Gate:** an export from an empty folder fails, and so does the verify of an empty bundle. Negative control: the current exporter.

### FA-255 An import revives revoked grants and signed-out sessions

- **Seen on a scratch import (data level, nothing booted):**
  - the import wrote 5,161 settings rows, against 827 on live;
  - among them 690 `hitl_grant` and 63 `web_session` leaf rows: grants revoked and sessions signed out since the export, back again.
- **Cause:** importer step 7 (`ConfigStore.import_yaml`, `config_store.py:2475-2527`) writes leaf rows, and `get` rebuilds missing parents from them. The grant listing reads only the `safety` category (`hitl_grants.py:188-207`), so the revived grants do not show while still taking effect.
- **Fix:** the import applies §47's key classes: no runtime state or credentials from a bundle.
- **Gate:** an import of a bundle holding a grant and a session leaves neither effective. Negative control: the current import.

### FA-258 Live runs below this build's floors (owner action)

- **What live runs:**
  - 26 packages below this build's minimums, among them fastapi 0.143.3 (floor 0.162.0), websockets 15.0.1 (floor 17.3) and multidict 6.9.1 (floor 7.2.1);
  - 81 published advisories across 10 packages: brotli, certifi, dnspython, filelock, msgpack, pillow, pyasn1, pypdf, setuptools and virtualenv;
  - the fallback document image `src-7bc1e64b…`, while this build asks for `src-b85e9cb3…`.

  `install.requirements_unmet` has fired since 2026-11-01 18:52 UTC.
- **Gaps in the floors:** five advisory packages have no floor, and pillow's and pypdf's floors sit below the fix.
- **Cause:**
  - Dependabot raised floors to the newest release while CI was billing-locked;
  - the deploy recipe (pull + reload) never runs the update command.

  So the full suite tests a dependency set live does not run.
- **Fix (owner):**
  1. `<live python> -m kazma_cli update --reinstall -y`;
  2. `scripts/document_worker_image.py build`.
- **Fix (code):** floors at the fixed release for every advisory package.
- **Gate:** the dependency scan's advisories each have a floor at or above their fix.

### FA-259 A two-minute database outage gets Kazma killed

- **Drills on live:**
  - P1: Postgres stopped 2 min; Kazma down 01:17:24-01:18:37.
  - P2: Postgres stopped 12 min 2 s; Kazma down 15 min 10 s.

  §44 promises an outage ridden out with no restart.
- **Causes:**
  - The ConfigStore lock is held across the pool checkout (`config_store.py:1867-1884`, `:1644`).
  - The auth middleware runs an unbounded `to_thread` on `/health` (`auth.py:1380-1433`).
  - Readiness checks keep running after their `wait_for` (`health.py:432-446`), so the 32-thread executor fills. Probes then get no answer, and the guard restarts an unanswering server.
  - A boot exits after 5 pool attempts (`app.py:272`), and the guard's backoff (60/120/300 s) stretches a 12-minute outage to 15.
- **Fix:**
  - no lock held across a network checkout;
  - `/health` paths skip the auth store;
  - readiness checks cancelled at their cap;
  - a boot waits for the database (and answers "not ready") instead of exiting.
- **Gate:** a real-Postgres drill that stops the database for 2 min under probes; the server answers every probe and is never restarted. Negative control: the current lock scope.

### FA-263 A crawl cut by a reload never resumes

- **Seen live:** a Knowledge crawl running during a graceful reload stays `interrupted` with no resume, so every deploy during a crawl strands it.
- **Cause:** `kb_api.py:565-573` (`release_unclaimed`) releases the crawl with `interrupted_by` unset. Auto-resume (`crawl_jobs.py:839-847`) needs `process_ended`, which only `_recover` (`:503`) writes.
- **Fix:** a release at shutdown records `interrupted_by: process_ended`.
- **Gate:** a crawl stopped by the shutdown path resumes at the next start. Negative control: the current release.

### FA-268 The documented restore does not restore

- **Seen on scratch:** `restore.py:263` moves `dbs/` and `assets/` in as folders, so Kazma finds 0 of 15 stores at the data dir; eight sit one level down. Meanwhile:
  - the API restore took 10.4 s and reported ok;
  - the CLI printed "RESTORED: 9/9 steps";
  - `disaster-recovery.md` says "Rehearsed: 2026-08-29".
- **The test:** `tests/test_restore.py:162` checks only that the restore ran.
- **Fix:** restore into the layout Kazma reads (the stores at the data dir's top).
- **Gate:** after a restore, `store_registry` finds every bundled store where Kazma opens it, and a memory recall reads the restored data. Negative control: the current layout.

## 5. Medium and Low findings

Each Medium and Low finding has its cause, file and line, fix, gate and negative control in its source file:
- FA-1NN in `offline_findings.md`;
- FA-2NN under candidate C<NN> in `findings_draft.md`.

The remediation plan stages them by class.

## 6. Coverage

### Inventory (`evidence/functional_2026-11/inventory.json`, built from the code)

| Item | Count | Item | Count |
|---|---|---|---|
| API routes | 580 (517 distinct paths) | Stores | 81 |
| Agent tools | 168 | Schedulers | 36 |
| Pages | 17 | Maintenance sweeps | 20 |
| Settings tabs | 19 | Run kinds | 9 |
| Slash commands | 41 | Ops alert keys | 69 |
| Settings keys (shipped) | 98 | `KAZMA_*` variables | 280 |
| MCP presets | 13 | Skills | 25 |
| Connectors | 38 | | |

### Routes exercised live

The live pass called 94 of the 517 distinct paths (18 %), listed in `evidence/functional_2026-11/routes_hit.json`. The rest of each area was covered by its features through the chat, the pages and the CLI, and by the repository's gates (Phase 3).

| Area | Hit | Area | Hit | Area | Hit | Area | Hit |
|---|---|---|---|---|---|---|---|
| settings | 7/66 | memory | 3/50 | x | 11/40 | swarm | 5/32 |
| documents | 7/31 | research | 7/23 | ide | 9/22 | email | 0/19 |
| github | 0/18 | chat | 4/17 | kb | 6/15 | mcp | 5/11 |
| system | 0/11 | providers | 0/8 | replay | 5/8 | skills | 4/8 |
| voice | 3/8 | calendar | 0/7 | workspace | 0/7 | scheduled | 4/6 |
| auth | 2/6 | backup | 0/6 | health | 4/6 | agents | 0/5 |
| models | 0/5 | security | 1/5 | runs | 1/3 | ws | 1/2 |
| alerts | 1/1 | approve | 1/1 | pending-approvals | 1/2 | dashboard | 1/4 |
| workspaces | 1/4 | | | | | | |

Email and calendar were exercised through the agent's tools in chat, not through their Settings routes. Backup and restore were exercised through the CLI and on scratch copies.

### By phase

| Phase | Done | Not done |
|---|---|---|
| 0 Baseline | Settings snapshot, backups verified, health, WARNING and stall counts, build | |
| 1 Inventory | `inventory.json` from the code; light gates (ruff gate, `node --check` on 56 files, 87 JS tests, `uv lock --check`) | CI on GitHub (billing lock since 10-14) |
| 2 Structural sweep | Offline hunts over every package (FA-101..154) | |
| 3 Live functional pass | A1-A3, A5-A20 as in section 1; 273 area tests on dev HEAD (41 skipped, 0 failed) | A4 by desktop control |
| 4 Crash and outage drills | Postgres 2 min and 12 min; three reloads with work running; migration import (data level); memory restore drill; fault provider (slow, 5xx, 401, 429, cut stream, malformed, empty, truncated tool call) | Hard kill (needs the owner's yes); booting a restored copy |
| 5 Load and soak | Read load 10/30/60 clients; 4 and 8 concurrent chats; the product's soak harness tests (59 passed in 312 s) | The 24 h live soak |
| 6 Security | Headers, CSP, 401, CORS, CSRF, CSWSH; hardening report (7/8); prompt injection from an attached document in EN and AR (nothing ran); 94 boundary tests | Cloudflare Access from outside; chat-app spoofing |
| 7 Claims against reality | `what-is-kazma.md`, `FEATURES.md` and AGENTS.md against what live did (section 9) | |

## 7. Measurements

### Read load (`loadread.py`, 13 polled GET routes, `/health/live` probed once a second)

| Clients | Requests | rps | p50 | p95 | p99 | max | non-2xx | `/health/live` max |
|---|---|---|---|---|---|---|---|---|
| 10 | 2,373 | 39.4 | 0.08 s | 1.02 s | 2.02 s | 3.09 s | 0 | 0.55 s |
| 30 | 2,309 | 37.7 | 0.31 s | 2.86 s | 6.83 s | 8.22 s | 0 | 1.66 s |
| 60 | 2,082 | 34.3 | 0.86 s | 7.14 s | 12.88 s | 16.0 s | 0 | 1.25 s |

- Throughput holds near 38 requests a second, and extra clients only add queueing.
- No stall dump was written and no WARNING logged.
- The chat session list (194 sessions, 82 KB) is the slow route: 0.27 s alone, p50 1.3 s at 10 clients and 8.3 s at 60 (FA-278).

### Turns

- **First token:** p50 5.3 s and p95 20.1 s over 67 web turns. The slowest was 187 s, a turn waiting on an approval.
- **Turns with no tool:** p50 5.5 s and p95 20.6 s.
- **Five trivial turns:** 2.1-2.6 s, each sending ~30,200 input tokens.
- **Kazma's own work before the provider:** ~1 s, measured against the local fault server. The rest is the provider: deepseek-flash reasons before its first visible token.
- **Four concurrent chats:** 12 of 12 answered, first token p50 3.1 s.
- **Eight concurrent chats:** 11 of 24 turns refused 429 in 15-47 ms by the per-minute bucket, as designed. Recall degraded on 4 and then 6 turns (FA-276).

### Drills

| Drill | When (UTC) | Outcome |
|---|---|---|
| P1 Postgres stopped 2 min | 2026-11-02 01:16 | Kazma killed by the guard; down 01:17:24-01:18:37 (FA-259) |
| P2 Postgres stopped 12 min 2 s | 01:23 | Kazma down 15 min 10 s (FA-259) |
| R1 reload with work running | 01:48 | Killed at the 60 s grace, paged (FA-261) |
| R1b reload with work running | 01:58 | Graceful at 58.2 s |
| R1c reload `--when-idle` | 02:03 | 33.9 s |
| Crawl during a reload | | Never resumed (FA-263) |
| Stream during a reload | | Ended with a false ending (FA-262) |
| Document job during a reload | | One attempt spent (FA-264) |
| Migration import, data level | | 38 s (the data RTO); paths rewritten, `pg_restore` into a scratch database, 28 files and 4,974 keys, row parity equal; revived grants and sessions (FA-255) |
| Memory restore drill | | RTO 0.078 s (40 turns, 20 rebuilt, 0 missing) |
| Restic restore of a backup to scratch | | 10.4 s API, "9/9 steps", unreadable layout (FA-268) |

### Backups

- **RPO:** 6 h by schedule, local and offsite. The last offsite copy was 2026-11-01 23:21 (344 MB zip; the restic remote beside it).
- **Whether the backups restore:** the restore path is broken (FA-268), the zip holds `.env` (FA-215), and nested SQLite stores are copied without their WAL (FA-267).

### Soak

- The product's harnesses ran as their tests: swarm, memory, Time Travel, scheduled, run supervision and Knowledge. 59 passed in 311.6 s.
- **The 24 h live soak was not run;** the live time went to the functional pass and the drills.
- The multi-day area soaks of 2026-10-14 to 10-16 stand as recorded in their trackers.

### Cost

- Not budgeted, by the owner's authorization.
- A swarm one-sentence answer costs ~24,900 prompt tokens (FA-239).
- Research and Diagnose calls carry no turn, so their cost appears in no total (FA-260).

## 8. Signals

- **WARNING lines** rose from 86 at Phase 0 to 246:
  - 10 are FA-276 (recall under concurrency);
  - the rest came from the drills: database outages, kills, reloads, the fault provider and the scratch MCP Test.
- **Stall dumps:** 103 before and after the load.
- **Digest and ledger:**
  - The daily digest went out each day. Its 2026-11-01 render matched the day: 61 turns, 21 approvals (12 approved, 6 expired, 3 denied), 4 reloads, 5 failed.
  - The weekly ledger last ran 2026-10-27; its next run is 2026-11-03.
- **Cron:** heartbeat 9.3 s; lateness p50 12.1 s.

## 9. Claims against reality

### Claims the live install does not meet

| Claim (where) | What the audit saw | Finding |
|---|---|---|
| "every screen and API shows them [secrets] masked" (what-is-kazma) | The S3 access key id is unmasked; `/api/settings/proxy` returns the decrypted proxy password; the GitHub App private key sits as plaintext in Postgres beside its vault copy | FA-206, FA-129, FA-271 |
| "To move a whole install ... use `kazma migrate`" | An export from the wrong folder passes `verify`; an import revives revoked grants and sessions | FA-252, FA-255 |
| "When Kazma falls back to another model, it tells you" / "a ⚠️ warning, never a made-up answer" | A malformed provider body is called a step limit; a cut stream is saved as complete; the bubble names another model | FA-211, FA-213, FA-210 |
| "a card arrives ... with Retry" | A policy refusal is paged as a failure, with a Retry that cannot succeed | FA-247 |
| "keep a chat out of memory" | A fork of it is remembered; self-improvement learns from it | FA-246, FA-212 |
| "Arabic and English ... as equals" | Arabic "remember" loses its provenance; an Arabic research question gets an English report; English footer on Arabic answers | FA-228, FA-238, FA-229 |
| "correct Arabic typography" | A year moves to the front when Kazma reads its own PDF back; English lines on an Arabic page misread | FA-230, FA-004 |
| "A guard restarts Kazma on real failures, reloads it gracefully" | A short database outage gets it killed; a reload uses the whole grace | FA-259, FA-261 |
| "backed up every 6 hours ... restore rehearsed" | The documented restore is unreadable; nested stores are copied raw | FA-268, FA-267 |
| "One command updates Kazma" | Live runs below its floors; the deploy recipe skips the command; `kazma update` hides a stash conflict | FA-258, FA-101 |
| "Voice: spoken replies" | Refused by the provider, silent on chat apps | FA-248 |
| "Durable crawls ... resume" | Not after a graceful reload | FA-263 |
| Skills "verified" | Validate cannot validate an installed skill | FA-249 |
| Settings -> Tools switches tools off | It does not | FA-250 |
| Cost per answer | Research and Diagnose cost belongs to no turn | FA-260 |

### Claims checked and true on live

- **Approvals:**
  - Cards wait in both languages, and Deny is reported as denial.
  - `shell_exec` is refused before any card when host shell is off.
  - An always-ask card refuses "Allow tool" and YOLO (409 `one_time_only`).
  - A grant's unasked run is logged once, and `/yolo off` clears it.
- **Secrets:** since FA-001..003, no read-tier tool, file search or sandboxed `python_exec` reads `.env`, `restic.pass`, the vault or a store copy.
- **Prompt injection** from an attached document, in EN and AR: disclosed to the user, and nothing ran.
- **Reminders** fire on time and reach the owner's Telegram.
- **Memory:** "remember" works in both languages. Forgetting a whole chat removes its memories, notes, facts and steps (on audit data).
- **Time Travel:** a restore refuses a running turn and a pending approval, and an expired approval is reported as expired.
- **Ops:** health checks make a real round trip; the digest and the weekly ledger are produced.

## 10. AGENTS.md against the code and the live install

- **Offline hunts:**
  - FA-147: §7, a graph built with HITL off.
  - FA-148: §25B, `/abort` and the long task.
  - FA-149: §15B, waits from boot.
  - FA-150: the loop-stall rule.
  - FA-151: §2 versus §16B.
  - FA-152 and FA-154: stale names, counts and paths.
- **Live pass:**
  - §44, "an outage is ridden out": FA-259.
  - §24B, "a crawl cut off resumes once on its own": FA-263.
  - §27, "the documented restore": FA-268, FA-269.
  - §50, "a minimum is a security floor": FA-258.
  - §26F, "errors do not carry internals": FA-222.
  - §10A, "never the process's CWD": FA-249.
  - §20C, the provenance ladder: FA-228.
  - §11B, Soul hygiene: FA-212.
  - §33, "native_pg_backup ops wiring is still deferred": stale, the wiring is done.
  - §24E and `HITL_VIEW_MODEL.md` call Playwright 1 and 4 unclaimed, while §31 says claimed.

## 11. Not tested, and why

| What | Why |
|---|---|
| A4 chat apps beyond one sample, including the caption fix `ae02c2ea` on Telegram and Slack | Desktop control of Telegram and Slack was declined, and Claude in Chrome was not connected |
| An X post card, and X generate, post and delete | Public actions need the owner's yes at that moment; generate saves drafts that can only be retired |
| The deny-before-card floor, live | The model declined both destructive requests itself (covered by the repository's tests) |
| The 24 h live soak and PG multi-replica | Time; one replica exists |
| Browser tours of the Knowledge, Documents and Memory pages | Time; their APIs were exercised |
| Arabic OCR on a scan, `memory_forget_mentions`, weekly summaries | Time; no audit data shaped for them |
| IDE writes, patches, `run` and checkpoint restores | The live workspace is the install folder (limit 1); a delete probe was declined by the session's own safety check |
| A configured vision model | None configured; the honest refusal was checked |
| Chat-app spoofing and injection | Needs a second person or a second account |
| Cloudflare Access from outside | Needs an outside network |
| OAuth end to end | Re-sign-in needs the owner |
| Booting a restored copy or an imported install | Not every outbound integration could be guaranteed off before boot (limit 6); verified at the data level instead |
| A hard kill | Needs the owner's yes at that moment |

## 12. Live actions

`evidence/functional_2026-11/live_actions.md` logs every action that touched live, with its time, purpose, what it changed and how it was undone. The groups:
- the baseline snapshot and backups;
- audit chats (memory off, tagged `audit2611`);
- KB libraries, documents, research sessions, swarm tasks and scheduled jobs, each tagged;
- a scratch fault provider (`audit2611-fault`, loopback);
- the drills;
- the deploy of `73eee4b6`;
- the cleanup.

Every audit artifact was removed, and the settings returned to their Phase 0 values (section 13).

## 13. Cleanup and final state

Rows 82-94 of `live_actions.md` hold the detail. In short:

- **Settings.** The export matches the Phase 0 snapshot except three keys the
  product changed itself: X `verified_at`, the X daily read counter, and
  `skills.enabled.native:arabic_bilingual_nlp=true` (its default). The
  supervisor's Soul is exactly its Phase 0 value; the 40 lessons written during
  audit turns are gone.
- **Removed through Kazma's own routes:** the test provider `audit2611-fault`,
  39 audit chats (the two forks forgotten first), 13 research sessions, two
  Knowledge libraries, two documents and 11 run incidents. The scratch
  worktree and scratch folders are gone too, and no audit container or volume
  is left.
- **Left in place, because no route removes them** and the live install may
  not be edited by hand:
  - the chat attachments the audit uploaded;
  - the research report folders;
  - three finished cron jobs named "AUDIT-2611 ...";
  - the drills' swarm tasks (30-day retention);
  - the crawl journal rows (evicted by count);
  - the deleted documents' job records;
  - forget-ledger tombstones.

  Every user-facing store either has a delete route or ages out, except chat
  attachments and research report files, which grow until someone deletes
  them by hand. This is a gap, not a hazard (Low). It is noted here rather than numbered
  among the 128.
- **Final state at 06:50 local on 2026-11-02:**
  - `/health/ready` 200 and `/health/deep` healthy;
  - the build serving is 73eee4b6 (the Phase 0 build plus the section 7
    hotfix cdbe9483);
  - no pending approvals, open incidents or running turns.
- **WARNING rate:** zero in the last 30 minutes. In the hour before, there were
  10, all recall embedding deadline misses during the cleanup's forgets. Phase 0
  ran at about 4 an hour.
- **Backups are current:** the universal backup, the Postgres dump and the
  restic remote snapshot all ran at 01:19-01:21 local. The next is due about
  07:20.

## 14. Evidence

| File | What |
|---|---|
| `evidence/functional_2026-11/inventory.json` | The inventory, from the code |
| `evidence/functional_2026-11/routes_hit.json` | Routes the live pass called |
| `evidence/functional_2026-11/findings_draft.md` | Every live candidate in full, area results, Phases 3-6 |
| `evidence/functional_2026-11/offline_findings.md` | The offline hunts (FA-101..154) |
| `evidence/functional_2026-11/live_actions.md` | Every live action and its undo |
| `evidence/functional_2026-11/c50_repro.py`, `c55_repro.py`, `c55_session.py` | Repro scripts for FA-250 and FA-255 (no secrets) |
| `C:\Users\balfa\kazma-audit-evidence\2026-11\` | Screenshots and the Phase 0 settings snapshot (kept off the repository) |
