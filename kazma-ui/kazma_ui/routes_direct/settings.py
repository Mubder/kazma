"""Settings read/write endpoints backed by ConfigStore.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Request
from kazma_core.background import spawn_background
from kazma_core.errors import safe_error

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["register_settings_routes"]


def register_settings_routes(self: Any) -> None:
    """Register the settings routes onto ``self.app``."""
    # ── Phase D: Memory backends Settings API ─────────────────────────
    @self.app.get("/api/settings/memory/merge-kb")
    async def _settings_memory_merge_kb_get():
        from kazma_core.config_store import get_config_store
        from kazma_core.memory.config import read_memory_cfg

        v2 = (read_memory_cfg() or {}).get("v2") or {}
        # Knowledge smart search lives under knowledge.* (ConfigStore)
        smart = False
        try:
            smart = bool(get_config_store().get("knowledge.smart_search") or False)
        except Exception:
            smart = False
        return {
            "merge_knowledge_into_chat": bool(v2.get("merge_knowledge_into_chat", True)),
            "promote_kb_to_episodes": bool(v2.get("promote_kb_to_episodes", True)),
            "explain_recall": bool(v2.get("explain_recall", True)),
            "smart_search": smart,
        }
    @self.app.put("/api/settings/memory/merge-kb")
    async def _settings_memory_merge_kb_put(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            return {"ok": False, "error": "invalid JSON"}
        try:
            from kazma_core.config_store import get_config_store

            store = get_config_store()
            items = []
            if "merge_knowledge_into_chat" in (body or {}):
                items.append(
                    (
                        "memory.v2.merge_knowledge_into_chat",
                        bool(body["merge_knowledge_into_chat"]),
                        "memory",
                    )
                )
            if "promote_kb_to_episodes" in (body or {}):
                items.append(
                    (
                        "memory.v2.promote_kb_to_episodes",
                        bool(body["promote_kb_to_episodes"]),
                        "memory",
                    )
                )
            if "explain_recall" in (body or {}):
                items.append(
                    (
                        "memory.v2.explain_recall",
                        bool(body["explain_recall"]),
                        "memory",
                    )
                )
            if "smart_search" in (body or {}):
                items.append(
                    (
                        "knowledge.smart_search",
                        bool(body["smart_search"]),
                        "knowledge",
                    )
                )
            if items and hasattr(store, "batch_set"):
                store.batch_set(items)
            else:
                for k, v, c in items:
                    store.set(k, v, category=c)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
    @self.app.get("/api/settings/memory/backends")
    async def _settings_memory_backends_get():
        from kazma_core.memory.backends import (
            get_backends_cfg,
            mask_backends_cfg,
            vector_capability,
        )

        cfg = get_backends_cfg()
        try:
            from kazma_core.memory.graph_backend import graph_capability
            from kazma_core.memory.state_backend import state_capability

            gcap = graph_capability(cfg)
            scap = state_capability(cfg)
        except Exception:
            gcap, scap = {}, {}
        return {
            "ok": True,
            "backends": mask_backends_cfg(cfg),
            "capability": vector_capability(cfg),
            "graph_capability": gcap,
            "state_capability": scap,
        }
    @self.app.put("/api/settings/memory/backends")
    async def _settings_memory_backends_put(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            return {"ok": False, "error": "invalid JSON"}
        try:
            from kazma_core.memory.backends import save_backends_cfg

            masked = save_backends_cfg(body if isinstance(body, dict) else {})
            return {"ok": True, "backends": masked}
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
    @self.app.post("/api/settings/memory/backends/test-embed")
    async def _settings_memory_test_embed():
        from kazma_core.memory.backends import test_embedder_backend

        return test_embedder_backend()
    @self.app.post("/api/settings/memory/backends/test-vector")
    async def _settings_memory_test_vector():
        from kazma_core.memory.backends import test_vector_backend

        return test_vector_backend()
    @self.app.post("/api/settings/memory/backends/test-neo4j")
    async def _settings_memory_test_neo4j(request: Request):
        """Probe Neo4j using saved config, or optional body override before save."""
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        from kazma_core.memory.backends import get_backends_cfg
        from kazma_core.memory.graph_backend import test_neo4j_connection

        cfg = get_backends_cfg()
        if isinstance(body, dict) and (body.get("graph") or body.get("url")):
            g = dict(cfg.get("graph") or {})
            saved_pw = str(g.get("password") or g.get("api_key") or "")
            if body.get("graph"):
                incoming = dict(body["graph"] or {})
                # UI masks secrets as "***" — never let that overwrite vault password
                ipw = incoming.get("password")
                if ipw is None or str(ipw).strip() in ("", "***") or str(ipw).strip().startswith("***"):
                    incoming.pop("password", None)
                iak = incoming.get("api_key")
                if iak is None or str(iak).strip() in ("", "***"):
                    incoming.pop("api_key", None)
                g.update(incoming)
            else:
                if body.get("url") is not None:
                    g["url"] = body["url"]
                if body.get("user") is not None:
                    g["user"] = body["user"]
                if body.get("password") is not None and str(body.get("password")) not in (
                    "",
                    "***",
                ):
                    g["password"] = body["password"]
            # Restore saved secret if client sent mask/empty
            if not str(g.get("password") or "").strip() or str(g.get("password")).strip() in (
                "***",
            ):
                if saved_pw and saved_pw not in ("***",):
                    g["password"] = saved_pw
            g["provider"] = "neo4j"
            cfg = {**cfg, "graph": g}
        return test_neo4j_connection(cfg)
    @self.app.post("/api/settings/memory/backends/sync-neo4j", dependencies=[Depends(rate_limit("admin_ops", 10))])
    async def _settings_memory_sync_neo4j():
        """Backfill active SQLite beliefs into Neo4j (needed once after enabling)."""
        from kazma_core.memory.graph_backend import sync_beliefs_to_neo4j

        return sync_beliefs_to_neo4j(tenant_id="default", limit=1000)
    @self.app.post("/api/settings/memory/backends/sync-postgres", dependencies=[Depends(rate_limit("admin_ops", 10))])
    async def _settings_memory_sync_postgres():
        """Backfill existing SQLite beliefs + episodes into the Postgres state mirror.

        The dual-mirror is write-forward only; this one-shot copies existing rows
        (and re-running re-syncs after edits). Idempotent. SQLite stays primary.
        """
        from kazma_core.memory.state_backend import backfill_state_mirror

        return backfill_state_mirror(tenant_id="default")
    @self.app.post("/api/settings/memory/backends/reset-local")
    async def _settings_memory_reset_local():
        from kazma_core.memory.backends import reset_backends_to_local

        return {"ok": True, "backends": reset_backends_to_local()}
    @self.app.post("/api/settings/memory/backends/rebuild", dependencies=[Depends(rate_limit("admin_ops", 10))])
    async def _settings_memory_rebuild():
        """Kick off embedding rebuild (reuses reembed module)."""
        try:
            import asyncio

            from kazma_core.memory.reembed import rebuild_embeddings

            spawn_background(asyncio.to_thread(rebuild_embeddings), name="memory-reembed")
            return {"ok": True, "started": True}
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
    @self.app.get("/api/settings/memory/backends/rebuild/status")
    async def _settings_memory_rebuild_status():
        from kazma_core.memory.reembed import get_rebuild_status

        return get_rebuild_status()
