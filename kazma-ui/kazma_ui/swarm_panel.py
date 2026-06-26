"""Swarm Panel — Web UI for managing multi-worker AI agent swarms.

Provides:
  GET    /api/swarm/status       — list all workers + health
  POST   /api/swarm/dispatch     — dispatch task to worker(s)
  POST   /api/swarm/workers      — add a new worker
  DELETE /api/swarm/workers/{name} — remove a worker
  POST   /api/swarm/start        — start all workers
  POST   /api/swarm/stop         — stop all workers
  GET    /swarm                  — Web UI panel (HTML)

If kazma_core.swarm is not installed, the API returns setup instructions
and the UI shows an onboarding banner.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# ---------------------------------------------------------------------------
# In-memory worker registry (local backend; replaced by kazma_core.swarm
# when the package is installed)
# ---------------------------------------------------------------------------
_workers: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_started = False


def _reset_swarm_state() -> None:
    """Reset all in-memory swarm state (for testing)."""
    global _workers, _started
    with _lock:
        _workers.clear()
        _started = False

_SUPPORTED_MODELS = [
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "gpt-4o",
    "claude-sonnet-4",
    "claude-haiku-3.5",
    "llama-3.1-70b",
]

_SUPPORTED_PROVIDERS = [
    "deepseek",
    "openai",
    "openrouter",
    "anthropic",
    "ollama",
]


def _has_swarm_core() -> bool:
    """Check if kazma_core.swarm package is available."""
    try:
        import kazma_core.swarm  # noqa: F401
        return True
    except ImportError:
        return False


def _worker_status(worker: dict) -> str:
    """Derive display status: online / offline / busy."""
    if not _started:
        return "offline"
    if worker.get("busy"):
        return "busy"
    return "online"


def create_swarm_router(templates: Any) -> APIRouter:
    """Create the Swarm Panel API router.

    Args:
        templates: Jinja2Templates instance for HTML rendering.

    Returns:
        APIRouter mounted at /api/swarm + /swarm page.
    """
    router = APIRouter(tags=["swarm"])

    _has_core = _has_swarm_core()

    # ── Web UI Page ──────────────────────────────────────────────────
    @router.get("/swarm", response_class=HTMLResponse)
    async def swarm_page(request: Request) -> HTMLResponse:
        """Render the Swarm management page."""
        template_path = _TEMPLATE_DIR / "swarm.html"
        if template_path.exists():
            return templates.TemplateResponse(
                request,
                "swarm.html",
                {
                    "workers": list(_workers.values()),
                    "worker_count": len(_workers),
                    "started": _started,
                    "has_swarm_core": _has_core,
                    "config": None,
                    "active_page": "swarm",
                },
            )

        # Fallback: inline HTML if template doesn't exist yet
        return HTMLResponse(_fallback_html(request, _has_core))

    # ── API: Worker Status ───────────────────────────────────────────
    @router.get("/api/swarm/status")
    async def swarm_status() -> dict[str, Any]:
        """List all workers with health status.

        Returns:
            {
                "workers": [{name, model, provider, type, status, ...}],
                "count": N,
                "started": bool,
                "has_swarm_core": bool,
                "setup_instructions": str | null,
            }
        """
        with _lock:
            worker_list = [
                {
                    "name": name,
                    "model": w.get("model", "?"),
                    "provider": w.get("provider", "?"),
                    "type": w.get("type", "?"),
                    "status": _worker_status(w),
                    "bot_token": "***" if w.get("bot_token") else None,
                    "added_at": w.get("added_at"),
                    "last_heartbeat": w.get("last_heartbeat"),
                }
                for name, w in _workers.items()
            ]

        result: dict[str, Any] = {
            "workers": worker_list,
            "count": len(worker_list),
            "started": _started,
            "has_swarm_core": _has_core,
            "setup_instructions": None,
        }

        if not _has_core:
            result["setup_instructions"] = (
                "kazma_core.swarm is not installed. "
                "Install with: pip install kazma-core[swarm] "
                "or add kazma_core.swarm to your project."
            )

        return result

    # ── API: Dispatch Task ───────────────────────────────────────────
    @router.post("/api/swarm/dispatch")
    async def swarm_dispatch(payload: dict[str, Any]) -> JSONResponse:
        """Dispatch a task to one or more workers.

        Request body:
            {
                "workers": ["worker1", "worker2"],
                "task": "Build feature gw-067",
                "context": "optional extra context"
            }
        """
        worker_names = payload.get("workers", [])
        task = payload.get("task", "")
        context = payload.get("context", "")

        if not worker_names:
            return JSONResponse(
                {"status": "error", "message": "No workers specified"}, status_code=400
            )
        if not task:
            return JSONResponse(
                {"status": "error", "message": "No task specified"}, status_code=400
            )

        dispatched = []
        missing = []
        with _lock:
            for name in worker_names:
                if name in _workers:
                    _workers[name]["busy"] = True
                    _workers[name]["last_task"] = task
                    dispatched.append(name)
                else:
                    missing.append(name)

        if not _has_core:
            return JSONResponse(
                {
                    "status": "warning",
                    "message": (
                        "kazma_core.swarm is not installed — task recorded locally "
                        "but no workers will execute it. Install: pip install kazma-core[swarm]"
                    ),
                    "dispatched": dispatched,
                    "missing": missing,
                }
            )

        return JSONResponse(
            {
                "status": "ok",
                "message": f"Task dispatched to {len(dispatched)} worker(s)",
                "dispatched": dispatched,
                "missing": missing,
                "task": task,
            }
        )

    # ── API: Add Worker ──────────────────────────────────────────────
    @router.post("/api/swarm/workers", status_code=201)
    async def swarm_add_worker(payload: dict[str, Any]) -> JSONResponse:
        """Add a new worker to the swarm.

        Request body:
            {
                "name": "worker-1",
                "model": "deepseek-chat",
                "provider": "deepseek",
                "bot_token": "optional-telegram-token",
                "type": "in-process"  // or "telegram"
            }
        """
        name = (payload.get("name") or "").strip()
        if not name:
            return JSONResponse(
                {"status": "error", "message": "Worker name is required"}, status_code=400
            )

        with _lock:
            if name in _workers:
                return JSONResponse(
                    {"status": "error", "message": f"Worker '{name}' already exists"},
                    status_code=409,
                )

            worker = {
                "name": name,
                "model": payload.get("model", "deepseek-chat"),
                "provider": payload.get("provider", "deepseek"),
                "type": payload.get("type", "in-process"),
                "bot_token": payload.get("bot_token"),
                "busy": False,
                "last_task": None,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "last_heartbeat": None,
            }
            _workers[name] = worker

        logger.info("[Swarm] Worker added: %s (%s/%s)", name, worker["model"], worker["provider"])
        return JSONResponse({"status": "ok", "worker": worker}, status_code=201)

    # ── API: Remove Worker ───────────────────────────────────────────
    @router.delete("/api/swarm/workers/{name}")
    async def swarm_remove_worker(name: str) -> JSONResponse:
        """Remove a worker from the swarm by name."""
        with _lock:
            if name not in _workers:
                return JSONResponse(
                    {"status": "error", "message": f"Worker '{name}' not found"},
                    status_code=404,
                )
            del _workers[name]

        logger.info("[Swarm] Worker removed: %s", name)
        return JSONResponse({"status": "ok", "message": f"Worker '{name}' removed"})

    # ── API: Start / Stop All ────────────────────────────────────────
    @router.post("/api/swarm/start")
    async def swarm_start() -> JSONResponse:
        """Start all workers in the swarm."""
        global _started

        if _started:
            return JSONResponse({"status": "ok", "message": "Swarm already started"})

        if not _workers:
            return JSONResponse(
                {"status": "error", "message": "No workers registered — add workers first"},
                status_code=400,
            )

        if not _has_core:
            return JSONResponse(
                {
                    "status": "warning",
                    "message": (
                        "kazma_core.swarm is not installed — workers registered but "
                        "cannot be started. Install: pip install kazma-core[swarm]"
                    ),
                    "worker_count": len(_workers),
                }
            )

        _started = True
        with _lock:
            for w in _workers.values():
                w["status"] = "online"

        logger.info("[Swarm] Started — %d workers", len(_workers))
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Swarm started — {len(_workers)} worker(s) online",
                "worker_count": len(_workers),
            }
        )

    @router.post("/api/swarm/stop")
    async def swarm_stop() -> JSONResponse:
        """Stop all workers in the swarm."""
        global _started

        if not _started:
            return JSONResponse({"status": "ok", "message": "Swarm already stopped"})

        _started = False
        with _lock:
            for w in _workers.values():
                w["busy"] = False

        logger.info("[Swarm] Stopped — %d workers", len(_workers))
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Swarm stopped — {len(_workers)} worker(s) offline",
                "worker_count": len(_workers),
            }
        )

    # ── Models / Providers lookup ────────────────────────────────────
    @router.get("/api/swarm/models")
    async def swarm_models() -> dict[str, Any]:
        """Return supported models and providers for dropdowns."""
        return {
            "models": _SUPPORTED_MODELS,
            "providers": _SUPPORTED_PROVIDERS,
        }

    return router


# ── Fallback inline HTML (served when swarm.html template is missing) ──


def _fallback_html(request: Request, has_core: bool) -> str:
    """Inline HTML fallback for /swarm page."""
    setup_banner = ""
    if not has_core:
        setup_banner = """
        <div style="background:#fff3cd;border:1px solid #ffc107;padding:12px 20px;
                    border-radius:6px;margin-bottom:20px;font-family:sans-serif;">
          ⚠️ <strong>kazma_core.swarm is not installed.</strong>
          Workers can be registered, but they won't execute tasks.
          Install: <code>pip install kazma-core[swarm]</code>
        </div>"""

    worker_rows = ""
    for name, w in sorted(_workers.items()):
        status = _worker_status(w)
        color = {"online": "#28a745", "offline": "#dc3545", "busy": "#ffc107"}.get(
            status, "#6c757d"
        )
        worker_rows += f"""
        <tr>
          <td>{name}</td>
          <td>{w.get('model', '?')}</td>
          <td>{w.get('provider', '?')}</td>
          <td>{w.get('type', 'in-process')}</td>
          <td><span style="color:{color};font-weight:bold;">● {status}</span></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kazma — Swarm Panel</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117; color: #c9d1d9; padding: 24px; }}
    h1 {{ color: #58a6ff; margin-bottom: 8px; }}
    h2 {{ color: #8b949e; font-weight: 400; margin-bottom: 24px; }}
    .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
              padding: 20px; margin-bottom: 20px; }}
    .card h3 {{ color: #e6edf3; margin-bottom: 12px; }}
    label {{ display: block; margin: 8px 0 4px; color: #8b949e; font-size: 0.9em; }}
    input, select, textarea {{
      width: 100%; padding: 8px 12px; background: #0d1117; border: 1px solid #30363d;
      border-radius: 4px; color: #c9d1d9; font-size: 14px;
    }}
    button {{ padding: 8px 20px; border: none; border-radius: 4px; font-size: 14px;
              cursor: pointer; margin-right: 8px; margin-top: 8px; }}
    .btn-primary {{ background: #238636; color: #fff; }}
    .btn-danger {{ background: #da3633; color: #fff; }}
    .btn-warning {{ background: #d29922; color: #000; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #30363d; color: #8b949e; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
    .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
    .dot-green {{ background: #28a745; }}
    .dot-red {{ background: #dc3545; }}
    .dot-yellow {{ background: #ffc107; }}
    .row {{ display: flex; gap: 20px; flex-wrap: wrap; }}
    .col {{ flex: 1; min-width: 300px; }}
    .toast {{ position: fixed; top: 20px; right: 20px; padding: 12px 20px;
              border-radius: 6px; font-size: 14px; z-index: 1000; display: none; }}
    .toast-success {{ background: #238636; color: #fff; }}
    .toast-error {{ background: #da3633; color: #fff; }}
  </style>
</head>
<body>
  <h1>🐝 Kazma Swarm Panel</h1>
  <h2>Multi-worker AI agent orchestration</h2>

  {setup_banner}

  <div class="row">
    <div class="col">
      <div class="card">
        <h3>⚙️ Controls</h3>
        <button class="btn-primary" onclick="swarmAction('start')">▶ Start All</button>
        <button class="btn-danger" onclick="swarmAction('stop')">⏹ Stop All</button>
        <span id="swarm-status" style="margin-left:12px;color:#8b949e;"></span>
      </div>

      <div class="card">
        <h3>👷 Workers</h3>
        <table>
          <thead><tr>
            <th>Name</th><th>Model</th><th>Provider</th><th>Type</th><th>Status</th>
          </tr></thead>
          <tbody id="worker-table">{worker_rows or '<tr><td colspan="5" style="color:#8b949e;">No workers registered</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <div class="col">
      <div class="card">
        <h3>➕ Add Worker</h3>
        <label>Name</label>
        <input id="add-name" placeholder="worker-1">
        <label>Model</label>
        <select id="add-model">
          {''.join(f'<option value="{m}">{m}</option>' for m in _SUPPORTED_MODELS)}
        </select>
        <label>Provider</label>
        <select id="add-provider">
          {''.join(f'<option value="{p}">{p}</option>' for p in _SUPPORTED_PROVIDERS)}
        </select>
        <label>Bot Token (optional)</label>
        <input id="add-token" placeholder="telegram-bot-token" type="password">
        <label>Type</label>
        <select id="add-type">
          <option value="in-process">In-Process</option>
          <option value="telegram">Telegram</option>
        </select>
        <button class="btn-primary" onclick="addWorker()">Add Worker</button>
        <button class="btn-danger" onclick="removeWorker()">Remove</button>
      </div>

      <div class="card">
        <h3>📤 Dispatch Task</h3>
        <label>Worker(s) — comma separated</label>
        <input id="dispatch-workers" placeholder="worker-1, worker-2">
        <label>Task</label>
        <textarea id="dispatch-task" rows="3" placeholder="Describe the task..."></textarea>
        <label>Context (optional)</label>
        <input id="dispatch-context" placeholder="Extra context...">
        <button class="btn-primary" onclick="dispatchTask()">Send Task</button>
      </div>
    </div>
  </div>

  <div id="toast" class="toast"></div>

  <script>
    function showToast(msg, ok) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = 'toast ' + (ok ? 'toast-success' : 'toast-error');
      t.style.display = 'block';
      setTimeout(() => t.style.display = 'none', 3000);
    }}

    async function swarmAction(action) {{
      try {{
        const r = await fetch('/api/swarm/' + action, {{ method: 'POST' }});
        const d = await r.json();
        showToast(d.message || d.status, r.ok);
        setTimeout(() => location.reload(), 500);
      }} catch(e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function addWorker() {{
      const payload = {{
        name: document.getElementById('add-name').value,
        model: document.getElementById('add-model').value,
        provider: document.getElementById('add-provider').value,
        bot_token: document.getElementById('add-token').value || null,
        type: document.getElementById('add-type').value,
      }};
      try {{
        const r = await fetch('/api/swarm/workers', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        const d = await r.json();
        showToast(d.message || 'Worker added', r.ok);
        if (r.ok) setTimeout(() => location.reload(), 500);
      }} catch(e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function removeWorker() {{
      const name = document.getElementById('add-name').value;
      if (!name) {{ showToast('Enter worker name to remove', false); return; }}
      try {{
        const r = await fetch('/api/swarm/workers/' + encodeURIComponent(name), {{ method: 'DELETE' }});
        const d = await r.json();
        showToast(d.message || 'Worker removed', r.ok);
        if (r.ok) setTimeout(() => location.reload(), 500);
      }} catch(e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function dispatchTask() {{
      const workers = document.getElementById('dispatch-workers').value
        .split(',').map(s => s.trim()).filter(Boolean);
      const payload = {{
        workers: workers,
        task: document.getElementById('dispatch-task').value,
        context: document.getElementById('dispatch-context').value,
      }};
      try {{
        const r = await fetch('/api/swarm/dispatch', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        const d = await r.json();
        showToast(d.message || d.status, r.ok);
      }} catch(e) {{ showToast('Network error: ' + e, false); }}
    }}
  </script>
</body>
</html>"""
