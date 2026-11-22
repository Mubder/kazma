"""What the passage triggers cost on episode writes with passages off (their
tables empty): the product's schema with and without the two triggers, the
same writes, alternated rounds; medians of per-statement microseconds."""
import json, os, sqlite3, statistics, tempfile, time
from kazma_core.memory.schema_v2 import ensure_primary_schema

N = 2000
def store(with_triggers):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA synchronous=NORMAL")
    ensure_primary_schema(conn)
    if not with_triggers:
        conn.execute("DROP TRIGGER IF EXISTS kazma_passages_ad")
        conn.execute("DROP TRIGGER IF EXISTS kazma_passages_au")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(episodes)")]
    return conn, path, cols

def run(with_triggers):
    conn, path, cols = store(with_triggers)
    out = {}
    def timed(label, sql, params):
        conn.execute("BEGIN")
        s = time.perf_counter()
        for p in params:
            conn.execute(sql, p)
        conn.execute("COMMIT")
        out[label] = (time.perf_counter() - s) / len(params) * 1e6
    now = time.time()
    timed("insert", "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
          "summary_text, tier, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
          [(f"e{i}", "default", "s", i, f"question {i}", f"answer {i} " * 20, "", "episodic", now) for i in range(N)])
    timed("tier move (not forgotten)", "UPDATE episodes SET tier='archived' WHERE id=?", [(f"e{i}",) for i in range(N)])
    timed("access bump", "UPDATE episodes SET access_count=access_count+1 WHERE id=?", [(f"e{i}",) for i in range(N)])
    timed("text change", "UPDATE episodes SET assistant_text=? WHERE id=?", [(f"new {i}", f"e{i}") for i in range(N)])
    timed("forget", "UPDATE episodes SET tier='forgotten', user_text='', assistant_text='', summary_text='' WHERE id=?",
          [(f"e{i}",) for i in range(N)])
    timed("delete", "DELETE FROM episodes WHERE id=?", [(f"e{i}",) for i in range(N)])
    conn.close()
    for suf in ("", "-wal", "-shm"):
        try: os.remove(path + suf)
        except OSError: pass
    return out

rounds = {True: [], False: []}
for r in range(6):
    for flag in (False, True) if r % 2 else (True, False):
        rounds[flag].append(run(flag))
res = {}
for label in rounds[True][0]:
    on = statistics.median(x[label] for x in rounds[True]); off = statistics.median(x[label] for x in rounds[False])
    res[label] = {"with_triggers_us": round(on, 2), "without_us": round(off, 2), "added_us": round(on - off, 2)}
print(json.dumps({"statements_each": N, "rounds": 6, "per_statement": res}, indent=1))
