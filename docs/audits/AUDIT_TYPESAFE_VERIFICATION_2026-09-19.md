# TypeSafe Full-Surface Audit — verification record

**Date:** 2026-09-19
**Subject:** `KAZMA_TYPESAFE_FULL_AUDIT.md` (TypeSafe System One, `jev-1.13.0`, 1338 windows)
**Method:** every flagged window read against the real code; claims tested by running them, not by reading alone.

The audit report is `KAZMA_TYPESAFE_FULL_AUDIT.md`, alongside this file. It
lived only in an operator's clone until 2026-09-19, which is why earlier
revisions of this paragraph said it was not in the repository. This file
records what its flags turned out to be, so the next person does not
re-derive it.

---

## Outcome

**43 flagged windows → 0 exploitable findings.** Two code changes were made,
neither of them from the flagged list:

| change | origin | commit |
|---|---|---|
| Boot posture guard: a kill switch plus a public bind refuses to start | **R2**, the report's hand-written residual-risks table | `fix(security): a kill switch plus a public bind…` |
| `create_session(role=...)` is required — no inherited admin | hand-read of the 27 un-triaged flags | `fix(security): minting a web session must state its authority` |

The single most useful thing in a 1338-window sweep came from the **human
paragraphs in section 7**, not from the model verdicts in section 3.

---

## The report's residual risks (section 7)

| id | claim | verdict |
|---|---|---|
| **R1** | Flag injection into `pip`/`uv` from agent-supplied package names (`runtime_manager.py`, `system/installer.py`) | **Already fixed, since 2026-09-12.** All three entry points allowlist against a frozenset of exact names: `asynchronous_install_package`, `asynchronous_install_extra` (raises), `trigger_package_promotion`. A `-`-prefixed value is not in the set. Pinned by `test_system_install_allowlist.py` and `test_alert_install_button.py`, the latter asserting *every* installing function consults the list. The proposed fix (reject `-`, validate PEP 508) would be **weaker** than what exists. The report's section 6 read these files and reported "packages are appended verbatim" — the allowlist is at the top of the same function. |
| **R2** | Auth is deployment-dependent | **Real, and fixed.** See below. |
| **R3** | `needs_context ≈ 0.7`; verdicts unresolved by design | **Correct, and the most important sentence in the report.** |
| **R4** | 43 flagged rows are an un-triaged backlog | **Correct.** Section 6 read 16; the other 27 are dispositioned below. |
| **R5** | A vendored LibreOffice tree sits at `core/` | **Not this repository.** `ls core/` is empty, `git ls-files core/` is 0, `git log --all -- core/` has no history. The scope statement — including its exclusion list — describes a tree that has never existed here. |

### R2 in detail (the one real finding)

Two guards existed and did not compose:

- `serve.py` / `kazma serve` refuse a non-loopback bind without `KAZMA_SECRET` — they ask *"is there a secret?"*
- `kazma_ui.auth` refuses `KAZMA_AUTH_DISABLED` / `KAZMA_DEMO_MODE` when `KAZMA_PRODUCTION` is set — it asks *"is this labelled production?"*

Neither asks **"are you exposed?"**. Reproduced against the real
`_bootstrap_bind_and_secret()`:

```
KAZMA_HOST=0.0.0.0  KAZMA_SECRET=<strong>  KAZMA_AUTH_DISABLED=1
-> startup guard PASSED, binding 0.0.0.0   # every /api/* open
```

The secret satisfies the boot check and the kill switch makes the secret
irrelevant. `KAZMA_PRODUCTION` is opt-in, so a VPS, a LAN box or a tunnel
inherits nothing from the second guard.

Fixed by `kazma_core.security.boot_guard` — one shared check both entry
points call, because the bind/secret logic was already copy-pasted into both.
`KAZMA_AUTH_DISABLED` + public bind now refuses to start; `KAZMA_DEMO_MODE` +
public bind is allowed and loud, because that is what demo mode is for.
Locked by `tests/test_boot_exposure_guard.py`.

---

## The 27 un-triaged flags

| disposition | count |
|---|---|
| Worth changing | **1** (`create_session` default role) |
| By design, documented, bounded | 4 |
| False positive | 22 |

**Worth changing — `security/web_sessions.py::create_session`.** `role or
"admin"`, and `auth.py` called it with no role. Reached only after
loopback-or-secret is proven, so it was correct for a single operator — but
the payload already carries `user_id` and `tenant_id`. Fixed: `role` is
required, every caller states it, no behaviour changed.

**By design, bounded:**

- `sessions/directory.py::sender_may_take_over` — fails open for owner-less threads (documented web/legacy compatibility). The actual attack, hijacking another user's season, is blocked by the owner check.
- `hub/loader.py::SkillLoader` — `exec_module` with fail-closed checksum/HMAC verification, which refuses when a signature is present and `KAZMA_SECRET` is not. **Unsigned skills load unverified**; the trust boundary is the HITL approval at install.
- `database_client::execute_db_query` — agent-supplied SQL is the tool's purpose; SELECT/WITH only, and it strips leading comments *before* the check (the non-obvious part).
- `backup/neo4j_backup.py::_driver` — empty-password default; fails at the database, bypasses nothing in Kazma.

**The false positives fall into three shapes**, and the first is systematic:

1. **Severity tracks subject matter, not risk.** Four confirmed cases where the flagged code *is the mitigation*: `llm_gateway::resolve_generic_egress` (flagged `info_exposure` — it exists to refuse forwarding a vendor key to a non-local gateway), `documents/resources::validate_restricted_render_resources` (a deny-by-default validator), `chat_attachments` (regex id, `resolve()`, parent check, size caps), and the report's own `safety/task_grants` (#4). The report noticed this once and called it an artifact. It is not an artifact — it is the method's bias.
2. **argv-list subprocess / filesystem-derived arguments** — `pg_bridge::_resolve_tool`, `documents/sandbox::run_isolated_subprocess`, `gateway/routers/git::_run_git` (literal argv, called via `asyncio.to_thread`), the five flagged scripts.
3. **Bounded or deliberately degrading helpers** — `mutation_worker::_redact` (≤100 terms / 10k chars), `v2_health::_safe_count`, `e2b::_run_sync`, `pollinations`, `intent/classify`, `mcp_server`, `agent_runner`, and the two test harnesses.

### The detail worth keeping

`tools/file_apply_patch.py::_run_pytest` appends `test_files` verbatim to a
subprocess — **the exact flag-injection shape R1 claimed for pip**. It is
safe, but for a different reason: the names come from `Path.resolve().glob()`,
so they are absolute and cannot begin with `-`.

So the audit named a real pattern, aimed it at the two places where an
allowlist already kills it, and did not flag the third place where the
pattern genuinely appears and is mitigated by something else entirely.

---

## What this says about the method

- The flagged list produced **nothing actionable**; the hand-written residual-risks table produced the only real finding. Budget the humans, not the windows.
- **43% of the sweep (572/1338 windows) audited `tests/`**, which the report itself names as its dominant false-positive mode. That is close to pure cost.
- `needs_context ≈ 0.7` is the honest headline and belongs in the abstract, not a one-line aside. A method that cannot settle 70% of its own questions is a hypothesis generator.
- The questions that mattered here were **runtime, not syntax**. "Is `x_post` actually gated?" was settled the same day by instrumenting the executor and driving the real path — twenty lines, a definite answer — while the static sweep gave `x_post` no flag at all.

R3 already points the right way: call-graph-aware auditing. The cheaper half
of that is to keep writing runtime probes for the specific properties that
matter, and to stop scoring modules on what they are about.
