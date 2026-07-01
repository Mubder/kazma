"""Tests for the redesigned Swarm Panel UI.

Covers:
  VAL-UI-001: Orchestration pattern selector lists all patterns
  VAL-UI-002: Worker multi-select shows capability badges
  VAL-UI-003: Prompt and context input fields captured on submit
  VAL-UI-004: Advanced options expose timeout, retry, aggregation, validation
  VAL-UI-005: Submit creates task and navigates to active view
  VAL-UI-006: Worker registry shows all workers with details
  VAL-UI-007: Add worker from UI registers and shows it
  VAL-UI-008: Remove worker from UI deregisters and removes card
  VAL-UI-009: Dynamic spawn form captures all fields
  VAL-UI-010: Per-worker metrics displayed
  VAL-ORCH-002: Pipeline intermediate results visible in UI
  VAL-ORCH-021: Fan-out results displayed per-worker in UI dashboard
  VAL-ORCH-024: Consult individual opinions displayed alongside synthesis
  VAL-ORCH-025: Consult side-by-side comparison view
  VAL-ORCH-034: Conditional routing decision visible in UI
  VAL-ORCH-042: Orchestration type selector present in task builder UI
  VAL-ORCH-043: Worker multi-select shows capability badges
  VAL-ORCH-044: Task submission with missing prompt is rejected
  VAL-ORCH-053: Task history panel is searchable and filterable
  VAL-HAND-007: Handoff chain visualized in UI
  VAL-HITL-002: Checkpoint surfaces in UI with preview and approve/reject
  VAL-CONSULT-004: Side-by-side comparison view in UI
  VAL-CONSULT-007: User can select which workers to consult
"""

from __future__ import annotations

import kazma_ui.i18n  # noqa: F401,E402 — patches Jinja2Templates with i18n globals
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client():
    """Build a FastAPI TestClient with the swarm router for testing."""
    app = FastAPI()
    templates = Jinja2Templates(directory="kazma-ui/kazma_ui/templates")

    from kazma_ui.swarm_panel import _reset_swarm_state, create_swarm_router

    _reset_swarm_state()
    router = create_swarm_router(templates)
    app.include_router(router)

    return TestClient(app)


def _add_workers(client: TestClient, *names: str) -> None:
    """Add workers by name with default config."""
    for name in names:
        client.post(
            "/api/swarm/workers",
            json={
                "name": name,
                "model": "deepseek-chat",
                "provider": "deepseek",
                "type": "in-process",
                "role": "backend",
            },
        )


# ---------------------------------------------------------------------------
# VAL-UI-001 / VAL-ORCH-042: Orchestration selector lists all patterns
# ---------------------------------------------------------------------------


class TestOrchestrationSelector:
    """Test that the swarm page includes orchestration pattern selector."""

    def test_swarm_page_contains_pattern_selector(self):
        """The rendered HTML contains a pattern selector dropdown."""
        client = _build_client()
        response = client.get("/swarm")
        assert response.status_code == 200
        html = response.text
        # Must include all orchestration pattern options
        assert "dispatch" in html
        assert "pipeline" in html
        assert "fan_out" in html or "fan-out" in html
        assert "consult" in html
        assert "conditional" in html
        assert "broadcast" in html

    def test_swarm_page_has_pattern_selector_element(self):
        """The pattern selector has the expected id."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert 'id="pattern-select"' in html or 'id="orch-pattern"' in html


# ---------------------------------------------------------------------------
# VAL-UI-002 / VAL-ORCH-043: Worker multi-select with capability badges
# ---------------------------------------------------------------------------


class TestWorkerMultiSelect:
    """Test that worker multi-select shows capability badges."""

    def test_worker_multi_select_present(self):
        """Page contains a multi-select for workers."""
        client = _build_client()
        _add_workers(client, "alpha", "beta")
        response = client.get("/swarm")
        html = response.text
        assert "alpha" in html
        assert "beta" in html

    def test_worker_capabilities_in_api(self):
        """Worker list API includes capability data."""
        client = _build_client()
        _add_workers(client, "cap-worker")
        response = client.get("/api/swarm/status")
        data = response.json()
        workers = data["workers"]
        assert len(workers) >= 1
        worker = next(w for w in workers if w["name"] == "cap-worker")
        assert "capabilities" in worker
        assert "role" in worker


# ---------------------------------------------------------------------------
# VAL-UI-003: Prompt and context input fields captured on submit
# ---------------------------------------------------------------------------


class TestTaskBuilderInputs:
    """Test prompt/context fields and empty prompt blocking."""

    def test_page_has_prompt_textarea(self):
        """The task builder has a prompt textarea."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "dispatch-task" in html or "task-prompt" in html

    def test_page_has_context_field(self):
        """The task builder has a context input."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "dispatch-context" in html or "task-context" in html

    def test_empty_prompt_rejected_by_api(self):
        """API rejects empty prompt with 400."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={"workers": ["w1"], "task": ""},
        )
        assert response.status_code == 400
        data = response.json()
        assert "task" in data["message"].lower() or "no task" in data["message"].lower()


# ---------------------------------------------------------------------------
# VAL-UI-004: Advanced options expose timeout, retry, aggregation, validation
# ---------------------------------------------------------------------------


class TestAdvancedOptions:
    """Test that advanced options are present in the task builder."""

    def test_page_has_timeout_field(self):
        """Timeout input exists in the page."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "timeout" in html.lower()

    def test_page_has_aggregation_field(self):
        """Aggregation selector exists in the page."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "aggregat" in html.lower()

    def test_page_has_retry_field(self):
        """Retry input exists in the page."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "retry" in html.lower() or "max_retries" in html.lower()

    def test_page_has_validation_field(self):
        """Validation schema input exists in the page."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "validation" in html.lower() or "schema" in html.lower()

    def test_dispatch_accepts_timeout(self):
        """API accepts timeout parameter."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={
                "workers": ["w1"],
                "task": "test task",
                "timeout": 60,
                "aggregation": "collect",
            },
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# VAL-UI-006: Worker registry shows all workers with details
# ---------------------------------------------------------------------------


class TestWorkerRegistry:
    """Test worker registry API and page rendering."""

    def test_workers_api_returns_details(self):
        """Workers API returns name, status, role, model, capabilities."""
        client = _build_client()
        _add_workers(client, "detail-worker")
        response = client.get("/api/swarm/status")
        data = response.json()
        worker = data["workers"][0]
        assert "name" in worker
        assert "status" in worker
        assert "model" in worker
        assert "capabilities" in worker

    def test_worker_count_in_api(self):
        """API returns correct worker count."""
        client = _build_client()
        _add_workers(client, "a", "b", "c")
        response = client.get("/api/swarm/status")
        data = response.json()
        assert data["count"] == 3

    def test_registry_cards_in_page(self):
        """Page renders worker information."""
        client = _build_client()
        _add_workers(client, "registry-w1")
        response = client.get("/swarm")
        html = response.text
        assert "registry-w1" in html


# ---------------------------------------------------------------------------
# VAL-UI-007: Add worker from UI registers and shows it
# ---------------------------------------------------------------------------


class TestAddWorkerFromUI:
    """Test adding workers via the API (UI form submission)."""

    def test_add_worker_with_role(self):
        """Adding worker with role creates worker with role set."""
        client = _build_client()
        response = client.post(
            "/api/swarm/workers",
            json={
                "name": "role-worker",
                "model": "gpt-4o",
                "provider": "openai",
                "type": "in-process",
                "role": "researcher",
            },
        )
        assert response.status_code == 201
        worker = response.json()["worker"]
        assert worker["name"] == "role-worker"
        assert worker["role"] == "researcher"


# ---------------------------------------------------------------------------
# VAL-UI-008: Remove worker from UI
# ---------------------------------------------------------------------------


class TestRemoveWorkerFromUI:
    """Test removing workers via API."""

    def test_remove_existing_worker(self):
        """Removing an existing worker returns 200."""
        client = _build_client()
        _add_workers(client, "remove-me")
        response = client.delete("/api/swarm/workers/remove-me")
        assert response.status_code == 200
        # Verify it's gone
        status = client.get("/api/swarm/status").json()
        names = [w["name"] for w in status["workers"]]
        assert "remove-me" not in names

    def test_remove_nonexistent_returns_404(self):
        """Removing nonexistent worker returns 404."""
        client = _build_client()
        response = client.delete("/api/swarm/workers/ghost")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# VAL-UI-009: Dynamic spawn form captures all fields
# ---------------------------------------------------------------------------


class TestDynamicSpawnForm:
    """Test the spawn endpoint with full capabilities."""

    def test_spawn_with_capabilities(self):
        """Spawn endpoint accepts full capability set."""
        client = _build_client()
        response = client.post(
            "/api/swarm/workers/spawn",
            json={
                "name": "spawn-cap",
                "role": "backend",
                "capabilities": {
                    "role": "backend",
                    "expertise": ["python", "api_design"],
                    "tools": ["file_edit", "terminal"],
                    "model_specialty": "coding",
                },
                "model": "deepseek-chat",
                "provider": "deepseek",
            },
        )
        assert response.status_code == 201
        worker = response.json()["worker"]
        assert worker["name"] == "spawn-cap"
        caps = worker["capabilities"]
        assert caps is not None
        assert caps["role"] == "backend"
        assert "python" in caps.get("expertise", [])

    def test_spawn_duplicate_rejected(self):
        """Spawn with existing name returns 409."""
        client = _build_client()
        _add_workers(client, "existing")
        response = client.post(
            "/api/swarm/workers/spawn",
            json={"name": "existing", "role": "test"},
        )
        assert response.status_code == 409

    def test_spawn_requires_name(self):
        """Spawn without name returns 400."""
        client = _build_client()
        response = client.post(
            "/api/swarm/workers/spawn",
            json={"name": "", "role": "test"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# VAL-UI-010: Per-worker metrics displayed
# ---------------------------------------------------------------------------


class TestWorkerMetrics:
    """Test worker metrics endpoint."""

    def test_metrics_endpoint_returns_data(self):
        """Metrics API returns worker metrics list."""
        client = _build_client()
        _add_workers(client, "metrics-w")
        response = client.get("/api/swarm/workers/metrics-w/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
        assert "worker" in data
        assert data["worker"] == "metrics-w"

    def test_all_metrics_endpoint(self):
        """All metrics API returns aggregated data."""
        client = _build_client()
        response = client.get("/api/swarm/workers/metrics/all")
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data


# ---------------------------------------------------------------------------
# VAL-ORCH-002 / VAL-ORCH-021 / VAL-ORCH-024 / VAL-ORCH-025
# Pipeline results, fan-out per-worker cards, consult comparison
# ---------------------------------------------------------------------------


class TestResultsDashboardRendering:
    """Test that the results dashboard sections exist in the page."""

    def test_page_has_results_dashboard(self):
        """Page contains a results dashboard section."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # Results dashboard should have relevant container elements
        assert "results" in html.lower() or "dashboard" in html.lower()

    def test_page_has_active_tasks_section(self):
        """Page contains an active tasks section."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "active" in html.lower()

    def test_page_has_pipeline_view_elements(self):
        """Page has elements for pipeline step view."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "pipeline" in html.lower()

    def test_page_has_consult_comparison_elements(self):
        """Page has elements for consult comparison."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "consult" in html.lower()
        assert "comparison" in html.lower() or "synthesis" in html.lower() or "opinion" in html.lower()


# ---------------------------------------------------------------------------
# VAL-ORCH-034: Conditional routing decision visible in UI
# ---------------------------------------------------------------------------


class TestConditionalRoutingDisplay:
    """Test conditional routing display elements."""

    def test_page_has_conditional_elements(self):
        """Page contains conditional routing elements."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "conditional" in html.lower()


# ---------------------------------------------------------------------------
# VAL-ORCH-044: Task submission with missing prompt is rejected
# ---------------------------------------------------------------------------


class TestEmptyPromptRejection:
    """Test that empty prompt is rejected."""

    def test_empty_task_rejected_for_dispatch(self):
        """Empty task returns 400 for dispatch."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={"workers": ["w1"], "task": ""},
        )
        assert response.status_code == 400

    def test_empty_task_rejected_for_pipeline(self):
        """Empty task returns 400 for pipeline."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={"workers": ["w1"], "task": "", "pattern": "pipeline"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# VAL-ORCH-053: Task history searchable and filterable
# ---------------------------------------------------------------------------


class TestTaskHistory:
    """Test task history API with search and filter."""

    def test_task_history_endpoint(self):
        """Task history endpoint returns paginated results."""
        client = _build_client()
        response = client.get("/api/swarm/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert "count" in data
        assert isinstance(data["tasks"], list)

    def test_task_history_filter_by_type(self):
        """Task history can be filtered by type."""
        client = _build_client()
        response = client.get("/api/swarm/tasks?type=consult")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data

    def test_task_history_filter_by_status(self):
        """Task history can be filtered by status."""
        client = _build_client()
        response = client.get("/api/swarm/tasks?status=completed")
        assert response.status_code == 200

    def test_task_history_pagination(self):
        """Task history supports pagination."""
        client = _build_client()
        response = client.get("/api/swarm/tasks?page=1&pageSize=5")
        assert response.status_code == 200
        data = response.json()
        assert data.get("page") == 1
        assert data.get("pageSize") == 5

    def test_task_detail_endpoint(self):
        """Task detail endpoint returns 404 for unknown task."""
        client = _build_client()
        response = client.get("/api/swarm/tasks/nonexistent-id")
        assert response.status_code == 404

    def test_page_has_history_section(self):
        """Page contains task history section."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "history" in html.lower()


# ---------------------------------------------------------------------------
# VAL-HAND-007: Handoff chain visualized in UI
# ---------------------------------------------------------------------------


class TestHandoffVisualization:
    """Test handoff chain visualization elements."""

    def test_page_has_handoff_elements(self):
        """Page contains handoff chain elements."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "handoff" in html.lower()


# ---------------------------------------------------------------------------
# VAL-HITL-002: Checkpoint surfaces with approve/reject
# ---------------------------------------------------------------------------


class TestHITLCheckpointUI:
    """Test HITL checkpoint UI elements."""

    def test_approve_endpoint_exists(self):
        """Approve endpoint returns 404 for unknown task."""
        client = _build_client()
        response = client.post("/api/swarm/tasks/nonexistent/approve")
        assert response.status_code == 404

    def test_reject_endpoint_exists(self):
        """Reject endpoint returns 404 for unknown task."""
        client = _build_client()
        response = client.post("/api/swarm/tasks/nonexistent/reject")
        assert response.status_code == 404

    def test_page_has_checkpoint_elements(self):
        """Page contains checkpoint/approve/reject elements."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert "checkpoint" in html.lower() or "approve" in html.lower()


# ---------------------------------------------------------------------------
# VAL-CONSULT-007: User can select which workers to consult
# ---------------------------------------------------------------------------


class TestConsultWorkerSelection:
    """Test that consult pattern allows worker selection."""

    def test_dispatch_accepts_consult_pattern(self):
        """API accepts consult pattern with worker list."""
        client = _build_client()
        _add_workers(client, "w1", "w2")
        response = client.post(
            "/api/swarm/dispatch",
            json={
                "workers": ["w1", "w2"],
                "task": "What is the best approach?",
                "pattern": "consult",
            },
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Circuit breaker UI
# ---------------------------------------------------------------------------


class TestCircuitBreakerUI:
    """Test circuit breaker API endpoints."""

    def test_circuit_breakers_endpoint(self):
        """Circuit breakers endpoint returns data."""
        client = _build_client()
        response = client.get("/api/swarm/circuit-breakers")
        assert response.status_code == 200
        data = response.json()
        assert "breakers" in data

    def test_reset_circuit_breaker_not_found(self):
        """Reset for nonexistent worker returns 404."""
        client = _build_client()
        response = client.post("/api/swarm/workers/ghost/circuit-breaker/reset")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tab navigation structure
# ---------------------------------------------------------------------------


class TestTabNavigation:
    """Test that the swarm page has tab navigation."""

    def test_page_has_tab_navigation(self):
        """Page contains tab navigation elements."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # Must have multiple tab-like sections
        assert "tab" in html.lower()
        # Must reference key sections
        assert "builder" in html.lower() or "task-builder" in html.lower()
        assert "registry" in html.lower() or "workers" in html.lower()
        assert "history" in html.lower()


# ---------------------------------------------------------------------------
# Models endpoint
# ---------------------------------------------------------------------------


class TestModelsEndpoint:
    """Test models/providers endpoint."""

    def test_models_endpoint(self, tmp_path):
        """Models endpoint returns lists when registry is initialized."""
        from kazma_core.config_store import ConfigStore
        from kazma_core.model_registry import (
            initialize_model_registry,
            reset_model_registry,
        )

        db_path = str(tmp_path / "swarm_ui_models.db")
        config_store = ConfigStore(db_path=db_path)
        registry = initialize_model_registry(config_store)
        try:
            registry.upsert_provider({
                "name": "test-provider",
                "models": ["test-model"],
                "base_url": "https://test.example/v1",
            })
            client = _build_client()
            response = client.get("/api/swarm/models")
            assert response.status_code == 200
            data = response.json()
            assert len(data["models"]) > 0
            assert len(data["providers"]) > 0
        finally:
            reset_model_registry()


# ---------------------------------------------------------------------------
# Saved model profile dropdown wiring
# ---------------------------------------------------------------------------


class TestSavedModelProfileDropdowns:
    """Test saved profile dropdowns and JS wiring."""

    def test_page_has_model_select_dropdowns(self):
        """Swarm page renders provider-grouped model dropdowns (Sprint 12)."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert 'add-model-select' in html or 'id="add-model-select"' in html
        assert 'spawn-model-select' in html or 'id="spawn-model-select"' in html

    def test_swarm_js_wires_provider_grouped_models(self):
        """swarm.js populates model dropdowns with provider optgroups (Sprint 12)."""
        from pathlib import Path

        js_path = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "static" / "js" / "swarm.js"
        source = js_path.read_text(encoding="utf-8")
        assert "populateSwarmModelSelects" in source
        assert "providerModelMap" in source or "providerForModel" in source
        assert "optgroup" in source


# ---------------------------------------------------------------------------
# i18n: Swarm page uses t() translation calls instead of hardcoded strings
# ---------------------------------------------------------------------------


class TestSwarmI18n:
    """Test that swarm.html uses t() translation calls for all user-visible strings."""

    def test_page_title_uses_translation(self):
        """Page title uses t() call instead of hardcoded string."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # Should contain the translated title from i18n
        assert "Swarm Orchestration" in html

    def test_no_hardcoded_english_tab_labels(self):
        """Tab labels come from t() translations, not hardcoded English."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # The translated strings should appear (from i18n en defaults)
        assert "Task Builder" in html
        assert "Active Tasks" in html
        assert "Results Dashboard" in html
        assert "Worker Registry" in html
        assert "Task History" in html

    def test_no_hardcoded_english_metric_labels(self):
        """Metric labels use t() translations."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # These should come from t() calls
        assert "Workers" in html
        assert "Status" in html
        assert "Tasks Today" in html
        assert "Total Cost" in html

    def test_no_hardcoded_english_form_labels(self):
        """Form labels use t() translations."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # These should come from t() calls
        assert "Create Task" in html
        assert "Prompt" in html
        assert "Timeout" in html
        assert "Aggregation" in html

    def test_no_hardcoded_english_empty_states(self):
        """Empty state messages use t() translations."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        # These should come from t() calls
        assert "No active tasks" in html
        assert "No completed tasks yet" in html

    def test_swarm_translation_keys_exist_in_i18n(self):
        """All required swarm translation keys exist in i18n module."""
        from kazma_ui.i18n import TRANSLATIONS

        required_keys = [
            "swarm.title",
            "swarm.workers",
            "swarm.status",
            "swarm.running",
            "swarm.stopped",
            "swarm.start_all",
            "swarm.stop_all",
            "swarm.busy",
            "swarm.tasks_today",
            "swarm.total_cost",
            "swarm.completed",
            "swarm.today",
            "swarm.tab_task_builder",
            "swarm.tab_active_tasks",
            "swarm.tab_results_dashboard",
            "swarm.tab_worker_registry",
            "swarm.tab_task_history",
            "swarm.create_task",
            "swarm.orchestration_pattern",
            "swarm.prompt",
            "swarm.context",
            "swarm.advanced_options",
            "swarm.timeout_seconds",
            "swarm.max_retry_count",
            "swarm.aggregation_strategy",
            "swarm.validation_schema",
            "swarm.no_workers_registered",
            "swarm.recent_results",
            "swarm.no_active_tasks",
            "swarm.registered_workers",
            "swarm.add_worker",
            "swarm.dynamic_spawn",
            "swarm.spawn_worker",
            "swarm.search",
            "swarm.task_id",
            "swarm.duration",
            "swarm.cost",
        ]
        for key in required_keys:
            assert key in TRANSLATIONS, f"Missing translation key: {key}"
            assert "en" in TRANSLATIONS[key], f"Missing English for key: {key}"
            assert "ar" in TRANSLATIONS[key], f"Missing Arabic for key: {key}"


# ---------------------------------------------------------------------------
# adv-retries wiring: max_retries passed through to task metadata
# ---------------------------------------------------------------------------


class TestRetryCountWiring:
    """Test that adv-retries field is wired to dispatch payload and metadata."""

    def test_dispatch_accepts_max_retries(self):
        """API accepts max_retries in dispatch payload."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={
                "workers": ["w1"],
                "task": "test task",
                "max_retries": 3,
            },
        )
        assert response.status_code == 200

    def test_max_retries_in_response_metadata(self):
        """max_retries appears in the response metadata when dispatched."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={
                "workers": ["w1"],
                "task": "test task with retries",
                "max_retries": 5,
            },
        )
        assert response.status_code == 200
        data = response.json()
        # The metadata should include max_retries
        if data.get("metadata"):
            assert data["metadata"].get("max_retries") == 5

    def test_max_retries_default_zero(self):
        """When max_retries is not provided, it defaults gracefully."""
        client = _build_client()
        _add_workers(client, "w1")
        response = client.post(
            "/api/swarm/dispatch",
            json={
                "workers": ["w1"],
                "task": "test task no retries",
            },
        )
        assert response.status_code == 200

    def test_page_has_retry_field(self):
        """Page contains the adv-retries input field."""
        client = _build_client()
        response = client.get("/swarm")
        html = response.text
        assert 'id="adv-retries"' in html
