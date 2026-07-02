# Task 4 — Active Tasks Tab Diagnosis

## Root Cause (TL;DR)

The Active Tasks tab is **purely client-side and ephemeral**. It has no backend endpoint to poll for in-flight tasks, and the dispatch endpoint is **synchronous** — the HTTP response (containing the `task_id`) only returns **after** the task has already completed. The SSE subscription is opened too late to catch any live events. For tasks dispatched from Telegram or other browsers, no card is ever created at all.

There are **four reinforcing defects**, not one. Any single one is sufficient to make the tab appear empty.

---

## The Four Defects

### Defect 1: No load function for Active Tasks (frontend)

**File:** `kazma-ui/kazma_ui/static/js/swarm.js:89-102`

When switching tabs, `switchTab()` calls load functions for some tabs:
```javascript
if (tabId === 'task-history') loadTaskHistory();
if (tabId === 'results-dashboard') loadResultsDashboard();
if (tabId === 'worker-registry') loadWorkerMetrics();
// NO 'active-tasks' case exists
```

There is no `loadActiveTasks()` function anywhere in the codebase. Clicking the Active Tasks tab shows only the empty-state div (`swarm.html:199-204`).

### Defect 2: Active task cards are session-local only (frontend)

**File:** `swarm.js:413` (dispatch), `swarm.js:503` (createActiveTaskCard)

Cards are only created when the user clicks Dispatch in **this browser tab**. There is:
- No global SSE listener for task_started events
- No polling of a backend endpoint
- No mechanism to discover tasks from other sources (Telegram, other browsers, background patterns)

### Defect 3: Dispatch is synchronous — SSE arrives too late (backend)

**File:** `kazma-ui/kazma_ui/swarm_panel.py:510`

```python
task_result = await engine.dispatch(swarm_task)   # RUNS TO COMPLETION
return JSONResponse({ ..., "task_id": task_result.task_id })  # returns AFTER done
```

The dispatch endpoint awaits `engine.dispatch()` to full completion. The HTTP response (with the `task_id`) is only sent **after** the task has finished. By the time the client opens the SSE stream (`connectSSE()` at `swarm.js:549`), the task is already terminal.

SSE handler at `swarm_sse.py:203-204` checks terminal status → replays history → returns immediately (`swarm_sse.py:238-239`). The card flashes "completed" for 5 seconds then auto-removes (`swarm.js:628-634`).

### Defect 4: No in-flight task tracking (engine)

**File:** `kazma-core/kazma_core/swarm/engine.py:89-106`

The engine has exactly one task container: `self._task_history` (dict). There is:
- No `_active_tasks` dict for in-flight tasks
- No `get_active_tasks()` or `list_active()` method
- No query path to find running tasks

A task exists only as a local variable inside `dispatch()` while running. It enters `_task_history` only at `_finalize_task()` (`engine.py:1109`) — by which point it's already terminal.

---

## Complete Data-Flow Trace

```
Dispatch button click
    │
    ▼
swarm.js: dispatchTask()              ← optimistic card created (this session only)
    │
    ▼
POST /api/swarm/dispatch              ← swarm_panel.py:385
    │
    ▼
engine.dispatch(swarm_task)           ← AWAITED TO COMPLETION (swarm_panel.py:510)
    │
    ├─ task.status = RUNNING          ← engine.py:239
    │                                   (NOT stored anywhere queryable)
    ├─ _dispatch_worker()             ← worker runs, emits SSE events
    │                                   (no subscriber yet)
    └─ _finalize_task()               ← engine.py:1070
        ├─ task.status = COMPLETED
        ├─ _task_history[id] = task   ← FIRST time task is stored
        └─ TaskStore.persist_task()   ← written to SQLite
    │
    ▼
HTTP response returns {task_id}       ← task ALREADY terminal
    │
    ▼
swarm.js: connectSSE(task_id)         ← opens EventSource (swarm.js:549)
    │
    ▼
swarm_sse.py: task is terminal →     ← replay history, close immediately
    return immediately                ← (swarm_sse.py:238-239)
    │
    ▼
Card shows "completed" briefly,      ← auto-removed after 5s
then disappears                        ← (swarm.js:628-634)
```

**For Telegram-dispatched tasks:** steps 1-2 (dispatch button, optimistic card) never happen in the browser. The task runs and completes, but the browser never learns the `task_id` exists.

---

## Comparison: Active vs History Tab

| Aspect | History Tab | Active Tab |
|--------|-------------|------------|
| Data source | `GET /api/swarm/tasks` → TaskStore SQLite | **None — no endpoint** |
| Trigger | `switchTab()` → `loadTaskHistory()` | `switchTab()` → **nothing** |
| Refresh | Poll + on-demand | Dispatch button only (this session) |
| Shows external tasks? | ✅ Yes (SQLite) | ❌ No |
| Shows live running? | ❌ No (completed only) | ❌ No (arrives too late) |

They are **completely different mechanisms**. History is server-backed polling. Active is a transient, session-local view that structurally cannot work.

---

## Fix Approach

The fix spans three layers. Minimum viable fix = #1 + #2 + #3.

### Fix 1: Track in-flight tasks in the engine (backend)

**File:** `kazma-core/kazma_core/swarm/engine.py`

```python
# In __init__:
self._active_tasks: dict[str, SwarmTask] = {}

# In dispatch() / _dispatch_inner() after task.status = RUNNING:
self._active_tasks[task.id] = task

# In _finalize_task() before returning:
self._active_tasks.pop(task.id, None)

# New method:
def list_active_tasks(self) -> list[SwarmTask]:
    return list(self._active_tasks.values())
```

### Fix 2: Make dispatch non-blocking (backend)

**File:** `kazma-ui/kazma_ui/swarm_panel.py:510`

```python
# Instead of:
task_result = await engine.dispatch(swarm_task)  # blocks until done

# Use:
import asyncio
asyncio.create_task(engine.dispatch(swarm_task))  # fire and forget
return JSONResponse({"task_id": swarm_task.id, "status": "dispatched"})  # return immediately
```

This lets the client subscribe to SSE **before** the task completes.

Also persist a `RUNNING` row immediately so it survives restarts.

### Fix 3: Add Active Tasks endpoint (backend)

**File:** `kazma-ui/kazma_ui/swarm_panel.py` (near line 547)

```python
@router.get("/api/swarm/tasks/active")
async def swarm_active_tasks() -> JSONResponse:
    engine = _current_engine()
    active = engine.list_active_tasks() if engine else []
    flat = [_flatten_swarm_task(t) for t in active]
    return JSONResponse({"tasks": flat, "count": len(flat)})
```

### Fix 4: Add frontend load function

**File:** `kazma-ui/kazma_ui/static/js/swarm.js`

```javascript
function loadActiveTasks() {
    fetch('/api/swarm/tasks/active')
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data || !data.tasks) return;
            var container = $('active-tasks-list');
            if (!container) return;
            data.tasks.forEach(function(t) {
                if (!$('active-task-' + t.id)) {
                    createActiveTaskCard(t);
                }
            });
        });
}
```

Wire it in `switchTab()` (`swarm.js:99`):
```javascript
if (tabId === 'active-tasks') { loadActiveTasks(); /* start poll */ }
```

### Fix 5 (optional): Global SSE channel

Add `GET /api/swarm/tasks/stream` (no task_id) that fans out all events. The engine already emits into a shared `_sse_bus`. This lets the browser auto-discover tasks from any source (Telegram, other browsers, patterns) without polling.

---

## Verification Steps

After implementing the fix:

1. **Dispatch from UI:** Click Dispatch → switch to Active Tasks tab → card should appear with "running" status and live progress
2. **Dispatch from Telegram:** Send `/swarm <task>` → open Active Tasks tab in browser → card should appear (proving cross-source discovery)
3. **Completion:** When task finishes → card updates to "completed" → moves to History after 5s
4. **Multiple concurrent:** Dispatch 3 tasks rapidly → all 3 appear as active cards simultaneously
