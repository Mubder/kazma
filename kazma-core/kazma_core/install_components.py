"""What a Kazma install can hold: one table, three ways to install it.

The owner's three installs (2026-11-07):

* **Express** (the default): the least Kazma can lack, in one run. Every part
  marked ``express`` below, on the systems its ``express_on`` names. Private
  web search (SearXNG) and the container engine it runs on joined Express on
  Windows and Linux on 2026-11-13 (the owner, reversing "Express stays
  container-free" of 2026-11-12): Kazma's engine starts at boot with nobody
  signed in, so a container no longer waits for a sign-in. macOS Express stays
  container-free (Docker Desktop starts only at a sign-in there). They are
  ``best_effort``: a computer that cannot run containers (virtualization off,
  Docker already in another WSL distro) gets everything else, is told what it
  lacks, and the install still ends as done.
* **Full**: everything Kazma has. Full is not a list kept by hand: it is
  every part of :data:`COMPONENTS`, so a part added here joins Full the day it
  is added (``tests/test_install_modes.py``).
* **Custom**: a checklist that starts from Express; each part says what Kazma
  lacks without it.

Every Python extra in ``pyproject.toml`` belongs to one part or is declared in
:data:`EXTRA_ALIASES` with why it needs none, so a new extra cannot be left
out of Full. ``kazma setup install`` (``kazma_cli.install``) checks each part,
shows the plan and installs what is missing; this module holds only the table
and the rules that read it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COMPONENTS", "EXTRA_ALIASES", "MODES", "OS_NAMES", "Component", "by_id", "components_for",
           "current_os", "express_ids", "extras_for", "full_ids", "install_overview",
           "parts_of_extra", "toggle"]

MODES = ("express", "full", "custom")

#: The systems the table knows, as ``postgres_service.Platform.os`` names them.
OS_NAMES = ("windows", "linux", "macos")


@dataclass(frozen=True)
class Component:
    """One part of an install.

    ``extras``: the Python extras it installs. ``system``: the system
    dependencies it needs (``kazma_core.system_deps.DEPENDENCIES``). ``needs``:
    the parts it cannot work without (chosen with it, dropped with them).
    ``admin``: installing it on Windows needs an administrator terminal.
    ``express_on``: the systems (:data:`OS_NAMES`) where an ``express`` part is
    in Express; empty means every system. ``best_effort``: a part some
    computers cannot have (no virtualization, a conflicting Docker): when the
    machine blocks it, the install says what Kazma lacks and still ends as
    done; a step of it that fails is still a failure."""

    id: str
    title: str
    gives: str
    lacks: str
    express: bool = False
    extras: tuple[str, ...] = ()
    system: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    size: str = ""
    admin: bool = False
    express_on: tuple[str, ...] = ()
    best_effort: bool = False


#: Every part, in install order: Kazma's own packages first, then what the
#: machine needs, then what runs on it, and the service that starts Kazma last.
COMPONENTS: tuple[Component, ...] = (
    Component(
        "meaning_search", "Meaning search (bge-m3)",
        "memory and the Knowledge Library find what a question is about, not only its words",
        "memory recall and the Knowledge Library match words only",
        express=True, extras=("rag",), system=("vcruntime",), size="about 2 GB (PyTorch)", admin=True),
    Component(
        "documents", "Document reading and writing (PyMuPDF, PDFium, WeasyPrint)",
        "PDFs read with their Arabic, tables and layout; HTML and Markdown to PDF",
        "PDFs are read by the simpler built-in readers (weaker on Arabic and tables)",
        express=True, extras=("document-platform",)),
    Component(
        "tests", "Kazma's test suite (pytest, ruff)",
        "you and the agent can run Kazma's tests on this install",
        "Kazma's tests cannot run here",
        express=True, extras=("dev",)),
    Component(
        "code_index", "Code structure (tree-sitter)",
        "the code index understands Python and JavaScript structure",
        "the code index reads code by pattern only",
        express=True, extras=("index",)),
    Component(
        "databases", "Database drivers (MySQL, MongoDB, Postgres)",
        "the database tools reach MySQL, MongoDB and Postgres servers",
        "the database tools reach SQLite files only",
        express=True, extras=("database",)),
    Component(
        "push", "Push notifications (Web Push)",
        "your browser or phone is told when a long task finishes",
        "no push notification when a task finishes (the chat still says it)",
        express=True, extras=("push",)),
    Component(
        "metrics", "Metrics for Prometheus",
        "a /metrics endpoint for Prometheus and Grafana",
        "no Prometheus metrics (the Dashboard still shows Kazma's state)",
        express=True, extras=("observability",)),
    Component(
        "ocr", "OCR with Arabic and English (Tesseract)",
        "scanned pages and photos of text are read",
        "a scanned PDF or a photo of a page gives no text",
        express=True, system=("ocr",), admin=True),
    Component(
        "neural_ocr", "Neural OCR for Arabic (its models)",
        "scans read with Arabic-Indic digits and better Arabic",
        "OCR reads with Tesseract only (weaker Arabic, no Arabic-Indic digits)",
        express=True, extras=("ocr",), system=("vcruntime",), needs=("ocr",), size="about 300 MB", admin=True),
    Component(
        "office", "Office documents (LibreOffice)",
        "Word, Excel and PowerPoint files are read and converted; Arabic PDFs drawn well",
        "Office files cannot be read or converted",
        express=True, system=("office",), size="about 350 MB", admin=True),
    Component(
        "malware_scan", "Malware scan (ClamAV, refreshed daily)",
        "every uploaded document is scanned for malware",
        "documents are not scanned for malware",
        express=True, system=("malware",), admin=True),
    Component(
        "fonts", "Arabic fonts",
        "IBM Plex Sans Arabic, Kazma's document font, is installed (Windows: for all users), "
        "so Office's Protected View shows Kazma's files in it",
        "Office's Protected View shows Kazma's Word and PowerPoint files in another face; "
        "on Linux Arabic may be drawn as boxes",
        express=True, system=("fonts",), admin=True),
    Component(
        "node", "Node.js",
        "MCP servers started with npx run, the shipped filesystem server among them",
        "MCP servers that need npx cannot start (health reads degraded)",
        express=True, system=("node",), admin=True),
    Component(
        "browser", "Browser tools (Playwright and Chromium)",
        "the browser tools and pages that need JavaScript",
        "no browser tools; pages are read without running their JavaScript",
        extras=("web",), size="about 200 MB"),
    Component(
        "docling", "Docling (the hardest PDFs)",
        "a second local reader for PDFs the main readers cannot read",
        "a PDF the main readers cannot read gets no second local try",
        extras=("docling",), size="about 1 GB"),
    Component(
        "postgres", "PostgreSQL as a service",
        "a database service with point-in-time restore",
        "SQLite: a restore goes back to the last backup (every 6 hours)",
        extras=("postgres",), admin=True),
    Component(
        "docker", "Docker",
        "containers: private search, the locked document worker and a container sandbox "
        "(Windows: Kazma's own engine, started at boot with nobody signed in)",
        "no containers (documents parse in a limited process; no private search)",
        express=True, express_on=("windows", "linux"), best_effort=True,
        system=("docker",), size="about 1 GB", admin=True),
    Component(
        "document_container", "Locked document worker (a container per document)",
        "every document is read in a container with no network",
        "documents are read in a limited process on this machine (not qualified for hostile files)",
        needs=("docker",), size="about 2 GB"),
    Component(
        "private_search", "Private web search (SearXNG)",
        "web search runs on this machine",
        "web search uses public engines' pages directly (or Tavily with your key)",
        express=True, express_on=("windows", "linux"), best_effort=True, needs=("docker",)),
    Component(
        "cloud_sandbox", "E2B cloud sandbox",
        "python_exec can run in an E2B cloud sandbox (your E2B key)",
        "no E2B sandbox for python_exec",
        extras=("sandbox",)),
    Component(
        "durable_swarm", "Temporal (durable swarm steps)",
        "swarm steps that survive a crash (with a Temporal server)",
        "swarm runs are not resumed through Temporal",
        extras=("durable",)),
    Component(
        "graph_neo4j", "Neo4j memory graph",
        "a Neo4j server on this machine holds a copy of the memory graph (facts and how they connect), "
        "for graph queries at scale",
        "the memory graph stays in SQLite only",
        express=True, express_on=("windows", "linux"), best_effort=True,
        extras=("neo4j",), needs=("docker",), size="about 600 MB"),
    Component(
        "service", "Kazma as a service (its guard)",
        "Kazma starts at boot and is restarted when it stops",
        "you start Kazma yourself (kazma serve), and nothing restarts it",
        express=True, admin=True),
)

#: Extras that belong to no part, and why.
EXTRA_ALIASES: dict[str, str] = {
    "tui": "textual and python-bidi are base dependencies: the TUI is always installed",
    "document": "its libraries are base dependencies",
    "test": "part of dev (the tests part)",
    "convert": "part of document-platform (the documents part)",
    "all": "every extra at once (the Full install)",
}

_BY_ID = {c.id: c for c in COMPONENTS}


def by_id(component_id: str) -> Component:
    return _BY_ID[component_id]


def full_ids() -> list[str]:
    """Every part, in install order."""
    return [c.id for c in COMPONENTS]


def current_os() -> str:
    """This computer's system as the table names it (:data:`OS_NAMES`)."""
    import sys

    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _in_express(comp: Component, os_name: str = "") -> bool:
    """Whether *comp* is in Express on *os_name*. With no system named, only
    the parts in Express on every system: a part limited to some systems is
    never assumed."""
    return comp.express and (not comp.express_on or os_name in comp.express_on)


def express_ids(os_name: str = "") -> list[str]:
    """Express's parts on *os_name* (see :func:`_in_express`), in install order."""
    return [c.id for c in COMPONENTS if _in_express(c, os_name)]


def _with_needs(chosen: set[str]) -> set[str]:
    todo = list(chosen)
    while todo:
        for need in _BY_ID[todo.pop()].needs:
            if need not in chosen:
                chosen.add(need)
                todo.append(need)
    return chosen


def _without_dependents(chosen: set[str], dropped: set[str]) -> set[str]:
    changed = True
    while changed:
        changed = False
        for cid in list(chosen):
            if any(need in dropped or need not in chosen for need in _BY_ID[cid].needs):
                chosen.discard(cid)
                dropped.add(cid)
                changed = True
    return chosen


def components_for(mode: str, *, with_: tuple[str, ...] | list[str] = (),
                   without: tuple[str, ...] | list[str] = (), os_name: str = "") -> list[Component]:
    """The parts a *mode* installs on *os_name*, in install order. ``custom``
    starts from Express. *with_* adds parts (and what they need); *without*
    drops parts (and what needs them). An unknown id raises ``KeyError``."""
    if mode not in MODES:
        raise ValueError(f"unknown install mode {mode!r}: choose one of {', '.join(MODES)}")
    for cid in (*with_, *without):
        by_id(cid)
    chosen = set(full_ids() if mode == "full" else express_ids(os_name))
    chosen = _with_needs(chosen | set(with_))
    dropped = set(without)
    chosen = _without_dependents(chosen - dropped, dropped)
    return [c for c in COMPONENTS if c.id in chosen]


def toggle(chosen: set[str] | frozenset[str], component_id: str) -> set[str]:
    """*chosen* with one part switched: on brings what it needs, off takes
    what needs it (the Custom checklist)."""
    by_id(component_id)
    result = set(chosen)
    if component_id in result:
        return _without_dependents(result - {component_id}, {component_id})
    return _with_needs(result | {component_id})


def extras_for(components: list[Component]) -> list[str]:
    """The Python extras *components* install, in table order, each once."""
    seen: list[str] = []
    for comp in components:
        for extra in comp.extras:
            if extra not in seen:
                seen.append(extra)
    return seen


def parts_of_extra(extra: str) -> list[str]:
    """The parts that install the Python extra *extra*, in table order."""
    return [comp.id for comp in COMPONENTS if extra in comp.extras]


def install_overview(record: dict | None, os_name: str = "") -> dict:
    """The install as `kazma setup install` recorded it, for Settings -> Packages.

    ``mode`` is None when no install was recorded (a hand install, an older
    one): then no part is called chosen or left out. With a record, a part
    left out reads as not chosen, never as missing: the VM's Express install
    showed the parts Express leaves out as "Missing" (2026-11-07).

    ``new_in_express``: parts Express has on *os_name* now that an Express
    install did not choose, and the owner did not leave out (``without``):
    the table gained them after it ran. ``kazma setup install`` adds them;
    ``kazma update`` never does (they can need administrator rights, a
    download of about 1 GB and a Windows restart).
    """
    install = (record or {}).get("install") or {}
    mode = install.get("mode") if install.get("mode") in MODES else None
    chosen = set(install.get("components") or []) if mode else set()
    declined = set(install.get("without") or [])
    results = install.get("results") or {}
    parts = []
    for comp in COMPONENTS:
        result = results.get(comp.id) or {}
        parts.append({
            "id": comp.id,
            "title": comp.title,
            "express": _in_express(comp, os_name),
            "extras": list(comp.extras),
            "chosen": (comp.id in chosen) if mode else None,
            "ok": result.get("ok"),
        })
    new = ([cid for cid in express_ids(os_name) if cid not in chosen and cid not in declined]
           if mode == "express" else [])
    return {"mode": mode, "at": install.get("at"), "chosen": len(chosen) if mode else 0,
            "total": len(COMPONENTS), "parts": parts, "new_in_express": new}
