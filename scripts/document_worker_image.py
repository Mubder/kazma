#!/usr/bin/env python3
"""Build the document worker image for the code on disk, and say which runs.

The container profile runs every untrusted document job in the worker image
(``deploy/document-worker/Dockerfile``), which holds a copy of ``kazma_core``.
The guard's broker asks for the image built from this checkout, tagged by a
fingerprint of everything the build reads
(``kazma_core/documents/worker_image.py``)::

    python scripts/document_worker_image.py build     # build (or say it is built)
    python scripts/document_worker_image.py status    # what is built, what serves
    python scripts/document_worker_image.py prune     # remove older builds

Run it in the install folder with the install's Python. A build takes a few
minutes the first time (the engines) and seconds after that (only the code
layer changes). A broker serving an older image switches to the new one by
itself within a minute; nothing needs a restart.

Exit codes: 0 done, 1 the build or a removal failed, 2 Docker or the checkout
is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "kazma-core"))

from kazma_core.docker_cli import find_docker_cli  # noqa: E402
from kazma_core.documents import worker_image  # noqa: E402
from kazma_core.documents.worker_bundle import code_fingerprint  # noqa: E402

DOCKERFILE = Path("deploy") / "document-worker" / "Dockerfile"


def _docker() -> str:
    docker = find_docker_cli()
    if not docker:
        print("The docker CLI was not found (PATH, KAZMA_DOCKER_BIN, Docker's install folders).",
              file=sys.stderr)
        raise SystemExit(2)
    return docker


def _run(docker: str, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed argv, docker CLI found by docker_cli, no shell
        [docker, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )


def _image(docker: str, ref: str) -> dict | None:
    """``{id, created, labels}`` of a local image, or None."""
    result = _run(docker, "image", "inspect", "--format",
                  "{{.Id}}|{{.Created}}|{{json .Config.Labels}}", ref)
    if result.returncode != 0:
        return None
    image_id, created, labels = (result.stdout.strip().split("|", 2) + ["", ""])[:3]
    try:
        parsed = json.loads(labels or "null") or {}
    except json.JSONDecodeError:
        parsed = {}
    return {"id": image_id, "created": created, "labels": parsed}


def _built(docker: str) -> list[str]:
    """Every image this script built, newest first, as ``repository:tag``."""
    result = _run(docker, "image", "ls", "--filter", f"label={worker_image.SOURCE_LABEL}",
                  "--format", "{{.Repository}}:{{.Tag}}")
    return [line for line in result.stdout.split() if "<none>" not in line]


def _python_here() -> str:
    """This Python as typed in the install folder."""
    exe = sys.executable or "python"
    try:
        relative = os.path.relpath(exe, REPO)
    except ValueError:  # another drive
        relative = ""
    if relative and not relative.startswith(".."):
        exe = relative
    return f'"{exe}"' if " " in exe else exe


def _guard_broker() -> dict:
    """What the guard's state file says its broker serves (or {})."""
    named = os.environ.get("KAZMA_GUARD_STATE")
    home = Path(os.environ.get("KAZMA_USER_HOME") or REPO / ".kazma")
    path = Path(named) if named else home / "guard.state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    broker = data.get("document_broker") if isinstance(data, dict) else None
    return broker if isinstance(broker, dict) else {}


def _fingerprints() -> tuple[str, str]:
    try:
        source = worker_image.build_fingerprint(REPO)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    documents = code_fingerprint(REPO / "kazma-core" / "kazma_core" / "documents")
    return source, documents


def cmd_build(args: argparse.Namespace) -> int:
    docker = _docker()
    source, documents = _fingerprints()
    tag = worker_image.tag_for(source)
    existing = _image(docker, tag)
    if existing and not args.force:
        print(f"Already built for this code: {tag} ({existing['id'][:19]})")
        return 0
    print(f"Building {tag} for the code in {REPO} ...", flush=True)
    argv = [docker, "build", "-f", str(DOCKERFILE), "-t", tag,
            "--label", f"{worker_image.SOURCE_LABEL}={source}",
            "--label", f"{worker_image.DOCUMENTS_LABEL}={documents}"]
    if args.no_cache:
        argv.append("--no-cache")
    argv.append(".")
    code = subprocess.call(argv, cwd=str(REPO))  # nosec B603 - fixed argv, no shell
    if code != 0:
        print(f"The build failed (docker exit {code}).", file=sys.stderr)
        return 1
    built = _image(docker, tag)
    if not built or built["labels"].get(worker_image.SOURCE_LABEL) != source:
        print(f"{tag} was built but does not carry this code's label.", file=sys.stderr)
        return 1
    print(f"Built {tag} ({built['id'][:19]}).")
    print("The guard's broker switches to it by itself within a minute when it serves an "
          "older one, or starts with it the next time it starts.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    docker = _docker()
    source, _documents = _fingerprints()
    tag = worker_image.tag_for(source)
    current = _image(docker, tag)
    broker = _guard_broker()
    report = {
        "code": tag,
        "built": bool(current),
        "image_id": current["id"] if current else None,
        "built_images": _built(docker),
        "broker": {key: broker.get(key) for key in (
            "state", "image", "image_requested", "image_fallback", "url", "log")} if broker else None,
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"this code needs : {tag} ({'built' if current else 'NOT BUILT'})")
    print(f"built here      : {', '.join(report['built_images']) or 'none'}")
    if broker:
        serving = broker.get("image") or "-"
        note = " (an older build)" if broker.get("image_fallback") else ""
        print(f"guard's broker  : {broker.get('state')}, serving {serving}{note}")
    else:
        print("guard's broker  : no record (no guard runs one here)")
    if not current:
        print(f"Build it: {worker_image.build_command(_python_here())}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    docker = _docker()
    source, _documents = _fingerprints()
    keep = {worker_image.tag_for(source)}
    broker = _guard_broker()
    for key in ("image", "image_requested"):
        if broker.get(key):
            keep.add(str(broker[key]))
    older = [ref for ref in _built(docker) if ref not in keep][args.keep:]
    failed = 0
    for ref in older:
        result = _run(docker, "image", "rm", ref, timeout=120)
        if result.returncode == 0:
            print(f"removed {ref}")
        else:
            failed += 1
            print(f"kept {ref}: {(result.stderr or result.stdout).strip()[:200]}")
    if not older:
        print("Nothing to remove.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The document worker image for the code on disk.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the image for this code (if not built)")
    build.add_argument("--force", action="store_true", help="build even when it is built")
    build.add_argument("--no-cache", action="store_true", help="rebuild every layer")
    build.set_defaults(func=cmd_build)
    status = sub.add_parser("status", help="what this code needs, what is built, what serves")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    prune = sub.add_parser("prune", help="remove older builds (never this code's or the serving one)")
    prune.add_argument("--keep", type=int, default=1, help="older builds to keep besides (default 1)")
    prune.set_defaults(func=cmd_prune)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
