---
id: deployment
title: Deployment
sidebar_label: Deployment
description: Kazma Deployment — code-audited reference (docs-v2 merge, July 2026)
---
> Production deployment paths for Kazma: Docker Compose (primary), Kubernetes (Hub service), Windows native, and server management. Honest notes on what each artifact actually deploys.

---

## 1. Deployment targets at a glance

| Target | What it deploys | Status |
|---|---|---|
| **Docker Compose** (`docker-compose.yml` + `Dockerfile`) | The main Kazma agent + Web UI (uvicorn). | ✅ Primary, production-ready. |
| **Windows native** (`setup.ps1`) | Local dev venv bootstrap. | ✅ Active. |
| **Kubernetes** (`kubernetes/`) | A separate **Hub API** service — **not** the main Kazma agent. | ⚠ See §4. |
| **Cloudflare Pages / edge workers** | — | ❌ Not applicable. Kazma is a Python/uvicorn server, not an edge deployment. |
| **Bare uvicorn** | The main agent. | ✅ `kazma serve` / `kazma-web`. |

> **Honest note:** Older tasking mentioned "Cloudflare Pages, serverless edge workers." Kazma is a stateful Python service (LangGraph + SQLite + optional ChromaDB). It is not designed for serverless/edge deployment. The MCP tooling skills in this environment cover Cloudflare Workers, but they are unrelated to deploying Kazma itself.

---

## 2. Docker Compose (recommended for production)

### 2.1 The Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app

# System deps for ChromaDB + sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# RAG extras (chromadb + sentence-transformers)
RUN pip install --no-cache-dir -e ".[rag]"

# Non-root user (least-privilege)
RUN useradd -r -m -d /home/kazma -s /bin/bash kazma \
    && mkdir -p /app/kazma-data /home/kazma/.kazma/vector_memory \
    && chown -R kazma:kazma /app /home/kazma
USER kazma

EXPOSE 8000

# host=0.0.0.0 is required inside Docker so the port mapping reaches the service.
# Docker's network isolation provides the security boundary.
CMD ["python", "-m", "uvicorn", "kazma_ui.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "15"]
```

Key points:

- Installs `.[rag]` (ChromaDB + sentence-transformers) — needed for vector memory.
- Runs as the non-root **`kazma`** user (least-privilege).
- `--host 0.0.0.0` is **required inside the container** so the published port reaches the service. Docker's network isolation is the security boundary (the comment in the Dockerfile explains this).
- `--timeout-graceful-shutdown 15` gives in-flight requests 15 s to drain.

### 2.2 docker-compose.yml

```yaml
version: "3.8"
services:
  kazma:
    build: .
    container_name: kazma
    ports:
      - "8000:8000"
    volumes:
      - kazma_data:/app/kazma-data
      - kazma_vectors:/root/.kazma/vector_memory
    env_file:
      - .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/gateway/status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

volumes:
  kazma_data:
    driver: local
  kazma_vectors:
    driver: local
```

- Two named volumes persist the SQLite stores and the ChromaDB vectors.
- Health check hits `/api/gateway/status` every 30 s.
- `restart: unless-stopped` survives host reboots.

> **⚠ Volume path caveat:** the `kazma_vectors` volume mounts `/root/.kazma/vector_memory`, but the container runs as user `kazma` (home `/home/kazma`). If you rely on the default `KAZMA_VECTOR_PATH` (`~/.kazma/vector_memory`), confirm the `~` resolves to `/home/kazma` for the `kazma` user, or set `KAZMA_VECTOR_PATH=/app/kazma-data/vector_memory` explicitly to align with the data volume.

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
curl -s http://localhost:8000/health/live
```

### 2.4 `.dockerignore`

Excludes `archive/`, `__pycache__/`, `.venv/`, `.git/`, `tests/`, `kazma-data/`, `kubernetes/`, `docs/`, `*.md`, `.env`, `*.db`, build caches — keeping the image lean and secrets out.

---

## 3. Bare uvicorn (without Docker)

```bash
# Development / single-host
pip install -e ".[rag]"
kazma serve                 # 127.0.0.1:8000
# or explicit:
python -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8000
```

For a public-facing host behind a reverse proxy:

```bash
# ONLY with KAZMA_SECRET set does `kazma serve` bind 0.0.0.0
KAZMA_SECRET=$(openssl rand -hex 32) kazma serve
```

> **Never expose `0.0.0.0` without `KAZMA_SECRET`.** The HITL approval endpoint would otherwise be unauthenticated. Put Kazma behind nginx/Caddy/Traefik with TLS and let the proxy hold the public socket.

---

## 4. Kubernetes (Hub service only — read carefully)

The `kubernetes/` directory contains **two manifests**:

- `kubernetes/hub-deployment.yaml` — a `Namespace`, `Deployment` (3 replicas), `Service`, and `Ingress` for an image named **`kazma/hub-api:latest`**.
- `kubernetes/hub-secrets.yaml` — a `Secret` with `database-url` (PostgreSQL) and `redis-url`.

```yaml
# Excerpt: hub-deployment.yaml
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: hub-api
        image: kazma/hub-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL       # PostgreSQL — NOT used by the main Kazma agent
          valueFrom:
            secretKeyRef: { name: hub-secrets, key: database-url }
        - name: REDIS_URL          # Redis — NOT used by the main Kazma agent
          valueFrom:
            secretKeyRef: { name: hub-secrets, key: redis-url }
        livenessProbe:
          httpGet: { path: /api/v1/health, port: 8000 }
```

### ⚠ Critical caveats

1. **These deploy a Hub service, not the main Kazma agent.** The image `kazma/hub-api:latest` is not built from this repo's `Dockerfile` and is not published from here.
2. **The referenced infrastructure does not match the main codebase.** The main Kazma agent uses **SQLite (WAL)** everywhere (ConfigStore, checkpointer, TaskStore, snapshots, vector memory via ChromaDB). It does **not** read `DATABASE_URL` or `REDIS_URL`, and has **no PostgreSQL or Redis client**. These env vars belong to a separate (aspirational or external) Hub API service.
3. **The health path `/api/v1/health`** differs from the main app's `/health/live` and `/health/ready` (`kazma-ui/.../health.py:94,104`).
4. **Resource limits** (`256Mi–512Mi`, `250m–500m`) are reasonable for a stateless API but **too small** for the main agent if ChromaDB + sentence-transformers are loaded (those alone can exceed 512 Mi).

**Recommendation:** treat `kubernetes/` as a starting point for deploying a **separate Hub API**. To deploy the **main Kazma agent** on Kubernetes, write a new manifest using the repo's `Dockerfile`, the `/health/live` probe, an explicit `KAZMA_SECRET`, a PVC for `kazma-data/` and the vector path, and resource limits adequate for the RAG extras (≥1 Gi memory).

---

## 5. Windows native (`setup.ps1`)

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

The canonical server-kill + restart pattern (PowerShell):

```powershell
# Kill any existing Kazma uvicorn server
Get-Process -Name python -ErrorAction SilentlyContinue |
  Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } |
  ForEach-Object { Stop-Process -Id $_.Id -Force }

# Start (background), dev port 9090
cd 'G:\GitHubRepos\kazma'
& '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 9090
```

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

- `sentence-transformers` (`all-MiniLM-L6-v6-v2`) is **CPU-friendly** (~90 MB) — it does not need a GPU. VRAM is only relevant if you point Kazma at a **local GPU model server** (Ollama/LM Studio/vLLM).
- For local LLM inference, the model server (not Kazma) owns the VRAM budget. Kazma itself is a lightweight `httpx` client to that server.
- ChromaDB is memory-mapped; size the vector volume accordingly.

---

## Documentation Audit Notes

- **`kubernetes/` deploys a Hub API, not the main agent**, and references PostgreSQL + Redis, which the main codebase does not use. Flagged prominently to prevent misdeployment.
- **Volume path mismatch** (`/root/.kazma/...` vs the `kazma` user's home) in `docker-compose.yml` — set `KAZMA_VECTOR_PATH` explicitly to be safe.
- **No Cloudflare/edge deployment path.** Kazma is a stateful Python service; don't attempt serverless packaging.
- **Health path differs** between the K8s manifest (`/api/v1/health`) and the real app (`/health/live`).
