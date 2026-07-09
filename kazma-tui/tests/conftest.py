"""TUI test configuration - ensures kazma_tui is importable."""

import sys
from pathlib import Path

# Add kazma-tui package root to path so tests can import kazma_tui
tui_root = Path(__file__).parent.parent
sys.path.insert(0, str(tui_root))