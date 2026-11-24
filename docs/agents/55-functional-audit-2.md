<!-- AGENTS.md, piece 55-functional-audit-2. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 55. The functional audit of 2026-11, Stage 2 (the Medium findings)

Plan `docs/plans/FUNCTIONAL_AUDIT_2026-11_REMEDIATION.md`, Stage 2. Each fix
has a gate that fails on the old code.

- **Secrets and error text.** Every `GET /api/settings*` route is walked with
  every sensitive key seeded with a sentinel: none echoes one (the S3
  `access_key` and the decrypted proxy password did, FA-206/129); the app's
  catch-all answers `safe_error` (FA-222); a plaintext secret row becomes a
  vault pointer even when the vault already holds the value (FA-271; the
  GitHub App key rotation stays the owner's). Gates:
  `tests/test_settings_never_echo_secrets.py`,
  `tests/test_plaintext_secrets_move_to_the_vault.py`.
- **Provider honesty.** A 200 that is not a model answer (not JSON, no
  choices) is `LLMError(kind="unusable_answer", transient=True)` naming the
  body, never an empty answer (FA-211); a stream that ends with neither
  `[DONE]` nor a `finish_reason` is `stream_truncated` (FA-213); a turn that
  ends without an answer says why -- the step limit, an output cut, an
  unusable answer -- and lists only this turn's tools that ran
  (`graph_respond._stop_cause`, `plan_fence.running_turn`); a tool call cut
  off twice ends the turn. The `done` frame names the model that answered
  (FA-210), and so does every save of the reply (`model`): the page's stream
  saved it once more with the model it asked for, and a reload named another
  model (2026-11-07). The row keeps the model the turn ran with apart
  (`run_model`): a client that names no model continues with it, never with
  the answering one (after a failover that is the backup). Failover chains follow a provider change
  (`ModelRegistry.generation`, `reset_failover_cache`, FA-106). Discovery
  returns a typed `DiscoveryResult`, the route answers 502 with the reason,
  the page says it (FA-107). Recall waits for an embedding slot inside its own
  deadline (FA-276). Gates: `tests/test_partial_result_says_what_happened.py`,
  `tests/test_stream_cut_off.py`, `tests/test_done_frame_names_the_answering_model.py`,
  `tests/test_reply_keeps_the_answering_model.py`,
  `tests/test_failover_follows_provider_changes.py`,
  `tests/test_model_discovery_failure_is_said.py`,
  `tests/js/test_discovery_failure.js`,
  `tests/test_recall_waits_for_an_embedding_slot.py`.
- **Failure results read where returned.** `vault_store` raises
  `VaultWriteFailed` and a sign-in whose tokens were not kept fails (FA-104);
  a Discord chunk counts only on a 2xx (FA-105); a rewind journals before it
  opens the database (`forget_journal` kind `rewind`, FA-102); the vault's
  first use is one locked construction (FA-103); an MCP Test says why it failed
  and pages nothing (`connect_from_config(quiet=True)`, FA-245/256). Gates:
  `tests/test_vault_write_failure_fails_the_sign_in.py`,
  `tests/test_chat_send_rate_limited_is_not_sent.py`,
  `tests/test_rewind_journaled_first.py`, `tests/test_vault_first_use.py`,
  `tests/test_mcp_test_says_why.py`.
- **Settings that reach what they name.** The per-tool timeout is
  `agent.tool_timeout_seconds` (the old Non-Stop key moved once,
  `config_store._MOVED_KEYS`, FA-125); Skills' Validate checks the real skill
  (`agent_skills/checkup.check_skill`, FA-249); a log level applies at once
  (`logging_config.apply_level`, FA-114); the proxy card names the
  acquisition policy (FA-115); `config_save` cannot redirect secrets or memory
  (`protected_config` prefixes and `PROTECTED_CONFIG_PATTERNS`, FA-126); a
  gate turned on after the graph was built gates (FA-147); every `/swarm
  config` word that writes is admin-only (FA-153). Gates:
  `tests/test_tool_timeout_setting_is_real.py`,
  `tests/test_skill_validate_checks_real_skills.py`,
  `tests/test_log_level_applies_now.py`, `tests/test_proxy_names_the_policy.py`,
  `tests/test_protected_config.py`, `tests/test_hitl_turned_on_after_build.py`,
  `tests/test_swarm_config_admin_gate.py`.
- **Language parity.** "Remember" provenance judges the stored value in
  either language (FA-228); a research report is written in its topic's
  language, headers included (`research_synthesize.report_language`, FA-238);
  the PDF reader keeps an Arabic paragraph's numbers beside their words
  (FA-230). Gates: `tests/test_remember_provenance_in_both_languages.py`,
  `tests/test_research_report_language.py`,
  `tests/test_document_pdf_number_stays_beside_its_words.py`.
- **Restart and shutdown.** One stop budget (`shutdown.step_bound`;
  the guard hands `KAZMA_SHUTDOWN_BUDGET_S` = its grace less
  10 s) and waiting streams woken at the stop (FA-261); a stream cut by a
  reload ends `done` with `stopped_for_shutdown` and the page's notice, never
  a false pause (FA-262); a graceful stop gives document jobs back on the same
  attempt (`release_leases`, FA-264); a fatal error exits at once
  (`process_exit.exit_after_fatal_error`, FA-138); the three unbounded cancels
  are bounded (`cancel_all_and_wait`, FA-137); a stuck WebSocket cannot hold up
  the turn's stream (`delivery._fan_out_sockets`, FA-136). Gates:
  `tests/test_shutdown_fits_one_budget.py`,
  `tests/test_stream_stopped_for_shutdown.py`,
  `tests/test_document_reload_keeps_the_attempt.py`,
  `tests/test_fatal_error_exits_now.py`, `tests/test_bounded_cancel.py`,
  `tests/test_stuck_socket_does_not_freeze_the_stream.py`.
- **Kazma refreshes stale malware signatures itself** (resilience plan H).
  The maintenance sweep "malware signatures"
  (`documents/malware.signature_alert_sweep`) runs freshclam
  (`refresh_signatures`: on PATH or beside the scanner, with
  `tool_child_env()`, at most every six hours) while the signatures are stale
  or of unknown age, and alerts only when that did not bring them up to date,
  saying what freshclam answered. The live install's were refreshed once, at
  setup. Gate: `tests/test_malware_signatures_refresh.py` (stand-in ClamAV
  programs; the sweep without the refresh as the negative control).
- **Documents, research and runs.** A policy-rejected document is a failure
  with no Retry that pages nobody (`announced_by`, FA-247); the memory repair
  copies (`*.pre_*`) never travel in a bundle (`REPAIR_COPY_SUFFIXES`,
  FA-254); `dispatch_swarm`'s "auto" is the engine's route, no permanent
  `researcher` (FA-113); research storage is sized with admission and checked
  in it (FA-266, §24B); readiness reports the malware signatures' age and
  degrades past 7 days, the sweep "malware signatures" alerts, and
  `clamdscan` is used only when a clamd answers (FA-232/233). Something must
  refresh them: the container's entrypoint starts `freshclam --daemon
  --checks=12` as the user Kazma runs as (the image hands it
  `/var/lib/clamav` and `/var/log/clamav`), and a freshclam that cannot start
  is said and Kazma starts anyway; the Dockerfile had claimed the entrypoint
  did it, and nothing did. A host schedules `freshclam` itself (the live
  install had run it once, at setup: 41 days old when the check first read
  it). Gates:
  `tests/test_run_kinds_declared.py`, `tests/test_repair_copies_stay_home.py`,
  `tests/test_dispatch_swarm_routes_auto.py`,
  `tests/test_research_storage_sized_with_admission.py`,
  `tests/test_malware_signatures_and_scan_cost.py`,
  `tests/test_container_signatures_refresh.py`.
- **What Kazma says about itself, and what it sends.** The backup text says
  every 6 hours and what it holds (FA-116). Every tool that messages someone
  declares its recipients (`side_effects.OUTBOUND_TARGET_FIELDS`) and the
  outbound allowlist checks each; this conversation is exempt (FA-117). The
  model's product knowledge matches the code, the always-ask list derived
  from `ALWAYS_HITL_TOOLS` (FA-118). `arabic_translate` uses the configured
  model; MyMemory only with `KAZMA_TRANSLATION_REMOTE=mymemory`, POST, 500
  bytes; fenced (FA-124). No chat-app send or edit previews a link
  (`kazma_gateway/outbound_flags.py`, FA-128). Logging writes on a writer
  thread behind one `QueueHandler`; the turn id is read on the caller's side
  and exceptions are rendered there (`logging_config._write_behind_a_queue`,
  `drain_logs`, FA-135). Telegram is split and cut by its own UTF-16 count, a
  "too long" answer splits again, and a reply that did not fully arrive is
  said in the chat (`telegram_send._telegram_units`, FA-140). Kazma's own share of the first
  token is benchmarked (FA-277). Gates:
  `tests/test_backup_text_says_the_schedule.py`,
  `tests/test_outbound_targets_declared.py`,
  `tests/test_product_knowledge_is_true.py`, `tests/test_native_skill_egress.py`,
  `tests/test_chat_link_previews_off.py`, `tests/test_logging_off_the_loop.py`,
  `tests/test_telegram_units.py`, `tests/e2e/test_first_token_latency.py`.

