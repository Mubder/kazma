"""Stub a module in ``sys.modules`` without evicting anything else.

``patch.dict(sys.modules, {...})`` snapshots ``sys.modules`` on entry and, on
exit, CLEARS it and restores the snapshot. Every module first imported while
the patch was open is therefore evicted. The next import builds a fresh module
object: a later test that patches the old object patches a copy nobody uses,
and ``isinstance`` across the two copies is False. That is how
``kazma_core.security.ssrf`` vanished mid-suite and made
``test_read_url_connection_error`` pass or fail depending on which files shared
its chunk (docs/KNOWN_GAPS.md, 2026-09-21).

:func:`stub_modules` touches only the names it is given and restores exactly
those. ``monkeypatch.setitem(sys.modules, name, fake)`` is equally safe where a
``monkeypatch`` fixture is at hand; ``tests/test_module_stubs.py`` keeps
``patch.dict(sys.modules, ...)`` out of the suite.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

__all__ = ["stub_modules"]

_ABSENT = object()


@contextmanager
def stub_modules(stubs: Mapping[str, Any]) -> Iterator[None]:
    """Install *stubs* in ``sys.modules`` for the block; restore only those names.

    A value of ``None`` makes ``import name`` raise ``ImportError``, exactly
    as it does with ``patch.dict``.
    """
    saved = {name: sys.modules.get(name, _ABSENT) for name in stubs}
    try:
        for name, module in stubs.items():
            sys.modules[name] = module
        yield
    finally:
        for name, previous in saved.items():
            if previous is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
