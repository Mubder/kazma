# Swarm Task Persistence & UI Bug Analysis

## 1. Active Tasks Not Showing After Dispatch

### Symptom
User sends a task from the Task Builder tab. After switching to **Active Tasks**, no card appears and the empty-state message remains.

### Code Paths & Root Cause

#### 1.1 Task dispatch flow
- `kazma-ui/kazma_ui/static/js/swarm.js`, `dispatchTask()` (line ~270)
  - Builds payload, calls `POST /api/swarm/dispatch`, then switches to the active-tasks tab via `switchTab('active-tasks')`.
  - Only after the network response does it call `connectSSE(data.task_id, data)` if `data.task_id` is present.
- `kazma-ui/kazma_ui/swarm_panel.py`, `swarm_dispatch()` (line ~250)
  - The response includes `task_id` only when `task_result is not None`.
  - Returns `"task_id": None if task_result is None else task_result.task_id`.

So if the server returns a dispatch error or the engine returns `None`, the UI will not create an active task card and no SSE will be opened. However, the user reports the task *does* complete and appears in the Results Dashboard, so `task_result` is generally present. The real issue is timing: the card is not created until the network round-trip completes.

#### 1.2 UI creates the card only after the response (not optimistically)
- `swarm.js`, `dispatchTask()` (line ~325):

```javascript
  // Switch to active tasks tab
  switchTab('active-tasks');

  fetch('/api/swarm/dispatch', ...)
    .then(...)
    .then(function(data) {
      if (data.status === 'ok' || data.status === 'warning') {
        ...
        if (data.task_id) {
          connectSSE(data.task_id, data);
        }
      }
    });
```

The active tasks panel is switched to immediately, but the card is not rendered until the response is received. The switch happens before the call, so the empty state is visible for the duration of the network request and the first chunk of execution. If the task finishes before the user switches tabs or before the response is processed, the card may appear and then immediately switch to `completed` and be removed because the SSE `task_completed` handler closes the stream and deletes `activeTasks[taskId]` (line ~500).

#### 1.3 SSE bus may not be wired for the engine used during dispatch
- `kazma-ui/kazma_ui/swarm_panel.py`, `_current_engine()` (line ~95):

```python
def _current_engine() -> Any:
    engine = _resolve_engine(swarm_manager)
    if engine is not None and _sse_bus is not None:
        try:
            from kazma_ui.swarm_sse import wire_engine_events
            wire_engine_events(engine, _sse_bus)
        except Exception:
            logger.debug(...)
    return engine
```

`_current_engine()` is called only when `/swarm`, `/api/swarm/status`, `/api/swarm/dispatch`, `/api/swarm/tasks`, and individual task endpoints are invoked. The SSE `wire_engine_events` is called there as well. So for the dispatch request, the engine is wired *before* the task is executed. This path is generally correct, but there is a subtlety: if the dispatch completes synchronously before the function returns, the `task_completed` event is emitted into the bus history, but the UI's SSE connection is not yet established because it only starts `connectSSE()` after the response. The event history will be replayed when the UI connects, so the card should appear and immediately show completed. This depends on the browser's EventSource reconnect behavior and whether the stream route returns terminal history correctly.

#### 1.4 `_patched_dispatch` cannot handle a `None` dispatch result
- `kazma-ui/kazma_ui/swarm_sse.py`, `_patched_dispatch()` (line ~260):

```python
async def _patched_dispatch(task: Any) -> Any:
    workers = list(task.workers) if task.workers else []
    bus.emit(task.id, "task_started", {"task_id": task.id, "workers": workers})
    engine._active_task_id = task.id
    engine._sse_step_counter = 0
    try:
        return await original_dispatch(task)
    finally:
        engine._active_task_id = ""
        engine._sse_step_counter = 0
```

It does not catch exceptions. If `original_dispatch(task)` raises, the `task_started` event remains in the history but the final `task_completed` event is never emitted. The SSE stream would replay the `task_started` event and then never close, leaving the active task card stuck in `running`. This matches the user's first symptom when the task fails silently in the engine.

However, the `swarm_panel.py` endpoint itself catches `Exception` only in the delegated dispatch branch (`uses_external_dispatch`). The internal engine path (`engine.dispatch()` or `engine.broadcast()`) is not wrapped in try/except, so a runtime exception would propagate to FastAPI and the UI would receive a 500. The user would see an error toast, not an empty active task list. Therefore, the more likely cause is the card timing mismatch described in 1.2 combined with the data shape mismatch in the Results Dashboard (see section 2), making the user believe the task was sent but not shown in Active Tasks.

### Conclusion for Active Tasks
The root cause is a **UI timing and lifecycle issue**: the active task card is created only after the network response completes, and the SSE handler deletes the card immediately when the task completes. A task that finishes quickly (or that the user switches to after completion) will not appear in Active Tasks. There is no optimistic UI render and no persistent "recently completed" state in the Active Tasks panel.

### Specific Fix Needed
- In `kazma-ui/kazma_ui/static/js/swarm.js`, `dispatchTask()`:
  - Create an active task card immediately with a pending status before the fetch.
  - Use the `connectSSE` card (or a placeholder) so that the task appears as soon as the user clicks **Create Task**.
  - If the server returns no `task_id`, remove the pending card and show an error.

```javascript
  // Show pending card immediately
  var pendingTaskId = 'pending-' + Date.now();
  connectSSE(pendingTaskId, {task: task}); // needs refactor or a new addActiveTaskCard() helper
  // Replace with the real task_id once the response arrives.
```

- Alternatively, add a `renderActiveTask()` helper that does not require SSE and call it before `switchTab('active-tasks')`.

---

## 2. Results Dashboard Shows Empty Outputs

### Symptom
A completed task appears in the Results Dashboard, but worker outputs, aggregated output, and synthesized output are missing or empty.

### Code Paths & Root Cause

#### 2.1 Dashboard loads from two sources
- `kazma-ui/kazma_ui/static/js/swarm.js`, `loadResultsDashboard()` (line ~560):

```javascript
function loadResultsDashboard() {
  fetch('/api/swarm/tasks?pageSize=20')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var tasks = data.tasks || [];
      var allResults = completedResults.slice();
      tasks.forEach(function(t) {
        if (!allResults.find(function(r) { return r.task_id === t.id; })) {
          allResults.push(t);
        }
      });
      renderResultsDashboard(allResults, 'all');
    })
    .catch(...);
}
```

`completedResults` contains in-memory `TaskResult.to_dict()` objects (from the dispatch response and the SSE `task_completed` event). The `/api/swarm/tasks` endpoint returns `SwarmTask.to_dict()` objects persisted by `TaskStore`. The merge relies on the assumption that `t.id` from a `SwarmTask` matches `r.task_id` from a `TaskResult`. That part is correct: both equal the task's `id`.

#### 2.2 Shape mismatch: `SwarmTask.to_dict()` vs `TaskResult.to_dict()`
- `kazma-core/kazma_core/swarm/task.py`, `SwarmTask.to_dict()` inherits from `_JsonSerializable` and serializes all fields including `result: TaskResult`.
- `TaskResult.to_dict()` (same file) serializes fields like `task_id`, `status`, `worker_results`, `aggregated_output`, `synthesized_output`, etc., at the top level.

Therefore, the objects stored in the dashboard list have different shapes depending on source:

| Field | `TaskResult.to_dict()` (in-memory) | `SwarmTask.to_dict()` (TaskStore) |
|---|---|---|
| `task_id` | top-level | inside `result.task_id` |
| `status` | top-level | top-level (task status, e.g. `completed`, not result status) |
| `worker_results` | top-level | inside `result.worker_results` |
| `aggregated_output` | top-level | inside `result.aggregated_output` |
| `synthesized_output` | top-level | inside `result.synthesized_output` |
| `individual_opinions` | top-level | inside `result.individual_opinions` |
| `duration_seconds` | top-level | inside `result.duration_seconds` (and also `duration_seconds` on the task itself? No, `SwarmTask` does not have `duration_seconds`) |
| `type` / `id` | `_pattern` / `task_id` | `type` / `id` |

- `renderResultsDashboard()` references `r.worker_results`, `r.aggregated_output`, `r.synthesized_output`, `r.status`, `r.task_id || r.id`, `r.type || r._pattern`, `r.duration_seconds`.

For a TaskStore task (`SwarmTask.to_dict()`):
- `r.worker_results` is `undefined` because it is inside `r.result.worker_results`.
- `r.aggregated_output` is `undefined`.
- `r.synthesized_output` is `undefined`.
- `r.status` is the task status (`completed` / `failed` / `paused`) which is a different vocabulary than result status (`success` / `failed` / `partial` / `timeout`). The dashboard shows the badge as `completed` or `failed` but not the per-worker statuses.
- `r.duration_seconds` is `undefined` because `SwarmTask` does not have that field; it is inside `r.result.duration_seconds`.

#### 2.3 Concrete evidence from the API code
- `kazma-ui/kazma_ui/swarm_panel.py`, `/api/swarm/tasks` (line ~330):

```python
return JSONResponse({
    "tasks": [task.to_dict() for task in tasks],
    ...
})
```

This returns `SwarmTask.to_dict()` objects, which contain a nested `result` dict. The dashboard expects a flattened `TaskResult`-like shape.

#### 2.4 Task detail endpoint also returns the same nested shape
- `kazma-ui/kazma_ui/swarm_panel.py`, `/api/swarm/tasks/{task_id}` (line ~360):

```python
return JSONResponse({
    "task": task.to_dict(),
})
```

The UI's `viewTaskDetail()` expects `data.task` and passes it to `renderTaskDetailHTML(task)`. `renderTaskDetailHTML` reads `task.worker_results`, `task.synthesized_output`, `task.aggregated_output`, etc. (line ~720). For a TaskStore task these are `undefined`, so the detail modal shows the header but no worker outputs.

### Conclusion for Results Dashboard
The root cause is a **data model shape mismatch**: the persisted `/api/swarm/tasks` endpoint returns `SwarmTask.to_dict()` (with a nested `result` object), while the dashboard and detail view expect a flattened `TaskResult.to_dict()` shape.

### Specific Fix Needed

#### Option A: Flatten the API response (recommended, minimal UI change)
- In `kazma-ui/kazma_ui/swarm_panel.py`, modify the `swarm_tasks` and `swarm_task_detail` endpoints so that when the source is a `SwarmTask` with a `result`, they return a flattened representation that matches `TaskResult.to_dict()` plus `id`, `type`, and `prompt`.

```python
# Helper to flatten a SwarmTask to the UI-expected shape
def _flatten_task(task: SwarmTask) -> dict[str, Any]:
    data = task.to_dict()
    result = data.get("result") or {}
    # Promote result fields to top level, but keep task-level fields.
    return {
        "id": data["id"],
        "task_id": data["id"],
        "type": data["type"],
        "prompt": data["prompt"],
        "context": data.get("context"),
        "workers": data.get("workers", []),
        "status": result.get("status", data.get("status")),
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at"),
        "completed_at": data.get("completed_at"),
        "duration_seconds": result.get("duration_seconds"),
        "total_cost": result.get("total_cost"),
        "total_tokens": result.get("total_tokens"),
        "worker_results": result.get("worker_results", []),
        "individual_opinions": result.get("individual_opinions", []),
        "aggregated_output": result.get("aggregated_output"),
        "synthesized_output": result.get("synthesized_output"),
        "error": result.get("error"),
        "metadata": result.get("metadata", data.get("metadata", {})),
    }
```

Then use `[_flatten_task(task) for task in tasks]` in both endpoints.

#### Option B: Normalize in the UI (more invasive)
- Update `renderResultsDashboard()` and `renderTaskDetailHTML()` to read from both `r.result.*` and top-level fields. This duplicates logic across the dashboard, task history, and detail views. Not recommended.

#### Option C: Make `TaskStore.to_dict()` return a flattened shape (architectural change)
- Change the `SwarmTask.to_dict()` behavior or add a `TaskResult.to_dict()` at persistence time. This changes the model contract and may affect other consumers. Not recommended.

---

## 3. Clicking a Completed Task Does Not Open the Output

### Symptom
Clicking a result card in the Results Dashboard or a row in the Task History table does not open a detail modal showing the output.

### Code Paths & Root Cause

#### 3.1 Click handler is attached to dynamically generated HTML
- `renderResultsDashboard()` (line ~600) and `renderHistoryTable()` (line ~790) generate HTML with `onclick="KazmaSwarm.viewTaskDetail('...')"`.
- The `onclick` attribute is inside the generated string. The IIFE exposes `window.KazmaSwarm`, so the inline handler is valid. However, this only works if the string is assigned to `innerHTML`, not if the DOM is sanitized or if the function is not yet defined. `KazmaSwarm` is defined at the end of the IIFE after the `DOMContentLoaded` event. If the HTML is rendered before the IIFE runs, the inline handlers would fail. In practice, the dashboard is rendered after `init()` has run, so this should be fine.

#### 3.2 The modal element is only defined in the Task History tab
- In `kazma-ui/kazma_ui/templates/swarm.html`, the task detail modal is only inside the `task-history` tab panel:

```html
<!-- TAB: Task History -->
<div class="tab-panel" id="panel-task-history" style="display:none;">
  ...
  <!-- Task Detail Modal -->
  <div id="task-detail-modal" class="modal-overlay" ...>
    ...
  </div>
</div>
```

When the user is on the **Results Dashboard** tab, the `task-detail-modal` element is in a hidden DOM subtree. The modal's `display: none` (via `panel-task-history` hidden) is determined by the parent panel, not the modal itself. `viewTaskDetail()` sets `modal.style.display = 'flex'` on the modal element, but if the modal's parent panel `panel-task-history` is `display: none`, the modal remains invisible. The user perceives that "clicking doesn't open the output".

This is the primary root cause for the "click doesn't open output" symptom.

#### 3.3 Detail content is also missing due to the shape mismatch
Even if the modal were visible, `renderTaskDetailHTML()` would read `task.worker_results`, `task.synthesized_output`, etc. Because the endpoint returns a nested `result`, the output would be empty (see section 2). This compounds the symptom.

### Conclusion for Task Detail Click
Two issues:
1. The modal is only present in the Task History tab, so clicking a Results Dashboard card opens a modal that is hidden inside an inactive panel.
2. The detail view expects a flattened task shape that the API does not provide.

### Specific Fix Needed
- Move the `task-detail-modal` element outside all tab panels (near the end of the `swarm-container` div, before the logs modal) so it is always available regardless of which tab is active.
- Apply the same `_flatten_task()` helper from section 2 to the `swarm_task_detail` endpoint so the detail view receives the expected shape.
- Alternatively, change `renderTaskDetailHTML()` to accept both shapes, but flattening the API response is cleaner.

---

## 4. History Lost on Server Restart

### Symptom
After restarting the web server, previously completed tasks are gone from the Results Dashboard and Task History.

### Code Paths & Root Cause

#### 4.1 TaskStore persists to `kazma-data/swarm_tasks.db`
- `kazma-core/kazma_core/swarm/task_store.py`, `_DEFAULT_DB = "kazma-data/swarm_tasks.db"` (line 28).
- `SwarmEngine._finalize_task()` (line ~920 in engine.py) calls `self._task_store.persist_task(task)` if the store is configured.
- `_handle_pipeline_checkpoint()` and `reject_checkpoint()` also call `persist_task()`. So persistence is wired into the engine.

#### 4.2 The main `SwarmManager` does not receive a `TaskStore`
- `kazma-core/kazma_core/swarm/manager.py`, `SwarmManager.__init__()` (line 23):

```python
class SwarmManager:
    def __init__(self, config: SwarmConfig) -> None:
        self.config = config
        self.engine = SwarmEngine(config)
        self._workers = self.engine._workers
```

`SwarmManager` creates a `SwarmEngine` with no `task_store` argument. Therefore, `self._task_store` is `None` in the engine. Even though `TaskStore` exists and the `_finalize_task` path is ready, the engine is never given one, so it never persists tasks.

#### 4.3 Web UI app creates the `SwarmManager` without a `TaskStore`
- `kazma-ui/kazma_ui/app.py`, Swarm Panel initialization (line ~245):

```python
try:
    from kazma_core.swarm import (
        SwarmConfig,
        SwarmManager,
        set_swarm_engine,
    )
    swarm_cfg_path = config_path or "kazma.yaml"
    swarm_cfg = SwarmConfig.from_yaml(swarm_cfg_path)
    if swarm_cfg is not None and swarm_cfg.enabled:
        _swarm_mgr = SwarmManager(swarm_cfg)
        ...
    else:
        _swarm_mgr = SwarmManager(SwarmConfig(enabled=True, workers=[]))
        ...
    set_swarm_engine(_swarm_mgr.engine)
except Exception as e:
    ...
```

No `TaskStore` is created or passed to `SwarmManager`. Because `SwarmManager` does not accept a `task_store` argument and does not pass one to `SwarmEngine`, the engine's `_task_store` is always `None` in the UI app path.

#### 4.4 `_create_empty_engine()` in the panel creates a fresh `TaskStore`
- `kazma-ui/kazma_ui/swarm_panel.py`, `_create_empty_engine()` (line 62):

```python
def _create_empty_engine() -> Any:
    if not _has_swarm_core():
        return None
    store = TaskStore() if TaskStore is not None else None
    return SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
```

This function is used only when `_resolve_engine()` cannot find an existing engine. It creates a new `TaskStore` with the default path, so it would persist if used. But in the normal UI app path, the engine is already set by `app.py` (via `set_swarm_engine(_swarm_mgr.engine)`), so `_create_empty_engine()` is never reached. Therefore, the persistence bug is not caused by a missing TaskStore in `_create_empty_engine()` but by the engine being initialized without a TaskStore in `app.py` / `SwarmManager`.

#### 4.5 `_create_empty_engine()` also creates a separate store from the main engine
If `_create_empty_engine()` were ever reached (e.g., if `get_swarm_engine()` returned `None` between requests), it would create a new `TaskStore()` with a default path. Since the main engine does not have a store, this would not share state, but the more common failure is that the main engine has no store at all. Once the main engine is given a store, `_create_empty_engine()` should reuse the same `TaskStore` instance rather than creating a new one, to avoid a split database.

#### 4.6 `restore_paused_tasks()` is not called at startup
- `kazma-core/kazma_core/swarm/engine.py`, `restore_paused_tasks()` (line ~1110) is defined but never called in `app.py` or `SwarmManager.__init__()`. Even if the engine had a store, paused HITL tasks would not be restored into memory on restart.

### Conclusion for History Lost on Restart
The root cause is that **the SwarmEngine used by the Web UI is created without a TaskStore**. `SwarmManager` does not accept or pass a `task_store`, and `app.py` does not create one. As a result, the persistence code in `_finalize_task()` and `_handle_pipeline_checkpoint()` is never invoked.

### Specific Fix Needed

1. **Pass a TaskStore through SwarmManager.**
   - `kazma-core/kazma_core/swarm/manager.py`, `SwarmManager.__init__()`:

```python
class SwarmManager:
    def __init__(self, config: SwarmConfig, task_store: TaskStore | None = None) -> None:
        self.config = config
        self.engine = SwarmEngine(config, task_store=task_store)
        self._workers = self.engine._workers
```

2. **Create and pass the TaskStore in the Web UI app.**
   - `kazma-ui/kazma_ui/app.py`, Swarm Panel initialization:

```python
from kazma_core.swarm import TaskStore, SwarmConfig, SwarmManager, set_swarm_engine

swarm_task_store = TaskStore()  # uses default kazma-data/swarm_tasks.db
swarm_cfg = SwarmConfig.from_yaml(swarm_cfg_path)
if swarm_cfg is not None and swarm_cfg.enabled:
    _swarm_mgr = SwarmManager(swarm_cfg, task_store=swarm_task_store)
else:
    _swarm_mgr = SwarmManager(SwarmConfig(enabled=True, workers=[]), task_store=swarm_task_store)
set_swarm_engine(_swarm_mgr.engine)
_swarm_mgr.engine.restore_paused_tasks()
```

3. **Share the same store with `_create_empty_engine()`.**
   - `kazma-ui/kazma_ui/swarm_panel.py`: instead of creating a new `TaskStore()` every time, reuse the `task_store` from the current engine if it exists, or create a shared one once.

4. **Call `restore_paused_tasks()` on startup.**
   - In `app.py` after creating the engine, call `_swarm_mgr.engine.restore_paused_tasks()` so paused HITL pipelines survive restart.

---

## 5. Ordered List of Files to Modify

1. `kazma-core/kazma_core/swarm/manager.py`
   - Add `task_store: TaskStore | None = None` parameter to `SwarmManager.__init__()` and pass it to `SwarmEngine`.

2. `kazma-ui/kazma_ui/app.py`
   - Create a single `TaskStore` instance and pass it to `SwarmManager`.
   - Call `_swarm_mgr.engine.restore_paused_tasks()` after `set_swarm_engine()`.

3. `kazma-ui/kazma_ui/swarm_panel.py`
   - Add a `_flatten_task()` helper to convert `SwarmTask` (with nested `result`) to the UI-expected flattened shape.
   - Use the helper in `/api/swarm/tasks` and `/api/swarm/tasks/{task_id}` responses.
   - Ensure `_create_empty_engine()` reuses the same `TaskStore` instance as the main engine (or create a shared one once at module level).

4. `kazma-ui/kazma_ui/templates/swarm.html`
   - Move the `#task-detail-modal` element outside the `panel-task-history` tab panel so it is accessible from all tabs.

5. `kazma-ui/kazma_ui/static/js/swarm.js`
   - Add an optimistic active task card render in `dispatchTask()` so the task appears immediately.
   - Update the card ID once the real `task_id` is returned, or remove it if dispatch fails.
   - (Optional) Ensure the `task_completed` event does not immediately delete the card if the user is currently viewing Active Tasks; instead, transition it to a completed state and leave it for a few seconds.

6. `kazma-ui/kazma_ui/swarm_sse.py` (defensive improvement)
   - Wrap `_patched_dispatch()` in a try/except that emits `task_failed` if `original_dispatch()` raises, so the SSE stream closes and the UI shows a failure rather than staying stuck.

---

## 6. Code Snippets with Line Numbers

### `kazma-ui/kazma_ui/swarm_panel.py` — response shape issue

```python
# line 330
        return JSONResponse({
            "tasks": [task.to_dict() for task in tasks],
            "count": len(tasks),
            "total": total,
            "page": page,
            "pageSize": page_size,
        })
```

```python
# line 360
    return JSONResponse({
        "task": task.to_dict(),
    })
```

### `kazma-ui/kazma_ui/swarm_panel.py` — task_id only present when task_result is not None

```python
# line 285
        "task_id": None if task_result is None else task_result.task_id,
```

### `kazma-ui/kazma_ui/static/js/swarm.js` — Active Tasks card created only after response

```javascript
// line 325
      if (data.task_id) {
        connectSSE(data.task_id, data);
      }
```

### `kazma-ui/kazma_ui/static/js/swarm.js` — `task_completed` immediately removes the card

```javascript
// line 470
    evtSource.addEventListener('task_completed', function(e) {
      ...
      evtSource.close();
      delete activeTasks[taskId];
      ...
    });
```

### `kazma-ui/kazma_ui/templates/swarm.html` — modal inside Task History panel only

```html
<!-- line ~380 -->
<div class="tab-panel" id="panel-task-history" style="display:none;">
  ...
  <div id="task-detail-modal" ...></div>
</div>
```

### `kazma-core/kazma_core/swarm/manager.py` — no TaskStore passed

```python
# line 23
class SwarmManager:
    def __init__(self, config: SwarmConfig) -> None:
        self.config = config
        self.engine = SwarmEngine(config)
```

### `kazma-ui/kazma_ui/app.py` — SwarmManager created without TaskStore

```python
# line ~245
        _swarm_mgr = SwarmManager(swarm_cfg)
```

### `kazma-core/kazma_core/swarm/task_store.py` — default DB path

```python
# line 28
_DEFAULT_DB = "kazma-data/swarm_tasks.db"
```

### `kazma-core/kazma_core/swarm/engine.py` — persistence calls ready but store is None

```python
# line 920
        if self._task_store is not None:
            try:
                self._task_store.persist_task(task)
```

---

## 7. Summary Table

| Symptom | Root Cause | Fix Location |
|---|---|---|
| Active Tasks not showing | UI card created only after dispatch response; fast tasks may finish before card is rendered | `swarm.js`, `dispatchTask()` |
| Results Dashboard empty | API returns `SwarmTask.to_dict()` with nested `result`; UI expects flattened `TaskResult` shape | `swarm_panel.py` endpoints |
| Task click doesn't open output | Detail modal is inside the hidden Task History panel; same nested shape issue | `swarm.html`, `swarm_panel.py` |
| History lost on restart | `SwarmManager`/`SwarmEngine` created without `TaskStore` in the UI app | `manager.py`, `app.py` |

