"""AUDIT-2611 C55, offline: does a migration import keep approval grants revocable?

The importer restores the Postgres dump (whole rows) and then imports config.yaml
(ConfigStore.export_yaml of the source, flattened to leaf rows by import_yaml).
Mode "export": private store A, grant shell_exec for one hour, write export_yaml and
the raw rows. Mode "sqlite": fresh store B, import_yaml only (a SQLite source).
Mode "pg": fresh store B, the raw rows first (the dump), then import_yaml.
Then list, clear (what /hitl revoke and the page call) and check the grant again.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

mode, work = sys.argv[1], sys.argv[2]
for k in ("KAZMA_DATABASE_URL", "DATABASE_URL", "KAZMA_STATE_BACKEND"):
    os.environ.pop(k, None)
os.environ["KAZMA_DATA_DIR"] = tempfile.mkdtemp(prefix=f"audit2611_c55_{mode}_")
os.environ["KAZMA_TEST_ISOLATION"] = "1"

from kazma_core.config_store import get_config_store  # noqa: E402
from kazma_core.safety import hitl_grants as g  # noqa: E402

T = "audit2611-c55-thread"
cs = get_config_store()
out = {"mode": mode}
if mode == "export":
    g.grant_tool(T, "shell_exec", actor="audit2611", ttl_seconds=3600)
    open(os.path.join(work, "c55_config.yaml"), "w", encoding="utf-8").write(cs.export_yaml())
    rows = {k: cs.get(k) for k in [f"hitl_grant.{T}.shell_exec"]}
    json.dump(rows, open(os.path.join(work, "c55_rows.json"), "w"))
    out["granted"] = g.has_tool_grant(T, "shell_exec")
else:
    if mode == "pg":
        for k, v in json.load(open(os.path.join(work, "c55_rows.json"))).items():
            cs.set(k, v, category="safety")
    n = cs.import_yaml(open(os.path.join(work, "c55_config.yaml"), encoding="utf-8").read())
    out["imported_keys"] = n
    out["leaf_rows"] = sorted(k for k in (cs.get_all() or {}).get("hitl_grant", {}) )[:8]
    out["granted_before"] = g.has_tool_grant(T, "shell_exec")
    out["listed"] = g.list_grants(T)
    out["cleared"] = g.clear_grants(T, actor="audit2611")
    out["granted_after_clear"] = g.has_tool_grant(T, "shell_exec")
    out["raw_after_clear"] = cs.get(f"hitl_grant.{T}.shell_exec")
print(json.dumps(out, default=str, indent=1))
