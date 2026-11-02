"""AUDIT-2611 C50, offline: does Settings -> Tools' switch stop a tool? Private data dir, dev repo code.

Switch `current_datetime` off the way the page does (SettingsManager.toggle_tool), then ask the
registry the model is offered from (get_tool_definitions) and run the tool (execute). For contrast,
switch a skill off the way the Skills page does (set_skill_enabled) and ask the same two questions.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

for k in ("KAZMA_DATABASE_URL", "DATABASE_URL", "KAZMA_STATE_BACKEND"):
    os.environ.pop(k, None)
d = tempfile.mkdtemp(prefix="audit2611_c50_")
os.environ["KAZMA_DATA_DIR"] = d
os.environ["KAZMA_TEST_ISOLATION"] = "1"

from kazma_core.agent.tool_registry import LocalToolRegistry  # noqa: E402
from kazma_core.config_store import get_config_store  # noqa: E402
from kazma_core.settings_manager import SettingsManager  # noqa: E402

TOOL = "current_datetime"
cs = get_config_store()
sm = SettingsManager(cs)
out = {"data_dir": d}

reg = LocalToolRegistry(include_builtins=True)
before = [t["function"]["name"] for t in reg.get_tool_definitions()]
out["offered_before"] = TOOL in before

sm.toggle_tool(TOOL, False)
out["stored"] = cs.get(f"tools.{TOOL}.enabled", None)
listing = {t["name"]: t["enabled"] for t in sm.get_tool_registry()}
out["settings_listing_says_enabled"] = listing.get(TOOL)

reg2 = LocalToolRegistry(include_builtins=True)
after = [t["function"]["name"] for t in reg2.get_tool_definitions()]
out["offered_after_switch_off"] = TOOL in after
res = asyncio.run(reg2.execute(TOOL, {}))
out["execute_after_switch_off"] = {"is_error": res.get("is_error"), "content": str(res.get("content"))[:120]}

# contrast: the skill switch
tool_obj = reg2.get_tool(TOOL)
skill_tools = [t for t in reg2._tools.values() if t.skill_id]
out["contrast"] = None
if skill_tools:
    from kazma_core.skills.switches import set_skill_enabled

    st = skill_tools[0]
    set_skill_enabled(st.skill_id, False)
    reg3 = LocalToolRegistry(include_builtins=True)
    names3 = [t["function"]["name"] for t in reg3.get_tool_definitions()]
    out["contrast"] = {"skill": st.skill_id, "tool": st.name, "offered_after_skill_off": st.name in names3}
    set_skill_enabled(st.skill_id, True)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0)
