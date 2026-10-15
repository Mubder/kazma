"""Which document worker image belongs to the code on disk.

The container profile runs every untrusted document job in the worker image
(``deploy/document-worker/Dockerfile``). That image holds a copy of
``kazma_core`` and its engines, so it is only right for the code it was built
from. Until 2026-10-15 it was built by hand under any tag, and nothing said
which code an image held beyond the documents package's own fingerprint.

Here the tag is derived from everything the build reads: the Dockerfile, its
``.dockerignore``, the hash-locked requirements and every file of
``kazma_core`` the build context sends (the ``.dockerignore`` keeps
``__pycache__`` and compiled files out, and so does this walk). The same code
gives the same tag on any machine, Windows line endings or not; any change to
the image's inputs gives another. ``scripts/document_worker_image.py build``
builds and labels it, the guard's broker asks for it by that tag, and Kazma's
readiness compares the label the broker reports with its own.

Standard library only, by design: the guard (``scripts/service/kazma_guard.py``)
loads this file by path, and the guard never imports the app.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "DOCUMENTS_LABEL",
    "REPOSITORY",
    "SOURCE_LABEL",
    "TAG_PREFIX",
    "build_command",
    "build_fingerprint",
    "expected_tag",
    "tag_for",
]

#: The image repository every guard-run broker asks for.
REPOSITORY = "kazma-document-worker"
#: Tags built from source: ``kazma-document-worker:src-<16 hex>``.
TAG_PREFIX = "src-"
#: Image labels the build writes. Image labels pass down to a container's
#: labels, so they are named apart from the broker's container labels
#: (``io.kazma.document-worker`` and ``...run``), which select containers.
SOURCE_LABEL = "io.kazma.document-worker-image.source"
DOCUMENTS_LABEL = "io.kazma.document-worker-image.documents-code"

_DOCKERFILE = Path("deploy") / "document-worker" / "Dockerfile"
_DOCKERIGNORE = Path("deploy") / "document-worker" / "Dockerfile.dockerignore"
_REQUIREMENTS = Path("deploy") / "document-worker" / "requirements.txt"
_PACKAGE = Path("kazma-core") / "kazma_core"
# What the .dockerignore keeps out of the package (**/__pycache__, **/*.pyc,
# **/*.pyo). tests/test_document_worker_image.py holds the two lists together.
_SKIPPED_DIRS = frozenset({"__pycache__"})
_SKIPPED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _package_files(root: Path) -> Iterator[Path]:
    package = root / _PACKAGE
    for path in sorted(package.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package)
        if _SKIPPED_DIRS.intersection(relative.parts[:-1]) or path.suffix in _SKIPPED_SUFFIXES:
            continue
        yield path


def _build_inputs(root: Path) -> list[Path]:
    """Every file the worker image is built from, in a fixed order.

    Raises ``FileNotFoundError`` when *root* is not a Kazma checkout (an
    install from a wheel has no Dockerfile to build from).
    """

    root = Path(root)
    fixed = [root / _DOCKERFILE, root / _DOCKERIGNORE, root / _REQUIREMENTS]
    missing = [str(path) for path in fixed if not path.is_file()]
    if missing or not (root / _PACKAGE).is_dir():
        raise FileNotFoundError(
            "not a Kazma checkout: the worker image's build inputs are missing "
            f"({', '.join(missing) or root / _PACKAGE})"
        )
    return [*fixed, *_package_files(root)]


def _normalized(data: bytes) -> bytes:
    # git keeps text files with LF and checks them out with CRLF on Windows
    # (text=auto decides by a NUL byte, as this does). A binary file -- a
    # font -- is taken as it is.
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def build_fingerprint(root: Path) -> str:
    """SHA-256 of the worker image's build inputs under *root*."""

    root = Path(root)
    digest = hashlib.sha256()
    for path in _build_inputs(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(_normalized(path.read_bytes()) + b"\0")
    return digest.hexdigest()


def tag_for(fingerprint: str) -> str:
    """The image reference built from *fingerprint*."""

    return f"{REPOSITORY}:{TAG_PREFIX}{fingerprint[:16]}"


def expected_tag(root: Path) -> str:
    """The image reference for the code under *root*."""

    return tag_for(build_fingerprint(root))


def build_command(python: str = "python") -> str:
    """The one command, run in the install folder, that builds the image for
    the code on disk. *python* is the install's interpreter as the operator
    types it."""

    script = Path("scripts") / "document_worker_image.py"
    return f"{python} {script} build"
