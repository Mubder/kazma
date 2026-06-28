"""Kazma Hub — REST API.

FastAPI-based REST API wrapping the KazmaHub registry and
CertificationBadgeSystem for the public hub.
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from kazma_core.hub.badges import CertificationBadgeSystem
from kazma_core.hub.manifest_schema import SkillManifest
from kazma_core.hub.registry import KazmaHub

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Kazma Hub API", version="0.1.0")

# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class SkillSummary(BaseModel):
    """Brief skill listing entry."""

    id: str
    name: str
    author: str
    description: str | None = None
    version: str
    category: str | None = None
    tags: list[str] = []
    certified: bool = False
    downloads: int = 0


class SkillListResponse(BaseModel):
    """Paginated list of skills."""

    items: list[SkillSummary]
    total: int
    page: int
    per_page: int


class SkillDetailResponse(BaseModel):
    """Detailed skill information."""

    id: str
    name: str
    author: str
    description: str | None = None
    version: str
    category: str | None = None
    tags: list[str] = []
    certified: bool = False
    downloads: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class SearchResponse(BaseModel):
    """Search results."""

    items: list[SkillSummary]
    total: int
    query: str


class SubmissionRequest(BaseModel):
    """Skill submission payload."""

    manifest: dict[str, Any]
    source_url: str
    submitter_id: str


class SubmissionResponse(BaseModel):
    """Result of a skill submission."""

    submission_id: str
    skill_id: str
    status: str
    message: str


class CertificationStatus(BaseModel):
    """Certification status for a skill."""

    skill_id: str
    level: str
    issued_at: str | None = None
    expires_at: str | None = None
    requirements_met: dict[str, bool] = {}


class StatsResponse(BaseModel):
    """Hub statistics."""

    total_skills: int
    certified_count: int
    by_category: dict[str, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest_to_skill_id(manifest: dict[str, Any]) -> str:
    """Build a kazma-hub:// ID from manifest data."""
    author = manifest.get("author", "unknown")
    name = manifest.get("name", "unnamed")
    version = manifest.get("version", "0.0.0")
    return f"kazma-hub://{author}/{name}@{version}"


def _manifest_to_summary(manifest: SkillManifest, certified: bool = False) -> SkillSummary:
    """Convert a SkillManifest to a SkillSummary."""
    data = manifest.data
    return SkillSummary(
        id=_manifest_to_skill_id(data),
        name=data.get("name", ""),
        author=data.get("author", ""),
        description=data.get("description"),
        version=data.get("version", ""),
        category=data.get("category"),
        tags=data.get("tags", []),
        certified=certified,
        downloads=0,
    )


# ---------------------------------------------------------------------------
# API wrapper class
# ---------------------------------------------------------------------------


class KazmaHubAPI:
    """Wraps the KazmaHub registry and badge system for the REST API."""

    def __init__(self, registry: KazmaHub, certifier: CertificationBadgeSystem):
        self.registry = registry
        self.certifier = certifier


# ---------------------------------------------------------------------------
# Global state (set by configure_api)
# ---------------------------------------------------------------------------

_api: KazmaHubAPI | None = None


def configure_api(registry: KazmaHub, certifier: CertificationBadgeSystem) -> KazmaHubAPI:
    """Configure the global API instance. Call at startup."""
    global _api
    _api = KazmaHubAPI(registry, certifier)
    return _api


def _get_api() -> KazmaHubAPI:
    """Get the configured API or raise."""
    if _api is None:
        raise HTTPException(status_code=503, detail="API not configured")
    return _api


# ---------------------------------------------------------------------------
# Routes — Health
# ---------------------------------------------------------------------------


@app.get("/api/v1/health")
async def health():
    """Health check endpoint for liveness/readiness probes."""
    return {"status": "ok", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Routes — Skills
# ---------------------------------------------------------------------------


@app.get("/api/v1/skills", response_model=SkillListResponse)
async def list_skills(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort: str = Query("name"),
    category: str | None = Query(None),
    certified: bool | None = Query(None),
):
    """List skills with pagination and optional filters."""
    api = _get_api()

    # Build search kwargs
    kwargs: dict[str, Any] = {}
    if category:
        kwargs["tags"] = [category]

    manifests = await api.registry.search(**kwargs)

    # Filter by certified status if requested
    items = []
    for m in manifests:
        cert = api.certifier.verify_badge(m.data["name"])
        if certified is not None and cert.valid != certified:
            continue
        items.append(_manifest_to_summary(m, certified=cert.valid))

    # Sort
    if sort == "name":
        items.sort(key=lambda x: x.name)
    elif sort == "version":
        items.sort(key=lambda x: x.version)

    total = len(items)
    start = (page - 1) * per_page
    page_items = items[start : start + per_page]

    return SkillListResponse(items=page_items, total=total, page=page, per_page=per_page)


@app.get("/api/v1/skills/search", response_model=SearchResponse)
async def search_skills(
    q: str = Query(""),
    category: str | None = Query(None),
    certified: bool | None = Query(None),
):
    """Full-text search across skills."""
    api = _get_api()

    kwargs: dict[str, Any] = {"query": q if q else None}
    if category:
        kwargs["tags"] = [category]

    manifests = await api.registry.search(**kwargs)

    items = []
    for m in manifests:
        cert = api.certifier.verify_badge(m.data["name"])
        if certified is not None and cert.valid != certified:
            continue
        items.append(_manifest_to_summary(m, certified=cert.valid))

    return SearchResponse(items=items, total=len(items), query=q)


@app.post("/api/v1/skills/submit", response_model=SubmissionResponse)
async def submit_skill(payload: SubmissionRequest):
    """Submit a skill for certification review."""
    api = _get_api()

    manifest = payload.manifest
    skill_id = _manifest_to_skill_id(manifest)
    submission_id = str(uuid.uuid4())

    # Register in the hub
    try:
        sm = SkillManifest.from_dict(manifest)
        await api.registry.register(sm)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid manifest: {exc}")

    return SubmissionResponse(
        submission_id=submission_id,
        skill_id=skill_id,
        status="pending",
        message="Skill submitted for certification review",
    )


@app.get("/api/v1/skills/{skill_id:path}/certification", response_model=CertificationStatus)
async def get_certification_status(skill_id: str):
    """Get the certification status for a skill."""
    api = _get_api()

    # Extract skill name from the ID
    parts = skill_id.split("/")
    skill_name = parts[-1].split("@")[0] if "/" in skill_id else skill_id

    result = api.certifier.verify_badge(skill_name)
    if not result.valid and result.reason == "No badge found":
        raise HTTPException(status_code=404, detail=f"No certification for: {skill_id}")

    return CertificationStatus(
        skill_id=skill_id,
        level=result.level or "none",
        requirements_met={"valid": result.valid},
    )


@app.get("/api/v1/skills/{skill_id:path}/download")
async def download_skill(skill_id: str):
    """Download a skill package as a tarball."""
    api = _get_api()

    try:
        manifest = await api.registry.get(skill_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    # Create a minimal tarball with the manifest
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_json = json.dumps(manifest.data, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="skill_manifest.yaml")
        info.size = len(manifest_json)
        tar.addfile(info, io.BytesIO(manifest_json))

    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={skill_id.split('/')[-1].split('@')[0]}.tar.gz"},
    )


@app.get("/api/v1/skills/{skill_id:path}", response_model=SkillDetailResponse)
async def get_skill(skill_id: str):
    """Get detailed information for a specific skill."""
    api = _get_api()

    try:
        manifest = await api.registry.get(skill_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    cert = api.certifier.verify_badge(manifest.data["name"])
    data = manifest.data

    return SkillDetailResponse(
        id=skill_id,
        name=data.get("name", ""),
        author=data.get("author", ""),
        description=data.get("description"),
        version=data.get("version", ""),
        category=data.get("category"),
        tags=data.get("tags", []),
        certified=cert.valid,
        downloads=0,
    )


# ---------------------------------------------------------------------------
# Routes — Stats
# ---------------------------------------------------------------------------


@app.get("/api/v1/stats", response_model=StatsResponse)
async def get_stats():
    """Get hub statistics."""
    api = _get_api()

    manifests = await api.registry.search()
    total = len(manifests)

    # Count certified
    certified = 0
    by_category: dict[str, int] = {}
    for m in manifests:
        cert = api.certifier.verify_badge(m.data["name"])
        if cert.valid:
            certified += 1
        cat = m.data.get("category", "uncategorized")
        by_category[cat] = by_category.get(cat, 0) + 1

    return StatsResponse(
        total_skills=total,
        certified_count=certified,
        by_category=by_category,
    )
