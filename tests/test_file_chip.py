"""The file chip's tool list is the server's file-writing tools, nothing else.

Live 2026-09-24: x_list_scheduled -- a read -- showed "WROTE Asia/Kuwait".
chat.js now shows the chip only for tools in _FILE_CHIP_OPS. This keeps that
list honest against the single source of truth for what a tool does:
kazma_core.safety.side_effects (EffectKind.WRITE_FS). Behaviour:
tests/js/test_file_chip.js.
"""

from __future__ import annotations

import re
from pathlib import Path

from kazma_core.safety.side_effects import EffectKind, get_effect_profile

CHAT_JS = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
CORE_FILE_TOOLS = {"file_write", "file_append", "file_apply_patch", "file_apply_patch_set", "file_delete"}


def _chip_tools(src: str) -> set[str]:
    block = re.search(r"var _FILE_CHIP_OPS = \{(.*?)\};", src, re.DOTALL)
    assert block, "_FILE_CHIP_OPS not found in chat.js"
    return set(re.findall(r"^\s*(\w+)\s*:", block.group(1), re.MULTILINE))


def _not_file_writers(tools: set[str]) -> list[str]:
    return sorted(t for t in tools if get_effect_profile(t).effect is not EffectKind.WRITE_FS)


def test_every_chip_tool_writes_files():
    tools = _chip_tools(CHAT_JS.read_text(encoding="utf-8"))
    assert not _not_file_writers(tools), (
        "a chip that says WROTE for a tool the side-effect registry does not "
        f"classify WRITE_FS: {_not_file_writers(tools)}"
    )


def test_the_core_file_tools_keep_their_chip():
    tools = _chip_tools(CHAT_JS.read_text(encoding="utf-8"))
    assert CORE_FILE_TOOLS <= tools, sorted(CORE_FILE_TOOLS - tools)


def test_the_parity_gate_catches_a_read_tool():
    """Negative control (AGENTS.md section 28): the live mislabel."""
    bad = "var _FILE_CHIP_OPS = {\n  file_write: 'wrote',\n  x_list_scheduled: 'wrote'\n};"
    assert _not_file_writers(_chip_tools(bad)) == ["x_list_scheduled"]
