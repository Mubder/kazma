"""Tests for CapabilityRouter — capability-based auto-routing of swarm tasks."""

from __future__ import annotations

import pytest

from kazma_core.swarm.router import CapabilityRouter, NoCapableWorkersError
from kazma_core.swarm.task import SwarmTask, TaskType, WorkerCapabilities


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def router() -> CapabilityRouter:
    return CapabilityRouter()


@pytest.fixture
def python_expert() -> WorkerCapabilities:
    return WorkerCapabilities(
        role="backend_core",
        expertise=["python", "api_design", "database", "sqlalchemy"],
        tools=["pytest", "ruff"],
        model_specialty="coding",
    )


@pytest.fixture
def frontend_expert() -> WorkerCapabilities:
    return WorkerCapabilities(
        role="frontend_ux",
        expertise=["react", "typescript", "css", "html"],
        tools=["eslint", "prettier"],
        model_specialty="creative",
    )


@pytest.fixture
def researcher() -> WorkerCapabilities:
    return WorkerCapabilities(
        role="researcher",
        expertise=["research", "analysis", "data_science", "python"],
        tools=["web_search", "calculator"],
        model_specialty="reasoning",
    )


@pytest.fixture
def generic_worker() -> WorkerCapabilities:
    return WorkerCapabilities(
        role="generalist",
        expertise=[],
        tools=[],
        model_specialty="fast",
    )


def _make_worker_info(
    name: str,
    capabilities: WorkerCapabilities,
) -> dict:
    """Helper to build a worker info dict like SwarmEngine provides."""
    return {
        "name": name,
        "role": capabilities.role,
        "capabilities": capabilities,
    }


# ---------------------------------------------------------------------------
# Core routing behavior
# ---------------------------------------------------------------------------

class TestCapabilityRouterBasicRouting:
    """Test fundamental routing logic."""

    def test_explicit_workers_returned_unchanged(self, router, python_expert):
        """When task.workers is explicit (not 'auto'), return those workers."""
        task = SwarmTask(prompt="Fix the auth bug", workers=["python-dev"])
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert result == ["python-dev"]

    def test_explicit_workers_multiple_returned(self, router, python_expert, frontend_expert):
        """Multiple explicit workers are returned as-is."""
        task = SwarmTask(prompt="Build feature", workers=["python-dev", "react-dev"])
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("react-dev", frontend_expert),
        ]

        result = router.route(task, workers)

        assert result == ["python-dev", "react-dev"]

    def test_auto_routing_selects_matching_workers(
        self, router, python_expert, frontend_expert,
    ):
        """workers=['auto'] triggers capability-based selection."""
        task = SwarmTask(
            prompt="Build a Python API endpoint",
            workers=["auto"],
            metadata={"requirements": ["python", "api_design"]},
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("react-dev", frontend_expert),
        ]

        result = router.route(task, workers)

        assert "python-dev" in result
        # frontend should score lower or not match
        assert len(result) >= 1

    def test_auto_routing_excludes_non_matching_workers(
        self, router, python_expert, frontend_expert,
    ):
        """Workers with no capability overlap are excluded."""
        task = SwarmTask(
            prompt="Build a REST API with SQLAlchemy",
            workers=["auto"],
            metadata={"requirements": ["python", "sqlalchemy"]},
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("react-dev", frontend_expert),
        ]

        result = router.route(task, workers)

        assert "python-dev" in result
        assert "react-dev" not in result

    def test_no_matching_workers_raises_error(self, router, frontend_expert):
        """When no workers match, raises NoCapableWorkersError."""
        task = SwarmTask(
            prompt="Deploy Kubernetes cluster",
            workers=["auto"],
            metadata={"requirements": ["kubernetes", "devops"]},
        )
        workers = [_make_worker_info("react-dev", frontend_expert)]

        with pytest.raises(NoCapableWorkersError) as exc_info:
            router.route(task, workers)

        assert "no capable workers" in str(exc_info.value).lower()

    def test_no_workers_at_all_raises_error(self, router):
        """Empty worker list raises NoCapableWorkersError."""
        task = SwarmTask(prompt="Do something", workers=["auto"])

        with pytest.raises(NoCapableWorkersError):
            router.route(task, [])


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------

class TestCapabilityRouterScoring:
    """Test keyword matching and scoring mechanics."""

    def test_expertise_keywords_matched_against_prompt(
        self, router, python_expert, researcher,
    ):
        """Router matches expertise keywords against task prompt text."""
        task = SwarmTask(
            prompt="Write Python code to process data",
            workers=["auto"],
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("researcher", researcher),
        ]

        result = router.route(task, workers)

        # Both have "python" expertise, so both should match
        assert len(result) >= 1

    def test_metadata_requirements_used_for_matching(self, router, python_expert):
        """metadata.requirements keywords are used in scoring."""
        task = SwarmTask(
            prompt="Implement the feature",
            workers=["auto"],
            metadata={"requirements": ["database", "sqlalchemy"]},
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert "python-dev" in result

    def test_context_used_for_matching(self, router, python_expert):
        """Task context text is also used for keyword matching."""
        task = SwarmTask(
            prompt="Help with this task",
            context="We need to build a REST API using Python and FastAPI",
            workers=["auto"],
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert "python-dev" in result

    def test_top_n_limit_respected(self, router, python_expert, researcher, frontend_expert):
        """Router returns at most top_n workers."""
        task = SwarmTask(
            prompt="Python data analysis research",
            workers=["auto"],
            metadata={"top_n": 1},
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("researcher", researcher),
            _make_worker_info("react-dev", frontend_expert),
        ]

        result = router.route(task, workers)

        assert len(result) <= 1

    def test_workers_sorted_by_score_descending(self, router, python_expert, researcher):
        """Higher-scoring workers appear first."""
        task = SwarmTask(
            prompt="Build Python API with database and SQLAlchemy ORM",
            workers=["auto"],
            metadata={"top_n": 2},
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("researcher", researcher),
        ]

        result = router.route(task, workers)

        # python-dev has more API/database/SQLAlchemy matches
        assert result[0] == "python-dev"

    def test_role_keyword_contributes_to_score(self, router, python_expert):
        """Worker role is also matched against task text."""
        task = SwarmTask(
            prompt="Need a backend_core specialist for API work",
            workers=["auto"],
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert "python-dev" in result

    def test_model_specialty_contributes_to_score(self, router, researcher):
        """model_specialty is also used in keyword matching."""
        task = SwarmTask(
            prompt="Need reasoning and analysis for this research task",
            workers=["auto"],
        )
        workers = [_make_worker_info("researcher", researcher)]

        result = router.route(task, workers)

        assert "researcher" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestCapabilityRouterEdgeCases:
    """Test boundary conditions and special scenarios."""

    def test_worker_with_empty_expertise_scores_zero(self, router, generic_worker):
        """Worker with no expertise has zero score unless role/specialty match."""
        task = SwarmTask(
            prompt="Build a complex Python application",
            workers=["auto"],
            metadata={"requirements": ["python"]},
        )
        workers = [_make_worker_info("generalist", generic_worker)]

        with pytest.raises(NoCapableWorkersError):
            router.route(task, workers)

    def test_case_insensitive_matching(self, router, python_expert):
        """Keyword matching is case-insensitive."""
        task = SwarmTask(
            prompt="BUILD A PYTHON API",
            workers=["auto"],
            metadata={"requirements": ["PYTHON", "API_DESIGN"]},
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert "python-dev" in result

    def test_underscore_normalized_keywords(self, router, python_expert):
        """Keywords with underscores match hyphenated or space-separated words."""
        task = SwarmTask(
            prompt="Design the API",
            workers=["auto"],
            metadata={"requirements": ["api design"]},
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert "python-dev" in result

    def test_single_matching_worker_selected(self, router, python_expert, frontend_expert):
        """When only one worker matches, it is selected."""
        task = SwarmTask(
            prompt="Fix the Python backend code",
            workers=["auto"],
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("react-dev", frontend_expert),
        ]

        result = router.route(task, workers)

        assert len(result) == 1
        assert result[0] == "python-dev"

    def test_all_workers_matching_returns_all_sorted(self, router, python_expert, researcher):
        """When all workers match, all are returned sorted by score."""
        task = SwarmTask(
            prompt="Python research and analysis",
            workers=["auto"],
            metadata={"top_n": 10},
        )
        workers = [
            _make_worker_info("python-dev", python_expert),
            _make_worker_info("researcher", researcher),
        ]

        result = router.route(task, workers)

        assert len(result) == 2

    def test_explicit_auto_single_worker(self, router, python_expert):
        """workers=['auto'] with a single matching worker works."""
        task = SwarmTask(prompt="Python coding task", workers=["auto"])
        workers = [_make_worker_info("python-dev", python_expert)]

        result = router.route(task, workers)

        assert result == ["python-dev"]


# ---------------------------------------------------------------------------
# Metadata recording
# ---------------------------------------------------------------------------

class TestCapabilityRouterMetadata:
    """Test that routing metadata is properly recorded."""

    def test_routed_workers_recorded_in_task_metadata(self, router, python_expert):
        """After routing, task.metadata['routed_workers'] is set."""
        task = SwarmTask(
            prompt="Build a Python API",
            workers=["auto"],
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        router.route(task, workers)

        assert "routed_workers" in task.metadata
        assert task.metadata["routed_workers"] == ["python-dev"]

    def test_routing_scores_recorded_in_metadata(self, router, python_expert):
        """Routing scores are recorded in task metadata for observability."""
        task = SwarmTask(
            prompt="Build a Python API",
            workers=["auto"],
        )
        workers = [_make_worker_info("python-dev", python_expert)]

        router.route(task, workers)

        assert "routing_scores" in task.metadata
        assert "python-dev" in task.metadata["routing_scores"]

    def test_explicit_workers_dont_modify_metadata(self, router, python_expert):
        """Explicit worker list does not add routing metadata."""
        task = SwarmTask(prompt="Fix bug", workers=["python-dev"])
        workers = [_make_worker_info("python-dev", python_expert)]

        router.route(task, workers)

        assert "routed_workers" not in task.metadata
        assert "routing_scores" not in task.metadata


# ---------------------------------------------------------------------------
# Integration with SwarmEngine (dispatch with auto-routing)
# ---------------------------------------------------------------------------

class TestAutoRoutingIntegration:
    """Test that auto-routing integrates correctly with SwarmEngine dispatch."""

    @pytest.mark.asyncio
    async def test_auto_routing_recording_routed_workers_in_result_metadata(self):
        """VAL-ORCH-049: Auto-routed task records routed_workers in result metadata."""
        from unittest.mock import AsyncMock

        from kazma_core.swarm.config import SwarmConfig, WorkerConfig
        from kazma_core.swarm.engine import SwarmEngine

        config = SwarmConfig(
            enabled=True,
            workers=[
                WorkerConfig(
                    name="python-dev",
                    type="in_process",
                    role="backend_core",
                    model="gpt-4o-mini",
                    provider="openai",
                    capabilities=WorkerCapabilities(
                        role="backend_core",
                        expertise=["python", "api_design", "database"],
                        tools=["pytest"],
                        model_specialty="coding",
                    ),
                ),
                WorkerConfig(
                    name="react-dev",
                    type="in_process",
                    role="frontend_ux",
                    model="gpt-4o-mini",
                    provider="openai",
                    capabilities=WorkerCapabilities(
                        role="frontend_ux",
                        expertise=["react", "typescript", "css"],
                        tools=["eslint"],
                        model_specialty="creative",
                    ),
                ),
            ],
        )
        engine = SwarmEngine(config)

        python_worker = engine.get_worker("python-dev")
        assert python_worker is not None
        python_worker.dispatch = AsyncMock(return_value={
            "worker": "python-dev",
            "task_id": "task-auto",
            "status": "success",
            "output": "Python API built",
            "error": None,
        })

        task = SwarmTask(
            prompt="Build a Python REST API",
            workers=["auto"],
        )
        result = await engine.dispatch(task)

        assert result.status == "success"
        assert "routed_workers" in result.metadata
        assert "python-dev" in result.metadata["routed_workers"]

    @pytest.mark.asyncio
    async def test_auto_routing_excludes_non_matching_workers(self):
        """VAL-ORCH-049: Non-matching workers are not included in results."""
        from unittest.mock import AsyncMock

        from kazma_core.swarm.config import SwarmConfig, WorkerConfig
        from kazma_core.swarm.engine import SwarmEngine

        config = SwarmConfig(
            enabled=True,
            workers=[
                WorkerConfig(
                    name="python-dev",
                    type="in_process",
                    role="backend_core",
                    model="gpt-4o-mini",
                    provider="openai",
                    capabilities=WorkerCapabilities(
                        role="backend_core",
                        expertise=["python", "api_design"],
                        tools=[],
                        model_specialty="coding",
                    ),
                ),
                WorkerConfig(
                    name="react-dev",
                    type="in_process",
                    role="frontend_ux",
                    model="gpt-4o-mini",
                    provider="openai",
                    capabilities=WorkerCapabilities(
                        role="frontend_ux",
                        expertise=["react", "typescript"],
                        tools=[],
                        model_specialty="creative",
                    ),
                ),
            ],
        )
        engine = SwarmEngine(config)

        python_worker = engine.get_worker("python-dev")
        assert python_worker is not None
        python_worker.dispatch = AsyncMock(return_value={
            "worker": "python-dev",
            "task_id": "task-auto",
            "status": "success",
            "output": "API done",
            "error": None,
        })

        task = SwarmTask(
            prompt="Build a Python API endpoint",
            workers=["auto"],
        )
        result = await engine.dispatch(task)

        assert result.status == "success"
        worker_names = [wr.worker for wr in result.worker_results]
        assert "python-dev" in worker_names
        assert "react-dev" not in worker_names

    @pytest.mark.asyncio
    async def test_auto_routing_no_match_returns_failed(self):
        """VAL-ORCH-049: No matching workers returns a clear error."""
        from kazma_core.swarm.config import SwarmConfig, WorkerConfig
        from kazma_core.swarm.engine import SwarmEngine

        config = SwarmConfig(
            enabled=True,
            workers=[
                WorkerConfig(
                    name="react-dev",
                    type="in_process",
                    role="frontend_ux",
                    model="gpt-4o-mini",
                    provider="openai",
                    capabilities=WorkerCapabilities(
                        role="frontend_ux",
                        expertise=["react", "typescript"],
                        tools=[],
                        model_specialty="creative",
                    ),
                ),
            ],
        )
        engine = SwarmEngine(config)

        task = SwarmTask(
            prompt="Deploy Kubernetes cluster",
            workers=["auto"],
            metadata={"requirements": ["kubernetes", "devops"]},
        )
        result = await engine.dispatch(task)

        assert result.status == "failed"
        assert "no capable workers" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_auto_routing_considers_spawned_workers(self):
        """VAL-SPAWN-004: Spawned workers are considered by auto-routing."""
        from unittest.mock import AsyncMock

        from kazma_core.swarm.config import SwarmConfig
        from kazma_core.swarm.engine import SwarmEngine
        from kazma_core.swarm.task import WorkerCapabilities

        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))

        await engine.spawn_worker(
            name="spawned-expert",
            role="devops",
            capabilities=WorkerCapabilities(
                role="devops",
                expertise=["kubernetes", "docker", "terraform"],
                tools=["kubectl"],
                model_specialty="coding",
            ),
        )

        spawned = engine.get_worker("spawned-expert")
        assert spawned is not None
        spawned.dispatch = AsyncMock(return_value={
            "worker": "spawned-expert",
            "task_id": "task-spawned",
            "status": "success",
            "output": "K8s deployed",
            "error": None,
        })

        task = SwarmTask(
            prompt="Deploy Kubernetes cluster with Docker containers",
            workers=["auto"],
        )
        result = await engine.dispatch(task)

        assert result.status == "success"
        assert "spawned-expert" in result.metadata.get("routed_workers", [])

    @pytest.mark.asyncio
    async def test_auto_routing_with_top_n_metadata(self):
        """top_n in metadata limits the number of selected workers."""
        from unittest.mock import AsyncMock

        from kazma_core.swarm.config import SwarmConfig, WorkerConfig
        from kazma_core.swarm.engine import SwarmEngine
        from kazma_core.swarm.task import WorkerCapabilities

        config = SwarmConfig(
            enabled=True,
            workers=[
                WorkerConfig(
                    name=f"worker-{i}",
                    type="in_process",
                    role="general",
                    model="gpt-4o-mini",
                    provider="openai",
                    capabilities=WorkerCapabilities(
                        role="general",
                        expertise=["python", "coding"],
                        tools=[],
                        model_specialty="coding",
                    ),
                )
                for i in range(5)
            ],
        )
        engine = SwarmEngine(config)

        for i in range(5):
            worker = engine.get_worker(f"worker-{i}")
            assert worker is not None
            worker.dispatch = AsyncMock(return_value={
                "worker": f"worker-{i}",
                "task_id": f"task-{i}",
                "status": "success",
                "output": f"Result from worker-{i}",
                "error": None,
            })

        task = SwarmTask(
            prompt="Python coding task",
            workers=["auto"],
            metadata={"top_n": 2},
        )
        result = await engine.dispatch(task)

        assert result.status == "success"
        assert len(result.worker_results) <= 2
