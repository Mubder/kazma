#!/usr/bin/env python3
"""Regenerate docs/docs/reference/tools-catalog.md from live code.

Usage (repo root):
    python scripts/generate_tools_catalog.py

Run after adding built-in tools or native skill tools so the published
catalog stays accurate. ``tests/test_tools_catalog.py`` fails when a
registered tool is missing from the catalog, or when its danger label
disagrees with the approval gate.

Built-in rows come from the LIVE registry (``LocalToolRegistry`` +
``register_builtin_tools``), not from grepping source. Until 2026-09-25 this
regex-scanned ``agent/tool_builtins.py`` for ``@registry.register``
decorators; that module had been split into a package that registers with
``register_function(...)``, so the scan found 1 built-in tool and a run would
have deleted ~70 rows. Native skill tools, which the same registry holds,
are listed once — in their manifest section, with the skill they belong to.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "docs" / "reference" / "tools-catalog.md"

#: Longest description kept in a table row (whitespace collapsed first).
_DESCRIPTION_LIMIT = 300

for _pkg in ("kazma-core", "kazma-skills"):
    if str(ROOT / _pkg) not in sys.path:
        sys.path.insert(0, str(ROOT / _pkg))


def _danger() -> set[str]:
    """Tools that need an approval by default: the canonical danger list plus
    every ``danger`` tier. The canonical list alone labelled danger-tier tools
    outside it — ``send_file``, ``browser_navigate``, ``email_categorize`` —
    as safe/read. Do not maintain a parallel set."""
    from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS, TOOL_TIERS

    return set(CANONICAL_DANGER_TOOLS) | {n for n, t in TOOL_TIERS.items() if t == "danger"}


DANGER = _danger()


def danger_label(name: str) -> str:
    return "**danger**" if name in DANGER else "safe/read"


def _one_line(text: object, limit: int = _DESCRIPTION_LIMIT) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _live_registry():
    """The registry a running Kazma builds: built-ins plus native skills."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry


def _is_native(tool: object) -> bool:
    module = getattr(getattr(tool, "func", None), "__module__", "") or ""
    return module.startswith("kazma_skills.native.")


def extract_builtin() -> list[dict]:
    """Every registered tool that is not a native skill tool, in registration order."""
    rows: list[dict] = []
    for name, tool in _live_registry()._tools.items():
        if _is_native(tool):
            continue  # listed with its skill in the native section
        rows.append(
            {
                "name": name,
                "description": _one_line(getattr(tool, "description", "")),
                "category": getattr(tool, "category", "") or "",
            }
        )
    return rows


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
                        "description": _one_line(meta.get("description")),
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
        "## Tools that need approval (HITL)",
        "",
        "From `kazma_core/safety/hitl.py`: `CANONICAL_DANGER_TOOLS` plus every `danger` tier in "
        "`TOOL_TIERS` — the same set the Danger column above is labelled from:",
        "",
    ]
    for name in sorted(DANGER):
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    builtin = extract_builtin()
    modules = extract_modules()
    native = extract_native()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(builtin, modules, native), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(builtin)} builtin, {len(native)} native)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
