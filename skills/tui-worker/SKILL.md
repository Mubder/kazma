---
name: tui-worker
description: Worker for TUI replacement tasks (cleanup, foundation, components, tests)
---

# TUI Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## Required Skills and Tools

- `textual` framework for TUI development
- `pytest` for testing
- `kazma-core` imports (HardwareMonitor, MetricsCollector, TraceStore, ModelRegistry)

## Work Procedure

### 1. Understand Feature Requirements
- Read feature description and expectedBehavior
- Identify which components to implement/modify
- Check preconditions are met

### 2. Write Tests First (TDD)
- Create test file in `kazma-tui/tests/`
- Write failing tests for expected behavior
- Run tests to confirm they fail

### 3. Implement Feature
- Create/modify files in `kazma-tui/kazma_tui/`
- Follow Textual patterns (App, Widget, Compose, etc.)
- Use existing Kazma conventions (type hints, docstrings, logging)

### 4. Run Tests
- Run `python -m pytest kazma-tui/tests/ -v`
- Fix any failures
- Ensure all tests pass

### 5. Manual Verification
- Launch TUI: `python -m kazma_tui`
- Verify feature works as expected
- Check for errors in terminal output

### 6. Lint and Typecheck
- Run `python -m ruff check kazma-tui/kazma_tui/`
- Run `python -m mypy kazma-tui/kazma_tui/`
- Fix any issues

## Example Handoff

```json
{
  "salientSummary": "Implemented dashboard metrics widget with CPU/Memory display from HardwareMonitor. Added periodic refresh every 2 seconds using Textual's set_interval. All tests pass.",
  "whatWasImplemented": "Created kazma-tui/kazma_tui/dashboard.py with MetricsDashboard widget. Displays CPU percentage and RAM usage from HardwareMonitor.get_stats(). Refreshes every 2 seconds. Handles missing metrics with 'N/A' fallback.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "python -m pytest kazma-tui/tests/test_dashboard.py -v", "exitCode": 0, "observation": "5 tests passed"},
      {"command": "python -m ruff check kazma-tui/kazma_tui/dashboard.py", "exitCode": 0, "observation": "No issues"},
      {"command": "python -m mypy kazma-tui/kazma_tui/dashboard.py", "exitCode": 0, "observation": "No issues"}
    ],
    "interactiveChecks": [
      {"action": "Launched TUI with python -m kazma_tui", "observed": "Dashboard shows CPU and RAM metrics, updates every 2 seconds"}
    ]
  },
  "tests": {
    "added": [
      {
        "file": "kazma-tui/tests/test_dashboard.py",
        "cases": [
          {"name": "test_dashboard_shows_cpu", "description": "Verifies CPU percentage is displayed"},
          {"name": "test_dashboard_shows_ram", "description": "Verifies RAM usage is displayed"},
          {"name": "test_dashboard_handles_missing_metrics", "description": "Verifies N/A fallback when metrics unavailable"}
        ]
      }
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Feature depends on infrastructure that doesn't exist yet
- Requirements are ambiguous or contradictory
- Existing bugs affect this feature
- Tests fail and cannot be fixed
