"""YOLO TTL + enable/disable audit helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.safety.yolo import disable_yolo, enable_yolo, is_yolo_active, yolo_status


@pytest.fixture()
def mem_store():
    data: dict = {}

    store = MagicMock()

    def _get(key, default=None):
        return data.get(key, default)

    def _set(key, value, category="general"):
        data[key] = value

    def _delete(key):
        data.pop(key, None)

    store.get.side_effect = _get
    store.set.side_effect = _set
    store.delete.side_effect = _delete
    return store, data


def test_enable_disable(mem_store):
    store, data = mem_store
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        st = enable_yolo("t1", actor="test")
        assert st["active"] is True
        assert is_yolo_active("t1") is True
        disable_yolo("t1", actor="test")
        assert is_yolo_active("t1") is False


def test_legacy_true_still_active(mem_store):
    store, data = mem_store
    data["yolo.t2"] = True
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        assert is_yolo_active("t2") is True


def test_expiry_auto_disables(mem_store, monkeypatch):
    store, data = mem_store
    monkeypatch.setenv("KAZMA_YOLO_TTL_SECONDS", "60")
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        enable_yolo("t3", actor="test")
        # Force expiry
        data["yolo.t3"]["expires_at"] = time.time() - 1
        assert is_yolo_active("t3") is False
        assert "yolo.t3" not in data
