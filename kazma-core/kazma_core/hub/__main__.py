"""Allow running ``python -m kazma_core.hub.cli``."""

from kazma_core.env_files import load_env_files
from kazma_core.hub.cli import main

__all__: list[str] = []

load_env_files()
main()
