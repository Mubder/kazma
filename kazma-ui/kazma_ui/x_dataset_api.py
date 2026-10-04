"""Attended annotation API; this surface cannot install qualification or call X."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kazma_core.x_api.datasets import DatasetStore
from pydantic import BaseModel, ConfigDict, Field, StrictBool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/x/datasets", tags=["x-datasets"])


async def _csrf(request: Request) -> None:
    from kazma_ui.x_api import _verify_same_origin

    await _verify_same_origin(request)


class _CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    purpose: str = "collection"
    document: dict[str, Any] | None = None


class _RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


class _CaseBody(_RevisionBody):
    case: dict[str, Any]
    reviewed: StrictBool = False
    critical_violations: int | None = Field(default=None, ge=0, le=1000, strict=True)


def _actor(request: Request) -> str:
    from kazma_ui.auth import get_request_principal

    principal = get_request_principal(request) or {}
    return str(principal.get("user_id") or principal.get("username") or "local-operator")


def _call(action: Any) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "dataset": action()})
    except KeyError:
        return JSONResponse({"ok": False, "error": "Dataset not found."}, status_code=404)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409 if "another editor" in str(exc) else 422)
    except (sqlite3.Error, OSError, RuntimeError):
        logger.exception("X dataset request failed")
        return JSONResponse({"ok": False, "error": "Dataset request failed. Check the server log."}, status_code=500)


@router.get("")
def list_datasets() -> JSONResponse:
    return _call(lambda: DatasetStore().list())


@router.get("/{ident}")
def get_dataset(ident: str) -> JSONResponse:
    return _call(lambda: DatasetStore().get(ident))


@router.post("", dependencies=[Depends(_csrf)])
def create_dataset(body: _CreateBody, request: Request) -> JSONResponse:
    return _call(lambda: DatasetStore().create(body.name, body.purpose, actor=_actor(request), document=body.document))


@router.put("/{ident}/case", dependencies=[Depends(_csrf)])
def save_case(ident: str, body: _CaseBody, request: Request) -> JSONResponse:
    return _call(lambda: DatasetStore().save_case(ident, body.case, revision=body.expected_revision,
                 actor=_actor(request), reviewed=body.reviewed, critical_violations=body.critical_violations))


@router.post("/{ident}/collect", dependencies=[Depends(_csrf)])
def collect_dataset(ident: str, body: _RevisionBody, request: Request) -> JSONResponse:
    return _call(lambda: DatasetStore().collect(ident, revision=body.expected_revision, actor=_actor(request)))


@router.get("/{ident}/export")
def export_dataset(ident: str, report: bool = False) -> JSONResponse:
    return _call(lambda: DatasetStore().export(ident, report=report))
