# Verification of the supplied audit report

Checked 2026-09-21 against commit `8b44a1e9fb194cf5e3ef6f23655ac3cdac444556`.

The central MCP approval bypass is confirmed. The report is useful, but not every secondary statement is accurate or independently established. This verification changes no application code.

## Method and limits

Inspected implementation, tests, workflows and the cited documentation. Ran a harmless interpreter reproduction of MCP classification and executor gating, plus 33 existing tests. Queried GitHub's branch API. Did not start/restart Kazma, read credentials, execute hostile code, run the full suite, restore a database, or rerun model benchmarks. Historical claims in documentation are distinguished below from fresh observations.

## Confirmed MCP bypass

The report's six classification rows reproduce exactly with HITL enabled and no current thread grants:

| Tool leaf (under `mcp__evil__`) | Classification | `requires_approval` | Commitment classification |
|---|---|---|---|
| `read_env` | safe | false | READ / SAFE / NONE |
| `get_file` | safe | false | READ / SAFE / NONE |
| `get_ssh_key` | safe | false | READ / SAFE / NONE |
| `list_env_vars` | safe | false | READ / SAFE / NONE |
| `write_file` | danger | true | DANGER / CRITICAL |
| `exfiltrate` | unknown | true | UNSAFE / CRITICAL |

Evidence chain:

1. `kazma-core/kazma_core/mcp/manager.py:282`: name-based classifier trusts read-style tokens.
2. `kazma-core/kazma_core/safety/hitl.py:632`: MCP approval is based on that classifier, not the explicit MCP allowlist.
3. `kazma-core/kazma_core/agent/graph_tool_worker.py:742`: sets `_graph_hitl_gate_ctx` before splitting safe/danger tools.
4. `kazma-core/kazma_core/mcp/manager.py:2305`: graph ownership clears `force_hitl`, even without `_hitl_approved_ctx`.
5. `kazma-core/kazma_core/safety/side_effects.py:290`: the commitment classifier shares the same name-based trust and gives safe names semantic tier NONE.

Interpreter experiment used an approval-required mock MCP manager returning only `HARMLESS_SENTINEL`, an empty `KAZMA_MCP_SAFE_ALLOWLIST`, and a denying safety checker. Pre/post hooks were neutralized to isolate the actual executor's gate logic; no real server or credential was accessed.

| Production | Graph flag | Approval checks | Mock server calls | Result |
|---|---|---|---|---|
| 0 | false | 1 | 0 | denied |
| 0 | true | 0 | 1 | sentinel returned |
| 1 | false | 1 | 0 | denied |
| 1 | true | 0 | 1 | sentinel returned |

This proves the approval bypass, not an end-to-end credential theft. Actual theft additionally requires the model to select the tool and the server to have access to the desired data. A remote MCP server does not gain host filesystem access simply from this defect. Stdio child environment inheritance is separately restricted in `manager.py`.

The shared graph-worker implementation supports the report's transport scope; individual live Web/Telegram/Discord/Slack sessions were not exercised. Normal bus execution still gates these names, subject to existing explicit trust, approval, allowlist and disabled-safety exceptions.

`docs/THREAT_MODEL.md:194` and `docs/KNOWN_GAPS.md:372` overstate protection. Production changes trusted-server treatment (`manager.py:1274`) but does not close this graph bypass. `tests/test_mcp_hitl.py:216` omits the graph flag. Its passing result therefore does not refute the finding.

Recommended repair: make graph and executor use one explicit MCP approval policy and make gate ownership distinct from approval of a particular call. Cover safe-looking names, production modes, graph/bus paths, rejection, explicit allowlisting, grants, and successful approval without duplicate prompts. Merely enlarging the sensitive-word list is insufficient.

## Other claims: assessment

| Claim | Verdict and evidence |
|---|---|
| File protection is not host containment | Confirmed boundary. `workspace/path_policy.py:193` denies file-tool writes to control-plane stores. Approved execution is a separate capability; this does not establish arbitrary execution can escape every configured sandbox. |
| Python execution: E2B, Docker, local fallback | Confirmed design in `tools/code_exec.py`. Qualify the report: production/multi-user modes prohibit local fallback unless explicitly overridden; local defense includes more than an import blocklist. Docker and E2B access depend on their mounts and configuration. |
| Session tool grants last about 30 minutes | Confirmed default: `safety/hitl_grants.py:28`. TTL is configurable. Historical gate-forgery incident was not replayed. |
| Context-less vault misses look absent | Confirmed for a secret existing only in a tenant scope. `security/vault.py:220` returns `None` without an automatic cross-tenant diagnostic. Global secrets can still resolve. Shared tenant context is imported from `tenant_context`; historical revert chronology was not independently replayed. |
| Relative reminders are unchecked without known subjects | Too broad. In non-strict compact-delay handling, the **memory-conflict** check requires a subject match (`commitment/authorize.py:432`). `relative_time.py` deliberately skips unrelated memory events. Timing parsing and commitment construction still occur, and strict/text resolution has additional logic. This alone does not prove a scheduling defect. |
| Alias matching has limited coverage | Supported by the rule-based matching in `commitment/relative_time.py`; no comprehensive linguistic coverage was established. |
| Prompt-fence empirical claims | Consistent with the recorded discussion in `docs/INJECTION.md` (direct override, social wording, cap effects and older Compound results). Benchmark outcomes were not independently rerun; do not present them as fresh measurements. |
| `KAZMA_DATA_DIR` does not isolate Postgres ConfigStore | Confirmed. `config_store.py:761` selects backend independently; `:828` documents the warning-only behavior. Relevant four tests pass. No live Postgres mutation was performed. |
| Old SQLite settings file survives backend switch | Confirmed implementation/notice in `config_store.py:884`. Reported historical provider-key differences were not remeasured. |
| Restore drill checks readability; restore rehearsal is manual | Confirmed in repository scope. `backup/restore_drill.py:512` renders the archive to a sink; `scripts/restore_rehearsal.py` performs restore. No scheduler reference to that script was found. External operator schedules were not inspected. |
| Postgres CI is not full parity | Confirmed. `.github/workflows/ci.yml:190` selects 17 named test files, rather than the whole suite. “A handful” understates the actual list. |
| Linux git-test chunk hang remains | Historical/unverified in this review. Recorded in `KNOWN_GAPS.md:444`; a Windows source review cannot prove a Linux race persists, that retries pass, or that today's CI is green. |
| `main` has no branch protection | Freshly confirmed: `gh api repos/Mubder/kazma/branches/main` returned `protected: false`. Workflow runs on main pushes and PRs; job failure still fails the job. No branch protection means it is not a required branch-protection merge gate. |
| Packaged check does not install and boot a wheel | Confirmed for the cited asset check. `tests/test_turn_assets_ship.py` inspects source/configuration and cache versioning. CI uses editable installation. Release wheel building does not itself prove installed-wheel startup. |
| External document security review not run | Not independently verifiable from this checkout. Absence of a review artifact is not proof that no external review occurred. |
| 229/272 environment variables undocumented | Historical count, not independently certified. Documentation repeats it; current counts require a defined scanner and documentation-coverage criteria. |
| 104 import bindings and 64 short-sleep assertions | Historical exposure counts, not independently recounted. They are not counts of proven defects, as `KNOWN_GAPS.md:810` itself explains. |
| Majlis only partly live; header stale | Confirmed with nuance. `majlis_runtime.py:14` imports `MajlisProtocol` and calls `process_input` for greetings/farewells. Phase transitions do run there, but ordinary conversation bypasses that path. `majlis.py`'s “NOT imported” claim is stale. |
| Dialect pipeline echoes, no second model call | Confirmed in `router.py` and tokenizer use in `routing_engine.py:101`. Naming correction: `DialectRouter.route()` calls pipeline `execute()`; the router itself has no `execute()` method. |
| Division checks do nothing unless `KAZMA_DIVISION` is set | Incorrect as written. `division_runtime.py:113` also supports `KAZMA_DIVISION_ENFORCE`; `:122` falls back to ConfigStore `agent.division`. Default unconfigured posture remains unenforced. **Correction 2026-09-25:** `KAZMA_DIVISION_ENFORCE` never reached the check — `check_division_tool` reads only the division context; the switch changed what Settings reported and nothing else, and has been removed. The original claim holds once `agent.division` is added to it. |
| MCP resources/prompts/sampling/vendor adapters marked done | Explicitly unverified by the supplied audit and not certified here. A plan checkbox is not implementation evidence. |
| Time travel import fallback is not proof of missing feature | Confirmed. Gateway `agent_handler/graph.py:2255` invokes replay and updates graph state. Note that `/replay` currently restores messages at `:2262`, whereas `/fork` writes the loaded state; avoid interpreting this as proof of complete-state rewind. |
| Narrow tests can miss sibling paths | Demonstrated by this MCP reproduction and the passing existing MCP tests. The count of seven historical defects was not independently re-audited. |
| `hitl_gates.db` is “one process” | Incorrect literal description. `safety/hitl_gates.py:218` uses SQLite connections, and the bus bridge explicitly supports cross-process interaction through the same file. Lack of a distributed/multi-host decision backend is a valid concern. |
| Watcher heartbeat proves process presence, not human attendance | Confirmed from `swarm/safety.py:354` and the registry bridge. Denial/timeout remains the default when approval is not received. |
| `fast_test.py` labels ordinary exit 1 as crashed/timed out | False on this commit. `scripts/fast_test.py:325` only retries exit 0/1 when no tally parsed; `:355` distinguishes timeout, crash and missing tally. Parsed ordinary failures are not classified as crashes. |
| Future Test Connection handlers could mutate config | A design/testing concern, not a demonstrated current defect. Existing secret-preservation guards do not establish general diagnostic purity; proving absence of every safeguard would require a wider dedicated audit. |
| Large-file read cache can miss same-stamp same-size rewrite | Confirmed residual in `tools/file_read.py:164`: content hashing stops above 1,048,576 bytes. This is a collision condition, not a claim that ordinary writes always serve stale content. |
| WebSocket and HTTP share settled-gate check | Confirmed call sites: `routes/ws_chat.py:2226`, `routes_direct/misc.py:763`; empty-ID fallback at `hitl_gate_bridge.py:71`. |
| Public bind refuses `KAZMA_DEV_WS_BYPASS` | Confirmed guard in `security/boot_guard.py:97`. Did not start a server to exercise it. |

## Executed checks

```text
.venv/Scripts/python.exe -m pytest tests/test_mcp_hitl.py tests/test_data_dir_does_not_isolate_postgres.py tests/test_turn_assets_ship.py -q --timeout=60
33 passed in 3.92s
```

The harmless interpreter experiment is separate from those passing tests. It exposes the missing graph-context case in the existing suite. No security fix has been applied by this verification.
