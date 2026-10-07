# Supplied audit follow-up

This follows the external report reviewed against
`ce61e9ed14f40cc269ec0e85665c53894964a876`. Its AUD-001–027 IDs belong to
that report, not the binding industrial audit's finding series. Input SHA-256:
`A454A6A48869A25755827CAFA890F30ADD9ED9E7B5B00BBED0B70F6FDE05CB22`.
Source fixes, regression qualification and deployment are distinct milestones.
This document records implementation disposition; full-suite, CI and live
release evidence must be completed before marking the release qualified.

PR #103 completed the remaining source batches at
`2da1ab79c3326bc9538111fcc27fc4d6f2526b63`: the default local full runner
passed 13,428 tests (59 skipped, 2 deselected), and all 23 CI checks passed,
including 13,358 Linux tests. Main merged as
`10b7fd3ff792e781aa429f9539bab874e57f8c0e`. The guarded live update is healthy
at `6ed6cd52`; English and Arabic chat smoke tests returned 42 and ٥٦ on
the existing deepseek-flash profile. This is transport verification, not
human-reviewed semantic qualification. The live navigation checks also
reproduced a pre-existing catalog-refresh warning: JSON parsing included
bootstrap JavaScript. A follow-up separates inert catalog data from executable
helpers and adds a browser regression for the warning.

Batch 1 merged as PR #101. Batch 2 merged as PR #102,
`ce98863319d3f7a811e2f0f347dbb18db1244934`: local default full runner
**13,341 passed, 59 skipped, 2 deselected**, exit 0; all **23** CI checks
passed at `fe97ecf2fb8fe53981225296564aaeabf8c164c9`. The refreshed installed
dependency scan covered 266 distributions with no unreviewed advisories and
four already documented reachability reviews.

## Disposition

| Finding | Implementation or retained contract | Evidence |
|---|---|---|
| AUD-001 | Text/button approval authorization unified; durable gate actor identity survives session cache expiry/restart. | PR #101; gateway approval and identity tests. |
| AUD-002 | MCP aliases derive canonical risk tiers and secret requirements; lower registry HITL remains wired. | PR #102; MCP alias/auth regressions. |
| AUD-003 | Attached short values enter path checks; tar clusters/indirection/extraction refuse. The report's glued Git example was rejected by installed Git. | `test_shell_glued_options.py`. |
| AUD-004 | Socket principal/tenant and exclusive ownership precede history/checkpoint/replay reads; unknown sockets cannot claim sessions. | PR #102; tenant/thread socket regressions. |
| AUD-005 | Authorization protects the early YOLO/capacity path, preserving production prohibition. | PR #101; capacity authorization tests. |
| AUD-006 | Read Git verbs/options/paths constrained; output-file writes use the gated path. | PR #101; safe Git policy tests. |
| AUD-007 | Telegram downloads enforce byte caps while streaming; misleading headers and compressed bodies cannot bypass them. | `test_audit_deployment_io.py`, voice/media compatibility tests. |
| AUD-008 | Unresolved binaries refuse in every mode; resolution uses explicit absolute PATH directories and rejects escaping symlinks/Windows CWD lookup. | `test_shell_glued_options.py`. |
| AUD-009 | Registered tenant/table capabilities, restricted parsed reads and constrained DB roles replace raw remote DSNs. SQLite opens read-only. | PR #102; real disposable PostgreSQL, MySQL and Mongo qualification. |
| AUD-010 | Preserve the explicit single-operator host policy; production/multi-user isolation requires Docker. Host budgets are not a security sandbox. | `test_code_exec.py`; limits documented in KNOWN_GAPS. |
| AUD-011 | Production folder selection/creation/switching share root confinement, including stored rows and autocomplete. | `test_audit_deployment_io.py`. |
| AUD-012 | Shared shell/native/hook/patch/MCP process budgets; descendant cleanup, bounded output and cancellation ownership. | `test_process_budgets.py`, hook/native/MCP compatibility tests. |
| AUD-013 | Attachment fetches validate public DNS and every redirect, pin addresses, check peers and confine Slack authorization. | `test_outbound_attachment_policy.py`. Latent risk; no live exploit claimed. |
| AUD-014 | Preserve three explicit 410 contracts, V2 search compatibility and authorized tenant-scoped bi-temporal clear. | API reference and route/tenant tests. |
| AUD-015 | Restore already has a documented module CLI. Document the existing ordered password-rotation API and persistence callback. | Disaster recovery runbook; existing restic/restore tests. |
| AUD-016 | Preserve declared public/operator endpoints; frontend absence is not evidence of an orphan API. | `test_api_route_callers.py`. |
| AUD-017 | Retain legacy migration; add an explicit offline restored-generation CLI and accurate idempotent insert counts. | `test_legacy_backfill_cli.py`: Arabic/English text, tenant, timestamp and embedding preservation; inherited live paths excluded. |
| AUD-018 | Local empty/cache scaffolds are not a demonstrated product vulnerability. No user cache or runtime data deleted. | Scope decision; generated files remain ignored. |
| AUD-019 | Central root policy reduces selection drift; the reported symlink bypass was unsupported because the caller resolves entries. | Root-policy tests; binding ladder retained. |
| AUD-020 | Dependency scan found fixable advisories; upgrade SDK/multidict/Werkzeug and CI setuptools rather than waive them. | PR #102 lock/floor and CI security checks. Existing documented reachability reviews remain separate. |
| AUD-021 | Require explicit Postgres/Neo4j passwords and loopback host ports in templates. No live database exposure claimed. | `test_database_compose_defaults.py` executes Compose validation. |
| AUD-022 | GitHub refresh credentials use a scoped environment header and result redaction, not argv. Local process-environment visibility remains a trust boundary. | `test_audit_deployment_io.py`. |
| AUD-023 | Validate configured rclone remote/path syntax and use the option terminator. | Deployment/backup compatibility tests. |
| AUD-024 | Provider probes retain real credential/model resolution but exclude response bodies and sensitive request URLs from failures. | Provider-probe conformance and deployment tests. |
| AUD-025 | Callback/OAuth failures and first-run output avoid raw credential disclosure. | Deployment/OAuth tests. |
| AUD-026 | Swarm events use DOM text nodes with a separate controlled worker span; no double escaping. | `tests/js/test_swarm_event_text.js`. No original live XSS demonstrated. |
| AUD-027 | Read Git policy disables execution helpers; ordinary clone does not copy source local config. | PR #101; synthetic Git tests. |

The additional LIKE observation is a correctness issue, not SQL injection.
Affected code-index, Hub and memory fallbacks now use literal escaping with
`ESCAPE '!'`; parameterization, tenant predicates and limits remain intact.
`test_audit_literal_search.py` exercises the real queries and cross-tenant decoys.

## Operational limits and qualification

Windows jobs start suspended before assignment, bound the aggregate tree to
2 GiB/32 processes and kill descendants on close. The correct JobMemory flag is
`0x200` ([Microsoft Job Object flags](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information));
the older local-code helper's flag and 64-bit handle signatures are corrected.
POSIX applies per-process address-space/CPU limits and group cleanup; deliberate
session detachment requires stronger container/cgroup isolation. Shell capture
is 30 KB; shared native/hook/patch capture is 256 KiB. MCP protocol buffers have
their configured finite limits and remain long-lived, with request deadlines.
The separate Settings diagnostic client retains its one-off protocol and
stderr diagnostics while using the same contained launcher and a finite
16 MiB protocol cap. Launch, initialization and tool-discovery cancellation
tests prove that unregistered children are cleaned up as well.
Stdio deadlines use `asyncio.timeout` so Python 3.11's `wait_for` completion
race cannot swallow caller cancellation. A deterministic regression fails
against the old deadline path and passes with the correction; the MCP/budget
subset passes on both local Python 3.11 and 3.12.

Tests use temporary synthetic data and disposable local services. No cloud
resources or live database migrations are needed for this audit. Human-reviewed
bilingual semantic accuracy remains postponed; text preservation is not an
accuracy qualification. Deployment follows reviewed source, required full
regression/CI checks and the guarded idle reload of the existing installation.
