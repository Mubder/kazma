"""Read a module's source whether it is a ``.py`` file or a package directory.

Several suites assert on module *text* rather than behaviour ("does sse_chat
still pin the turn model?"). Those greps broke when the four god modules were
split into packages (audit O5), because ``kazma_ui/sse_chat.py`` became
``kazma_ui/sse_chat/``.

``module_source`` accepts the historical file path and transparently falls back
to the package of the same stem, concatenating every ``.py`` inside it. The
assertions keep working, and they keep working the next time a module is split.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["module_source", "module_exists"]


def module_exists(path: str | Path) -> bool:
    """True when *path* resolves to a module file or a package of that name.

    The companion to :func:`module_source`. A plain ``path.exists()`` check
    returns False for a module that has become a package, which silently
    skipped several "is this endpoint wired?" assertions after the audit-O5
    splits — the tests passed by checking nothing.
    """
    p = Path(path)
    if p.exists():
        return True
    return p.suffix == ".py" and p.with_suffix("").is_dir()


def module_source(path: str | Path) -> str:
    """Return the full source text for *path*.

    Args:
        path: Either ``.../name.py`` or ``.../name`` (a package directory).
            A ``.py`` path that no longer exists falls back to the package
            directory of the same stem.

    Raises:
        FileNotFoundError: if neither a module file nor a package exists.
    """
    p = Path(path)

    # A package's ``__file__`` points at its ``__init__.py``. Callers that got
    # here from ``module.__file__`` mean "the whole module", which for a
    # package is every file in it — so read the package, not just the shim.
    if p.name == "__init__.py" and p.parent.is_dir():
        p = p.parent
    elif p.is_file():
        return p.read_text(encoding="utf-8")

    pkg = p.with_suffix("") if p.suffix == ".py" else p
    if pkg.is_dir():
        # Sorted for determinism; __init__ first so "module header" greps that
        # expect the top of the old file still hit early.
        files = sorted(pkg.rglob("*.py"), key=lambda f: (f.name != "__init__.py", str(f)))
        parts = []
        for f in files:
            if "__pycache__" in f.parts:
                continue
            parts.append(f"# ── {f.relative_to(pkg).as_posix()} ──\n")
            parts.append(f.read_text(encoding="utf-8"))
        if parts:
            return "\n".join(parts)

    raise FileNotFoundError(f"no module file or package at {path}")
