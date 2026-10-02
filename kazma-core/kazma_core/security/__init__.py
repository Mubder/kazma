"""Kazma's security modules.

Import what you use from its own module (``kazma_core.security.vault``,
``kazma_core.security.secret_scan``, ...). The package imports nothing
itself: until 2026-10-02 it imported six modules for every ``from
kazma_core.security import vault``, each behind ``except ImportError: pass``,
so a broken module became a silently missing name (AGENTS.md §24A) and no
caller used any of those re-exports.
"""

from __future__ import annotations
