# Kazma security policy

[العربية](SECURITY.ar.md) · On the website: [kazma.ai/docs/security/security-policy](https://kazma.ai/docs/security/security-policy/)

Kazma is software you run for yourself. It can run code, write files and send
messages on your machine, so this page says what protects you, how strong each
protection is, and where it stops. Where a protection has been measured, the
number is here; where it has not, the page says so. It also says how to report
a weakness, and what happens after you do.

## 1. Report it privately

Use GitHub's private form first:
**[Report a vulnerability](https://github.com/Mubder/kazma/security/advisories/new)**.
Only you and the maintainers see what you write there. If you cannot use
GitHub, write to **admin@kazma.ai**. Never open a public issue, discussion or
pull request about a security problem.

There is no PGP key. If you need an encrypted channel, say so in your first
message and we will arrange one. The same contacts are written for machines
(RFC 9116) in [`.well-known/security.txt`](.well-known/security.txt), which
every install also serves at `/.well-known/security.txt`.

A useful report holds:

- what is wrong, and how serious you think it is;
- the file and lines, if you have them;
- the version Kazma prints (`kazma --version`, for example `0.21.2+g4d37b2c`:
  the release, then the commit), your operating system and Python version, and
  whether Kazma runs in Docker;
- steps that reproduce it on the latest release or on `main`;
- which row of [section 3](#3-what-holds-and-what-does-not) it gets past;
- which of the [switches that weaken a security default](https://kazma.ai/docs/environment-variables/#switches-that-weaken-a-security-default)
  are set, if any.

Please read sections 3 and 6 before you write. A report outside them is closed
privately, with the reason, and is welcome again as an ordinary issue. There
is no paid bounty.

## 2. Who Kazma protects

Each install has one operator: the person who runs the server, holds its keys
and edits its settings. Kazma protects that operator from the agent: from its
mistakes, and from the web pages, documents and messages it reads. It does not
protect an install from its own operator, who can switch any safeguard off.
Until Spaces is on, anyone the operator lets in (a web login, or an account
on a chat app's allowlist) acts with the operator's authority. Kazma is not a
hosted service; it is provided as is, under the [MIT license](LICENSE).

**Spaces**, which will let an operator share an install with a few people they
know, each in a space of their own, is being built and is not in any release
yet. Once it is on, it is a boundary between the people who use an install,
not against its operator, who can read the disk, the databases and the
backups. It has not been reviewed independently.

The whole account, mechanism by mechanism, is the
[threat model](docs/THREAT_MODEL.md).

## 3. What holds, and what does not

Approval is the protection that matters most, and it is a human one, not a
technical one. Every other row narrows what an approved action can do, or what
reaches the model in the first place.

| Protection | What it stops | What it does not stop |
|---|---|---|
| **Approval** | A dangerous tool running without you seeing it. The always-ask tools (posting to X, the vault's tools, widening what the file tools may reach) ask every time, even with `KAZMA_ALLOW_YOLO=1`. | Anything you approve. Approval is consent, not containment. |
| **Where code runs** (`python_exec`) | With E2B (opt-in): a microVM with its own kernel. With Docker (the default when Docker is present): no network, a read-only root, no Linux capabilities, an unprivileged user, and caps on memory, CPU and processes. | A kernel exploit: Docker shares your kernel. With neither, code runs as you on the host; production refuses that unless `KAZMA_CODE_EXEC_ALLOW_LOCAL=1`. |
| **`shell_exec`** | Shells, `env`, unknown binaries, paths outside the workspace, and an allowed binary asked to run another program. In production it is off unless `KAZMA_HOST_SHELL=1`. | What the allowed binaries can do: `git` can push, `uv` can install a package and run its build hooks, `pytest` runs a repository's `conftest.py`. It runs on the host. |
| **The prompt fence** | Untrusted text forging the fence's delimiters, and forged role tokens of five known chat-template patterns. | A model choosing to follow a polite request inside the fence. |
| **Secrets** | Secrets at rest on disk (the vault, keyed by `KAZMA_VAULT_KEY`). The processes tools start get no `KAZMA_*` variable, no credential-named variable and no URL carrying a password; MCP servers get none of Kazma's secrets. | A process that can read the vault key from the environment. |
| **Ways in** | Kazma listens on `127.0.0.1` by default, a network bind needs `KAZMA_SECRET`, and another machine must sign in. A chat app with an empty allowlist answers only its owner (since 2026-11-11). Agent-to-agent (A2A) is off by default and has read tools only. `kazma mcp` refuses a dangerous tool when no Kazma is running to ask you. | Anyone the operator lets in, who acts with the operator's authority. |
| **What you add** (skills, MCP servers, packages) | Their tool calls pass the same approval gate. An MCP tool runs without a card only when you listed it in `KAZMA_MCP_SAFE_ALLOWLIST`, approved that call, or marked its server `trust: trusted` (ordinary reads only, and ignored in production unless `KAZMA_MCP_TRUSTED_IN_PROD=1`). A tool's name is never an approval. | What a skill, server or package does with the rights you give it. A stdio MCP server runs with Kazma's rights. |

For code you do not trust, the strongest option puts the boundary around
Kazma, not inside it: run Kazma itself in a virtual machine.
`KAZMA_CODE_EXEC_DOCKER=force` and E2B are the steps before that (threat
model, section 7). Every protection above can be switched off by the operator;
each switch, with what it stops protecting, is listed under
[switches that weaken a security default](https://kazma.ai/docs/environment-variables/#switches-that-weaken-a-security-default).

## 4. Measured, not asserted

**On [AgentDojo](https://agentdojo.spylab.ai)**, a public prompt-injection
benchmark by an independent group (Debenedetti et al., NeurIPS 2024), with 996
runs per condition and each condition measured four times:

| Condition | Attack success |
|---|:---:|
| Undefended | 18.1% |
| Spotlighting (a 4-character delimiter, from the literature) | 11.6% |
| Kazma's fence | 10.4% |

The fence works (p < 0.001 against undefended), and it cannot be told apart
from the four-character delimiter (p = 0.39). Both are stated, because a
reviewer needs the second. Every figure re-derives from the committed run logs
without an API key.

**Offline, with no model in the loop**, in every build
(`python scripts/injection_report.py`): none of the 35 attack payloads in the
corpus forges the fence (56/56 with the 21 benign controls), and the
persistence denylist refuses 13 of 13 payloads meant to plant a standing
instruction, while 21 controls that sit one word from a deny pattern must stay
storable.

**Against live models** (three runs each, temperature 0), the fence lowered
compliance from 42% to 8% on `groq/compound-mini`, from 100% to 58% on
`ollama/qwen2.5:7b` and from 42% to 8% on `ollama/mistral:7b`. The
`compound-mini` row was measured before the 2026-09-12b hardening and has not
been re-run, and none of these differences has a measured noise band: the same
unchanged setup, run four times on AgentDojo, spread by 5.7 points. Two models
complied with nothing in either condition, so the fence's effect on them is
not measurable.

The method, the payloads that still get through and a free offline
reproduction are in [docs/INJECTION.md](docs/INJECTION.md).

## 5. Known gaps

Weaknesses that are open, unproven or unfinished are listed, each with its
evidence, in [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md). They are published
beside the numbers so that a reader does not have to find them. This page
names none of them, and no unfixed report is described anywhere before its fix
is released.

## 6. What counts as a vulnerability

**In scope.** Each item names the protection it gets past.

- A tool that needs approval running without its card; an approval taken from
  anywhere but your own channel; or `KAZMA_ALLOW_YOLO=1` letting an always-ask
  tool run without asking. *(Approval)*
- Reaching an install from another machine without signing in (the web, a
  socket, the API, A2A), or a private chat app answering a stranger. *(Ways in)*
- A secret getting past the control meant to hold it: into logs, answers,
  approval cards, URLs, command lines or a child process; or a tool reading
  Kazma's own stores, settings or secret files. *(Secrets)*
- Untrusted text that forges the fence's delimiters or a covered role token,
  or that gets one of the persistence denylist's covered shapes stored as a
  standing instruction. *(The prompt fence)*
- Text from outside choosing an address that Kazma then fetches without
  pinning it. *(The SSRF rules in [Security and safety](https://kazma.ai/docs/security-and-safety/))*
- Code running where this page says it cannot: host `python_exec` in
  production with `KAZMA_CODE_EXEC_ALLOW_LOCAL` unset, host `shell_exec` in
  production with `KAZMA_HOST_SHELL` unset, or a Docker run without its
  documented flags. *(Where code runs, `shell_exec`)*
- Something you told Kazma to forget (a turn, a fact or a chat) coming back
  from any writer, copy, backup or rebuild. Forgetting is a promise, so a
  failure of it is a security report.
- A flaw in Kazma's own install, update or release path: for example
  `kazma update` installing a wheel without a valid signature, or a release
  that verifies but was not built from this repository.
- Code doing the opposite of what this page, the threat model or the switches
  table says.
- A weakness already listed in [KNOWN_GAPS](docs/KNOWN_GAPS.md) counts only
  with a new way past the limit documented there.

**Spaces, once it ships.** Spaces is not in any release yet. From the release
that ships it, one space reaching another space's data is in scope.

**Not a vulnerability under this policy.** Send these as an ordinary issue, or
to whoever owns the code:

- What you approved.
- Prompt injection on its own, without one of the outcomes above: a model
  following text inside the fence is the fence's documented limit.
- What a setup you chose allows: host `shell_exec` after approval, what the
  allowed binaries can do, the local fallback once switched on, or an escape
  through the kernel Docker shares (report that to Docker or the kernel).
- Anything that needs a switch from the weakening list to be set.
- The operator, or anyone the operator let in; and the older multi-user
  mode's tenancy (tenant ids on secrets, sessions and gates), which the threat
  model says is not a boundary to rely on.
- Third-party skills, MCP servers and packages, and what they do with the
  rights you give them: report to their authors.
- Third-party dependencies: report upstream; we will help coordinate.
- The model provider: Kazma fences what comes back, and does not encrypt what
  goes out.
- my.kazma.ai and kazma.ai's infrastructure. my.kazma.ai is the maintainer's
  own live install; do not test it.
- Denial of service by volume, physical access to the host, and social
  engineering.

## 7. Versions and updates

| Version | Gets security fixes |
|---|---|
| The latest release on [GitHub Releases](https://github.com/Mubder/kazma/releases) | Yes |
| `main` | Yes: a fix lands here first |
| Any older release | No: update to the latest |

A fix lands on `main` first and reaches installs in the next release. Update
with `kazma update`; from 0.21.2 on it refuses a release whose wheel carries
no valid signature (section 8). The old `v1.0.0` tag (July 2026) comes from an
earlier numbering scheme: it is not a release and gets no fixes.

## 8. Verifying a release

A release is published by the repository's release workflow, which signs each
file with Sigstore (keyless, so there is no private key to leak), attaches
SLSA build provenance, and adds a CycloneDX 1.6 SBOM of the locked
dependencies and a `SHA256SUMS` file. v0.21.2 is the first release since
v0.11.0 published that way; v0.12.0 to v0.21.1 were uploaded by hand and carry
no signatures. The check that matters most is one command:

```bash
gh attestation verify kazma-<version>-py3-none-any.whl --repo Mubder/kazma
```

These prove where a file was built and from which commit. They do not make
the build reproducible, they are not a code review, and the SBOM lists
dependencies, not their vulnerabilities. Kazma is not on PyPI: the names there
are held, with no code, so that no one else can publish under them. Never
install `kazma` from PyPI by name. The full procedure is in
[docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md).

## 9. Running it safely

The two choices that matter most are where code runs and keeping approval on.

- Keep Kazma on `127.0.0.1` (the default). To reach it from elsewhere, put a
  reverse proxy with TLS in front, set a long random `KAZMA_SECRET`
  (`openssl rand -hex 32`), and declare the proxy in `KAZMA_TRUSTED_PROXIES`.
  Never use the old default secret `kazma-local-dev-secret`.
- Set `KAZMA_VAULT_KEY`, so that secrets are encrypted at rest.
- On Docker or a shared network, set `KAZMA_PRODUCTION=1`: it refuses host
  `python_exec`, turns host `shell_exec` off, and requires an explicit
  workspace.
- For code you do not trust, use `KAZMA_CODE_EXEC_DOCKER=force` or E2B
  (`KAZMA_E2B_API_KEY`), or run Kazma in a virtual machine.
- Leave `KAZMA_ALLOW_YOLO` unset, and `KAZMA_TRUST_LAN` at `0`.
- Give each chat app an allowlist of the accounts that may use it.
- Approve each MCP call, and name a tool in `KAZMA_MCP_SAFE_ALLOWLIST` only
  once you know what it does.
- In Docker, keep the non-root `kazma` user the image already uses.
- Check that no switch from the weakening list is set. The security report,
  `GET /api/security/hardening`, reads the install's posture on demand.

More in the [hardening guide](https://kazma.ai/docs/security/hardening-guide/)
and [Security and safety](https://kazma.ai/docs/security-and-safety/).

## 10. After you report

| Step | Target |
|---|---|
| We acknowledge your report | within 3 days |
| First assessment | within 7 days |
| Fix, for an issue rated High or Critical | within 30 days |
| Public disclosure | at most 90 days after your report, on a date agreed with you |

These are targets, not a contract. If one will slip, we tell you why.

- **Advisories.** A fix is published as a GitHub security advisory only after
  the release that carries it is out. Where it fits, a CVE is requested
  through GitHub. Kazma's internal `KAZMA-ADV-…` ids are not CVEs.
- **Credit.** You are credited by name in the advisory and in the CHANGELOG,
  unless you ask not to be.
- **No bounty.** Kazma has no paid bug bounty.

### Safe harbour

Good-faith security research on an install of Kazma that you run yourself is
welcome, and we will not take or support legal action against you for it. In
return: test only your own install, never my.kazma.ai (the maintainer's own
install), kazma.ai's infrastructure or anyone else's install; do not degrade a
service other people use; if you reach data that is not yours, stop there and
tell us; and keep the details private until the fix is released or the agreed
date arrives.

## 11. Contact

- A private report: [the GitHub form](https://github.com/Mubder/kazma/security/advisories/new),
  or **admin@kazma.ai**
- Anything that is not sensitive: [issues](https://github.com/Mubder/kazma/issues)
- This policy on the website: [English](https://kazma.ai/docs/security/security-policy/)
  · [العربية](https://kazma.ai/ar/docs/security/security-policy/)

*Reviewed 2026-11-27.*
