"""Asset Manager — Versioned storage and retrieval for generated assets.

Manages generated images and videos with metadata indexing,
versioning, expiration cleanup, and division-scoped queries.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AssetMetadata:
    """Metadata for a stored asset."""

    asset_id: str
    division: str
    asset_type: str          # "logo", "marketing", "inspection", "video"
    variant: str
    width: int
    height: int
    file_path: str
    file_size_bytes: int
    created_at: float
    prompt_used: str = ""
    backend: str = ""
    version: int = 1
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "division": self.division,
            "asset_type": self.asset_type,
            "variant": self.variant,
            "width": self.width,
            "height": self.height,
            "file_path": self.file_path,
            "file_size_bytes": self.file_size_bytes,
            "created_at": self.created_at,
            "prompt_used": self.prompt_used,
            "backend": self.backend,
            "version": self.version,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AssetMetadata:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class AssetManager:
    """Manages generated assets with versioning and caching.

    Provides:
    - Persistent storage with metadata JSON sidecar files
    - Version tracking (newer versions increment version counter)
    - Division and type-scoped queries
    - Automatic cleanup of old assets
    """

    INDEX_FILENAME = "assets_index.json"

    def __init__(self, storage_path: str = "kazma-data/assets") -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, AssetMetadata] = {}
        self._load_index()

    def _index_path(self) -> Path:
        return self.storage_path / self.INDEX_FILENAME

    def _load_index(self) -> None:
        """Load asset index from disk."""
        idx_path = self._index_path()
        if idx_path.exists():
            try:
                with open(idx_path) as f:
                    data = json.load(f)
                self._index = {
                    k: AssetMetadata.from_dict(v) for k, v in data.items()
                }
                logger.debug("Loaded %d assets from index", len(self._index))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to load asset index: %s", exc)
                self._index = {}
        else:
            self._index = {}

    def _save_index(self) -> None:
        """Persist asset index to disk."""
        idx_path = self._index_path()
        data = {k: v.to_dict() for k, v in self._index.items()}
        with open(idx_path, "w") as f:
            json.dump(data, f, indent=2)

    def _generate_asset_id(self, division: str, asset_type: str) -> str:
        ts = int(time.time() * 1000)
        rand = uuid.uuid4().hex[:8]
        return f"asset-{division}-{asset_type}-{ts}-{rand}"

    def _determine_asset_type(self, asset: Any) -> str:
        """Infer asset type from the object's class name."""
        class_name = type(asset).__name__
        if "Video" in class_name:
            return "video"
        if "Image" in class_name:
            return getattr(asset, "image_type", "image")
        return "unknown"

    def _get_file_extension(self, asset_type: str) -> str:
        if asset_type == "video":
            return ".mp4"
        return ".png"

    async def store_asset(
        self,
        asset: Any,
        division: str,
        asset_type: str,
        metadata: dict | None = None,
    ) -> str:
        """Store asset with metadata, return asset_id.

        Args:
            asset: GeneratedImage or GeneratedVideo instance.
            division: Division identifier.
            asset_type: Asset type string (e.g. "logo", "marketing").
            metadata: Optional extra metadata dict.

        Returns:
            Asset ID string.
        """
        asset_id = getattr(asset, "image_id", None) or getattr(
            asset, "video_id", None
        ) or self._generate_asset_id(division, asset_type)

        # Determine file extension
        ext = self._get_file_extension(asset_type)
        rel_path = f"{division}/{asset_type}/{asset_id}{ext}"
        abs_path = self.storage_path / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        # Write asset data
        data = getattr(asset, "data", None)
        file_size = 0
        if data and isinstance(data, bytes):
            with open(abs_path, "wb") as f:
                f.write(data)
            file_size = len(data)
        else:
            # Write a placeholder file
            placeholder = json.dumps({
                "asset_id": asset_id,
                "division": division,
                "type": asset_type,
            }).encode()
            with open(abs_path, "wb") as f:
                f.write(placeholder)
            file_size = len(placeholder)

        # Determine version: if same division+type+variant exists, increment
        version = 1
        for existing in self._index.values():
            if (
                existing.division == division
                and existing.asset_type == asset_type
                and existing.variant == getattr(asset, "variant", "")
            ):
                version = max(version, existing.version + 1)

        width = getattr(asset, "width", 0)
        height = getattr(asset, "height", 0)
        variant = getattr(asset, "variant", "")
        prompt = getattr(asset, "prompt_used", "")
        backend = getattr(asset, "backend", "")

        meta = AssetMetadata(
            asset_id=asset_id,
            division=division,
            asset_type=asset_type,
            variant=variant,
            width=width,
            height=height,
            file_path=str(abs_path),
            file_size_bytes=file_size,
            created_at=time.time(),
            prompt_used=prompt,
            backend=backend,
            version=version,
            tags=[],
            extra=metadata or {},
        )

        self._index[asset_id] = meta
        self._save_index()

        logger.info(
            "Stored asset %s (%s/%s, v%d, %d bytes)",
            asset_id, division, asset_type, version, file_size,
        )
        return asset_id

    async def get_asset(self, asset_id: str) -> AssetMetadata | None:
        """Retrieve asset metadata by ID.

        Args:
            asset_id: Asset identifier.

        Returns:
            AssetMetadata if found, None otherwise.
        """
        meta = self._index.get(asset_id)
        if meta is None:
            return None
        # Verify file still exists
        if not Path(meta.file_path).exists():
            logger.warning("Asset file missing: %s", meta.file_path)
            return None
        return meta

    async def list_assets(
        self,
        division: str | None = None,
        asset_type: str | None = None,
        created_after: datetime | None = None,
    ) -> list[AssetMetadata]:
        """List assets with optional filters.

        Args:
            division: Filter by division.
            asset_type: Filter by asset type.
            created_after: Only include assets created after this datetime.

        Returns:
            List of matching AssetMetadata objects.
        """
        results: list[AssetMetadata] = []
        cutoff_ts = created_after.timestamp() if created_after else 0.0

        for meta in self._index.values():
            if division and meta.division != division:
                continue
            if asset_type and meta.asset_type != asset_type:
                continue
            if created_after and meta.created_at < cutoff_ts:
                continue
            results.append(meta)

        # Sort by creation time, newest first
        results.sort(key=lambda m: m.created_at, reverse=True)
        return results

    async def cleanup_old_assets(self, max_age_days: int = 90) -> int:
        """Remove assets older than max_age_days.

        Deletes both the asset files and index entries.

        Args:
            max_age_days: Maximum age in days before cleanup.

        Returns:
            Count of assets removed.
        """
        cutoff_ts = time.time() - (max_age_days * 86400)
        removed = 0
        to_remove: list[str] = []

        for asset_id, meta in self._index.items():
            if meta.created_at < cutoff_ts:
                to_remove.append(asset_id)

        for asset_id in to_remove:
            meta = self._index[asset_id]
            file_path = Path(meta.file_path)
            if file_path.exists():
                try:
                    file_path.unlink()
                    # Try to remove empty parent dirs
                    parent = file_path.parent
                    while parent != self.storage_path:
                        if not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                        else:
                            break
                except OSError as exc:
                    logger.warning("Failed to remove %s: %s", file_path, exc)

            del self._index[asset_id]
            removed += 1

        if removed > 0:
            self._save_index()
            logger.info("Cleaned up %d assets older than %d days", removed, max_age_days)

        return removed

    async def get_asset_count(
        self, division: str | None = None
    ) -> int:
        """Get total count of assets, optionally filtered by division."""
        if division is None:
            return len(self._index)
        return sum(1 for m in self._index.values() if m.division == division)

    async def get_total_size_bytes(
        self, division: str | None = None
    ) -> int:
        """Get total size of stored assets in bytes."""
        total = 0
        for meta in self._index.values():
            if division and meta.division != division:
                continue
            total += meta.file_size_bytes
        return total
