"""Remaining-gap hardenings: Docker-only exec, tenant filters, replica cookie."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.tools.code_exec import local_exec_forbidden, use_docker_jail


def test_local_exec_forbidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", raising=False)
    assert local_exec_forbidden() is True
    assert use_docker_jail() is True


def test_local_exec_allowed_with_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", "1")
    monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "0")
    assert local_exec_forbidden() is False


def test_task_store_injects_tenant_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
    from kazma_core.swarm.task_store import TaskStore
    from kazma_core.tenant_context import set_current_tenant_id

    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_TENANT_FILTER", "1")
    store = TaskStore(db_path=str(tmp_path / "tasks.db"))

    set_current_tenant_id("tenant-a")
    t_a = SwarmTask(prompt="a", workers=["w"], type=TaskType.DISPATCH)
    t_a.metadata = {"tenant_id": "tenant-a"}
    t_a.status = TaskStatus.COMPLETED
    store.persist_task(t_a)

    set_current_tenant_id("tenant-b")
    t_b = SwarmTask(prompt="b", workers=["w"], type=TaskType.DISPATCH)
    t_b.metadata = {"tenant_id": "tenant-b"}
    t_b.status = TaskStatus.COMPLETED
    store.persist_task(t_b)

    set_current_tenant_id("tenant-a")
    listed = store.list_tasks(page=1, page_size=50)
    ids = {t.id for t in listed}
    assert t_a.id in ids
    assert t_b.id not in ids


def test_knowledge_tenant_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.stores.knowledge import KnowledgeStore, reset_knowledge_store
    from kazma_core.tenant_context import set_current_tenant_id

    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_TENANT_FILTER", "1")
    reset_knowledge_store()
    store = KnowledgeStore(db_path=str(tmp_path / "kb.db"))

    set_current_tenant_id("t1")
    store.create_library("lib_a", name="A")
    set_current_tenant_id("t2")
    store.create_library("lib_b", name="B")

    set_current_tenant_id("t1")
    libs = store.list_libraries()
    ids = {x["id"] for x in libs}
    assert "lib_a" in ids
    assert "lib_b" not in ids
    assert store.get_library("lib_b") is None
    assert store.get_library("lib_a") is not None
    reset_knowledge_store()


def test_replica_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_ui.replica_affinity import replica_id

    monkeypatch.setenv("KAZMA_REPLICA_ID", "node-west-1")
    assert replica_id() == "node-west-1"
