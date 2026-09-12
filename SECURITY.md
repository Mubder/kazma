# Kazma Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.10.x  | :white_check_mark: |
| 0.9.x   | :white_check_mark: (critical security fixes only) |
| < 0.9   | :x:                |

Only the latest **0.10.x** release receives feature work and routine security
patches. **0.9.x** may receive critical fixes for a limited window. Upgrade to
the latest supported version when possible.

## Threat Model (operator)

Kazma is designed as a **single-operator trusted-host agent** by default:

| Profile | Ready when |
|---------|------------|
| Localhost + strong `KAZMA_SECRET` + HITL on | Recommended daily use |
| Docker / LAN with `KAZMA_PRODUCTION=1` | After production hardening (see `.env.example`) |
| Public multi-user SaaS | **Not** the default threat model — needs IdP, opaque sessions, real tenancy |

**Production flags (summary):**

- `KAZMA_HOST=127.0.0.1` default; non-loopback requires a strong secret
- `KAZMA_TRUST_LAN=0` (default) — no LAN auto-cookie
- `KAZMA_PRODUCTION=1` — Docker code_exec, YOLO off (override: `KAZMA_ALLOW_YOLO=1`), workspace root required
- `KAZMA_VAULT_KEY` — encrypt secrets at rest
- Never use the historical default secret `kazma-local-dev-secret`

**Multi-replica / multi-user (Phase 4):**

- Shared state: `KAZMA_DATABASE_URL=postgresql://…` + `pip install -e ".[postgres]"` — never share SQLite across replicas
- Roles: viewer / operator / admin (`platform_rbac`); OIDC via `KAZMA_OIDC_*`
- DR: `docs/docs/ops/disaster-recovery.md` + `scripts/backup_kazma.py` / `restore_kazma.py`

## Reporting a Vulnerability

We take security seriously. If you discover a vulnerability in Kazma,
**please report it privately**. Do **not** open a public GitHub issue for
security vulnerabilities.

### Preferred channels

| Channel | Contact | Notes |
| ------- | ------- | ----- |
| Email | **admin@kazma.ai** | Primary channel for all vulnerability reports |
| GitHub | [Private vulnerability reporting](https://github.com/Mubder/kazma/security/advisories/new) | Use when enabled on the repository |

Machine-readable contact policy (RFC 9116):

- Served by the app at `/.well-known/security.txt` and `/security.txt`
- Source of truth in the repo: [`.well-known/security.txt`](.well-known/security.txt)
- Canonical public URL (when the site is live): `https://kazma.ai/.well-known/security.txt`

**Encryption:** we do **not** currently publish a PGP key. Send reports over
email or GitHub private reporting. If you need encrypted mail, say so in your
initial contact and we will coordinate out of band.

### Response targets (best effort)

These are **targets**, not contractual SLAs. Small open-source projects may
miss them under load; we still aim to keep you informed.

| Milestone | Target |
| --------- | ------ |
| Acknowledgment | 48 hours |
| Initial assessment | 7 days |
| Severity determination | 14 days |
| Patch (when fix is in our control) | 30 days when practical |
| Coordinated public disclosure | After a fix is available, or ~90 days if no fix is ready — coordinated with the reporter |

### What to include

- **Description** — what is wrong and why it matters
- **Reproduction** — steps, config, commands, or API calls
- **Impact** — who/what is affected; exploit difficulty
- **Affected version** — tag, commit, or package version
- **Environment** — OS, Python version, Docker vs bare metal
- **Suggested fix** (optional)

## Scope

### In scope

| Component | Description |
| --------- | ----------- |
| Core engine | Task scheduling, session management, LLM dispatch |
| MCP client | Model Context Protocol client connections |
| Skill manifests | SKILL.md parsing, validation, and loading |
| Delegation protocol | Agent-to-agent communication and task handoff |
| RBAC / permissions | Role-based access control, tenant isolation |
| Configuration system | Config loading, secrets handling, provider keys |
| CLI interface | Command-line input handling, injection classes |
| Data persistence | Session DB, memory stores, SQLite/Postgres |
| Network layer | API endpoints, webhook handlers, gateway sockets |
| Plugin / skill system | Loading, lifecycle, permission boundaries |
| Document Intelligence | Intake, sandbox, storage, indexing (see docs) |

### Out of scope

- **Third-party dependencies** — report to upstream; we will help coordinate when relevant
- **Social engineering** of maintainers or users outside the software
- **Volume-based DoS** against hosted instances (resource-exhaustion *bugs in code* remain in scope)
- **Physical security** of the deployment host
- **Issues solely in upstream LLM providers** (OpenAI, Anthropic, etc.)

## No bug bounty (at this time)

Kazma does **not** currently operate a paid bug bounty program. There are **no**
guaranteed cash payouts, tiers, or SLAs for compensation.

We still welcome responsible reports. At our discretion we may:

- Credit you in release notes or GitHub Security Advisories (with your consent)
- Offer non-cash thanks (swag, public thanks) when practical

Do **not** treat any historical draft language, sample YAML, or third-party
summaries as an active bounty. The authoritative statement is this file and
`bug_bounty.enabled: false` in [`kazma-security.yaml`](kazma-security.yaml).

## Security update process

```
Report → Acknowledge → Investigate → Patch → Advisory → Notify
```

1. **Report** — private channel above
2. **Acknowledge** — tracking ID / confirmation when we can
3. **Investigate** — severity and impact
4. **Patch** — fix developed and reviewed (maintainer review; dual review when available)
5. **Advisory ID** — internal IDs use `KAZMA-ADV-YYYY-…`. A real **CVE** is requested
   only when appropriate (e.g. via GitHub Security Advisories / CVE assignment).
   Generated IDs are **not** MITRE CVEs.
6. **Notify** — GitHub Security Advisories and/or release notes when a fix ships

Critical fixes may be backported to older supported versions at our discretion.

## Prompt injection — measured, not asserted

Untrusted text (web, tool results, MCP output, skill bodies) is wrapped in a
labelled data fence before it reaches a model. That claim is scored against an
adversarial corpus, with no model in the loop:

```bash
python scripts/injection_report.py
```

**56/56 containment** — no corpus payload can forge the fence's delimiters and
place text outside it, or smuggle another vendor's role-control tokens
(`<|im_start|>`, `[INST]`, `<<SYS>>`, Gemma turns) through it to the model's
tokenizer. A hard gate; any escape fails the build.

**13/13 persistence denylist** — every payload whose purpose is to plant a
standing directive in a future system prompt is refused. Balanced by 21 control
cases: real summaries that sit one word from a deny pattern and must stay
storable, because a false positive here silently makes the agent forget. See
**[docs/INJECTION.md](docs/INJECTION.md)**.

Measured against live models as well (3 runs each, temperature 0): injection
compliance on `groq/compound-mini` drops **42% -> 8%** with the fence on, on
`ollama/qwen2.5:7b` **100% -> 58%**, and on `ollama/mistral:7b` **42% -> 8%** —
three model families, three double-digit deltas. `deepseek-flash` complied with nothing
in either condition, so the fence's effect on it is unmeasurable rather than
proven — the doc reports that as a non-result instead of a win, along with the
payloads that still get through and which of the current defenses have been
measured against a model versus merely written down.

## Known gaps

Weaknesses that are open, unproven, or unfinished are listed in
**[docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md)**, with the evidence for each. A
security claim is worth what its author will say against it, so the gaps are
published beside the numbers rather than left for a reader to find.

## Verifying a release (supply chain)

Every release artifact is signed with **Sigstore** (keyless — there is no
private key to leak or rotate), carries **SLSA build provenance**, and ships a
**CycloneDX 1.6 SBOM** generated from the committed lockfile.

```bash
gh attestation verify kazma-<version>-py3-none-any.whl --repo Mubder/kazma
```

Full instructions, including signature verification and what these guarantees
do *not* cover, are in **[docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md)**.

Two third-party UI assets are vendored rather than fetched at runtime
(CodeMirror, MIT; IBM Plex Sans Arabic, OFL) so the interface renders with no
outbound network. A test fails the build if a template starts loading remote
script or styles again.

## Hardening checklist (operator recommendations)

These are **recommended** controls for deployments — not an assertion that every
host automatically enforces all of them:

- [ ] Secrets never logged or committed; vault + strong `KAZMA_SECRET`
- [ ] Input validation on external surfaces (API, CLI, skill manifests)
- [ ] HITL / RBAC on for multi-user or network-exposed hosts
- [ ] Dependency audits (OSV / GitHub Advisories) on a regular cadence
- [ ] Least-privilege process user; skill/MCP permissions reviewed
- [ ] TLS for any non-localhost exposure
- [ ] Audit trail enabled for privileged operations where configured
- [ ] Periodic review of `kazma-security.yaml` / `kazma-permissions.yaml` posture

See also: [Security & Safety](docs/docs/guide/security-and-safety.md),
[Hardening guide](docs/docs/security/hardening-guide.md).

## Contact

- **Security email:** admin@kazma.ai
- **GitHub:** [github.com/Mubder/kazma](https://github.com/Mubder/kazma) (non-sensitive issues only)
- **Policy (docs mirror):** [Vulnerability reporting](docs/docs/security/vulnerability-reporting.md)

---

*Policy reviewed August 2026. No paid bounty. Re-review when program posture changes.*
