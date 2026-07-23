"""Allow running kazma_tui as a module: python -m kazma_tui."""

from __future__ import annotations

from kazma_tui.app import main

__all__: list[str] = []

if __name__ == "__main__":
    main()
