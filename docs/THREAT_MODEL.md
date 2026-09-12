# Threat model — what Kazma's boundaries actually stop

Kazma runs an LLM that can execute code, write files and send messages on your
machine. This page states, mechanism by mechanism, what each safeguard stops and
what it does not.

It is written to be *disappointing in the right places*. A safety claim is worth
what its author is willing to say against it, and the fastest way to lose a
security reviewer is a boundary described as stronger than it is. Every limit
below is a real one; several are load-bearing; none of them is a jail in the
sense that word is usually used.

**Reviewed 2026-09-12** against the code, not against the README.

---

## The short version

| Mechanism | Stops | Does **not** stop |
|---|---|---|
| HITL approval | A tool running **without you seeing it** | Anything you approve. It is consent, not containment |
| Docker jail (`python_exec`) | Network access, filesystem writes, fork bombs, memory exhaustion, a snippet reading your home directory | A kernel exploit. Docker shares your kernel |
| Shell allowlist (`shell_exec`) | Shell interpreters, `env`, unknown binaries, paths outside the workspace | What the allowlisted binaries can do — `git` and `uv` are powerful |
| Prompt fence | Untrusted text forging the fence, and forged role tokens reaching the tokenizer | A model choosing to obey a polite request inside the fence |
| Vault | Secrets at rest on disk | A process that can read the vault key from the environment |

If you take one sentence: **approval is the boundary that matters, and it is a
human boundary, not a technical one.**

---

## 1. HITL approval — consent, not containment

Every danger-tier tool routes through `LocalToolRegistry.execute()` →
`SafetyMiddleware.check()`. 57 tools are in `CANONICAL_DANGER_TOOLS`. The gate
posts an approval request and waits for a human.

**What it genuinely gives you.** Nothing dangerous happens while you are not
looking. A misread web page cannot cause a `shell_exec` at 2am, because there is
nobody to press the button. That is the product, and it is real: the whole
surface goes through one chokepoint, and `kazma mcp` exposes that same
chokepoint to other agents rather than a second one beside it.

**What it does not give you.** Once you approve, the tool runs with whatever
power it has. Approval does not sandbox, scope down, or review the command for
you. If you approve `shell_exec` on a command you did not read, the gate did its
job and you still ran the command.

**`KAZMA_ALLOW_YOLO=1`** turns the gate off per-thread for canonical danger
tools. It cannot bypass `ALWAYS_HITL_TOOLS` (`x_post`, `x_delete_post`,
`x_schedule_post`, `x_cancel_scheduled_post`) or git write commands, which are
re-gated regardless. But for everything else, YOLO means the boundary above is
not in place.

**A bus-less process fails closed.** `kazma mcp` spawned by an editor has no
in-process approval bus. It queues into the shared gate registry when a live
Kazma instance is watching, and **denies** when one is not — it never runs a
danger tool because it could not find a human.

---

## 2. The code-execution jail — and why "jail" is the wrong word

`python_exec` picks the first available of three tiers:

**E2B (Firecracker microVM)** — only when `KAZMA_E2B_API_KEY` is set. This is a
real VM boundary with its own kernel, and it is the only tier here that stands
up to a hostile snippet. It is opt-in and off by default.

**Docker** — the default when Docker is on PATH. The container is configured
properly, and the flags are worth reading because they are what the protection
actually is:

```
--network none                         no egress, no lateral movement
--memory / --memory-swap               no swap escape from the memory cap
--cpus 1  --pids-limit 64              no CPU monopoly, no fork bomb
--read-only                            immutable root filesystem
--tmpfs /tmp:noexec,nosuid  (64m)      scratch space that cannot execute
--user 65534:65534                     runs as nobody, not root
-v <workspace>:<workspace>:ro          your code is visible, not writable
python -I                              isolated mode; no user site-packages
```

That is a serious blast-radius reduction. A snippet cannot phone home, cannot
write to your disk, cannot exhaust your RAM, and cannot read outside the
workspace.

**It is still not a security boundary against a capable adversary.** Docker
shares your kernel. A container escape is a kernel bug away, and kernel bugs
happen. Anyone who tells you a Docker container contains a determined attacker
is either selling something or has not thought about it. Use E2B if the code is
genuinely untrusted.

**Two hardening flags are missing** and should be added: `--cap-drop=ALL` and
`--security-opt=no-new-privileges`. Docker's default profile already drops many
capabilities and applies a seccomp filter, so this is a gap rather than a hole —
but it is a gap, and it is named here rather than left for a reviewer to find.

**Local fallback — no isolation at all.** With no Docker and no E2B,
`python_exec` runs as a plain subprocess **as you, on your host**. It is banned
in production and multi-user mode, and `KAZMA_CODE_EXEC_ALLOW_LOCAL=1` re-enables
it anyway. If that variable is set, there is no jail; there is a subprocess.

---

## 3. `shell_exec` — a narrow door, not a locked one

`shell_exec` runs **on the host** after approval. It is not containerized, and
the HITL card says so in those words.

What constrains it:

- **A binary allowlist**, not a denylist: `ls`, `cat`, `grep`, `find`, `git`,
  `uv`, `pytest`, `ruff`, `mypy`, `jq`, and a handful more.
- **No shell interpreters.** No `bash`, `sh`, `cmd`, `powershell` — so no
  pipelines, no `$(...)`, no chaining.
- **No `env` and no `ps`** in production. Both dump secrets on some systems, and
  one approval should not become a credential dump.
- **Restricted PATH with `which`-only resolution** in strict mode.
- **A path guard** that rejects absolute paths outside the workspace, including
  values hidden behind flags (`--file=../../etc/passwd`, `-oC:\out`).
- **An argument guard** stopping an allowlisted binary being asked to run
  another program by name — otherwise the allowlist is decorative.

What it does not constrain: **what the allowed binaries can legitimately do.**
`git` can push. `uv` can install a package and execute its build hooks.
`pytest` runs arbitrary code in `conftest.py`. The allowlist is a meaningful
reduction in surface, not a capability boundary — and none of it applies until
you have approved the call.

---

## 4. The prompt fence — a measured reduction, not a guarantee

Untrusted text (web pages, tool results, MCP output, skill bodies) is wrapped in
a labelled data fence before a model sees it. Two structural properties are
tested exhaustively and hold absolutely:

- No corpus payload can forge the fence's delimiters (56/56).
- Foreign chat-template tokens (`<|im_start|>`, `[INST]`, `<<SYS>>`, Gemma
  turns) are redacted, so a document cannot forge a role turn at the
  tokenizer.

Those are properties of the code. **Whether a model obeys the fence is a
property of the model**, and it is measured rather than asserted: 42% → 8% on
`groq/compound-mini`, 100% → 58% on `qwen2.5:7b`, 42% → 8% on `mistral:7b`. See
[INJECTION.md](INJECTION.md), including the reproduction you can run yourself
for free, and the payloads that still get through.

The fence reduces attack success. It does not eliminate it, and no honest
number here will ever be 0%.

---

## 5. What your configuration actually means

The defaults are not the only posture, and the variables below change which of
the above applies. Check yours:

| Setting | Effect on this page |
|---|---|
| `KAZMA_CODE_EXEC_DOCKER=force` | Section 2's jail applies; host `shell_exec` is off unless `KAZMA_HOST_SHELL=1` |
| `KAZMA_CODE_EXEC_DOCKER=0` | No container. `python_exec` is a host subprocess |
| `KAZMA_CODE_EXEC_ALLOW_LOCAL=1` | Local fallback re-enabled **even in production** |
| `KAZMA_E2B_API_KEY` set | The only real isolation boundary here |
| `KAZMA_ALLOW_YOLO=1` | Section 1 is off for canonical danger tools |
| `KAZMA_PRODUCTION=1` | Bans local exec, narrows the shell allowlist, requires a workspace root |
| `KAZMA_SHELL_STRICT=0` | Relaxes PATH restriction and binary resolution |

A single-operator box with `DOCKER=0`, `ALLOW_LOCAL=1` and `ALLOW_YOLO=1` is a
**convenience posture**: the human gate is off for most tools and code runs on
the host as you. That is a defensible choice for a developer on their own
machine and it is not the posture the safety claims describe. Both things are
true; the point of this page is that you should know which one you are running.

---

## 6. Out of scope, stated plainly

- **Multi-tenant isolation.** The default threat model is a single operator on a
  trusted host. Tenancy exists in the data layer (`tenant_id` on secrets,
  sessions, gates) but it has not been adversarially reviewed, and it is not a
  boundary to rely on for mutually distrusting users.
- **A malicious operator.** Everything here assumes the human at the approval
  prompt is on your side.
- **Supply chain of what you approve.** Signing covers Kazma's own releases
  ([SUPPLY_CHAIN.md](SUPPLY_CHAIN.md)). It says nothing about a package `uv`
  installs after you approve the command.
- **The model provider.** Prompts and tool results go to whichever provider you
  configure. Kazma fences what comes *back*; it does not encrypt what goes out.

---

## 7. If you want a real boundary

In increasing order of strength and inconvenience:

1. **`KAZMA_CODE_EXEC_DOCKER=force`** — actually use the jail in section 2, and
   accept that host `shell_exec` turns off with it.
2. **Set `KAZMA_E2B_API_KEY`** — a Firecracker microVM per execution. This is
   the tier to use if the code is genuinely untrusted.
3. **Run Kazma itself in a VM.** The honest answer for a hostile workload is
   that the boundary should be around Kazma, not inside it.

---

*Open weaknesses across the project are tracked in
[KNOWN_GAPS.md](KNOWN_GAPS.md). Report a vulnerability privately — see
[SECURITY.md](../SECURITY.md).*
