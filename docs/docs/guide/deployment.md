---
id: deployment
title: Deployment
sidebar_label: Deployment
description: Kazma Deployment — code-audited reference (unified docs, v0.9+)
---
> Production deployment paths for Kazma: Docker Compose (primary), Kubernetes (Hub service), Windows native, and server management. Honest notes on what each artifact actually deploys.

---

## 1. Deployment targets at a glance

| Target | What it deploys | Status |
|---|---|---|
| **Docker Compose** (`docker-compose.yml` + `Dockerfile`) | The main Kazma agent + Web UI (uvicorn). | ✅ Primary, production-ready. |
| **Windows native** (`setup.ps1`) | Local dev venv bootstrap. | ✅ Active. |
| **Kubernetes** | Single-owner StatefulSet template with database ownership and a fenced state volume. | ⚠ Requires target-cluster qualification; see §4. |
| **Cloudflare Pages / edge workers** | — | ❌ Not applicable. Kazma is a Python/uvicorn server, not an edge deployment. |
| **Bare uvicorn** | The main agent. | ✅ `kazma serve` / `kazma-web`. |

> **Honest note:** Older tasking mentioned "Cloudflare Pages, serverless edge workers." Kazma is a stateful Python service (LangGraph + SQLite + optional ChromaDB). It is not designed for serverless/edge deployment. The MCP tooling skills in this environment cover Cloudflare Workers, but they are unrelated to deploying Kazma itself.

---

## 2. Docker Compose (recommended for production)

### 2.1 The Dockerfile

The live file is `Dockerfile` at the repo root — do not copy-paste a snapshot here (it drifted before: missing git, Arabic fonts, LibreOffice, Tesseract, ClamAV, and `document-platform`). What it actually does:

- Base: `python:3.11-slim`.
- System deps: git, libpq, Noto Arabic fonts, LibreOffice, Tesseract (`eng`+`ara`), ClamAV, build-essential, curl.
- Python: `pip install -e ".[rag,postgres,document-platform]"`.
- Runs as non-root **`kazma`**. Listens on **container port 8000**.
- Entrypoint: `scripts/docker-entrypoint.sh`.

`--host 0.0.0.0` is **required inside the container** so the published port reaches the service. Docker's network isolation is the security boundary.

### 2.2 docker-compose.yml

Live file: `docker-compose.yml`. Accurate facts (do not restore the old `8000:8000` / `/root/.kazma` snapshot):

- Host port **9090** → container **8000** (`HOST_PORT` to override) so bookmarks match `kazma serve`.
- Volumes: `kazma_data` → `/app/kazma-data`; `kazma_vectors` → `/home/kazma/.kazma/vector_memory` (the `kazma` user home, not `/root`).
- `KAZMA_VECTOR_PATH=/home/kazma/.kazma/vector_memory` is set in compose.
- Health check: `curl -f http://localhost:8000/health/ready` every 30 s, **300 s** start period (cold start loads embeddings + MCP).
- `restart: unless-stopped` survives host reboots (it does **not** restart on unhealthy).

### 2.3 Deploy steps

```bash
cp .env.example .env
# Edit .env: set OPENAI_API_KEY, KAZMA_SECRET, any platform tokens
# Generate a strong secret:
#   openssl rand -hex 32   # → put in KAZMA_SECRET

docker compose up -d --build
docker compose logs -f kazma
```

Verify:

```bash
curl -s http://localhost:9090/health/ready
```

### 2.4 Runtime ownership and readiness

Run one server process per data directory, including when relational state
uses Postgres. A local OS writer lock refuses a second owner and releases on
process death. Never delete its lock file to force takeover. Shared local
volumes, process-owned turn delivery and remaining SQLite stores prevent a
cross-host HA claim; WAL contention is not fencing. Do not use NFS or scale
the application replicas for this deployment.

The Postgres recipe in `docker-compose.ha.yml` now specifies one runtime,
mounts its state at `/app/kazma-data`, and exposes it through nginx. The
filename is historical: this is a single-owner recovery baseline. Restart
the stopped owner on the same local state, keep verified backups, and
qualify restore before replacing its host. PostgreSQL alone does not make
the whole framework safe for multiple active runtimes.

Readiness requires settings, database, registry, agent, provider and a graph
bound to its initialized saver. Optional failures remain visible. Add
deployment dependencies in the configuration:

```yaml
health:
  required_capabilities: [mcp, swarm_engine, code_execution]
```

Unknown or malformed requirements refuse readiness. Supported additions are
`mcp`, `swarm_engine`, `cron`, `schedulers`, `temporal`, and `code_execution`.
Required Temporal also needs its running worker. Code execution checks the
Docker daemon without pulling an image, or reports E2B SDK/credential presence;
the latter does not verify the remote service. Run a controlled execution
acceptance test before rollout. Keep liveness separate from readiness so a
database outage is answered with 503 rather than a restart loop.

Production host shell is off unless explicitly granted. Deploying inside
Docker does not itself provide the Docker daemon required for nested code
execution; configure an isolated execution backend rather than exposing the
host's privileged Docker socket to the agent.

For a recovery drill, keep the application stopped while restoring both
Postgres and the complete local data directory, together with their vault
key. Test on a separate host or isolated directory first. Verify settings,
workspace identity, paused approvals and turn history before reopening
traffic. Do not restore only Postgres and assume the local stores match it.

If a Temporal result says `execution_outcome: unknown`, use the reported
workflow ID to inspect its history and actual effects. Resubmitting that same
task ID recovers its existing workflow; creating a new task ID can repeat
effects. Whole-agent activities intentionally have one attempt. Add effect
identities and per-step recovery before enabling automatic activity retries.

If an approval returns 503, restore decision storage before acting again;
the graph remains paused. A claimed/resuming decision after a crash needs
checkpoint reconciliation and the normal orphan sweep, rather than an
automatic second approval. Old platform buttons are bound to their interrupt
and cannot decide a later pause. Use the current card after recovery.

After a workspace switch, a failed MCP reconnect leaves the binding
unverified. Reconnect the affected server and confirm its current workspace
before dispatching tools; do not suppress the scope guard. Scoped instances
have a bounded cache, and all-busy capacity refuses new dispatch instead of
closing a request in flight.

### 2.5 `.dockerignore`

Excludes `archive/`, `__pycache__/`, `.venv/`, `.git/`, `tests/`, `kazma-data/`, `docs/`, `*.md`, `.env`, `*.db`, build caches — keeping the image lean and secrets out.

---

## 3. Bare uvicorn (without Docker)

```bash
# Development / single-host — use kazma serve (not raw uvicorn on Windows)
pip install -e ".[rag,dev,tui]"
kazma serve                 # 127.0.0.1:9090
```

For a public-facing host behind a reverse proxy:

```bash
# ONLY with KAZMA_SECRET set does `kazma serve` bind 0.0.0.0
KAZMA_SECRET=$(openssl rand -hex 32) \
KAZMA_TRUSTED_PROXIES=127.0.0.1 \
kazma serve
```

> **Never expose `0.0.0.0` without `KAZMA_SECRET`.** The HITL approval endpoint would otherwise be unauthenticated. Put Kazma behind nginx/Caddy/Traefik with TLS and let the proxy hold the public socket.

> **`KAZMA_TRUSTED_PROXIES` is required whenever a reverse proxy is in front of Kazma.** Set it to the address the proxy connects *from* — `127.0.0.1` for a same-host nginx/Caddy, or the container/bridge IP in Docker.
>
> Without it, `request.client.host` is the proxy for every request. A same-host proxy makes every internet visitor look like `127.0.0.1`, which Kazma treats as the local operator and auto-issues an admin session to — a complete auth bypass over both HTTP and WebSocket (audit F-01, fixed 2026-08-29). With it set, Kazma reads the real client from `X-Forwarded-For` and stops trusting peer address as a credential.
>
> Your proxy **must** set the forwarded headers, and must overwrite rather than append a client-supplied value. The shipped `deploy/nginx-ha.conf` already does:
>
> ```nginx
> proxy_set_header Host              $host;
> proxy_set_header X-Real-IP         $remote_addr;
> proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
> proxy_set_header X-Forwarded-Proto $scheme;
> ```
>
> Kazma applies this variable itself, in the app, so uvicorn must **not** rewrite forwarded headers: `serve.py`, `kazma serve` and `kazma-web` start it with `proxy_headers=False`. If you launch uvicorn yourself, pass `--no-proxy-headers` — its default trusts `127.0.0.1` and hides the proxy's address from Kazma's undeclared-proxy check, which then raises a false alarm naming your visitor's IP (seen live behind Cloudflare Tunnel, 2026-09-23 to 2026-09-25).

---

## 4. Kubernetes

`deploy/kubernetes/runtime.yaml` prepares the main agent for active/passive
recovery. It runs **one** StatefulSet replica on port 8000, with a
`ReadWriteOncePod` PVC holding data, vectors, user state and installed skills
at stable paths under `/state`. UID/GID 10001 match the Docker image. Its
150-second termination budget allows the application's 120-second shutdown
ceiling to finish before storage moves. It mounts no Docker socket or
Kubernetes credentials. Provide an isolated code execution service separately.

Before applying, choose a supported replicated block CSI driver whose
detach/reattach fencing you can verify. Replace the storage class placeholder
and the image placeholder with a built, scanned release digest. Create
`kazma-runtime-secrets` containing `database-url`, `auth-secret`, `vault-key`
and `trusted-proxies`. Use a managed HA PostgreSQL **direct endpoint** or
session pooling; transaction pooling is incompatible with the ownership lock.
Set ingress proxy addresses narrowly and require TLS. Supply provider and
platform credentials through the normal protected configuration workflow.

```bash
kubectl kustomize deploy/kubernetes
# Review the rendered resources after replacing both placeholders.
kubectl apply --dry-run=server -k deploy/kubernetes
kubectl apply -k deploy/kubernetes
```

`KAZMA_RUNTIME_HA=1` acquires a dedicated Postgres session advisory lock
before constructing services. A second runtime on another directory or host
is refused. The database records the volume's `.runtime-state-id`; a different
or blank volume cannot take over that database. Pair the **authoritative**
volume on the first HA boot, after backing up the database and complete state.
The monitor never reconnects: session loss exits the entire process (75),
including workers that are still running while the event loop is stalled.
The Kubernetes controller may restart it after database connectivity returns.

The lock supplements storage fencing. PostgreSQL releasing a lost session
does not itself stop a partitioned host's SQLite writes or external effects.
Keep one replica; never force-delete the old StatefulSet pod to accelerate
failover until its node and volume have been fenced. Protect the database's
volume identity table from manual edits. Restore the database, complete PVC
snapshot and vault key as a coordinated set. Do not copy only the identity
file to authorize an unrelated volume.

Qualification remains **open** until the target cluster passes these drills:

1. Gracefully replace the pod and verify readiness, paused approval identity,
   settings, workspace, vectors and committed turn history on the same PVC.
2. Terminate the ownership database session and verify the old process exits,
   traffic stops and one successor acquires ownership; no pending effect is
   silently replayed. Duplicate/unknown external effects require reconciliation.
3. Lose the node and separately partition its network. Prove storage fencing
   stops the former owner before the successor can attach; record RTO and RPO.
4. Attempt a second runtime and a blank/mismatched volume. Both must refuse
   admission without changing business state.
5. Restore paired PostgreSQL/PVC backups into an isolated environment and
   verify approvals, vault access, history and isolation before accepting traffic.

Local disposable-Postgres tests cover admission, mismatched volumes, process
death and database-session loss. They do not certify a CSI driver or cross-host
recovery. Keep deployment approval conditional on measured results from the
actual cluster. See Kubernetes' [StatefulSet guidance](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
and [force-deletion warning](https://kubernetes.io/docs/tasks/run-application/force-delete-stateful-set-pod/).

---

## 5. Windows native (`setup.ps1`)

Cross-platform path policy and data layout: **[Portability](../ops/portability)**.

`setup.ps1` is the deterministic, fail-fast, idempotent Windows bootstrap (PowerShell 5.0+). It:

1. Validates the environment (Python 3.11+, `uv`, `kazma.yaml`).
2. Syncs the virtual environment from `pyproject.toml`.
3. Runs a foundation integrity check (core imports + test collection).

```powershell
.\setup.ps1
.\setup.ps1 -Debug     # verbose
```

> **PowerShell rule (from AGENTS):** never chain commands with `&&` or `||`. Use `;` and check `$LASTEXITCODE`. The Bash tool in this environment uses Git Bash, not PowerShell.

---

## 6. Server management (from AGENTS)

On a host watched by `KazmaAgent` (the health-gated supervisor), pick up
code with **one** command. Do not kill `python`/`uvicorn` by hand — that
fights the guard.

```powershell
cd <kazma-install>
& '.venv\Scripts\python.exe' scripts\service\kazma_guard.py --reload --when-idle
```

The **running guard** carries out the reload: `--reload` writes a request,
and the guard stops the server gracefully (Ctrl+Break or SIGTERM, waiting up
to `KAZMA_GUARD_GRACEFUL_STOP_S`, 60 s, so the app's shutdown hooks run) and
starts the code on disk. The command waits for the new build to answer
(`Kazma is up. build …`; a first boot can take a few minutes for imports,
MCP and Postgres). `--when-idle` first waits until no chat turn is running;
exit code 3 means one still was at `--idle-timeout` (default 900 s) and
nothing was touched. `--status` says whether a guard is running (from its
heartbeat) and whether the server answers `/health/ready`. With no guard
running, `--reload` starts the `KazmaAgent` task, and the new guard clears
the old server with its own rights, so a reload never needs an elevated
shell. A change to the guard itself needs the task restarted
(`schtasks /End /TN KazmaAgent`, then `schtasks /Run /TN KazmaAgent`).

**The task.** `python scripts/service/install_service.py --install`
registers `KazmaAgent`: at boot with nobody logged on (S4U, highest
privileges), at logon, and every 5 minutes to bring back a guard that exited
— a trigger that finds the guard running is ignored, which Task Scheduler
records as `0x800710E0` — at priority 4. `install_service.py --status`
compares the registered task with that, setting by setting, marks each
difference `FIX` and exits 1. To change an existing task, run `--install`
again from an **elevated** PowerShell, with the installer of the folder
whose guard the task starts (`--status` names it). It refuses to make things
worse: without admin rights it does not replace a task that starts at boot
with the user-level one, which starts only after a logon; and run from a
second checkout on the same machine it does not move the task there unless
you add `--move`.

**PATH changes.** Windows hands a process its parent's environment, and the
guard runs from one boot to the next, so a tool installed (or put back on
PATH) while it runs used to stay invisible until `KazmaAgent` restarted. The
server now appends the PATH entries the OS settings gained since then, and
logs them (`[env] PATH gained N entries from the OS settings …`), so
`--reload` is enough. It only appends: an entry removed from the settings
stays until the task restarts. Other variables set in System Properties
still need that restart; Kazma's own settings belong in `.env`, which every
boot re-reads.

**Priority.** A Scheduled Task registered without a priority runs at Task
Scheduler's default, 7, which Microsoft reserves for background tasks. The
guard started below every normal program on the machine, and the server it
starts inherited that, along with lower memory and disk priority. Whenever
something heavy ran beside Kazma (a test suite, a benchmark), it froze for
15–27 seconds. The server and the guard now raise themselves to an
interactive program's priority as they start — CPU, memory and disk,
never lower — and the server's log says what it started with
(`[startup] Raised the process to interactive priority …`).
`install_service.py` registers the task at priority 4, and its `--status`
shows the priority the registered task has; to change an existing task, run
`python scripts/service/install_service.py --install` again from an elevated
PowerShell. `KAZMA_PROCESS_PRIORITY=keep` leaves both where they were
started.

On Windows, start via `kazma serve` or the guard — not `python -m uvicorn`.
Uvicorn 0.36+ hardcodes `ProactorEventLoop`, and psycopg-async then cannot
open the Postgres checkpointer.

**What the guard counts.** It probes `/health/ready` every 30 s and restarts
Kazma after **3 consecutive** probes that get **no answer** — a timeout or a
refused connection (`guard.restarting` with reason `unhealthy (…)`). A single
miss answered by the next probe is logged as `health.recovered` and nothing
else. A probe that cannot even get a local port (`WinError 10048` / `10055` —
the machine is out of ephemeral ports) is logged as `health.probe_unrunnable`
with a snapshot of who holds the sockets (`health.port_exhaustion`), is
**not** counted toward a restart, and pages once if it lasts ~5 minutes:
restarting Kazma cannot free a port.

**A database outage is ridden out, not restarted.** When Kazma answers but
says it is not ready — a 503 naming a failing dependency, such as the
database — a restart cannot bring the database back. The guard logs
`health.dependency_down`, pages once ("Kazma is up, but not ready"), and
restarts only if the outage lasts `KAZMA_GUARD_DEPENDENCY_OUTAGE_S` (default
10 minutes), in case the database is back and Kazma's own connections are
what is stuck. One failure is restarted after the usual 3 probes instead:
when Kazma says only a restart clears it (`restart_required` — the settings
store fell back to memory at a boot while the database was away, and stays
there for the life of the process). `/health/ready` runs its checks at once, each capped, so it
answers within ~5 s whatever its dependencies do; the Postgres pools connect
with a 5 s timeout and replace a connection the server closed when it is
handed out, so Kazma is back seconds after the database is.

**When the server itself exits,** its last words are kept: the guard writes
the server's stderr to `.kazma/server.stderr.log` (an uncaught exception's
traceback, the stacks of a native crash — `serve.py` enables
`faulthandler` — or a refusal at boot) and quotes its last lines in the
`guard.restarting` event and the page.

**Settings → "Restart server"** asks the guard for a reload when a guard
started the server (the same graceful reload as `kazma_guard.py --reload`);
without a guard the server restarts itself as before.

---

## 7. Health endpoints

| Endpoint | Purpose | Location |
|---|---|---|
| `GET /health/live` | Liveness | `health.py:94` |
| `GET /health/ready` | Readiness | `health.py:104` |
| `GET /health/details` | Detailed health | `health.py:148` |
| `GET /api/gateway/status` | Gateway/adapter status (used by Docker healthcheck + `kazma status`) | gateway router |

---

## 8. Production checklist

- [ ] `KAZMA_SECRET` set (strong random) — required to protect `/api/approve`.
- [ ] Server bound to `127.0.0.1` (or behind a TLS-terminating reverse proxy).
- [ ] **`KAZMA_TRUSTED_PROXIES` set to the proxy's address** whenever a reverse proxy is in front — otherwise every visitor is treated as the local operator (audit F-01). Verify with `curl -s https://your-host/api/auth/status`: `authenticated` must be `false` before login.
- [ ] Provider API keys rotated if this instance ever served `/api/settings` on a build before 2026-08-29 (audit F-02 leaked them in plaintext).
- [ ] Volumes persisted for `kazma-data/` and the vector memory path.
- [ ] `kazma.yaml` `safety.hitl.enabled: true` and a complete `require_approval_for` list.
- [ ] All three HITL build sites pass `hitl_config` (default builds do; verify any custom build).
- [ ] MCP stdio servers sandboxed (no auth on stdio transport).
- [ ] Skills signed (`kazma hub sign`) with the same `KAZMA_SECRET` used at load time.
- [ ] Resource limits account for ChromaDB + sentence-transformers if RAG is enabled (≥1 Gi).
- [ ] Health check wired (`/api/gateway/status` or `/health/live`).
- [ ] Logs shipping to your collector (JSON format available via `logging.format: json`).

---

## 9. Resource considerations (24 GB VRAM setups)

The repo's notes mention "resource constraints on 24 GB VRAM setups." Practical guidance:

- `sentence-transformers` (`BAAI/bge-m3`) is **CPU-friendly** (~2.2 GB) — it does not need a GPU. VRAM is only relevant if you point Kazma at a **local GPU model server** (Ollama/LM Studio/vLLM).
- For local LLM inference, the model server (not Kazma) owns the VRAM budget. Kazma itself is a lightweight `httpx` client to that server.
- ChromaDB is memory-mapped; size the vector volume accordingly.

---

## 10. Lifecycle status notifications

Kazma sends one card to your alert routes (Telegram/Discord/Slack, as chosen under **Alerts go to**) each time it is back up, and one when startup fails -- so you can tell from chat that a restart finished, how long it took, and whether every chat app came back:

```
🟢 [System] Kazma restarted
Down for 34.8 s · build 3b7422d

Adapters
✅ Telegram
✅ Discord
❌ Slack: Slack refused the app-level token (invalid_auth)

Model: deepseek-flash
```

The card waits until every chat app has connected or failed (up to 45 s; they usually connect within a few seconds) and marks each one with what its connection said. It is green when everything connected and the last run stopped cleanly, yellow otherwise. "Down for" runs from the stop to the moment Kazma was serving again; the wait for the chat apps is not counted. Until 2026-09-30 it ran to the card's send, so one slow chat app turned a 35-second reload into "started".

To check the routes without waiting for a restart, press **Send a test alert** under **Alerts go to**: one message goes along the saved routes the way a real alert does, and the page names each route that took it.

### Messages

| Message | On by default | When | What it tells you |
|-------|------|------|-------------------|
| `started` | yes | Once the chat apps have connected (or failed to) | Kazma is up: **restarted** after a clean stop within the restart window, otherwise **started**, with how long it was down -- or "The last run did not shut down cleanly" after a crash, a forced stop or a power cut. |
| `startup_failed` | yes | Gateway-start failure | Boot error (bad token, network) -- the error is in the message. |
| `starting` | no | Top of startup | Boot began. |
| `shutting_down` | no | Top of a graceful shutdown | Kazma is stopping. |

The start and the stop are recorded whether or not their messages are on: that is how the next card knows the downtime, and whether the last run ended cleanly. Until 2026-09-29 all four were on, three messages per restart; an install still holding that stored default is moved to the new one once, at boot.

Switch them in **Settings → Providers & Connectors → Platform Connectors → Adapters & Routes → Server status messages**.

### How it works

Notifications route through the **SwarmMessageBus** — the same bus that delivers swarm worker output. No separate notification path is constructed. The bus is wired during `KazmaAppBuilder.build()` (before the lifespan), and `FanOutBusAdapter` fans out to the selected platforms. When no platform bus is configured (`NullBusAdapter`), the feature self-disables silently.

### Configuration

```yaml
notifications:
  lifecycle:
    enabled: true
    events: [started, startup_failed]   # also: starting, shutting_down; [] sends none
    restart_window_seconds: 60          # 0 turns restart detection off
```

Config is **live-re-read** on every boot/shutdown — change it in Settings (or `kazma.yaml`) without a restart for the *next* boot.

### Enabling notifications

The messages need a destination. Set `connectors.<platform>.swarm_chat_id` to the chat ID where you want them delivered (typically your DM with the bot):

```bash
# Telegram example — set to your user ID (same as allowed_users)
# Via the Settings API:
curl -X PUT http://127.0.0.1:9090/api/settings/single \
  -H "Content-Type: application/json" \
  -H "X-Kazma-Secret: $KAZMA_SECRET" \
  -d '{"key":"connectors.telegram.swarm_chat_id","value":"<your-chat-id>"}'
```

Without `swarm_chat_id`, the bus stays `NullBusAdapter` and notifications are dropped silently.

See: [Configuration → `notifications.lifecycle`](configuration#notificationslifecycle) for the full key reference.

---


- **Volume path mismatch** (`/root/.kazma/...` vs the `kazma` user's home) in `docker-compose.yml` — set `KAZMA_VECTOR_PATH` explicitly to be safe.
- **No Cloudflare/edge deployment path.** Kazma is a stateful Python service; don't attempt serverless packaging.
