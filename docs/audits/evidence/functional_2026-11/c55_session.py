"""AUDIT-2611 C55 sibling, offline: a web session copied by a migration import, then signed out."""
from __future__ import annotations

import json
import os
import sys
import tempfile

mode, work = sys.argv[1], sys.argv[2]
for k in ("KAZMA_DATABASE_URL", "DATABASE_URL", "KAZMA_STATE_BACKEND"):
    os.environ.pop(k, None)
os.environ["KAZMA_DATA_DIR"] = tempfile.mkdtemp(prefix=f"audit2611_c55s_{mode}_")
os.environ["KAZMA_TEST_ISOLATION"] = "1"

from kazma_core.config_store import get_config_store  # noqa: E402
from kazma_core.security import web_sessions as ws  # noqa: E402

cs = get_config_store()
out = {"mode": mode}
if mode == "export":
    sid = ws.create_session(role="admin", actor="audit2611", username="audit2611")
    key = f"web_session.{ws._hash(sid)}"
    json.dump({"sid": sid, "rows": {key: cs.get(key)}}, open(os.path.join(work, "c55s_rows.json"), "w"))
    open(os.path.join(work, "c55s_config.yaml"), "w", encoding="utf-8").write(cs.export_yaml())
    out["live"] = ws.get_session_payload(sid) is not None
else:
    d = json.load(open(os.path.join(work, "c55s_rows.json")))
    if mode == "pg":
        for k, v in d["rows"].items():
            cs.set(k, v, category="auth")
    cs.import_yaml(open(os.path.join(work, "c55s_config.yaml"), encoding="utf-8").read())
    sid = d["sid"]
    out["live_before"] = ws.get_session_payload(sid) is not None
    ws.revoke_session(sid)
    out["live_right_after_signout"] = ws.get_session_payload(sid) is not None
    ws._cache.clear(); ws._revoked.clear()  # 30 s later, or after a restart
    p = ws.get_session_payload(sid)
    out["live_after_signout_30s"] = p is not None
    out["role"] = (p or {}).get("role")
print(json.dumps(out, indent=1))
