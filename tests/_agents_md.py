"""``scripts/agents_md.py`` for tests: AGENTS.md is a core and section pieces.

A test that reads what AGENTS.md says asks ``joined()`` (the single file the
pieces make) or ``load()`` (the core and the pieces), never the core alone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agents_md.py"

_spec = importlib.util.spec_from_file_location("kazma_agents_md", SCRIPT)
assert _spec and _spec.loader
agents_md = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = agents_md  # a dataclass looks its module up
_spec.loader.exec_module(agents_md)

joined = agents_md.joined
load = agents_md.load
