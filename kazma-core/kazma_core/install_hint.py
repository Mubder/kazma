"""How Kazma tells someone to install one of its optional extras.

Kazma is installed from its own folder -- a git checkout (``pip install -e``)
or a release wheel -- and never from PyPI. No Kazma package is published
there, so ``kazma``, ``kazma-core`` and the rest are anyone's names to
register, and a hint that installs ``kazma[web]`` by name sends whoever
follows it, from any environment where Kazma is not already installed, to the
first person who registers the name (2026-09-30: twelve such hints, and
``kazma update`` itself installed ``kazma`` from PyPI). Settings -> Packages
installs an extra the same local way (``system/installer.py``).
``tests/test_no_pypi_kazma.py`` holds product text to this.
"""

from __future__ import annotations

__all__ = ["extra_install_hint"]


def extra_install_hint(extra: str) -> str:
    """The words for installing *extra* (``web`` -> Settings or ``pip install -e ".[web]"``)."""
    return f'Settings -> Packages, or pip install -e ".[{extra}]" in the Kazma folder'
