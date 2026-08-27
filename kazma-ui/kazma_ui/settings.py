"""Settings management routes for the Kazma WebUI.

Provides a comprehensive 12-tab settings UI with real API endpoints
for providers, models, agent config, connectors, MCP, skills,
appearance, shortcuts, account, tools, system, and import/export.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from kazma_ui.rate_limit import rate_limit
from fastapi.templating import Jinja2Templates

from kazma_ui.models import (
    AgentConfigUpdate,
    AppearanceUpdate,
    ConnectorConfigUpdate,
    ConnectorTestRequest,
    ImportConfigRequest,
    MCPServerAddRequest,
    MCPServerToggleRequest,
    ModelCompareRequest,
    ModelDefaultUpdate,
    ModelTestRequest,
    PasswordChange,
    ProviderAddRequest,
    ProviderToggleRequest,
    SettingsUpdate,
    ShortcutUpdate,
    VoiceSettingsUpdate,
)

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent
    from kazma_core.config_store import ConfigStore

logger = logging.getLogger(__name__)

__all__ = ["SettingsRouterBuilder", "create_settings_router"]

# Keys whose values are secrets and must be masked in API responses.
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key", "apikey", "token", "secret", "password", "passphrase",
    "passwd", "credential", "private_key", "authorization", "webhook",
    "pat", "bearer",
)


def _mask_sensitive_values(data: dict[str, dict[str, Any]]) -> None:
    """Recursively mask values whose key name looks like a secret."""
    import json
    try:
        from kazma_core.config_store import is_sensitive_config_key
    except Exception:
        is_sensitive_config_key = lambda k: False  # noqa: E731

    for category, settings_dict in data.items():
        if not isinstance(settings_dict, dict):
            continue
        for key, val in list(settings_dict.items()):
            key_lower = key.lower()
            sensitive = any(frag in key_lower for frag in _SENSITIVE_KEY_FRAGMENTS)
            try:
                sensitive = sensitive or bool(is_sensitive_config_key(key))
            except Exception:
                pass
            if not sensitive:
                continue
            # Only mask non-empty string values — constant *** (no last-4).
            raw = val
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(raw, str) and raw.strip():
                settings_dict[key] = "***"
            elif isinstance(raw, dict):
                for sub_k, sub_v in list(raw.items()):
                    if isinstance(sub_v, str) and sub_v.strip():
                        raw[sub_k] = "***"
                settings_dict[key] = json.dumps(raw)


class SettingsRouterBuilder:
    """Builder that decomposes the massive settings router into modular sub-routers."""

    def __init__(self, agent: KazmaAgent, config_store: ConfigStore, templates: Jinja2Templates) -> None:
        self.agent = agent
        self.config_store = config_store
        self.templates = templates

        self.router = APIRouter(tags=["settings"])
        self.providers_router = APIRouter()
        self.models_router = APIRouter()
        self.mcp_router = APIRouter()
        self.general_router = APIRouter()

        # Lazily initialized SettingsManager
        self._sm = None

    def _get_sm(self):
        if self._sm is None:
            from kazma_core.settings_manager import SettingsManager
            self._sm = SettingsManager(self.config_store)
        return self._sm

    def _build_general_routes(self) -> None:
        router = self.general_router
        _get_sm = self._get_sm
        agent = self.agent
        config_store = self.config_store
        templates = self.templates

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page(request: Request) -> HTMLResponse:
            """Render the settings page."""
            # Use the agent's facade method to avoid direct llm_config access.
            llm_cfg = agent.get_llm_config()
            try:
                from kazma_core.model_registry import get_model_registry
                reg = get_model_registry()
                profile = reg.get_active_profile()
                # Never embed raw API keys in HTML — mask like the API paths.
                _raw_key = profile.get("api_key") or llm_cfg.get("api_key") or ""
                model_settings = {
                    "base_url": profile.get("base_url") or llm_cfg["base_url"],
                    "api_key": ("***" if _raw_key else ""),
                    "model": profile.get("model") or llm_cfg["model"],
                    "max_tokens": config_store.get("llm.max_tokens", llm_cfg["max_tokens"]),
                    "temperature": config_store.get("llm.temperature", llm_cfg["temperature"]),
                    "timeout": config_store.get("llm.timeout", llm_cfg["timeout"]),
                }
            except RuntimeError:
                model_settings = {
                    "base_url": config_store.get("llm.base_url", llm_cfg["base_url"]),
                    "api_key": "***" if config_store.get("llm.api_key", llm_cfg["api_key"]) else "",
                    "model": config_store.get("llm.model", llm_cfg["model"]),
                    "max_tokens": config_store.get("llm.max_tokens", llm_cfg["max_tokens"]),
                    "temperature": config_store.get("llm.temperature", llm_cfg["temperature"]),
                    "timeout": config_store.get("llm.timeout", llm_cfg["timeout"]),
                }
            agent_settings = {
                "name": config_store.get("agent.name", agent.config.name),
                "language": config_store.get("agent.language", agent.config.language),
                "system_prompt": config_store.get("agent.system_prompt", agent.system_prompt),
                "max_iterations": config_store.get("agent.max_iterations", 15),
            }
            connector_settings = {
                "telegram_token": "***" if config_store.get("connectors.telegram.token", "") else "",
                "telegram_allowed_users": config_store.get("connectors.telegram.allowed_users", ""),
                "discord_token": "***" if config_store.get("connectors.discord.token", "") else "",
                "slack_token": "***" if config_store.get("connectors.slack.token", "") else "",
                "slack_app_token": "***" if config_store.get("connectors.slack.app_token", "") else "",
            }

            return templates.TemplateResponse(
                request,
                "settings.html",
                {
                    "model": model_settings,
                    "agent": agent_settings,
                    "connectors": connector_settings,
                    "config": agent.config,
                    "active_page": "settings",
                },
            )

        @router.get("/api/settings")
        async def api_get_all_settings() -> dict[str, dict[str, Any]]:
            """Get all settings grouped by category (secrets masked)."""
            data = config_store.get_all()
            _mask_sensitive_values(data)
            return data

        @router.get("/api/settings/vault/status")
        async def api_vault_status() -> dict[str, Any]:
            """Check if the encrypted secret vault is enabled."""
            from kazma_core.security.vault import get_vault
            vault = get_vault()
            return {
                "enabled": vault is not None,
                "secret_count": len(vault.list_secrets()) if vault else 0,
            }

        @router.get("/api/notifications/turn-complete")
        async def api_get_turn_notify() -> dict[str, Any]:
            """Task-completion desktop-notification gate (Turn Delivery V2 P4).

            Live-read, never raises (mirrors get_lifecycle_config). The chat
            client consults this once at boot; the Settings toggle writes it
            via PUT /api/settings/single with key ``notifications.turn_complete``
            ('1'/'0') and mirrors it into localStorage for instant effect on
            already-open tabs.
            """
            try:
                raw = config_store.get("notifications.turn_complete")
                enabled = True if raw is None else str(raw).strip().lower() not in (
                    "0", "false", "off", "no",
                )
            except Exception:
                logger.debug("turn-complete config read failed; defaulting on", exc_info=True)
                enabled = True
            return {"enabled": bool(enabled)}

        @router.get("/api/push/vapid-public-key")
        async def api_push_vapid_key() -> dict[str, Any]:
            """VAPID application server key for Web Push subscription (P5)."""
            from kazma_ui.push import get_vapid_public_key, push_available

            return {
                "available": push_available(),
                "public_key": get_vapid_public_key(),
            }

        @router.post("/api/push/subscribe")
        async def api_push_subscribe(request: Request) -> dict[str, Any]:
            """Persist a browser PushSubscription JSON (P5)."""
            from kazma_ui.push import push_available, subscribe

            if not push_available():
                return {"status": "error", "error": "push support not installed"}
            try:
                body = await request.json()
            except Exception:
                return {"status": "error", "error": "invalid JSON"}
            return subscribe(body.get("subscription") or body)

        @router.post("/api/push/unsubscribe")
        async def api_push_unsubscribe(request: Request) -> dict[str, Any]:
            """Remove a browser PushSubscription by endpoint (P5)."""
            from kazma_ui.push import unsubscribe

            try:
                body = await request.json()
            except Exception:
                return {"status": "error", "error": "invalid JSON"}
            return unsubscribe(str((body.get("endpoint")) or ""))

        @router.get("/sw.js")
        async def api_service_worker() -> Response:
            """Serve the Web Push service worker at ROOT scope.

            Service worker scope = script URL directory, so /static/sw.js
            could never notify on /chat. Must be served from the origin root
            with the JS MIME type.
            """
            sw_path = Path(__file__).resolve().parent / "static" / "sw.js"
            try:
                body = sw_path.read_text(encoding="utf-8")
            except Exception:
                return Response("// service worker unavailable", media_type="application/javascript")
            return Response(
                content=body,
                media_type="application/javascript",
                headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
            )

        @router.get("/api/settings/export")
        async def api_export_yaml(fmt: str = Query("yaml", alias="format")) -> Response:
            """Export settings as YAML or JSON file download (secrets masked)."""
            sm = _get_sm()
            try:
                content = sm.export_config(fmt, mask_secrets=True)
                media = "application/json" if fmt == "json" else "text/yaml"
                ext = "json" if fmt == "json" else "yaml"
                return Response(
                    content=content,
                    media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=kazma-config.{ext}"},
                )
            except Exception as e:
                logger.error("Failed to export: %s", e)
                return Response(content="Error: Unable to export settings", media_type="text/plain", status_code=500)

        @router.put("/api/settings")
        async def api_update_settings(updates: list[SettingsUpdate]) -> dict[str, str]:
            """Update multiple settings at once (atomic batch)."""
            items = [(u.key, u.value, u.category) for u in updates]
            count = config_store.batch_set(items)
            return {"status": "ok", "updated": str(count)}

        @router.put("/api/settings/single")
        async def api_update_single(setting: SettingsUpdate) -> dict[str, str]:
            """Update a single setting."""
            config_store.set(setting.key, setting.value, category=setting.category)
            return {"status": "ok"}

        @router.get("/api/settings/backup/offsite")
        async def api_get_offsite_config() -> dict[str, Any]:
            """Get the current offsite backup configuration + provider statuses."""
            from kazma_core.config_store import get_config_store as _gcs
            store = _gcs()
            provider = str(store.get("backups.offsite.provider") or "")
            remote = str(store.get("backups.offsite.rclone_remote") or "")
            enabled = store.get("backups.offsite.enabled")
            if enabled is None:
                enabled = bool(provider or remote)

            # Collect statuses from all native providers
            providers_status: list[dict[str, Any]] = []
            try:
                from kazma_core.backup.cloud_sync import (
                    FTPSync,
                    GoogleDriveSync,
                    OneDriveSync,
                    S3Sync,
                    WebDAVSync,
                )
                for cls in (GoogleDriveSync, OneDriveSync, WebDAVSync, FTPSync, S3Sync):
                    try:
                        providers_status.append(cls().status())
                    except Exception:
                        providers_status.append({
                            "provider": cls.__name__,
                            "connected": False,
                            "remote": "",
                            "error": "status check failed",
                        })
            except ImportError:
                pass

            def _vault_has(key: str) -> bool:
                try:
                    from kazma_core.security.vault import get_vault as _gv
                    _v = _gv()
                    return bool(_v is not None and _v.retrieve(key))
                except Exception:
                    return False

            return {
                "provider": provider,
                "enabled": bool(enabled),
                "providers": providers_status,
                "webdav": {
                    "url": str(store.get("backups.offsite.webdav.url") or ""),
                    "username": str(store.get("backups.offsite.webdav.username") or ""),
                    "password_set": _vault_has("backups.offsite.webdav.password"),
                },
                "ftp": {
                    "host": str(store.get("backups.offsite.ftp.host") or ""),
                    "port": str(store.get("backups.offsite.ftp.port") or "21"),
                    "username": str(store.get("backups.offsite.ftp.username") or ""),
                    "path": str(store.get("backups.offsite.ftp.path") or ""),
                    "password_set": _vault_has("backups.offsite.ftp.password"),
                },
                "s3": {
                    "access_key": str(store.get("backups.offsite.s3.access_key") or ""),
                    "bucket": str(store.get("backups.offsite.s3.bucket") or ""),
                    "endpoint": str(store.get("backups.offsite.s3.endpoint") or ""),
                    "region": str(store.get("backups.offsite.s3.region") or "us-east-1"),
                    "secret_key_set": _vault_has("backups.offsite.s3.secret_key"),
                },
                # Legacy rclone fields kept for backward compat
                "rclone_remote": remote,
            }

        def _store_vault_global(key: str, value: str, category: str = "backups") -> None:
            """Store a NAS password in the vault's GLOBAL scope.

            The backup sync path (server background) reads the global scope,
            but this endpoint runs inside a web request with tenant 'default'
            active — without the pin the password lands in a scope the backup
            can never read (same class of split that broke Gmail token
            refresh, incident 2026-08-16).
            """
            from kazma_core.tenant_context import (
                reset_current_tenant_id,
                set_current_tenant_id,
            )

            token = set_current_tenant_id(None)
            try:
                from kazma_core.security.vault import get_vault as _gv

                _v = _gv()
                if _v is not None:
                    _v.store(key, value, category=category)
            finally:
                reset_current_tenant_id(token)

        def _apply_offsite_payload(req: dict[str, Any]) -> None:
            """Persist provider + credentials from the offsite settings form."""
            from kazma_core.config_store import get_config_store as _gcs
            store = _gcs()

            provider = str(req.get("provider") or "").strip()
            store.set("backups.offsite.provider", provider, category="backups")
            enabled = req.get("enabled")
            if enabled is not None:
                store.set("backups.offsite.enabled", bool(enabled), category="backups")

            # WebDAV credentials (password to vault; blank keeps the old value)
            webdav_url = str(req.get("webdav_url") or "").strip()
            if webdav_url:
                store.set("backups.offsite.webdav.url", webdav_url, category="backups")
            webdav_user = str(req.get("webdav_username") or "").strip()
            if webdav_user:
                store.set("backups.offsite.webdav.username", webdav_user, category="backups")
            webdav_pass = str(req.get("webdav_password") or "")
            if webdav_pass:
                try:
                    _store_vault_global("backups.offsite.webdav.password", webdav_pass)
                except Exception:
                    pass

            # FTP credentials (password to vault; blank keeps the old value)
            ftp_host = str(req.get("ftp_host") or "").strip()
            if ftp_host:
                store.set("backups.offsite.ftp.host", ftp_host, category="backups")
            ftp_port = str(req.get("ftp_port") or "").strip()
            if ftp_port:
                store.set("backups.offsite.ftp.port", ftp_port, category="backups")
            ftp_user = str(req.get("ftp_username") or "").strip()
            if ftp_user:
                store.set("backups.offsite.ftp.username", ftp_user, category="backups")
            ftp_path = str(req.get("ftp_path") or "").strip()
            if ftp_path:
                store.set("backups.offsite.ftp.path", ftp_path, category="backups")
            ftp_pass = str(req.get("ftp_password") or "")
            if ftp_pass:
                try:
                    _store_vault_global("backups.offsite.ftp.password", ftp_pass)
                except Exception:
                    pass

            # S3 credentials (secret to vault)
            s3_key = str(req.get("s3_access_key") or "").strip()
            if s3_key:
                store.set("backups.offsite.s3.access_key", s3_key, category="backups")
            s3_bucket = str(req.get("s3_bucket") or "").strip()
            if s3_bucket:
                store.set("backups.offsite.s3.bucket", s3_bucket, category="backups")
            s3_endpoint = str(req.get("s3_endpoint") or "").strip()
            if s3_endpoint:
                store.set("backups.offsite.s3.endpoint", s3_endpoint, category="backups")
            s3_region = str(req.get("s3_region") or "").strip()
            if s3_region:
                store.set("backups.offsite.s3.region", s3_region, category="backups")
            s3_secret = str(req.get("s3_secret_key") or "")
            if s3_secret:
                try:
                    _store_vault_global("backups.offsite.s3.secret_key", s3_secret)
                except Exception:
                    pass

            # Legacy rclone remote
            rclone = str(req.get("rclone_remote") or "").strip()
            if rclone:
                store.set("backups.offsite.rclone_remote", rclone, category="backups")

        @router.put("/api/settings/backup/offsite")
        async def api_set_offsite_config(req: dict[str, Any]) -> dict[str, str]:
            """Configure the offsite backup provider."""
            _apply_offsite_payload(req)
            return {"status": "ok"}

        @router.post("/api/settings/backup/offsite/test")
        async def api_test_offsite_remote(req: dict[str, Any]) -> dict[str, Any]:
            """Test the cloud provider connection."""
            provider_name = str(req.get("provider") or "").strip()
            if not provider_name:
                # Legacy: test rclone remote
                remote = str(req.get("rclone_remote") or "").strip()
                if not remote:
                    return {"ok": False, "error": "No provider or remote specified"}
                import shutil as _shutil
                import asyncio as _aio
                rclone = _shutil.which("rclone")
                if not rclone:
                    return {"ok": False, "error": "rclone is not installed. Use a native provider instead."}
                import subprocess as _sp
                try:
                    proc = await _aio.to_thread(
                        _sp.run, [rclone, "lsd", remote, "--max-depth", "1"],
                        capture_output=True, text=True, timeout=15,
                    )
                    if proc.returncode == 0:
                        return {"ok": True, "message": "Connection successful"}
                    return {"ok": False, "error": f"rclone: {(proc.stderr or '')[:200]}"}
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}

            # Native provider test — persist the form first so freshly-typed
            # credentials are what gets tested, then call test_connection().
            try:
                from kazma_core.backup.cloud_sync import get_sync_provider
                _apply_offsite_payload(req)
                provider = get_sync_provider()
                if provider is None:
                    return {"ok": False, "error": f"Unknown provider: {provider_name}"}
                return await provider.test_connection()
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @router.get("/api/settings/backup/retention")
        async def api_get_backup_retention() -> dict[str, Any]:
            """Effective universal-backup retention (env override wins)."""
            from kazma_core.backup.universal import _read_retention

            return {"retention": _read_retention()}

        @router.put("/api/settings/backup/retention")
        async def api_set_backup_retention(req: dict[str, Any]) -> dict[str, str]:
            """Persist backups.retention (number of local backups to keep)."""
            from kazma_core.config_store import get_config_store as _gcs

            try:
                value = max(1, int(req.get("retention")))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return {"status": "error", "error": "retention must be a number >= 1"}
            _gcs().set("backups.retention", value, category="backups")
            return {"status": "ok"}

        @router.get("/api/settings/agent")
        async def api_get_agent() -> dict[str, Any]:
            """Get agent configuration (name, language, max tool rounds, …)."""
            return _get_sm().get_agent_config()

        @router.put("/api/settings/agent")
        async def api_update_agent(req: AgentConfigUpdate) -> dict[str, str]:
            """Update agent configuration."""
            sm = _get_sm()
            data = {k: v for k, v in req.model_dump().items() if v is not None}
            sm.save_agent_config(data)
            return {"status": "ok"}

        @router.get("/api/settings/agent/personalities")
        async def api_get_personalities() -> list[dict[str, Any]]:
            """List available personality templates."""
            return _get_sm().get_personalities()

        @router.put("/api/settings/agent/safety")
        async def api_save_safety(req: dict[str, Any]) -> dict[str, str]:
            """Save safety/HITL settings."""
            _get_sm().save_safety_settings(req)
            return {"status": "ok"}

        @router.get("/api/settings/agent/safety")
        async def api_get_safety() -> dict[str, Any]:
            """Get HITL safety settings (short keys for JS model)."""
            return _get_sm().get_safety_settings()

        @router.get("/api/settings/system/logging")
        async def api_get_logging() -> dict[str, Any]:
            """Get logging settings (level, format, rotation retention)."""
            return _get_sm().get_logging_settings()

        @router.put("/api/settings/system/logging")
        async def api_save_logging(req: dict[str, Any]) -> dict[str, str]:
            """Save logging settings.

            Level hot-applies; rotation/retention changes take effect on next
            server restart (Python logging handlers are configured at boot).
            """
            _get_sm().save_logging_settings(req)
            return {"status": "ok"}

        @router.get("/api/settings/proxy")
        async def api_get_proxy() -> dict[str, Any]:
            """Get proxy provider config (opt-in scraping resilience addon)."""
            return _get_sm().get_proxy_settings()

        @router.put("/api/settings/proxy")
        async def api_save_proxy(req: dict[str, Any]) -> dict[str, str]:
            """Save proxy provider config. Password auto-vault-encrypts."""
            _get_sm().save_proxy_settings(req)
            return {"status": "ok"}

        @router.post("/api/settings/proxy/test")
        async def api_test_proxy() -> dict[str, Any]:
            """Health-check the active proxy (returns exit IP)."""
            return await _get_sm().test_proxy()

        # ══════════════════════════════════════════════════════════════
        # EMBEDDER — memory vector model (Web UI Embedder settings page)
        # ══════════════════════════════════════════════════════════════

        @router.get("/api/settings/embedder")
        async def api_get_embedder() -> dict[str, Any]:
            """Get embedder status: effective config, persisted override,
            live singleton state, DB vector-space composition, presets."""
            from kazma_core.memory.embedder import get_embedder_status
            from kazma_core.memory.reembed import embedding_version_counts, get_rebuild_status

            status = get_embedder_status()
            store = _get_sm().get_embedder_settings()
            # DB counts open the DB read-only — cheap enough per page load.
            db = {}
            try:
                db = embedding_version_counts()
            except Exception:
                logger.debug("[Settings] embedder version counts failed", exc_info=True)
            return {
                "config": status.get("config", {}),
                "store": store,
                "active": status.get("active"),
                "db": db,
                "rebuild": get_rebuild_status(),
                "presets": [
                    {"model": "BAAI/bge-m3", "dim": 1024, "label": "BAAI/bge-m3 — multilingual (recommended)", "multilingual": True},
                    {"model": "BAAI/bge-large-en-v1.5", "dim": 1024, "label": "BAAI/bge-large-en-v1.5 — English", "multilingual": False},
                    {"model": "intfloat/multilingual-e5-large", "dim": 1024, "label": "intfloat/multilingual-e5-large", "multilingual": True},
                    {"model": "Snowflake/snowflake-arctic-embed-l", "dim": 1024, "label": "Snowflake arctic-embed-l (English)", "multilingual": False},
                    {"model": "nomic-ai/nomic-embed-text-v1.5", "dim": 768, "label": "nomic-embed-text-v1.5", "multilingual": False},
                    {"model": "sentence-transformers/paraphrase-multilingual-mistral", "dim": 768, "label": "paraphrase-multilingual-mistral", "multilingual": True},
                    {"model": "all-MiniLM-L6-v2", "dim": 384, "label": "all-MiniLM-L6-v2 — lightweight (legacy)", "multilingual": False},
                ],
            }

        @router.put("/api/settings/embedder")
        async def api_save_embedder(req: dict[str, Any]) -> dict[str, Any]:
            """Persist the embedder override (takes effect after restart)."""
            try:
                _get_sm().save_embedder_settings(req)
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}
            return {"status": "ok"}

        @router.post(
            "/api/settings/embedder/rebuild",
            dependencies=[Depends(rate_limit("admin_ops", 10))],
        )
        async def api_start_embedder_rebuild() -> dict[str, Any]:
            """Start a background embedding rebuild (incremental: only rows
            whose model version differs from the configured model)."""
            import asyncio

            from kazma_core.memory.embedder import get_embedding_model_name
            from kazma_core.memory.reembed import (
                REBUILD_STATUS_KEY,
                get_rebuild_status,
                rebuild_embeddings,
            )

            current = get_rebuild_status()
            if current.get("state") == "running":
                return {"status": "already", "detail": "A rebuild is already running."}
            from datetime import UTC, datetime

            model = get_embedding_model_name()
            now_iso = datetime.now(UTC).isoformat()
            _get_sm()._cs.set(
                REBUILD_STATUS_KEY,
                {
                    "state": "running",
                    "model": model,
                    "total": 0,
                    "done": 0,
                    "started_at": now_iso,
                    "finished_at": None,
                    "error": None,
                },
                category="embedding",
            )

            loop = asyncio.get_running_loop()

            def _progress(done: int, total: int) -> None:
                _get_sm()._cs.set(
                    REBUILD_STATUS_KEY,
                    {
                        "state": "running",
                        "model": model,
                        "total": total,
                        "done": done,
                        "started_at": get_rebuild_status().get("started_at"),
                        "finished_at": None,
                        "error": None,
                    },
                    category="embedding",
                )

            async def _run() -> None:
                try:
                    summary = await loop.run_in_executor(None, rebuild_embeddings, _progress)
                    _get_sm()._cs.set(
                        REBUILD_STATUS_KEY,
                        {
                            "state": "done",
                            "model": summary.get("model") or model,
                            "total": summary.get("episodes", 0) + summary.get("beliefs", 0),
                            "done": summary.get("episodes", 0) + summary.get("beliefs", 0),
                            "started_at": summary.get("started_at"),
                            "finished_at": summary.get("finished_at"),
                            "error": None,
                        },
                        category="embedding",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("[Settings] embedder rebuild failed: %s", exc)
                    _get_sm()._cs.set(
                        REBUILD_STATUS_KEY,
                        {
                            "state": "error",
                            "model": model,
                            "total": get_rebuild_status().get("total", 0),
                            "done": get_rebuild_status().get("done", 0),
                            "started_at": get_rebuild_status().get("started_at"),
                            "finished_at": None,
                            "error": str(exc),
                        },
                        category="embedding",
                    )

            loop.create_task(_run())
            return {"status": "ok", "model": model}

        @router.get("/api/settings/embedder/rebuild")
        async def api_get_embedder_rebuild() -> dict[str, Any]:
            """Poll the background rebuild status."""
            from kazma_core.memory.reembed import get_rebuild_status

            return get_rebuild_status()

        # ══════════════════════════════════════════════════════════════
        # TIME TRAVEL — replay/fork snapshot retention
        # ══════════════════════════════════════════════════════════════

        @router.get("/api/settings/time_travel")
        async def api_get_time_travel() -> dict[str, Any]:
            """Get the time-travel override plus the LIVE recorder cap.

            ``effective`` is what the running SnapshotRecorder was built
            with (store > kazama.yaml > default), so the UI can show a
            "restart required" banner until a saved value is applied.
            """
            from kazma_core.time_travel import DEFAULT_MAX_SNAPSHOTS

            store = _get_sm().get_time_travel_settings()
            effective: int | None = None
            try:
                rec = agent.snapshot_recorder
                if rec is not None:
                    effective = int(rec.max_snapshots)
            except Exception:
                logger.debug("[Settings] live time-travel cap unavailable", exc_info=True)
            if effective is None:
                yaml_cfg = {}
                try:
                    yaml_cfg = (agent.config.raw or {}).get("time_travel", {}) if getattr(agent, "config", None) else {}
                except Exception:
                    yaml_cfg = {}
                yaml_val = yaml_cfg.get("max_snapshots")
                effective = int(yaml_val) if yaml_val is not None else DEFAULT_MAX_SNAPSHOTS
            return {"store": store, "effective": effective}

        @router.put("/api/settings/time_travel")
        async def api_save_time_travel(req: dict[str, Any]) -> dict[str, Any]:
            """Persist the time-travel override (takes effect after restart)."""
            try:
                _get_sm().save_time_travel_settings(req)
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}
            return {"status": "ok"}

        # ══════════════════════════════════════════════════════════════
        # SERVER RESTART — used after saving embedder / other boot-time
        # settings. Best-effort: re-execs the same uvicorn command line
        # detached, then gracefully shuts the current process down.
        # ══════════════════════════════════════════════════════════════

        _restart_in_flight = False

        @router.post("/api/settings/system/restart")
        async def api_restart_server() -> dict[str, Any]:
            """Restart the server process (same command line, detached)."""
            import asyncio
            import os
            import signal
            import subprocess
            import sys
            from pathlib import Path

            nonlocal _restart_in_flight
            if _restart_in_flight:
                return {"status": "already", "detail": "Restart already in progress."}

            try:
                # Reconstruct the original launch command (works for both
                # `uvicorn` CLI and `python -m uvicorn` invocations, on all
                # platforms — preserves --host/--port which /proc/self/cmdline
                # cannot provide on Windows).
                args: list[str] = []
                try:
                    import psutil

                    args = list(psutil.Process().cmdline())
                except Exception:
                    args = []
                if not args:
                    args = [sys.executable, "-m", "uvicorn", "kazma_ui.app:create_app", "--factory"]
                args[0] = sys.executable

                log_path = Path("kazma-data") / "restart.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                logf = open(log_path, "ab")
                kwargs = dict(
                    stdin=subprocess.DEVNULL,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    cwd=os.getcwd(),
                    close_fds=True,
                )
                if os.name == "nt":
                    kwargs["creationflags"] = (
                        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                    )
                else:
                    kwargs["start_new_session"] = True
                subprocess.Popen(args, **kwargs)
                _restart_in_flight = True
                logger.info("[Settings] restart requested — relaunching %s", args)

                loop = asyncio.get_running_loop()

                def _graceful_exit() -> None:
                    try:
                        if os.name == "posix":
                            os.kill(os.getpid(), signal.SIGTERM)
                        else:
                            os._exit(0)
                    except Exception:
                        os._exit(0)

                def _hard_exit() -> None:
                    os._exit(0)

                # Let the new process boot; then stop this one gracefully
                # (uvicorn closes the listener at shutdown start, so the
                # new process can bind once its Python imports finish).
                loop.call_later(0.8, _graceful_exit)
                loop.call_later(25.0, _hard_exit)
                return {"status": "ok", "detail": "Server restarting…", "log": str(log_path)}
            except Exception as exc:  # noqa: BLE001
                logger.error("[Settings] restart failed: %s", exc)
                return {"status": "error", "detail": f"Restart failed: {exc}"}

        @router.put("/api/settings/agent/context")
        async def api_save_context(req: dict[str, Any]) -> dict[str, str]:
            """Save context window settings."""
            _get_sm().save_context_settings(req)
            return {"status": "ok"}

        @router.get("/api/settings/agent/context")
        async def api_get_context() -> dict[str, Any]:
            """Get context window settings (short keys for JS model)."""
            return _get_sm().get_context_settings()

        @router.get("/api/settings/agent/nonstop")
        async def api_get_nonstop() -> dict[str, Any]:
            """Get non-stop / self-healing settings (agent.nonstop.*)."""
            return _get_sm().get_nonstop_settings()

        @router.put("/api/settings/agent/nonstop")
        async def api_save_nonstop(req: dict[str, Any]) -> dict[str, str]:
            """Save non-stop / self-healing settings. Live-re-read by the
            supervisor path (get_nonstop_config) — no restart needed."""
            _get_sm().save_nonstop_settings(req)
            return {"status": "ok"}

        @router.get("/api/settings/voice")
        async def api_get_voice_settings() -> dict[str, Any]:
            """Get voice subsystem settings."""
            def _get_val(key: str, default: str) -> str:
                v = config_store.get(key)
                if v is None or str(v).strip() == "" or str(v).strip().lower() == "none":
                    return default
                return str(v)

            def _get_bool(key: str, default: bool = False) -> bool:
                v = config_store.get(key)
                if v is None:
                    return default
                if isinstance(v, bool):
                    return v
                return str(v).strip().lower() in ("1", "true", "yes", "on")

            return {
                "enabled": _get_bool("voice.enabled", False),
                "tts_reply": _get_bool("voice.tts_reply", True),
                "stt_provider": _get_val("voice.stt_provider", "openai"),
                "stt_model": _get_val("voice.stt_model", "default"),
                "stt_base_url": _get_val("voice.stt_base_url", ""),
                "tts_provider": _get_val("voice.tts_provider", "edgetts"),
                "tts_voice": _get_val("voice.tts_voice", "default"),
                "stt_language": _get_val("voice.stt_language", "auto"),
                "tts_output_format": _get_val("voice.tts_output_format", "mp3"),
            }

        @router.put("/api/settings/voice")
        async def api_save_voice_settings(req: VoiceSettingsUpdate) -> dict[str, str]:
            """Save voice subsystem settings."""
            config_store.set("voice.enabled", req.enabled, category="voice")
            config_store.set("voice.tts_reply", req.tts_reply, category="voice")
            config_store.set("voice.stt_provider", req.stt_provider, category="voice")
            config_store.set("voice.stt_model", req.stt_model, category="voice")
            config_store.set("voice.stt_base_url", req.stt_base_url or "", category="voice")
            config_store.set("voice.tts_provider", req.tts_provider, category="voice")
            config_store.set("voice.tts_voice", req.tts_voice, category="voice")
            config_store.set("voice.stt_language", req.stt_language, category="voice")
            config_store.set("voice.tts_output_format", req.tts_output_format, category="voice")
            return {"status": "ok"}

        @router.get("/api/settings/documents")
        async def api_get_document_settings() -> dict[str, Any]:
            """Live Document Intelligence settings (ConfigStore-backed)."""
            try:
                from kazma_core.documents.config import get_document_config, get_document_rollout
                from kazma_core.documents.malware import probe_malware_scanner

                cfg = get_document_config()
                rollout = get_document_rollout()
                return {
                    "enabled": cfg.enabled,
                    "shadow": cfg.shadow,
                    "default_authoritative": cfg.default_authoritative,
                    "mode": rollout.mode,
                    "intake_max_bytes": cfg.intake_max_bytes,
                    "intake_max_files": cfg.intake_max_files,
                    "ocr_enabled": cfg.ocr_enabled,
                    "worker_timeout_seconds": cfg.worker_timeout_seconds,
                    "worker_memory_mb": cfg.worker_memory_mb,
                    "capacity_storage_free_floor_bytes": cfg.capacity_storage_free_floor_bytes,
                    "security_malware_scan": cfg.security_malware_scan,
                    "security_malware_fail_closed": cfg.security_malware_fail_closed,
                    "malware_probe": probe_malware_scanner(),
                    "gc_enabled": cfg.gc_enabled,
                    "indexing_enabled": cfg.indexing_enabled,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("document settings get failed: %s", exc)
                return {"error": str(exc)}

        @router.put("/api/settings/documents")
        async def api_save_document_settings(req: dict[str, Any]) -> dict[str, str]:
            """Persist document platform keys (nested ConfigStore primary keys)."""
            mapping = {
                "enabled": ("documents.enabled", "documents"),
                "shadow": ("documents.shadow", "documents"),
                "default_authoritative": ("documents.default_authoritative", "documents"),
                "intake_max_bytes": ("documents.intake.max_bytes", "documents"),
                "intake_max_files": ("documents.intake.max_files", "documents"),
                "ocr_enabled": ("documents.ocr.enabled", "documents"),
                "worker_timeout_seconds": ("documents.workers.timeout_seconds", "documents"),
                "worker_memory_mb": ("documents.workers.memory_mb", "documents"),
                "capacity_storage_free_floor_bytes": (
                    "documents.capacity.storage_free_floor_bytes",
                    "documents",
                ),
                "security_malware_scan": ("documents.security.malware_scan", "documents"),
                "security_malware_fail_closed": (
                    "documents.security.malware_fail_closed",
                    "documents",
                ),
                "gc_enabled": ("documents.gc.enabled", "documents"),
                "indexing_enabled": ("documents.indexing.enabled", "documents"),
            }
            items: list[tuple[str, Any, str]] = []
            for field, (key, cat) in mapping.items():
                if field not in req:
                    continue
                items.append((key, req[field], cat))
            if items:
                config_store.batch_set(items)
            return {"status": "ok", "updated": str(len(items))}

        @router.get("/api/voice/providers")
        async def api_get_voice_providers() -> dict[str, list[str]]:
            """Get available voice providers for STT and TTS."""
            return {
                "stt": ["openai", "groq", "cohere", "nvidia", "faster-whisper"],
                "tts": ["edgetts", "openai", "nvidia", "kokoro", "coqui"],
            }

        @router.get("/api/voice/voices")
        async def api_get_voice_voices(provider: str = "edgetts") -> list[str]:
            """Get available voice models/ShortNames for a specific TTS provider."""
            p_lower = provider.strip().lower()
            if p_lower == "openai":
                return ["default", "alloy", "echo", "fable", "onyx", "nova", "shimmer"]
            elif p_lower == "nvidia":
                return [
                    "default",
                    "Magpie-Multilingual.EN-US.Aria",
                    "Magpie-Multilingual.EN-US.Benjamin",
                    "Magpie-Multilingual.ES-ES.Alba",
                    "Magpie-Multilingual.FR-FR.Denise",
                    "Magpie-Multilingual.ZH-CN.Xiaoxiao",
                ]
            elif p_lower == "edgetts":
                try:
                    import edge_tts  # type: ignore
                    voices = await edge_tts.VoicesManager.create()
                    return ["default"] + sorted([v["ShortName"] for v in voices.voices])
                except Exception:
                    return [
                        "default",
                        "en-US-AriaNeural",
                        "en-US-GuyNeural",
                        "en-GB-SoniaNeural",
                        "en-GB-RyanNeural",
                        "es-ES-ElviraNeural",
                        "fr-FR-DeniseNeural",
                        "ar-EG-SalmaNeural",
                        "ar-EG-ShakirNeural",
                        "ar-SA-HamedNeural",
                        "ar-SA-ZariyahNeural",
                    ]
            return ["default"]

        @router.get("/api/voice/stt-models")
        async def api_get_voice_stt_models(provider: str = "openai") -> list[Any]:
            """Get available STT models (delegates to voice router catalog)."""
            from kazma_ui.routes_voice import list_stt_models

            return await list_stt_models(provider=provider)



        @router.get("/api/settings/connectors")
        async def api_get_connectors() -> dict[str, Any]:
            """Get all connector configurations."""
            return _get_sm().get_connectors()

        @router.put("/api/settings/connectors")
        async def api_save_connector(req: ConnectorConfigUpdate) -> dict[str, str]:
            """Save a connector's configuration."""
            _get_sm().save_connector(req.platform, req.settings)
            return {"status": "ok"}

        @router.post("/api/settings/connectors/test")
        async def api_test_connector(req: ConnectorTestRequest) -> dict[str, Any]:
            """Test a connector connection."""
            return await _get_sm().test_connector(req.platform)

        @router.get("/api/settings/skills")
        async def api_get_skills() -> list[dict[str, Any]]:
            """List installed skills."""
            return _get_sm().get_installed_skills()

        @router.put("/api/settings/skills/{skill_id}/toggle")
        async def api_toggle_skill(skill_id: str, req: dict[str, Any]) -> dict[str, str]:
            """Toggle skill enabled/disabled."""
            _get_sm().toggle_skill(skill_id, req.get("enabled", True))
            return {"status": "ok"}

        @router.delete("/api/settings/skills/{skill_id}")
        async def api_uninstall_skill(skill_id: str) -> dict[str, str]:
            """Uninstall a skill."""
            _get_sm().uninstall_skill(skill_id)
            return {"status": "ok"}

        @router.get("/api/settings/appearance")
        async def api_get_appearance() -> dict[str, Any]:
            """Get appearance settings."""
            return _get_sm().get_appearance()

        @router.put("/api/settings/appearance")
        async def api_save_appearance(req: AppearanceUpdate) -> dict[str, str]:
            """Save appearance settings."""
            data = {k: v for k, v in req.model_dump().items() if v is not None}
            _get_sm().save_appearance(data)
            return {"status": "ok"}

        @router.get("/api/settings/shortcuts")
        async def api_get_shortcuts() -> dict[str, str]:
            """Get all keyboard shortcuts."""
            sm = _get_sm()
            shortcuts = sm.get_shortcuts()
            logger.info("[Settings] Shortcuts: %s", shortcuts)
            return shortcuts

        @router.put("/api/settings/shortcuts")
        async def api_save_shortcut(req: ShortcutUpdate) -> dict[str, str]:
            """Update a single shortcut."""
            _get_sm().save_shortcut(req.action, req.keys)
            return {"status": "ok"}

        @router.post("/api/settings/shortcuts/reset")
        async def api_reset_shortcuts() -> dict[str, str]:
            """Reset shortcuts to defaults."""
            _get_sm().reset_shortcuts()
            return {"status": "ok"}

        @router.put("/api/settings/account/password")
        async def api_change_password(req: PasswordChange, request: Request) -> Response:
            """Change account password."""
            from fastapi.responses import JSONResponse
            result = _get_sm().change_password(req.old_password, req.new_password)
            if result.get("error"):
                return JSONResponse(result, status_code=400)
            return JSONResponse(result)

        @router.get("/api/settings/account/tokens")
        async def api_get_tokens() -> list[dict[str, Any]]:
            """List API tokens."""
            return _get_sm().get_api_tokens()

        @router.post("/api/settings/account/tokens")
        async def api_create_token(req: dict[str, Any]) -> dict[str, Any]:
            """Create an API token."""
            return _get_sm().create_api_token(req.get("name", "unnamed"))

        @router.delete("/api/settings/account/tokens/{token_id}")
        async def api_revoke_token(token_id: str) -> dict[str, Any]:
            """Revoke an API token."""
            removed = _get_sm().revoke_api_token(token_id)
            if not removed:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"Token '{token_id}' not found")
            return {"status": "ok", "removed": True, "id": token_id}

        @router.get("/api/settings/account/sessions")
        async def api_get_sessions() -> list[dict[str, Any]]:
            """List active sessions."""
            return _get_sm().get_sessions()

        @router.get("/api/settings/tools")
        async def api_get_tools(request: Request) -> list[dict[str, Any]]:
            """List all registered tools with UI-language descriptions."""
            tools = _get_sm().get_tool_registry()
            lang = request.cookies.get("kazma-lang") or "en"
            if lang not in ("ar", "en"):
                lang = "en"
            try:
                from kazma_ui.i18n import t as i18n_t

                for tool in tools:
                    name = tool.get("name") or ""
                    key = f"tool.desc.{name}"
                    localized = i18n_t(key, lang=lang)
                    if localized and localized != key:
                        tool["description"] = localized
                        tool["description_i18n"] = True
            except Exception:
                pass
            return tools

        @router.put("/api/settings/tools/{tool_name}/toggle")
        async def api_toggle_tool(tool_name: str, req: dict[str, Any]) -> dict[str, str]:
            """Toggle a tool enabled/disabled."""
            _get_sm().toggle_tool(tool_name, req.get("enabled", True))
            return {"status": "ok"}

        @router.post("/api/settings/tools/{tool_name}/test")
        async def api_test_tool(tool_name: str, req: dict[str, Any]) -> dict[str, Any]:
            """Test a tool with arguments."""
            return await _get_sm().test_tool(tool_name, req.get("arguments", {}))

        @router.get("/api/settings/system/logs")
        async def api_get_logs(lines: int = Query(100)) -> dict[str, Any]:
            """Get system logs."""
            return _get_sm().get_logs(lines)

        @router.get("/api/settings/system/backup")
        async def api_backup() -> Response:
            """Download a full config backup."""
            content = _get_sm().create_backup()
            return Response(
                content=content,
                media_type="text/yaml; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=kazma-backup.yaml"},
            )

        @router.post("/api/settings/system/restore")
        async def api_restore(request: Request) -> dict[str, str]:
            """Restore from backup."""
            body = await request.body()
            if len(body) > 10 * 1024 * 1024:  # 10MB limit
                return {"error": "Backup too large (max 10MB)"}
            # Validate the payload is valid YAML/JSON before restoring
            try:
                import yaml as _yaml

                _yaml.safe_load(body.decode("utf-8"))
            except Exception:
                try:
                    import json as _json

                    _json.loads(body.decode("utf-8"))
                except Exception:
                    return {"error": "Backup content is not valid YAML or JSON"}
            count = _get_sm().restore_backup(body.decode("utf-8"))
            return {"status": "ok", "restored": str(count)}

        @router.get("/api/settings/system/diagnostics")
        async def api_diagnostics() -> dict[str, Any]:
            """Get system diagnostics."""
            return _get_sm().get_diagnostics()

        @router.get("/api/security/hardening")
        async def api_security_hardening() -> dict[str, Any]:
            """Run the offline hardening suite (operator report, not a gate)."""
            from pathlib import Path

            from kazma_core.security.hardening import SecurityHardeningRunner

            report = await SecurityHardeningRunner(Path.cwd()).run_all_checks()
            return {
                "total": report.total,
                "passed": report.passed,
                "failed": report.failed,
                "critical_failures": report.critical_failures,
                "timestamp": report.timestamp,
                "checks": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "severity": c.severity,
                        "message": c.message,
                        "recommendation": c.recommendation,
                    }
                    for c in report.checks
                ],
            }

        @router.get("/api/divisions/status")
        async def api_division_status() -> dict[str, Any]:
            from kazma_core.division_runtime import (
                current_division_context,
                division_enforcement_on,
                list_auth_requests,
            )

            ctx = current_division_context()
            return {
                "enforced": division_enforcement_on(),
                "user": ctx[0] if ctx else None,
                "division": ctx[1] if ctx else None,
                "pending_requests": [
                    r for r in list_auth_requests() if r.get("status") == "pending"
                ],
            }

        @router.get("/api/divisions/requests")
        async def api_division_requests() -> list[dict[str, Any]]:
            from kazma_core.division_runtime import list_auth_requests

            return list_auth_requests()

        @router.post("/api/divisions/requests/{request_id}/approve")
        async def api_division_approve(request_id: str) -> dict[str, Any]:
            from kazma_core.division_runtime import current_division_context, get_authorization_flow

            ctx = current_division_context()
            approver = ctx[0] if ctx else "default"
            result = await get_authorization_flow().approve_request(request_id, approver)
            return {
                "success": result.success,
                "request_id": result.request_id,
                "message": result.message,
                "expires_at": result.expires_at,
            }

        @router.post("/api/divisions/requests/{request_id}/deny")
        async def api_division_deny(request_id: str, req: dict[str, Any] | None = None) -> dict[str, Any]:
            from kazma_core.division_runtime import current_division_context, get_authorization_flow

            ctx = current_division_context()
            approver = ctx[0] if ctx else "default"
            reason = str((req or {}).get("reason") or "denied")
            result = await get_authorization_flow().deny_request(request_id, approver, reason)
            return {
                "success": result.success,
                "request_id": result.request_id,
                "message": result.message,
            }

        @router.get("/api/security/disclosure")
        async def api_list_disclosure(status: str | None = None) -> list[dict[str, Any]]:
            from kazma_core.security.disclosure import VulnerabilityDisclosure

            return await VulnerabilityDisclosure().list_reports(status=status)

        @router.post("/api/security/disclosure")
        async def api_submit_disclosure(req: dict[str, Any]) -> dict[str, Any]:
            from kazma_core.security.disclosure import VulnerabilityDisclosure

            rid = await VulnerabilityDisclosure().submit_report(req or {})
            return {"status": "ok", "id": rid}

        @router.post("/api/security/disclosure/{report_id}/acknowledge")
        async def api_ack_disclosure(report_id: str) -> dict[str, Any]:
            from kazma_core.security.disclosure import VulnerabilityDisclosure

            try:
                return await VulnerabilityDisclosure().acknowledge(report_id)
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @router.post("/api/security/disclosure/{report_id}/status")
        async def api_disclosure_status(report_id: str, req: dict[str, Any]) -> dict[str, Any]:
            from kazma_core.security.disclosure import VulnerabilityDisclosure

            try:
                await VulnerabilityDisclosure().update_status(
                    report_id,
                    str(req.get("status") or ""),
                    notes=str(req.get("notes") or ""),
                )
                return {"status": "ok"}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @router.get("/api/security/deps")
        async def api_security_deps() -> dict[str, Any]:
            """Scan installed skill manifests (local; no NVD/OSV required)."""
            from kazma_core.security.dependency_scanner import DependabotStyleScanner

            scanner = DependabotStyleScanner()
            try:
                hits = await scanner.scan_skill_manifests()
            except Exception as exc:
                logger.debug("[security] skill dep scan failed", exc_info=True)
                return {"status": "error", "error": str(exc), "results": []}
            out = []
            for h in hits or []:
                if hasattr(h, "__dict__"):
                    out.append({k: v for k, v in vars(h).items() if not k.startswith("_")})
                else:
                    out.append(str(h))
            return {"status": "ok", "results": out}

        @router.get("/api/settings/system/updates")
        async def api_check_updates() -> dict[str, Any]:
            """Check for updates."""
            return _get_sm().check_updates()

        @router.post("/api/settings/import")
        async def api_import_config(req: ImportConfigRequest) -> dict[str, str]:
            """Import configuration."""
            count = _get_sm().import_config(req.data, req.format, req.selective, req.sections)
            return {"status": "ok", "imported": str(count)}

        @router.post("/api/settings/reset")
        async def api_reset_settings(request: Request) -> dict[str, str]:
            """Reset all DB settings (reverts to YAML defaults).

            Requires a confirmation body ``{"confirm": "RESET"}`` to
            prevent accidental or malicious triggering.
            """
            try:
                body = await request.json()
            except Exception:
                return {"error": "Invalid JSON body. Expected {'confirm': 'RESET'}"}
            if body.get("confirm") != "RESET":
                return {"error": "Confirmation required. Send {\"confirm\": \"RESET\"} to confirm."}
            count = config_store.reset_all()
            config_store.invalidate_yaml_cache()
            return {"status": "ok", "reset": str(count)}

    def _build_providers_routes(self) -> None:
        router = self.providers_router
        _get_sm = self._get_sm

        @router.get("/api/settings/providers")
        async def api_get_providers() -> list[dict[str, Any]]:
            """List all configured providers."""
            return _get_sm().get_all_providers()

        @router.post("/api/settings/providers")
        async def api_add_provider(req: ProviderAddRequest) -> dict[str, Any]:
            """Add a new provider."""
            return _get_sm().add_provider(req.model_dump())

        @router.delete("/api/settings/providers/{name}")
        async def api_delete_provider(name: str) -> dict[str, str]:
            """Delete a provider."""
            _get_sm().delete_provider(name)
            return {"status": "ok"}

        @router.put("/api/settings/providers/{name}/toggle")
        async def api_toggle_provider(name: str, req: ProviderToggleRequest) -> dict[str, str]:
            """Toggle provider enabled/disabled."""
            _get_sm().toggle_provider(name, req.enabled)
            return {"status": "ok"}

        @router.post("/api/settings/providers/{name}/test")
        async def api_test_provider(name: str) -> dict[str, Any]:
            """Test a provider connection."""
            return await _get_sm().test_provider(name)

        @router.get("/api/settings/providers/{name}/health")
        async def api_provider_health(name: str) -> dict[str, Any]:
            """Get provider health status."""
            return _get_sm().get_provider_health(name)

    def _build_models_routes(self) -> None:
        router = self.models_router
        _get_sm = self._get_sm

        @router.post("/api/settings/test-model")
        async def api_test_model(req: ModelTestRequest) -> dict[str, Any]:
            """Test a model connection by sending a simple request."""
            import httpx

            try:
                # SSRF protection: validate the base_url
                from kazma_core.security.ssrf import SSRFError, validate_url
                validate_url(req.base_url)
            except SSRFError as exc:
                return {"success": False, "error": f"Blocked: {exc}"}
            except ValueError as exc:
                return {"success": False, "error": f"Invalid URL: {exc}"}
            except ImportError:
                return {"success": False, "error": "SSRF validation module not available"}

            try:
                headers = {
                    "Authorization": f"Bearer {req.api_key or 'not-needed'}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": req.model,
                    "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
                    "max_tokens": 10,
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{req.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return {"success": True, "response": content, "model": data.get("model", req.model)}
            except httpx.ConnectError:
                return {"success": False, "error": f"Cannot connect to {req.base_url}"}
            except httpx.HTTPStatusError as e:
                return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
            except Exception as e:
                logger.debug("Provider test failed: %s", e)
                return {"success": False, "error": "Connection test failed unexpectedly"}

        @router.get("/api/settings/models/registry")
        async def api_model_registry() -> list[dict[str, Any]]:
            """Get model registry."""
            return _get_sm().get_model_registry()

        @router.get("/api/settings/models/options")
        async def api_model_options() -> dict[str, Any]:
            """Get unified model/provider/profile options."""
            return _get_sm().get_unified_model_options()

        @router.get("/api/settings/models/defaults")
        async def api_model_defaults() -> dict[str, str]:
            """Get default models per task type."""
            return _get_sm().get_model_defaults()

        @router.put("/api/settings/models/defaults")
        async def api_set_model_default(req: ModelDefaultUpdate) -> dict[str, str]:
            """Set default model for a task type."""
            _get_sm().set_default_model(req.task_type, req.model_name)
            return {"status": "ok"}

        @router.get("/api/settings/models/usage")
        async def api_model_usage() -> dict[str, Any]:
            """Get token usage stats per model."""
            return _get_sm().get_model_usage()

        @router.post("/api/settings/models/compare")
        async def api_model_compare(req: ModelCompareRequest) -> list[dict[str, Any]]:
            """Compare models with the same prompt."""
            return await _get_sm().compare_models(req.prompt, req.models, req.temperature, req.max_tokens)

        @router.put("/api/settings/active_model")
        async def api_set_active_model(req: Request) -> dict[str, Any]:
            """Set the active chat model, rebind the agent/graph, and persist it.

            Body: ``{"active_model": "deepseek-v4-pro"}`` or ``{"model": "..."}``

            Returns ``status: error`` with ``error_code`` when the switch fails
            (env lock, invalid model, rebind failure). Callers must not treat a
            non-ok response as a successful switch.
            """
            try:
                body = await req.json()
            except Exception as _e:
                logger.debug("Failed to parse model switch body: %s", _e)
                body = {}
            model = (body.get("active_model") or body.get("model") or "").strip()
            if not model:
                return {
                    "error": "active_model is required",
                    "status": "error",
                    "ok": False,
                    "error_code": "invalid_model",
                }
            try:
                from kazma_core.runtime.model_switch import switch_active_model

                result = switch_active_model(
                    model,
                    agent=getattr(self, "agent", None),
                )
                return result.to_dict()
            except Exception as exc:
                logger.warning("[Settings] set_active_model failed: %s", exc)
                return {
                    "active_model": model,
                    "model": model,
                    "status": "error",
                    "ok": False,
                    "error": str(exc),
                    "error_code": "rebind_failed",
                }

        @router.post("/api/settings/memory/clean")
        async def api_clean_memory() -> dict[str, Any]:
            """Purge backfill garbage, SoulEvolution noise, and scan chunks."""
            try:
                from kazma_core.memory.backfill_v2 import cleanup_polluted_backfill

                stats = cleanup_polluted_backfill()
                return {"status": "ok", "stats": stats}
            except Exception as exc:
                logger.warning("[Settings] Memory cleanup failed: %s", exc)
                return {"status": "error", "error": str(exc)}

    def _build_mcp_routes(self) -> None:
        router = self.mcp_router
        _get_sm = self._get_sm

        @router.get("/api/settings/mcp")
        async def api_get_mcp() -> list[dict[str, Any]]:
            """List all MCP servers."""
            return _get_sm().get_mcp_servers()

        @router.post("/api/settings/mcp")
        async def api_add_mcp(req: MCPServerAddRequest) -> dict[str, Any]:
            """Add an MCP server."""
            return _get_sm().add_mcp_server(req.model_dump())

        @router.delete("/api/settings/mcp/{name}")
        async def api_delete_mcp(name: str) -> dict[str, str]:
            """Delete an MCP server."""
            from fastapi.responses import JSONResponse

            result = _get_sm().delete_mcp_server(name)
            if isinstance(result, dict) and result.get("status") == "error":
                return JSONResponse(
                    {"status": "error", "message": result.get("error", "delete failed")},
                    status_code=500,
                )
            return {"status": "ok"}

        @router.put("/api/settings/mcp/{name}/toggle")
        async def api_toggle_mcp(name: str, req: MCPServerToggleRequest) -> dict[str, str]:
            """Toggle MCP server enabled/disabled."""
            _get_sm().toggle_mcp_server(name, req.enabled)
            return {"status": "ok"}

        @router.post("/api/settings/mcp/{name}/test")
        async def api_test_mcp(name: str) -> dict[str, Any]:
            """Test an MCP server connection."""
            return await _get_sm().test_mcp_server(name)

        # Catch-all DELETE must come AFTER all specific routes above,
        # otherwise it matches paths like /api/settings/account/tokens/{id}
        # before the specific handler can fire.
        @router.delete("/api/settings/{key:path}")
        async def api_delete_setting(key: str) -> dict[str, str]:
            """Delete a setting (reverts to YAML default)."""
            config_store.delete(key)
            return {"status": "ok"}

    def build(self) -> APIRouter:
        self._build_general_routes()
        self._build_providers_routes()
        self._build_models_routes()
        self._build_mcp_routes()

        # Mount the decoupled sub-routers on the parent router
        self.router.include_router(self.general_router)
        self.router.include_router(self.providers_router)
        self.router.include_router(self.models_router)
        self.router.include_router(self.mcp_router)
        return self.router


def create_settings_router(agent: KazmaAgent, config_store: ConfigStore, templates: Jinja2Templates) -> APIRouter:
    """Create the settings router with agent and config store wired in."""
    builder = SettingsRouterBuilder(agent, config_store, templates)
    return builder.build()

