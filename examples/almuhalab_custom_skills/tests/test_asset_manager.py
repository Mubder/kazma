"""Tests for Asset Manager."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from almuhalab_custom_skills.asset_generation.asset_manager import AssetManager, AssetMetadata
from almuhalab_custom_skills.asset_generation.image_generator import (
    DivisionImageGenerator,
)


@pytest.fixture
def temp_storage(tmp_path):
    """Provide a temporary storage directory."""
    return str(tmp_path / "test_assets")


@pytest.fixture
def asset_manager(temp_storage):
    return AssetManager(storage_path=temp_storage)


@pytest.fixture
def mock_image_generator():
    return DivisionImageGenerator(backend="mock")


@pytest.fixture
async def sample_logo(mock_image_generator):
    return await mock_image_generator.generate_logo("gas_oil", "standard")


@pytest.fixture
async def sample_marketing(mock_image_generator):
    return await mock_image_generator.generate_marketing_material(
        "tourism", "banner", "Summer campaign"
    )


class TestAssetStorage:
    """Test storing assets."""

    @pytest.mark.asyncio
    async def test_store_logo(self, asset_manager, sample_logo):
        asset_id = await asset_manager.store_asset(
            sample_logo, "gas_oil", "logo"
        )
        assert asset_id is not None
        assert len(asset_id) > 0

    @pytest.mark.asyncio
    async def test_store_returns_unique_ids(self, asset_manager, mock_image_generator):
        img1 = await mock_image_generator.generate_logo("gas_oil")
        img2 = await mock_image_generator.generate_logo("gas_oil")
        id1 = await asset_manager.store_asset(img1, "gas_oil", "logo")
        id2 = await asset_manager.store_asset(img2, "gas_oil", "logo")
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_store_creates_file(self, asset_manager, sample_logo, temp_storage):
        asset_id = await asset_manager.store_asset(
            sample_logo, "gas_oil", "logo"
        )
        meta = await asset_manager.get_asset(asset_id)
        assert meta is not None
        assert os.path.exists(meta.file_path)

    @pytest.mark.asyncio
    async def test_store_with_metadata(self, asset_manager, sample_logo):
        asset_id = await asset_manager.store_asset(
            sample_logo, "gas_oil", "logo",
            metadata={"campaign": "Q2-2026", "approved_by": "manager"}
        )
        meta = await asset_manager.get_asset(asset_id)
        assert meta.extra["campaign"] == "Q2-2026"
        assert meta.extra["approved_by"] == "manager"

    @pytest.mark.asyncio
    async def test_version_increments(self, asset_manager, mock_image_generator):
        img1 = await mock_image_generator.generate_logo("gas_oil", "icon")
        img2 = await mock_image_generator.generate_logo("gas_oil", "icon")

        id1 = await asset_manager.store_asset(img1, "gas_oil", "logo")
        id2 = await asset_manager.store_asset(img2, "gas_oil", "logo")

        meta1 = await asset_manager.get_asset(id1)
        meta2 = await asset_manager.get_asset(id2)
        assert meta2.version > meta1.version

    @pytest.mark.asyncio
    async def test_storage_directory_created(self, temp_storage):
        nested = os.path.join(temp_storage, "deep", "nested", "path")
        manager = AssetManager(storage_path=nested)
        assert os.path.isdir(nested)

    @pytest.mark.asyncio
    async def test_store_marketing_material(self, asset_manager, sample_marketing):
        asset_id = await asset_manager.store_asset(
            sample_marketing, "tourism", "marketing"
        )
        meta = await asset_manager.get_asset(asset_id)
        assert meta.division == "tourism"
        assert meta.asset_type == "marketing"


class TestAssetRetrieval:
    """Test retrieving assets."""

    @pytest.mark.asyncio
    async def test_get_existing_asset(self, asset_manager, sample_logo):
        asset_id = await asset_manager.store_asset(
            sample_logo, "gas_oil", "logo"
        )
        meta = await asset_manager.get_asset(asset_id)
        assert meta is not None
        assert meta.asset_id == asset_id
        assert meta.division == "gas_oil"

    @pytest.mark.asyncio
    async def test_get_nonexistent_asset(self, asset_manager):
        meta = await asset_manager.get_asset("nonexistent-id")
        assert meta is None

    @pytest.mark.asyncio
    async def test_get_asset_missing_file(self, asset_manager, sample_logo):
        asset_id = await asset_manager.store_asset(
            sample_logo, "gas_oil", "logo"
        )
        # Delete the file
        meta = await asset_manager.get_asset(asset_id)
        os.unlink(meta.file_path)
        # Should return None when file is missing
        result = await asset_manager.get_asset(asset_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_metadata_fields(self, asset_manager, sample_logo):
        asset_id = await asset_manager.store_asset(
            sample_logo, "gas_oil", "logo"
        )
        meta = await asset_manager.get_asset(asset_id)
        assert meta.width == 1024
        assert meta.height == 1024
        assert meta.file_size_bytes > 0
        assert meta.created_at > 0
        assert meta.version >= 1


class TestAssetListing:
    """Test listing and filtering assets."""

    @pytest.mark.asyncio
    async def test_list_all_assets(self, asset_manager, mock_image_generator):
        for div in ["gas_oil", "tourism", "general_trading"]:
            img = await mock_image_generator.generate_logo(div)
            await asset_manager.store_asset(img, div, "logo")

        assets = await asset_manager.list_assets()
        assert len(assets) == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_division(self, asset_manager, mock_image_generator):
        for div in ["gas_oil", "tourism", "gas_oil"]:
            img = await mock_image_generator.generate_logo(div)
            await asset_manager.store_asset(img, div, "logo")

        gas_assets = await asset_manager.list_assets(division="gas_oil")
        assert len(gas_assets) == 2
        for a in gas_assets:
            assert a.division == "gas_oil"

    @pytest.mark.asyncio
    async def test_list_filter_by_type(self, asset_manager, mock_image_generator):
        img = await mock_image_generator.generate_logo("gas_oil")
        await asset_manager.store_asset(img, "gas_oil", "logo")
        mkt = await mock_image_generator.generate_marketing_material(
            "gas_oil", "banner", "Test"
        )
        await asset_manager.store_asset(mkt, "gas_oil", "marketing")

        logos = await asset_manager.list_assets(asset_type="logo")
        assert len(logos) == 1
        assert logos[0].asset_type == "logo"

    @pytest.mark.asyncio
    async def test_list_filter_by_date(self, asset_manager, mock_image_generator):
        img = await mock_image_generator.generate_logo("gas_oil")
        await asset_manager.store_asset(img, "gas_oil", "logo")

        # All assets created after an hour ago should include it
        recent = await asset_manager.list_assets(
            created_after=datetime.now(UTC) - timedelta(hours=1)
        )
        assert len(recent) == 1

        # No assets created after next hour
        future = await asset_manager.list_assets(
            created_after=datetime.now(UTC) + timedelta(hours=1)
        )
        assert len(future) == 0

    @pytest.mark.asyncio
    async def test_list_sorted_newest_first(self, asset_manager, mock_image_generator):
        for div in ["gas_oil", "tourism", "general_trading"]:
            img = await mock_image_generator.generate_logo(div)
            await asset_manager.store_asset(img, div, "logo")

        assets = await asset_manager.list_assets()
        timestamps = [a.created_at for a in assets]
        assert timestamps == sorted(timestamps, reverse=True)


class TestAssetCleanup:
    """Test old asset cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_assets(self, asset_manager, mock_image_generator):
        img = await mock_image_generator.generate_logo("gas_oil")
        asset_id = await asset_manager.store_asset(img, "gas_oil", "logo")

        # Tamper with created_at to make it old
        meta = await asset_manager.get_asset(asset_id)
        meta.created_at = time.time() - (100 * 86400)  # 100 days ago
        asset_manager._index[asset_id] = meta
        asset_manager._save_index()

        removed = await asset_manager.cleanup_old_assets(max_age_days=90)
        assert removed == 1
        assert await asset_manager.get_asset(asset_id) is None

    @pytest.mark.asyncio
    async def test_cleanup_keeps_recent_assets(self, asset_manager, mock_image_generator):
        img = await mock_image_generator.generate_logo("gas_oil")
        asset_id = await asset_manager.store_asset(img, "gas_oil", "logo")

        removed = await asset_manager.cleanup_old_assets(max_age_days=90)
        assert removed == 0
        assert await asset_manager.get_asset(asset_id) is not None

    @pytest.mark.asyncio
    async def test_cleanup_returns_count(self, asset_manager, mock_image_generator):
        for i in range(5):
            img = await mock_image_generator.generate_logo("gas_oil")
            aid = await asset_manager.store_asset(img, "gas_oil", "logo")
            meta = await asset_manager.get_asset(aid)
            meta.created_at = time.time() - (100 * 86400)
            asset_manager._index[aid] = meta
        asset_manager._save_index()

        removed = await asset_manager.cleanup_old_assets(max_age_days=90)
        assert removed == 5


class TestAssetPersistence:
    """Test index persistence across manager instances."""

    @pytest.mark.asyncio
    async def test_index_persists(self, temp_storage, mock_image_generator):
        # Store with first manager
        mgr1 = AssetManager(storage_path=temp_storage)
        img = await mock_image_generator.generate_logo("gas_oil")
        asset_id = await mgr1.store_asset(img, "gas_oil", "logo")

        # Retrieve with second manager
        mgr2 = AssetManager(storage_path=temp_storage)
        meta = await mgr2.get_asset(asset_id)
        assert meta is not None
        assert meta.division == "gas_oil"

    @pytest.mark.asyncio
    async def test_index_file_exists(self, asset_manager, sample_logo):
        await asset_manager.store_asset(sample_logo, "gas_oil", "logo")
        idx_path = os.path.join(
            asset_manager.storage_path, AssetManager.INDEX_FILENAME
        )
        assert os.path.exists(idx_path)

    @pytest.mark.asyncio
    async def test_index_is_valid_json(self, asset_manager, sample_logo):
        await asset_manager.store_asset(sample_logo, "gas_oil", "logo")
        idx_path = os.path.join(
            asset_manager.storage_path, AssetManager.INDEX_FILENAME
        )
        with open(idx_path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) == 1


class TestAssetMetadata:
    """Test AssetMetadata data structure."""

    def test_to_dict(self):
        meta = AssetMetadata(
            asset_id="test-123",
            division="gas_oil",
            asset_type="logo",
            variant="standard",
            width=1024,
            height=1024,
            file_path="/tmp/test.png",
            file_size_bytes=1024,
            created_at=1234567890.0,
        )
        d = meta.to_dict()
        assert d["asset_id"] == "test-123"
        assert d["division"] == "gas_oil"

    def test_from_dict(self):
        d = {
            "asset_id": "test-456",
            "division": "tourism",
            "asset_type": "marketing",
            "variant": "banner",
            "width": 1920,
            "height": 600,
            "file_path": "/tmp/test2.png",
            "file_size_bytes": 2048,
            "created_at": 1234567891.0,
            "prompt_used": "test prompt",
            "backend": "mock",
            "version": 1,
            "tags": ["campaign"],
            "extra": {},
        }
        meta = AssetMetadata.from_dict(d)
        assert meta.asset_id == "test-456"
        assert meta.division == "tourism"

    def test_roundtrip(self):
        meta = AssetMetadata(
            asset_id="rt-1",
            division="general_trading",
            asset_type="logo",
            variant="icon",
            width=512,
            height=512,
            file_path="/tmp/rt.png",
            file_size_bytes=512,
            created_at=time.time(),
            tags=["test"],
        )
        d = meta.to_dict()
        meta2 = AssetMetadata.from_dict(d)
        assert meta2.asset_id == meta.asset_id
        assert meta2.tags == ["test"]


class TestAssetCounts:
    """Test count and size queries."""

    @pytest.mark.asyncio
    async def test_asset_count(self, asset_manager, mock_image_generator):
        for div in ["gas_oil", "tourism", "gas_oil"]:
            img = await mock_image_generator.generate_logo(div)
            await asset_manager.store_asset(img, div, "logo")

        assert await asset_manager.get_asset_count() == 3
        assert await asset_manager.get_asset_count(division="gas_oil") == 2
        assert await asset_manager.get_asset_count(division="tourism") == 1

    @pytest.mark.asyncio
    async def test_total_size(self, asset_manager, mock_image_generator):
        for div in ["gas_oil", "tourism"]:
            img = await mock_image_generator.generate_logo(div)
            await asset_manager.store_asset(img, div, "logo")

        total = await asset_manager.get_total_size_bytes()
        assert total > 0

        gas_size = await asset_manager.get_total_size_bytes(division="gas_oil")
        assert gas_size > 0
        assert gas_size <= total
