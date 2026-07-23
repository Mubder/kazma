"""General routes for the Swarm panel.

Extracted from the original god module swarm_panel.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from kazma_ui.services import get_swarm_service

logger = logging.getLogger(__name__)

__all__ = ["register_general_routes"]

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _registry_options() -> dict[str, Any] | None:
    """Return supporting models and providers from registry."""
    try:
        from kazma_core.model_registry import get_model_registry
        return get_model_registry().list_unified_options()
    except Exception:
        logger.warning("[Swarm] Failed to read unified model options", exc_info=True)
        return None


def _workflow_to_mermaid(workflow: Any) -> str:
    """Generate a Mermaid.js diagram string from a DAGWorkflow."""
    import html as _html

    lines = ["graph TD"]
    # Style definitions for luxury, enterprise feel
    lines.append("    classDef agent fill:#1a1b26,stroke:#7aa2f7,stroke-width:2px,color:#c0caf5;")
    lines.append("    classDef router fill:#1e1e2e,stroke:#f5c2e7,stroke-width:2px,color:#cdd6f4;")
    lines.append("    classDef default fill:#15161e,stroke:#414868,stroke-width:1px,color:#a9b1d6;")

    def _safe_id(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in name)

    def _mermaid_escape(s: str) -> str:
        """Escape HTML entities and Mermaid metacharacters for safe labels."""
        return _html.escape(s, quote=True).replace("&#x27;", "'").replace("]", "&#93;").replace("|", "&#124;")

    # Add nodes
    for node_id, node in workflow.nodes.items():
        node_type = getattr(node, "type", "dispatch")
        label = f'"{_mermaid_escape(node_id)}<br/><small style=\'color:#565f89;\'>{_mermaid_escape(node_type)}</small>"'
        safe_node_id = _safe_id(node_id)
        lines.append(f"    {safe_node_id}[{label}]")
        
        # Apply style classes
        if "router" in node_type.lower() or "dispatch" in node_type.lower() or "decision" in node_type.lower():
            lines.append(f"    class {safe_node_id} router;")
        else:
            lines.append(f"    class {safe_node_id} agent;")

    # Add edges with conditions
    for edge in workflow.edges:
        src = edge.from_node
        tgt = edge.to_node
        safe_src = _safe_id(src)
        safe_tgt = _safe_id(tgt)
        cond = getattr(edge, "condition", "")
        if cond:
            lines.append(f'    {safe_src} -->|"{_mermaid_escape(cond)}"| {safe_tgt}')
        else:
            lines.append(f"    {safe_src} --> {safe_tgt}")

    return "\n".join(lines)


def register_general_routes(
    router: APIRouter,
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> None:
    """Register all general-purpose routes."""

    @router.get("/swarm", response_class=HTMLResponse)
    async def swarm_page(request: Request) -> HTMLResponse:
        """Render the Swarm panel."""
        svc = get_swarm_service()
        # Resolve engine at request time using the services facade
        svc.resolve_engine(swarm_manager)

        workers = svc.list_workers()
        started = svc.is_started()
        template_path = _TEMPLATE_DIR / "swarm.html"
        if template_path.exists():
            return cast(
                HTMLResponse,
                templates.TemplateResponse(
                    request,
                    "swarm.html",
                    {
                        "workers": workers,
                        "worker_count": len(workers),
                        "started": started,
                        "has_swarm_core": svc.has_swarm_core(),
                        "config": None,
                        "active_page": "swarm",
                    },
                ),
            )

        return HTMLResponse(_fallback_html(svc.has_swarm_core(), workers))

    @router.get("/api/swarm/status")
    async def swarm_status() -> dict[str, Any]:
        """Return current worker status."""
        svc = get_swarm_service()
        svc.resolve_engine(swarm_manager)

        workers = svc.list_workers()
        result: dict[str, Any] = {
            "workers": workers,
            "count": len(workers),
            "started": svc.is_started(),
            "has_swarm_core": svc.has_swarm_core(),
            "setup_instructions": None,
        }
        if not svc.has_swarm_core():
            result["setup_instructions"] = (
                "kazma_core.swarm is not installed. "
                "Install with: pip install kazma-core[swarm] "
                "or add kazma_core.swarm to your project."
            )
        return result

    @router.post("/api/swarm/workflows/validate")
    async def validate_workflow(request: Request) -> JSONResponse:
        """Validate and parse a YAML/JSON DAG workflow definition."""
        from pydantic import ValidationError
        import yaml
        
        try:
            from kazma_core.swarm.dag_schema import DAGWorkflow
        except ImportError:
            return JSONResponse(
                content={"valid": False, "error": "DAGWorkflow schema is not available in kazma_core"},
                status_code=400,
            )

        try:
            body = await request.json()
        except Exception as _e:
            logger.debug("[Swarm] invalid JSON in DAG validate: %s", _e)
            return JSONResponse(
                content={"valid": False, "error": "Invalid request body; must be valid JSON"},
                status_code=400,
            )

        definition_str = body.get("workflow_definition")
        if not definition_str:
            try:
                workflow = DAGWorkflow(**body)
                return JSONResponse(
                    content={
                        "valid": True,
                        "workflow": workflow.model_dump(),
                        "nodes": [n.model_dump() for n in workflow.nodes.values()],
                        "edges": [e.model_dump() for e in workflow.edges],
                        "mermaid": _workflow_to_mermaid(workflow),
                    }
                )
            except ValidationError:
                return JSONResponse(
                    content={"valid": False, "error": "Workflow validation failed. Check node and edge definitions."},
                    status_code=422,
                )
            except Exception:
                return JSONResponse(
                    content={"valid": False, "error": "Validation error. Check workflow definition syntax."},
                    status_code=422,
                )

        try:
            parsed_data = yaml.safe_load(definition_str)
            if not isinstance(parsed_data, dict):
                return JSONResponse(
                    content={"valid": False, "error": "Parsed workflow must be a top-level JSON/YAML object/dictionary"},
                    status_code=422,
                )
            
            workflow = DAGWorkflow(**parsed_data)
            return JSONResponse(
                content={
                    "valid": True,
                    "workflow": workflow.model_dump(),
                    "nodes": [n.model_dump() for n in workflow.nodes.values()],
                    "edges": [e.model_dump() for e in workflow.edges],
                    "mermaid": _workflow_to_mermaid(workflow),
                }
            )
        except yaml.YAMLError:
            return JSONResponse(
                content={"valid": False, "error": "YAML/JSON syntax error. Check workflow definition format."},
                status_code=422,
            )
        except ValidationError:
            return JSONResponse(
                content={"valid": False, "error": "Workflow validation failed. Check node and edge definitions."},
                status_code=422,
            )
        except Exception:
            return JSONResponse(
                content={"valid": False, "error": "Validation error. Check workflow definition syntax."},
                status_code=422,
            )

    @router.get("/api/swarm/output-target")
    async def get_output_target() -> JSONResponse:
        """Return the current swarm output-routing target."""
        svc = get_swarm_service()
        if svc.get_config_store() is None:
            return JSONResponse(
                {"status": "error", "message": "Config store unavailable"},
                status_code=503,
            )
        target = svc.get_output_target()
        # Mask bot_token in GET response to avoid exposing it in the API
        if target.get("bot_token"):
            target = {**target, "bot_token": "***"}
        # Serialize chat_id as a string so large Telegram supergroup IDs
        # (>2^53) survive JSON.parse on the client without precision loss.
        if target["chat_id"] is not None:
            target = {**target, "chat_id": str(target["chat_id"])}
        return JSONResponse({"output_target": target})

    @router.put("/api/swarm/output-target")
    async def set_output_target(payload: dict[str, Any]) -> JSONResponse:
        """Set or clear the swarm output-routing target."""
        svc = get_swarm_service()
        if svc.get_config_store() is None:
            return JSONResponse(
                {"status": "error", "message": "Config store unavailable"},
                status_code=503,
            )
        try:
            target = svc.set_output_target(payload)
            # Mask bot_token in response to avoid exposing it
            bot_token = target.get("bot_token")
            response_target = {**target, "bot_token": "***" if bot_token else ""}
            return JSONResponse({"status": "ok", "output_target": response_target})
        except ValueError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @router.get("/api/swarm/models")
    async def swarm_models() -> dict[str, Any]:
        """Return supported models and providers."""
        options = _registry_options()
        if options is not None:
            return {
                "models": options.get("models", []),
                "providers": options.get("providers", []),
                "provider_entries": options.get("provider_entries", []),
                "provider_models": options.get("provider_models", {}),
                "profiles": options.get("profiles", []),
                "defaults": options.get("defaults", {}),
                "source": "registry",
            }
        return {
            "models": [],
            "providers": [],
            "provider_entries": [],
            "provider_models": {},
            "profiles": [],
            "defaults": {},
            "source": "unavailable",
        }

    @router.get("/api/swarm/templates")
    async def list_templates() -> JSONResponse:
        """List all worker templates for auto-scaling."""
        svc = get_swarm_service()
        svc.resolve_engine(swarm_manager)
        scaler = svc.get_autoscaler()
        if scaler is None:
            return JSONResponse({"templates": [], "instances": []})
        return JSONResponse({
            "templates": [t.to_dict() for t in scaler.list_templates()],
            "instances": scaler.get_instance_info(),
        })

    @router.post("/api/swarm/templates")
    async def add_template(payload: dict[str, Any]) -> JSONResponse:
        """Register a new worker template."""
        svc = get_swarm_service()
        svc.resolve_engine(swarm_manager)
        scaler = svc.get_autoscaler()
        if scaler is None:
            return JSONResponse({"status": "error", "message": "AutoScaler not available"}, status_code=503)
        try:
            from kazma_core.swarm.autoscaler import WorkerTemplate
            template = WorkerTemplate.from_dict(payload)
            if not template.name:
                return JSONResponse({"status": "error", "message": "Template name required"}, status_code=400)
            scaler.register_template(template)
            scaler.save_templates()
            return JSONResponse({"status": "ok", "template": template.to_dict()})
        except Exception as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @router.delete("/api/swarm/templates/{name}")
    async def delete_template(name: str) -> JSONResponse:
        """Remove a template and reap its instances."""
        svc = get_swarm_service()
        svc.resolve_engine(swarm_manager)
        scaler = svc.get_autoscaler()
        if scaler is None:
            return JSONResponse({"status": "error", "message": "AutoScaler not available"}, status_code=503)
        try:
            scaler.unregister_template(name)
            scaler.save_templates()
            return JSONResponse({"status": "ok"})
        except Exception as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @router.post("/api/swarm/autoscaler/reap")
    async def reap_idle_workers() -> JSONResponse:
        """Trigger idle worker reaping."""
        svc = get_swarm_service()
        svc.resolve_engine(swarm_manager)
        scaler = svc.get_autoscaler()
        if scaler is None:
            return JSONResponse({"status": "error", "message": "AutoScaler not available"}, status_code=503)
        try:
            reaped = scaler.reap_idle()
            return JSONResponse({"status": "ok", "reaped": reaped})
        except Exception as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


def _fallback_html(has_core: bool, workers: list[dict[str, Any]]) -> str:
    """Inline HTML fallback for /swarm when the template is unavailable."""
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
    from html import escape as _html_escape
    for worker in sorted(workers, key=lambda item: item["name"]):
        color = {"online": "#28a745", "offline": "#dc3545", "busy": "#ffc107"}.get(
            worker["status"],
            "#6c757d",
        )
        worker_rows += f"""
        <tr>
          <td>{_html_escape(str(worker['name']))}</td>
          <td>{_html_escape(str(worker.get('model', '?')))}</td>
          <td>{_html_escape(str(worker.get('provider', '?')))}</td>
          <td>{_html_escape(str(worker.get('type', 'in-process')))}</td>
          <td><span style="color:{color};font-weight:bold;">● {_html_escape(str(worker['status']))}</span></td>
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
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #30363d; color: #8b949e; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
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
        <input id="add-model" placeholder="gpt-4o-mini">
        <label>Provider</label>
        <input id="add-provider" placeholder="openai">
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
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
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
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const d = await r.json();
        showToast(d.message || 'Worker added', r.ok);
        if (r.ok) setTimeout(() => location.reload(), 500);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function removeWorker() {{
      const name = document.getElementById('add-name').value;
      if (!name) {{ showToast('Enter worker name to remove', false); return; }}
      try {{
        const r = await fetch('/api/swarm/workers/' + encodeURIComponent(name), {{ method: 'DELETE' }});
        const d = await r.json();
        showToast(d.message || 'Worker removed', r.ok);
        if (r.ok) setTimeout(() => location.reload(), 500);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
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
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const d = await r.json();
        showToast(d.message || d.status, r.ok);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
    }}
  </script>
</body>
</html>"""
