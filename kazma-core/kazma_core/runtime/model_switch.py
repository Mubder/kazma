"""Single pipeline for switching the process-wide active LLM profile.

All UI / gateway / settings entry points must call these helpers so that:

1. ``ModelRegistry`` is the only source of truth for active provider/model
2. ``registry.active_chat_model`` stays mirrored
3. The agent rebinds its LLM client and fires recompile hooks
4. Callers receive an honest success/failure (env lock, empty model, rebind error)

Never pass masked API keys (``***``) into ``LLMProvider.reconfigure`` — use
``registry.get_client()`` via ``agent.sync_active_model()`` instead.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "SwitchResult",
    "ensure_active_model",
    "register_rebind_hook",
    "switch_active_model",
    "switch_active_provider",
    "unregister_rebind_hook",
]

# Extra rebind hooks beyond agent.sync_active_model's callback (e.g. tests).
_extra_hooks: list[Callable[[], None]] = []
_hooks_lock = threading.Lock()


@dataclass(frozen=True)
class SwitchResult:
    """Outcome of a model/provider switch attempt."""

    ok: bool
    model: str = ""
    provider: str = ""
    error: str | None = None
    error_code: str | None = None  # env_locked | invalid_model | rebind_failed | error

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": "ok" if self.ok else "error",
            "ok": self.ok,
            "model": self.model,
            "provider": self.provider,
            "active_model": self.model,
        }
        if self.error:
            d["error"] = self.error
        if self.error_code:
            d["error_code"] = self.error_code
        return d


def register_rebind_hook(cb: Callable[[], None]) -> None:
    """Register an extra callback after a successful registry + agent sync."""
    with _hooks_lock:
        if cb not in _extra_hooks:
            _extra_hooks.append(cb)


def unregister_rebind_hook(cb: Callable[[], None]) -> None:
    with _hooks_lock:
        try:
            _extra_hooks.remove(cb)
        except ValueError:
            pass


def _profile_snapshot(registry: Any) -> tuple[str, str]:
    try:
        profile = registry.get_active_profile() or {}
        return (
            str(profile.get("model") or getattr(registry, "_active_model", "") or ""),
            str(profile.get("provider") or getattr(registry, "_active_provider", "") or ""),
        )
    except Exception:
        return (
            str(getattr(registry, "_active_model", "") or ""),
            str(getattr(registry, "_active_provider", "") or ""),
        )


def _mirror_chat_model(registry: Any, model: str) -> None:
    """Keep registry.active_chat_model in lockstep for gateway/bus readers."""
    try:
        store = getattr(registry, "_config_store", None)
        if store is not None and model:
            store.set("registry.active_chat_model", model, category="registry")
    except Exception as exc:
        logger.debug("[model_switch] mirror active_chat_model failed: %s", exc)


def _sync_agent(agent: Any | None) -> None:
    if agent is None:
        return
    if hasattr(agent, "sync_active_model"):
        agent.sync_active_model()
        return
    # Fallback: drop cached graphs so next build picks up new client.
    if hasattr(agent, "_streaming_graph"):
        agent._streaming_graph = None
    if hasattr(agent, "_graph"):
        agent._graph = None
    try:
        from kazma_core.model_registry import get_model_registry

        if hasattr(agent, "llm"):
            agent.llm = get_model_registry().get_client()
            if hasattr(agent, "llm_config") and getattr(agent.llm, "config", None) is not None:
                agent.llm_config = agent.llm.config
    except Exception as exc:
        logger.warning("[model_switch] agent fallback rebind failed: %s", exc)


def _run_extra_hooks() -> None:
    with _hooks_lock:
        hooks = list(_extra_hooks)
    for cb in hooks:
        try:
            cb()
        except Exception as exc:
            logger.warning("[model_switch] rebind hook failed: %s", exc)


def switch_active_model(
    model: str,
    *,
    agent: Any | None = None,
    registry: Any | None = None,
) -> SwitchResult:
    """Set the active chat model and rebind the live agent/graph pipeline.

    Returns a :class:`SwitchResult`. On env lock the registry is not mutated.
    """
    clean = (model or "").strip()
    if not clean:
        return SwitchResult(
            ok=False,
            error="active_model is required",
            error_code="invalid_model",
        )

    try:
        from kazma_core.model_registry import get_model_registry

        reg = registry or get_model_registry()
    except Exception as exc:
        return SwitchResult(
            ok=False,
            model=clean,
            error=f"Model registry unavailable: {exc}",
            error_code="error",
        )

    if getattr(reg, "_env_locked", lambda: False)():
        cur_model, cur_prov = _profile_snapshot(reg)
        return SwitchResult(
            ok=False,
            model=cur_model,
            provider=cur_prov,
            error="Profile is locked by KAZMA_MODEL/KAZMA_PROVIDER environment variables",
            error_code="env_locked",
        )

    try:
        reg.set_active_model(clean)
        # set_active_model is a silent no-op under env lock; re-check.
        if getattr(reg, "_env_locked", lambda: False)():
            cur_model, cur_prov = _profile_snapshot(reg)
            return SwitchResult(
                ok=False,
                model=cur_model,
                provider=cur_prov,
                error="Profile is locked by KAZMA_MODEL/KAZMA_PROVIDER environment variables",
                error_code="env_locked",
            )
        _mirror_chat_model(reg, clean)
        _sync_agent(agent)
        _run_extra_hooks()
    except Exception as exc:
        logger.warning("[model_switch] switch_active_model failed: %s", exc, exc_info=True)
        cur_model, cur_prov = _profile_snapshot(reg)
        return SwitchResult(
            ok=False,
            model=cur_model or clean,
            provider=cur_prov,
            error=str(exc),
            error_code="rebind_failed",
        )

    final_model, final_prov = _profile_snapshot(reg)
    # Prefer the registry active model (may normalize); fall back to requested.
    if not final_model:
        final_model = clean
    logger.info(
        "[model_switch] active model set: model=%s provider=%s",
        final_model,
        final_prov,
    )
    return SwitchResult(ok=True, model=final_model, provider=final_prov)


def switch_active_provider(
    provider: str,
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    agent: Any | None = None,
    registry: Any | None = None,
) -> SwitchResult:
    """Switch the active provider (and optional model) then rebind the agent.

    ``api_key`` must be a real key or empty — never a masked ``***`` value.
    Empty api_key leaves the stored key untouched (registry upsert rules).
    """
    clean_provider = (provider or "").strip()
    if not clean_provider:
        return SwitchResult(
            ok=False,
            error="Provider name is required",
            error_code="invalid_model",
        )

    # Refuse masked secrets so we never wipe a live client key.
    if api_key and str(api_key).strip() in ("***", "••••", "****"):
        api_key = ""

    try:
        from kazma_core.model_registry import get_model_registry

        reg = registry or get_model_registry()
    except Exception as exc:
        return SwitchResult(
            ok=False,
            provider=clean_provider,
            model=(model or "").strip(),
            error=f"Model registry unavailable: {exc}",
            error_code="error",
        )

    if getattr(reg, "_env_locked", lambda: False)():
        cur_model, cur_prov = _profile_snapshot(reg)
        return SwitchResult(
            ok=False,
            model=cur_model,
            provider=cur_prov,
            error="Profile is locked by KAZMA_MODEL/KAZMA_PROVIDER environment variables",
            error_code="env_locked",
        )

    try:
        result = reg.set_active_provider(
            provider=clean_provider,
            base_url=base_url or "",
            model=(model or "").strip(),
            api_key=api_key or "",
        )
        if isinstance(result, dict) and result.get("error"):
            return SwitchResult(
                ok=False,
                model=str(result.get("model") or ""),
                provider=str(result.get("provider") or clean_provider),
                error=str(result["error"]),
                error_code="env_locked" if "locked" in str(result["error"]).lower() else "error",
            )
        final_model, final_prov = _profile_snapshot(reg)
        if final_model:
            _mirror_chat_model(reg, final_model)
        _sync_agent(agent)
        _run_extra_hooks()
    except Exception as exc:
        logger.warning("[model_switch] switch_active_provider failed: %s", exc, exc_info=True)
        cur_model, cur_prov = _profile_snapshot(reg)
        return SwitchResult(
            ok=False,
            model=cur_model,
            provider=cur_prov or clean_provider,
            error=str(exc),
            error_code="rebind_failed",
        )

    final_model, final_prov = _profile_snapshot(reg)
    logger.info(
        "[model_switch] active provider set: provider=%s model=%s",
        final_prov,
        final_model,
    )
    return SwitchResult(ok=True, model=final_model, provider=final_prov)


def ensure_active_model(
    model: str,
    *,
    agent: Any | None = None,
    registry: Any | None = None,
) -> SwitchResult:
    """If *model* differs from the active profile, switch; otherwise no-op ok.

    Used by chat transports that receive a selected model on each turn.
    """
    clean = (model or "").strip()
    if not clean:
        try:
            from kazma_core.model_registry import get_model_registry

            reg = registry or get_model_registry()
            m, p = _profile_snapshot(reg)
            return SwitchResult(ok=True, model=m, provider=p)
        except Exception:
            return SwitchResult(ok=True, model="", provider="")

    try:
        from kazma_core.model_registry import get_model_registry

        reg = registry or get_model_registry()
    except Exception as exc:
        return SwitchResult(
            ok=False,
            model=clean,
            error=f"Model registry unavailable: {exc}",
            error_code="error",
        )

    cur_model, cur_prov = _profile_snapshot(reg)
    if cur_model.strip() == clean:
        return SwitchResult(ok=True, model=cur_model, provider=cur_prov)

    return switch_active_model(clean, agent=agent, registry=reg)
