#!/usr/bin/env python3
"""Regenerate docs/docs/reference/tools-catalog.md from live code.

Usage (repo root):
    python scripts/generate_tools_catalog.py

Run after adding built-in tools or native skill tools so the published
catalog stays accurate. Safe to run in CI as a drift check (compare git diff).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "docs" / "reference" / "tools-catalog.md"

def _danger() -> set[str]:
    """SoT is CANONICAL_DANGER_TOOLS — do not maintain a parallel set."""
    sys.path.insert(0, str(ROOT / "kazma-core"))
    from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

    return set(CANONICAL_DANGER_TOOLS)


DANGER = _danger()


def danger_label(name: str) -> str:
    return "**danger**" if name in DANGER else "safe/read"


def extract_builtin() -> list[dict]:
    files = [
        ROOT / "kazma-core/kazma_core/agent/tool_registry.py",
        ROOT / "kazma-core/kazma_core/agent/tool_builtins.py",
    ]
    patterns = (
        r"@self\.register\((.*?)\)\s*\n\s*async def ([a-zA-Z0-9_]+)",
        r"@registry\.register\((.*?)\)\s*\n\s*async def ([a-zA-Z0-9_]+)",
    )
    tools: list[dict] = []
    seen: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            for m in re.finditer(pat, text, re.S):
                block, fn = m.group(1), m.group(2)
                nm = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", block)
                cat = re.search(r"category\s*=\s*['\"]([^'\"]+)['\"]", block)
                desc_s = re.search(r"description\s*=\s*['\"]([^'\"]+)['\"]", block)
                desc_p = re.search(
                    r"description\s*=\s*\(\s*((?:['\"][^'\"]*['\"]\s*)+)\)",
                    block,
                )
                if desc_s:
                    description = desc_s.group(1)
                elif desc_p:
                    description = "".join(re.findall(r"['\"]([^'\"]*)['\"]", desc_p.group(1)))
                else:
                    description = ""
                name = nm.group(1) if nm else fn
                if name in seen:
                    continue
                seen.add(name)
                tools.append(
                    {
                        "name": name,
                        "description": description[:200],
                        "category": cat.group(1) if cat else "",
                    }
                )
    return tools


def extract_modules() -> list[str]:
    d = ROOT / "kazma-core/kazma_core/tools"
    return sorted(p.stem for p in d.glob("*.py") if p.stem != "__init__")


def extract_native() -> list[dict]:
    try:
        import yaml
    except ImportError:
        print("PyYAML required for native skill scan", file=sys.stderr)
        return []

    native: list[dict] = []
    root = ROOT / "kazma-skills/kazma_skills/native"
    for p in sorted(root.rglob("skill_manifest.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        tools_map = data.get("tools") or {}
        if not isinstance(tools_map, dict):
            continue
        skill = data.get("name") or p.parent.name
        for tname, meta in tools_map.items():
            if isinstance(meta, dict):
                native.append(
                    {
                        "name": tname,
                        "skill": skill,
                        "description": (meta.get("description") or "")[:200],
                        "category": meta.get("category") or "",
                    }
                )
            else:
                native.append(
                    {
                        "name": tname,
                        "skill": skill,
                        "description": "",
                        "category": "",
                    }
                )
    return native


def render(builtin: list[dict], modules: list[str], native: list[dict]) -> str:
    lines: list[str] = [
        "---",
        "id: tools-catalog",
        "title: Tools Catalog",
        "sidebar_label: Tools Catalog",
        "description: Complete catalog of built-in agent tools and native skill tools",
        "---",
        "",
        "> Exhaustive tool list extracted from `LocalToolRegistry` and native skill manifests. "
        "Regenerate with `python scripts/generate_tools_catalog.py`. "
        "Danger classification aligns with `CANONICAL_DANGER_TOOLS` — see "
        "[Security & Safety](../guide/security-and-safety).",
        "",
        "## How tools run",
        "",
        "| Layer | Module | Notes |",
        "|-------|--------|-------|",
        "| Built-in registry | `kazma_core/agent/tool_registry.py` | Supervisor SoT; HITL in `execute()` |",
        "| Schema | `kazma_core/agent/tool_schema.py` | Closed JSON Schema (`additionalProperties: false`). `KAZMA_STRICT_TOOLS=1` adds OpenAI `function.strict`. |",
        "| Structured JSON | `LLMProvider.chat(..., response_format=)` | Opt-in per call (`json_schema` / `json_object`). Not on every supervisor turn. |",
        "| Hooks | `kazma_core/agent/tool_hooks.py` | PreToolUse (deny/rewrite) + PostToolUse (observe). Not HITL. `KAZMA_TOOL_HOOKS=0`. |",
        "| Unified executor | MCP + local | MCP non-allowlist tools force danger under production |",
        "| IDE path | `IdeService._call_tool` | Same registry — no bypass |",
        "| Native skills | `kazma-skills/kazma_skills/native/*` | Loaded via skill manifests |",
        "| MCP spec (client) | `mcp_list_resources` / `mcp_read_resource` / `mcp_list_prompts` / `mcp_get_prompt` | Resources fenced; prompts user-visible; sampling HITL. Not an MCP server. |",
        "| Computer use | `computer_use` | Screenshot→action (Playwright). Native Anthropic CUA / Gemini function when that model is active; else vision-JSON. HITL **danger**. `KAZMA_COMPUTER_USE=0`. `KAZMA_CUA_PLANNER=0`. |",
        "",
        "## Built-in tools (LocalToolRegistry)",
        "",
        "| Tool | Category | Danger (typical) | Description |",
        "|------|----------|------------------|-------------|",
    ]
    for t in builtin:
        desc = (t.get("description") or "").replace("|", "\\|")
        lines.append(
            f"| `{t['name']}` | {t.get('category') or '—'} | {danger_label(t['name'])} | {desc} |"
        )

    lines += [
        "",
        "### Related tool modules (`kazma_core/tools/`)",
        "",
        "These modules implement or support tools (some registered at startup, some via skills):",
        "",
    ]
    for m in modules:
        lines.append(f"- `{m}.py`")

    lines += [
        "",
        "## Native skill tools",
        "",
        "| Tool | Skill | Category | Danger (typical) | Description |",
        "|------|-------|----------|------------------|-------------|",
    ]
    for t in native:
        desc = (t.get("description") or "").replace("|", "\\|")
        lines.append(
            f"| `{t['name']}` | {t.get('skill') or '—'} | {t.get('category') or '—'} | "
            f"{danger_label(t['name'])} | {desc} |"
        )

    lines += [
        "",
        "## Manifest-only coding skills",
        "",
        "Some native folders ship manifests without a tools map (prompt/workflow skills): "
        "`code-review`, `fix-lint`, `refactor-file`, `write-tests`. "
        "They appear in the hub/skills UI but do not register discrete tool functions like the rows above.",
        "",
        "## MCP tools",
        "",
        "MCP servers configured under `mcp.servers` in `kazma.yaml` contribute tools at runtime. Classification:",
        "",
        "- Name patterns containing write/exec/delete → danger",
        "- read/list/get → often safe",
        "- Unknown → danger (fail-closed)",
        "- Production may force HITL for non-allowlisted MCP tools",
        "",
        "See [Skills, MCP & Tools](../guide/skills-mcp-and-tools).",
        "",
        "## Canonical danger list (HITL)",
        "",
        "From `kazma_core/safety/hitl.py` → `CANONICAL_DANGER_TOOLS` (also mirrored in this script):",
        "",
    ]
    for name in sorted(DANGER):
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    builtin = extract_builtin()
    modules = extract_modules()
    native = extract_native()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(builtin, modules, native), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(builtin)} builtin, {len(native)} native)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
