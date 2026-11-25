"""The guided provider setup's routes (Settings -> Providers -> Add a provider).

Put on ``kazma_ui.providers``'s router (the install's, the admin's: ``/api/providers``
in ``route_scopes``). Nothing here answers a key: a typed key is used to list
the models when the person presses for it (:func:`setup_models`), and kept by
the save alone (``POST /api/providers``, or one more key on a row).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = ["add_provider_setup_routes"]

#: The most models a list shown by the setup holds.
_MAX_LISTED = 1000


class _SetupLook(BaseModel):
    """Where a provider is, as typed: an endpoint of the catalog and the
    values its address is filled with, or an address."""

    name: str = Field(default="", max_length=200)
    preset: str = Field(default="", max_length=60)
    endpoint: str = Field(default="", max_length=60)
    values: dict[str, str] = Field(default_factory=dict)
    base_url: str = Field(default="", max_length=2000)
    kind: str = Field(default="", max_length=20)
    api_version: str = Field(default="", max_length=60)
    region: str = Field(default="", max_length=60)
    google_mode: str = Field(default="", max_length=20)
    project_id: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=60)


class _SetupModels(_SetupLook):
    """The same with what its sign-in needs, to list its models."""

    api_key: str = Field(default="", max_length=8000)
    headers: dict[str, str] = Field(default_factory=dict)
    url_as_typed: bool = False
    aws_access_key_id: str = Field(default="", max_length=200)
    aws_secret_access_key: str = Field(default="", max_length=400)
    aws_session_token: str = Field(default="", max_length=8000)
    models: list[str] = Field(default_factory=list, max_length=_MAX_LISTED)


class _NewKey(BaseModel):
    """One more key for a saved provider."""

    api_key: str = Field(max_length=8000)
    label: str = Field(default="", max_length=60)
    #: Where the key was offered for: a row that moved meanwhile takes none.
    base_url: str = Field(default="", max_length=2000)


class _TidyRows(BaseModel):
    """The rows to merge into the row each repeats, and the unused to remove."""

    merge: list[str] = Field(default_factory=list, max_length=200)
    remove: list[str] = Field(default_factory=list, max_length=200)


def _registry() -> Any:
    from kazma_core.model_registry import get_model_registry

    return get_model_registry()


def _refuse(exc: Any, status: int = 400) -> HTTPException:
    """A refused step as the pages read it: a code they word, never more."""
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message, "field": exc.field})


def _unreadable() -> HTTPException:
    return HTTPException(status_code=503, detail={
        "code": "identity_unavailable", "message": "Who may use which model cannot be read right now; nothing was changed."})


def _look(req: _SetupLook) -> dict[str, Any]:
    from kazma_core.provider_endpoints import ENDPOINTS
    from kazma_core.provider_setup import SetupRefused, endpoint_address, field_problems, rows_at
    from kazma_core.providers import PROVIDER_PRESETS

    preset = req.preset.strip().lower()
    entry = req.model_dump(exclude={"endpoint", "values"})
    entry["preset"] = preset
    if any(eid == req.endpoint for eid, _url in ENDPOINTS.get(preset, ())):
        try:
            entry["base_url"] = endpoint_address(preset, req.endpoint, req.values)
        except SetupRefused as exc:
            return {"ok": False, "base_url": "", "same": [], "problems": [
                {"field": exc.field or "base_url", "code": "malformed" if (req.values.get(exc.field) or "").strip() else "missing"}]}
    form = str((PROVIDER_PRESETS.get(preset) or {}).get("form") or "key")
    problems = field_problems(entry)
    if form not in ("google", "bedrock") and not entry["base_url"].strip():
        problems.append({"field": "base_url", "code": "missing"})
    if problems:
        return {"ok": False, "base_url": "", "same": [], "problems": problems}
    return {"ok": True, "base_url": entry["base_url"].strip(), "problems": [],
            "same": rows_at(_registry(), entry)}


def _candidate(registry: Any, req: _SetupModels) -> dict[str, Any]:
    from kazma_core.provider_check import candidate_entry

    typed = req.model_dump(exclude={"name", "base_url", "api_key", "headers", "endpoint", "values", "models"})
    typed["secret_headers"] = req.headers
    entry = candidate_entry(registry, req.name.strip(), base_url=req.base_url,
                            api_key=req.api_key.strip(), typed=typed)
    if req.models:
        # A service with no model list (Azure, Bedrock): the models are typed.
        entry["models"] = [m.strip() for m in req.models if m.strip()]
    return entry


def _listed(entry: dict[str, Any], ids: list[str], details: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from kazma_core.provider_check import known_price

    return [{"id": mid, "price": known_price(entry, mid, details.get(mid)),
             "context_window": (details.get(mid) or {}).get("context_window")} for mid in ids[:_MAX_LISTED]]


def add_provider_setup_routes(router: APIRouter) -> None:
    """The routes of the guided setup and of tidying the provider list, put
    on the providers' own *router*."""

    @router.get("/api/providers/setup")
    def setup_options() -> dict[str, Any]:
        """What the setup asks with: the groups, the fields' shapes, the
        regions, and what each provider's keys look like (matched in the
        page: a pasted key is sent nowhere to be recognised)."""
        from kazma_core.provider_endpoints import FIELDS, GROUPS, KEY_SHAPES, REGIONS

        return {"groups": list(GROUPS), "fields": FIELDS, "regions": {k: list(v) for k, v in REGIONS.items()},
                "key_shapes": [{"pattern": p, "providers": list(ids)} for p, ids in KEY_SHAPES]}

    @router.post("/api/providers/setup/look")
    def setup_look(req: _SetupLook) -> dict[str, Any]:
        """The address the choices make, what is wrong with them, and the
        rows that already are this provider there. Nothing is sent to it."""
        return _look(req)

    @router.post("/api/providers/setup/models")
    async def setup_models(req: _SetupModels) -> dict[str, Any]:
        """The models the provider lists for the key as typed, with the price
        Kazma knows of each; nothing is saved. ``refused``: there is nothing
        to ask (a key or a field is missing)."""
        from kazma_core.models.modality import chat_models
        from kazma_core.provider_check import CandidateRefused, list_models

        registry = _registry()
        try:
            entry = await asyncio.to_thread(_candidate, registry, req)
        except CandidateRefused as exc:
            return {"ok": False, "models": [], "error_code": exc.code, "error": exc.message, "refused": True}
        found = await list_models(registry, entry)
        typed = [m.strip() for m in req.models if m.strip()]
        ids = list(dict.fromkeys([*typed, *chat_models(found.models)])) if found.ok else typed
        answer: dict[str, Any] = {
            "ok": bool(found.ok), "models": await asyncio.to_thread(_listed, entry, ids, found.details),
        }
        if not found.ok:
            answer["error_code"] = "list_failed"
            answer["error"] = str(found.detail or found.error)
            # A refused key is told as what it is, here as by the test.
            if found.error in ("http_401", "http_403"):
                answer["error_code"], answer["refused"] = "key_refused", True
        elif found.models and not chat_models(found.models) and not typed:
            answer["error_code"] = "speech_only"
            answer["error"] = "The models listed are speech, not chat."
        return answer

    @router.post("/api/providers/{name}/keys")
    def add_provider_key(name: str, req: _NewKey) -> dict[str, Any]:
        """One more key for a saved provider, tried when its others fail
        (kazma_core.provider_setup.add_key)."""
        from kazma_core.provider_setup import SetupRefused, add_key

        try:
            return {"ok": True, **add_key(
                _registry(), name, req.api_key, req.label, base_url=req.base_url)}
        except SetupRefused as exc:
            raise _refuse(exc, {"unknown_provider": 404, "row_moved": 409, "env_key": 409}.get(exc.code, 400)) from None

    @router.get("/api/providers/tidy")
    def tidy_list() -> dict[str, Any]:
        """The rows that repeat each other, and the rows nothing uses."""
        from kazma_core.provider_setup import tidy_report
        from kazma_core.spaces.identity import IdentityUnavailable

        try:
            return tidy_report(_registry())
        except IdentityUnavailable:
            logger.warning("[provider-setup] the people's model lists could not be read", exc_info=True)
            raise _unreadable() from None

    @router.post("/api/providers/tidy")
    def tidy_rows(req: _TidyRows) -> dict[str, Any]:
        """Merge and remove what :func:`tidy_list` offered; a list that
        changed meanwhile is a 409 and nothing is changed."""
        from kazma_core.provider_setup import SetupRefused, tidy
        from kazma_core.spaces.identity import IdentityUnavailable

        try:
            return {"ok": True, **tidy(_registry(), merge=req.merge, remove=req.remove)}
        except SetupRefused as exc:
            raise _refuse(exc, 409 if exc.code == "plan_changed" else 400) from None
        except IdentityUnavailable:
            logger.warning("[provider-setup] the people's model lists could not be read", exc_info=True)
            raise _unreadable() from None
