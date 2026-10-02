"""``python -m kazma_cli``: the ``kazma`` command, run without its launcher.

On Windows ``kazma update`` runs this way: a reinstall must replace
``kazma.exe``, and Windows lets nothing replace -- or rename -- a launcher
while a program runs it (``kazma_cli.update._launchers_in_use``).
"""

from __future__ import annotations

from kazma_cli.main import main

if __name__ == "__main__":
    main()
