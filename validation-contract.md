# Validation Contract: TUI Replacement Mission

## Overview
This document defines the validation assertions for replacing the old Arabic-focused Textual-based TUI with a new professional English-only Textual-based TUI dashboard.

---

## 1. TUI Launch & Basic Functionality

### VAL-TUI-001: TUI Launches Successfully
- **Title**: TUI application starts without errors
- **Behavioral Description**: The new Textual-based TUI must launch without raising exceptions, import errors, or dependency failures. The application should render the initial screen within 3 seconds.
- **Tool**: Manual execution of `kazma-tui` entry point or `python -m kazma_tui`
- **Evidence Requirements**:
  - Screenshot of TUI initial render
  - Terminal output showing clean startup (no tracebacks)
  - Exit code 0 on clean shutdown

### VAL-TUI-002: English-Only UI
- **Title**: All UI text displays in English
- **Behavioral Description**: The TUI must display exclusively English text. No Arabic characters, RTL markers, or bilingual labels should appear anywhere in the interface (header, footer, panels, messages, placeholders).
- **Tool**: Visual inspection + automated scan of source files
- **Evidence Requirements**:
  - Screenshot of each major UI region (header, dashboard, chat, footer)
  - Grep result confirming no Arabic Unicode ranges (`\u0600-\u06FF`, `\u0750-\u077F`, `\u08A0-\u08FF`, `\uFB50-\uFDFF`, `\uFE70-\uFEFF`) in new TUI source files

### VAL-TUI-003: Header Shows Provider/Model Info
- **Title**: Header displays active provider and model from ModelRegistry
- **Behavioral Description**: The TUI header must show the currently active provider name and model name, sourced from `ModelRegistry.get_active_profile()`. The display should update if the active profile changes.
- **Tool**: Visual inspection + unit test
- **Evidence Requirements**:
  - Screenshot showing provider/model in header
  - Unit test verifying header text matches `get_active_profile()` output

### VAL-TUI-004: Footer Shows Keyboard Shortcuts
- **Title**: Footer displays available keyboard shortcuts
- **Behavioral Description**: The TUI footer must display keyboard shortcuts for key actions (e.g., `Ctrl+Q` quit, `Tab` switch panels, `Enter` send message). Shortcuts must be visible without scrolling.
- **Tool**: Visual inspection
- **Evidence Requirements**:
  - Screenshot of footer with keyboard shortcuts visible
  - Functional test that each documented shortcut performs its stated action

---

## 2. Metrics Dashboard

### VAL-TUI-010: CPU/Memory Metrics Display
- **Title**: Dashboard shows CPU and memory utilization
- **Behavioral Description**: The metrics dashboard must display CPU percentage and RAM usage (used/total GB) sourced from `HardwareMonitor.get_stats()`. Values must be formatted with appropriate units (e.g., "45.2%", "16.4 / 32.0 GB").
- **Tool**: Unit test + visual inspection
- **Evidence Requirements**:
  - Screenshot showing CPU and RAM metrics
  - Unit test verifying metrics match `HardwareMonitor.get_stats()` return values

### VAL-TUI-011: RPM Display
- **Title**: Dashboard shows requests per minute
- **Behavioral Description**: The dashboard must display requests per minute (RPM) calculated from `TraceStore.stats()` or `TraceStore.recent()`. RPM should reflect trace activity over a rolling 60-second window.
- **Tool**: Unit test + visual inspection
- **Evidence Requirements**:
  - Screenshot showing RPM metric
  - Unit test verifying RPM calculation from trace data

### VAL-TUI-012: Latency Metrics Display
- **Title**: Dashboard shows average latency from MetricsCollector
- **Behavioral Description**: The dashboard must display average latency (in milliseconds) sourced from `MetricsCollector.get_all_metrics()` or per-worker aggregates. Values must be formatted with "ms" suffix.
- **Tool**: Unit test + visual inspection
- **Evidence Requirements**:
  - Screenshot showing latency metric
  - Unit test verifying latency values match `MetricsCollector` output

### VAL-TUI-013: Error Rate Display
- **Title**: Dashboard shows error rate from MetricsCollector
- **Behavioral Description**: The dashboard must display error rate as a percentage, calculated from `tasks_failed / (tasks_completed + tasks_failed)` using `MetricsCollector` data. Must handle division by zero gracefully (show 0% or "N/A").
- **Tool**: Unit test + visual inspection
- **Evidence Requirements**:
  - Screenshot showing error rate metric
  - Unit test verifying error rate calculation and zero-division handling

### VAL-TUI-014: Active Agents List
- **Title**: Dashboard displays active agents from SwarmEngine
- **Behavioral Description**: The dashboard must display a list of active agents/workers sourced from `SwarmEngine._workers` or equivalent public API. Each agent should show its name and status.
- **Tool**: Unit test + visual inspection
- **Evidence Requirements**:
  - Screenshot showing active agents list
  - Unit test verifying agent list matches `SwarmEngine` state

### VAL-TUI-015: Real-Time Metrics Updates
- **Title**: Dashboard refreshes metrics periodically
- **Behavioral Description**: The metrics dashboard must update all displayed metrics at a configurable interval (default: 1-5 seconds). Updates must not cause UI flicker or layout shifts. The refresh mechanism must use Textual's timer system (`set_interval`).
- **Tool**: Visual inspection + timing test
- **Evidence Requirements**:
  - Video/GIF showing metrics changing over time
  - Code inspection confirming use of `set_interval` or equivalent
  - Log output showing periodic refresh timestamps

---

## 3. Chat Interface

### VAL-TUI-020: Chat Input Accepts User Text
- **Title**: Chat panel accepts keyboard input
- **Behavioral Description**: The chat input widget must accept text input, display typed characters, and support standard editing (backspace, cursor movement). Input must be focused on mount.
- **Tool**: Manual interaction test
- **Evidence Requirements**:
  - Screenshot showing text entered in input field
  - Confirmation that input is focused on TUI launch

### VAL-TUI-021: Chat Displays Messages
- **Title**: Chat panel renders sent and received messages
- **Behavioral Description**: The chat panel must display user messages (with user prefix/label) and assistant responses (with assistant prefix/label). Messages must be scrollable when they exceed the visible area.
- **Tool**: Manual interaction test
- **Evidence Requirements**:
  - Screenshot showing conversation with user and assistant messages
  - Confirmation that scrollback works for long conversations

### VAL-TUI-022: Basic Commands Support
- **Title**: Chat supports /help, /clear, and /quit commands
- **Behavioral Description**: The chat must recognize and handle:
  - `/help` — displays available commands and shortcuts
  - `/clear` — clears the chat log
  - `/quit` — exits the TUI cleanly
  Commands must be case-insensitive and must not be sent to the LLM.
- **Tool**: Manual interaction test
- **Evidence Requirements**:
  - Screenshot of `/help` output
  - Confirmation that `/clear` empties the chat log
  - Confirmation that `/quit` exits with code 0

---

## 4. ModelRegistry Integration

### VAL-TUI-030: Active Provider from ModelRegistry
- **Title**: TUI pulls active provider from ModelRegistry singleton
- **Behavioral Description**: The TUI must call `get_model_registry().get_active_profile()` to retrieve the active provider name. The provider name must be displayed in the header. No hardcoded provider names should appear.
- **Tool**: Unit test + code inspection
- **Evidence Requirements**:
  - Unit test verifying TUI header text matches `get_active_profile()["provider"]`
  - Code inspection confirming no hardcoded provider strings in TUI source

### VAL-TUI-031: Active Model from ModelRegistry
- **Title**: TUI pulls active model from ModelRegistry singleton
- **Behavioral Description**: The TUI must call `get_model_registry().get_active_profile()` to retrieve the active model name. The model name must be displayed in the header. No hardcoded model names should appear.
- **Tool**: Unit test + code inspection
- **Evidence Requirements**:
  - Unit test verifying TUI header text matches `get_active_profile()["model"]`
  - Code inspection confirming no hardcoded model strings in TUI source

### VAL-TUI-032: No Model-Switching Logic
- **Title**: TUI does not contain model-switching or configuration logic
- **Behavioral Description**: The TUI must not contain any code that switches models, modifies provider configuration, or writes to `ConfigStore`. The TUI is a read-only consumer of `ModelRegistry`. Any model/provider settings UI must be absent.
- **Tool**: Code inspection + grep
- **Evidence Requirements**:
  - Grep result showing no calls to `set_active_profile()`, `ConfigStore.write()`, or equivalent mutation methods in TUI source
  - Code review confirming TUI only reads from `ModelRegistry`

---

## 5. Cleanup

### VAL-TUI-040: Old TUI Directory Deleted
- **Title**: `kazma-tui/kazma_tui/` directory is removed
- **Behavioral Description**: The old Textual-based TUI directory must be completely deleted. No files from the old TUI (`tui.py` with Arabic support, `ArabicInput`, `_fix_arabic`) should remain.
- **Tool**: Filesystem check
- **Evidence Requirements**:
  - `ls kazma-tui/` showing directory is empty or removed
  - Grep result confirming no references to old TUI classes (`ArabicInput`, `_fix_arabic`, `_has_arabic`)

### VAL-TUI-041: router.py Archived
- **Title**: `kazma-providers/kazma_providers/router.py` moved to `archive/`
- **Behavioral Description**: The `router.py` file must be moved from `kazma-providers/kazma_providers/` to `archive/kazma-providers/` (or similar archive location). The original file must not exist in its former location.
- **Tool**: Filesystem check
- **Evidence Requirements**:
  - `ls kazma-providers/kazma_providers/` showing `router.py` is absent
  - `ls archive/` showing `router.py` is present in archive subdirectory

### VAL-TUI-042: pyproject.toml Entry Point Updated
- **Title**: `kazma-tui` entry point points to new TUI module
- **Behavioral Description**: The `[project.scripts]` section in `pyproject.toml` must have `kazma-tui` pointing to the new TUI module (e.g., `kazma_tui.app:main` or similar). The entry point must not reference the old `kazma_tui.tui:main`.
- **Tool**: File inspection + launch test
- **Evidence Requirements**:
  - `pyproject.toml` showing updated entry point
  - Successful execution of `kazma-tui` command launching the new TUI

---

## 6. Cross-Area Flows

### VAL-TUI-050: Launch → ModelRegistry Integration Flow
- **Title**: TUI launch initializes and displays ModelRegistry data
- **Behavioral Description**: On launch, the TUI must:
  1. Initialize or connect to the `ModelRegistry` singleton
  2. Call `get_active_profile()` to retrieve provider/model
  3. Display provider/model in the header
  4. Handle `RuntimeError` if `ModelRegistry` is not initialized (show "Not configured" fallback)
- **Tool**: Integration test
- **Evidence Requirements**:
  - Test that mocks `ModelRegistry` and verifies header text
  - Test that verifies graceful handling when `ModelRegistry` raises `RuntimeError`
  - Screenshot showing provider/model in header after launch

### VAL-TUI-051: Metrics Dashboard → Real-Time Updates Flow
- **Title**: Dashboard metrics refresh from live data sources
- **Behavioral Description**: The metrics dashboard must:
  1. On mount, fetch initial metrics from `HardwareMonitor`, `MetricsCollector`, `TraceStore`, and `SwarmEngine`
  2. Start a periodic timer (1-5 second interval)
  3. On each timer tick, re-fetch metrics from all sources
  4. Update dashboard widgets with new values
  5. Handle source unavailability gracefully (show "N/A" or last known value)
- **Tool**: Integration test + visual inspection
- **Evidence Requirements**:
  - Test that verifies metrics are fetched on mount
  - Test that verifies periodic refresh calls all data sources
  - Test that verifies graceful handling when a data source is unavailable
  - Video/GIF showing live metric updates

---

## Assertion Summary

| ID | Area | Title |
|----|------|-------|
| VAL-TUI-001 | Launch | TUI Launches Successfully |
| VAL-TUI-002 | Launch | English-Only UI |
| VAL-TUI-003 | Launch | Header Shows Provider/Model Info |
| VAL-TUI-004 | Launch | Footer Shows Keyboard Shortcuts |
| VAL-TUI-010 | Metrics | CPU/Memory Metrics Display |
| VAL-TUI-011 | Metrics | RPM Display |
| VAL-TUI-012 | Metrics | Latency Metrics Display |
| VAL-TUI-013 | Metrics | Error Rate Display |
| VAL-TUI-014 | Metrics | Active Agents List |
| VAL-TUI-015 | Metrics | Real-Time Metrics Updates |
| VAL-TUI-020 | Chat | Chat Input Accepts User Text |
| VAL-TUI-021 | Chat | Chat Displays Messages |
| VAL-TUI-022 | Chat | Basic Commands Support |
| VAL-TUI-030 | ModelRegistry | Active Provider from ModelRegistry |
| VAL-TUI-031 | ModelRegistry | Active Model from ModelRegistry |
| VAL-TUI-032 | ModelRegistry | No Model-Switching Logic |
| VAL-TUI-040 | Cleanup | Old TUI Directory Deleted |
| VAL-TUI-041 | Cleanup | router.py Archived |
| VAL-TUI-042 | Cleanup | pyproject.toml Entry Point Updated |
| VAL-TUI-050 | Cross-Area | Launch → ModelRegistry Integration Flow |
| VAL-TUI-051 | Cross-Area | Metrics Dashboard → Real-Time Updates Flow |
