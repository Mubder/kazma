# Kazma Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

Only the latest release in the 0.1.x series receives security patches. Versions
prior to 0.1 are end-of-life and will not receive updates. Users are strongly
encouraged to upgrade to the latest supported version.

## Reporting a Vulnerability

The Kazma team takes the security of our software seriously. If you discover a
security vulnerability, please report it responsibly through the channels below.
**Do not open a public GitHub issue for security vulnerabilities.**

### Encrypted Disclosure Channels

| Channel  | Contact                           | Use Case                        |
| -------- | --------------------------------- | ------------------------------- |
| Email    | admin@kazma.ai                | All vulnerabilities             |
| Signal   | +1-XXX-XXX-XXXX                  | Critical / actively exploited   |

- **PGP Key**: A PGP key for encrypting email reports is available at
  `https://kazma.ai/.well-known/security.txt`. Always encrypt sensitive
  details.
- **Signal** is preferred for critical vulnerabilities that are actively being
  exploited or have immediate impact on production deployments.

### Response Timeline

| Milestone             | Target     |
| --------------------- | ---------- |
| Acknowledgment        | 48 hours   |
| Initial assessment    | 7 days     |
| Severity determination| 14 days    |
| Patch release         | 30 days    |
| Public disclosure     | 90 days    |

We commit to acknowledging your report within **48 hours** and providing an
initial assessment within **7 days**. If a patch takes longer, we will keep you
informed of our progress.

## What to Include in a Report

To help us triage and respond quickly, please include as much of the following
as possible:

- **Description**: A clear summary of the vulnerability and its potential impact.
- **Reproduction steps**: Step-by-step instructions to reproduce the issue, including
  any relevant configuration, commands, or API calls.
- **Impact assessment**: Your evaluation of the severity — who is affected, what data
  or systems are at risk, and whether exploitation is trivial or requires special
  conditions.
- **Suggested fix** (optional): If you have a recommendation for remediation, please
  include it.
- **Affected version**: The version(s) of Kazma where you observed the issue.
- **Environment**: Operating system, Python version, deployment method (Docker,
  bare metal, cloud).

## Scope

### In Scope

The following components and attack surfaces are covered by this policy:

| Component                  | Description                                        |
| -------------------------- | -------------------------------------------------- |
| Core engine                | Task scheduling, session management, LLM dispatch  |
| MCP client                 | Model Context Protocol client connections           |
| Skill manifests            | SKILL.md parsing, validation, and loading          |
| Delegation protocol        | Agent-to-agent communication and task handoff      |
| RBAC / permissions         | Role-based access control, tenant isolation         |
| Configuration system       | Config loading, secrets handling, provider keys     |
| CLI interface              | Command-line input handling, shell injection        |
| Data persistence           | Session DB, memory stores, SQLite operations        |
| Network layer              | API endpoints, webhook handlers, gateway sockets    |
| Plugin system              | Plugin loading, lifecycle, sandboxing               |

### Out of Scope

- **Third-party dependencies**: Vulnerabilities in upstream packages should be
  reported to the respective maintainers. We will assist with coordination if
  needed.
- **Social engineering**: Attacks targeting Kazma maintainers, contributors, or
  users outside the software itself.
- **Denial of service**: Volume-based DoS attacks against hosted instances (though
  resource exhaustion bugs in the code are in scope).
- **Physical security**: Attacks requiring physical access to the deployment
  environment.
- **Recently disclosed zero-days in LLM providers**: Issues originating entirely
  within upstream model providers (OpenAI, Anthropic, etc.) are not in scope for
  our bounty, though we will help coordinate disclosure.

## Bug Bounty Program

Kazma operates a bug bounty program to reward responsible security researchers.
Bounties are paid in USD via the method of your choice after validation.

| Severity | Bounty Range | Criteria                                                        |
| -------- | ------------ | --------------------------------------------------------------- |
| Critical | $500–$2,000  | Remote code execution, full RBAC bypass, data exfiltration     |
| High     | $200–$500    | Privilege escalation, authentication bypass, significant info leak |
| Medium   | $50–$200     | Limited information disclosure, CSRF, stored XSS                |
| Low      | Hall of Fame | Minor issues, defense-in-depth improvements, best-practice gaps |

### Bounty Rules

1. Reports must follow the disclosure process above (encrypted channel, no
   public disclosure before patch).
2. One bounty per unique vulnerability. Duplicate reports go to the first
   reporter.
3. Severity is determined at our sole discretion based on the CVSS framework
   and real-world impact.
4. Contributors who report vulnerabilities are eligible for the Hall of Fame
   regardless of severity tier.
5. Bounties are paid within 30 days of patch release.

## Security Update Process

All security fixes follow this pipeline:

```
Report → Acknowledge → Patch → CVE → Advisory → Notify
```

1. **Report**: Researcher submits vulnerability via encrypted channel.
2. **Acknowledge**: Team confirms receipt within 48 hours and assigns a tracking
   ID.
3. **Patch**: Fix is developed, reviewed by at least two maintainers, and merged
   to the release branch.
4. **CVE**: A CVE identifier is requested (if applicable) and associated with
   the fix.
5. **Advisory**: A security advisory is published on GitHub and at
   `https://kazma.ai/security`.
6. **Notify**: Affected users are notified via the mailing list, release notes,
   and (for critical issues) direct communication.

Patches for critical vulnerabilities may be backported to older supported versions
at the team's discretion.

## Security Hardening Checklist

The following hardening measures are applied to all Kazma deployments:

- [ ] **Secrets are never logged or committed.** API keys, tokens, and PGP keys
  are stored outside version control and redacted from all log outputs.
- [ ] **Input validation on all external surfaces.** CLI arguments, API payloads,
  skill manifests, and configuration files are validated against schemas before
  processing.
- [ ] **RBAC enforced at every access point.** Tenant isolation and role-based
  permissions are checked before any data or action is exposed.
- [ ] **Dependencies audited regularly.** Automated scanning via OSV, GitHub
  Advisories, and NVD runs every 24 hours (see `kazma-security.yaml`).
- [ ] **Least-privilege execution.** Agents run with minimal required
  permissions; skill sandboxes restrict filesystem and network access.
- [ ] **Encrypted transport.** All API and gateway communication uses TLS 1.2+.
  PGP is used for sensitive disclosure channels.
- [ ] **Audit trail for privileged operations.** All RBAC changes, config
  mutations, and security-relevant actions are logged immutably.
- [ ] **Regular security reviews.** At minimum quarterly reviews of the security
  configuration, dependency landscape, and incident response process.

## Contact

For questions about this security policy, contact the Kazma security team:

- **Email**: admin@kazma.ai
- **GitHub**: [github.com/kazma-dev/kazma](https://github.com/kazma-dev/kazma)
  (for non-sensitive inquiries only)

---

*This policy is effective as of June 2026 and will be reviewed quarterly.*
