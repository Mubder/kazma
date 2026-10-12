"""Allow running ``python -m kazma_core.hub.cli``."""

from kazma_core import env_files
from kazma_core.hub.cli import main

__all__: list[str] = []

env_files.load_env_files()
main()
