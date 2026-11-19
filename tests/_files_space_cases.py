"""The matrix cases of Spaces S1-19: the space folder (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b`` and against the owner's own workspace on the machine.
Both are sandboxed spaces: each has its own folder under
``<data dir>/spaces/``. B's folder holds files with B's marker (a note, a
database, a picture, a research report, a shared file, an upload); the
owner's workspace holds a note with the owner's marker.

A tool that takes a path is called as A with B's file, the owner's file and
a path that climbs out of A's folder: nothing of B or the owner is shown,
and neither folder changes by one byte. A tool that writes a file of its
own writes it into A's folder. The routes are driven through the product's
routers behind the real tenant middleware.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import files as space_files
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-19"
OWNER_MARK = "ownerworkspacemarkq19"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tree(folder: Path) -> dict[str, str]:
    """Every file under *folder* with the hash of its bytes."""
    if not folder.exists():
        return {}
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*")) if p.is_file()
    }


class World:
    def __init__(self, matrix: Matrix, marks: Any, owner_dir: Path, patch: pytest.MonkeyPatch) -> None:
        self.matrix, self.marks, self.owner_dir, self.patch = matrix, marks, owner_dir, patch
        self.dir_a = space_files.space_dir(Matrix.SPACE_A)
        self.dir_b = space_files.space_dir(Matrix.SPACE_B)
        self.client: TestClient | None = None
        self._before: tuple[dict[str, str], dict[str, str]] = ({}, {})

    def as_who(self, who: str) -> Any:
        if who == "owner":
            return scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
        return {"a": self.matrix.as_a, "b": self.matrix.as_b}[who]()

    def files(self, who: str) -> Path:
        return {"a": self.dir_a, "b": self.dir_b}[who] / "files"

    def tool(self, who: str, tool_name: str, /, **kwargs: Any) -> str:
        from kazma_core.agent.tool_refusal import RefusedBeforeEffect

        from tests._chat_space_cases import _registry

        tool = _registry().get_tool(tool_name)
        assert tool is not None, tool_name

        async def as_them() -> str:
            with self.as_who(who):
                try:
                    return str(await tool.func(**kwargs))
                except RefusedBeforeEffect as refused:
                    return str(refused)

        return asyncio.run(as_them())

    # ── what must not move ───────────────────────────────────────────────

    def remember(self) -> None:
        self._before = (_tree(self.dir_b), _tree(self.owner_dir))

    def others_unchanged(self) -> None:
        assert _tree(self.dir_b) == self._before[0], "B's folder changed"
        assert _tree(self.owner_dir) == self._before[1], "the owner's workspace changed"

    def nothing_of_the_others(self, shown: str) -> None:
        assert self.marks.b not in shown, shown[:600]
        assert OWNER_MARK not in shown, shown[:600]

    def foreign(self, name: str) -> list[str]:
        """B's file, the owner's file and a path that climbs out of A's folder."""
        return [
            str(self.files("b") / name),
            str(self.owner_dir / name),
            f"../../{Matrix.SPACE_B}/files/{name}",
            f"..\\..\\{Matrix.SPACE_B}\\files\\{name}",
        ]

    # ── requests ─────────────────────────────────────────────────────────

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        assert self.client is not None
        space = {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: space,
                   "Origin": "http://testserver", **kwargs.pop("headers", {})}
        return self.client.request(method, path, headers=headers, **kwargs)


def _plant(folder: Path, mark: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "note.md").write_text(f"# note\n\nthe word is {mark}\n", encoding="utf-8")
    (folder / "doc.pdf").write_text(f"%PDF-1.4 {mark}\n", encoding="utf-8")
    (folder / "pic.png").write_bytes(_PNG + mark.encode())
    con = sqlite3.connect(folder / "data.db")
    try:
        con.execute("CREATE TABLE notes (word TEXT)")
        con.execute("INSERT INTO notes VALUES (?)", (mark,))
        con.commit()
    finally:
        con.close()
    report = folder / "research" / "reports" / f"run-{mark}"
    report.mkdir(parents=True, exist_ok=True)
    (report / "report.md").write_text(f"# Report {mark}\n\n{mark} findings.\n", encoding="utf-8")
    (report / "sources.json").write_text("[]", encoding="utf-8")


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    from kazma_core.workspace.binding import resolve_active_root

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    marks = matrix.plant("file")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
            owner_dir = resolve_active_root() / f"s119-{marks.a}"
        made = World(matrix, marks, owner_dir, patch)
        try:
            _plant(made.files("a"), marks.a)
            _plant(made.files("b"), marks.b)
            _plant(owner_dir, OWNER_MARK)
            made.remember()
            yield made
        finally:
            for folder in (made.dir_a, made.dir_b, owner_dir):
                shutil.rmtree(folder, ignore_errors=True)


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


# ── tools that take a path ───────────────────────────────────────────────

#: tool -> (the planted file it is pointed at, kwargs for a path). A
#: ``None`` file means the folder itself.
_PATH_TOOLS: dict[str, tuple[str | None, Callable[[str], dict[str, Any]]]] = {
    "file_read": ("note.md", lambda p: {"path": p}),
    "file_list": (None, lambda p: {"path": p}),
    "file_search": (None, lambda p: {"pattern": "word", "path": p, "glob": "*.md"}),
    "file_write": ("note.md", lambda p: {"path": p, "content": "overwritten by A"}),
    "file_append": ("note.md", lambda p: {"path": p, "content": "appended by A"}),
    "file_apply_patch": ("note.md", lambda p: {"path": p, "old_string": "word", "new_string": "changed"}),
    "file_apply_patch_set": ("note.md", lambda p: {
        "patches": [{"path": p, "old_string": "word", "new_string": "changed"}], "verify": False}),
    "file_delete": ("note.md", lambda p: {"path": p}),
    "send_file": ("note.md", lambda p: {"file_path": p}),
    "analyze_image": ("pic.png", lambda p: {"image_path": p}),
    "analyze_local_image": ("pic.png", lambda p: {"path": p}),
    "generate_pdf": ("note.md", lambda p: {"title": "T", "markdown_path": p}),
    "generate_docx": ("note.md", lambda p: {"title": "T", "markdown_path": p}),
    "read_document": ("doc.pdf", lambda p: {"path": p}),
    "parse_document": ("doc.pdf", lambda p: {"path": p}),
    "convert_document": ("note.md", lambda p: {"file_path": p, "target_format": "pdf"}),
    "ocr_document": ("doc.pdf", lambda p: {"path": p}),
    "pdf_info": ("doc.pdf", lambda p: {"file_path": p}),
    "pdf_merge": ("doc.pdf", lambda p: {"file_paths": [p, p]}),
    "pdf_split": ("doc.pdf", lambda p: {"file_path": p}),
    "pdf_fill_form": ("doc.pdf", lambda p: {"file_path": p, "fields": {"a": "b"}}),
    "pdf_redact": ("doc.pdf", lambda p: {"file_path": p, "terms": ["word"]}),
    "sqlite_query": ("data.db", lambda p: {"query": "SELECT word FROM notes", "db_path": p}),
    "execute_db_query": ("data.db", lambda p: {"db_uri": p, "query": "SELECT word FROM notes"}),
    "inspect_db_schema": ("data.db", lambda p: {"db_uri": p}),
    "digest_research_file": ("note.md", lambda p: {"path": p}),
    "summarize_research_file": ("note.md", lambda p: {"path": p}),
    "list_research_chunks": ("note.md", lambda p: {"path": p}),
    "read_research_chunk": ("note.md", lambda p: {"path": p}),
    "synthesize_from_digests": ("note.md", lambda p: {"paths": [p], "question": "what is the word?"}),
    "read_url_to_file": ("note.md", lambda p: {"url": "http://127.0.0.1:9/page", "path": p}),
}


def _no_model(w: World) -> None:
    """No tool of a case reaches a model or a document worker: a call that
    got past the path check would fail there for another reason, and the
    case reads what was shown either way."""
    from kazma_core.documents.service import DocumentService

    async def refused(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("stand-in: no document worker in the matrix")

    for name in ("generate", "parse_transient", "convert", "mutate", "ocr_transient_within",
                 "read_with_ocr_budget"):
        if hasattr(DocumentService, name):
            w.patch.setattr(DocumentService, name, refused)


def _path_case(tool_name: str) -> None:
    planted, kwargs = _PATH_TOOLS[tool_name]

    def run(w: World) -> None:
        _no_model(w)
        for target in w.foreign(planted or ""):
            try:
                shown = w.tool("a", tool_name, **kwargs(target))
            except Exception as exc:  # noqa: BLE001 -- a stand-in's own error shows nothing
                shown = f"{type(exc).__name__}: {exc}"
            w.nothing_of_the_others(shown)
            w.others_unchanged()

    run.__name__ = f"path_tool_{tool_name}"
    case(f"tool:{tool_name}", ITEM)(_in_world(run))


for _name in _PATH_TOOLS:
    _path_case(_name)


# ── positives: A's own files work ────────────────────────────────────────


@case("tool:python_exec", ITEM)
@_in_world
def python_exec(w: World) -> None:
    from kazma_core.tools import code_exec, file_write

    with w.as_who("a"):
        assert file_write._get_workspace().resolve() == w.files("a").resolve()
        w.patch.setenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", "1")
        assert code_exec.local_exec_forbidden() is True  # never on the machine itself
    w.patch.setattr(code_exec, "use_docker_jail", lambda: False)
    shown = w.tool("a", "python_exec", code="print(open('note.md').read())")
    assert "Host-local python_exec is disabled" in shown, shown
    # A snippet that names B's or the owner's file is refused before it runs.
    for target in w.foreign("note.md")[:2]:
        shown = w.tool("a", "python_exec", code=f"print(open(r'{target}').read())")
        w.nothing_of_the_others(shown)
    w.others_unchanged()


def _own_files_work(w: World) -> None:
    shown = w.tool("a", "file_read", path="note.md")
    assert w.marks.a in shown, shown
    w.tool("a", "file_write", path="made_by_a.md", content="hello")
    assert (w.files("a") / "made_by_a.md").read_text(encoding="utf-8") == "hello"
    listed = w.tool("a", "file_list", path=".")
    assert "made_by_a.md" in listed and "note.md" in listed, listed
    rows = w.tool("a", "sqlite_query", query="SELECT word FROM notes", db_path="data.db")
    assert w.marks.a in rows, rows


# ── tools that write a file of their own ─────────────────────────────────


def _made_in_a(w: World, shown: str, kind: str) -> None:
    w.nothing_of_the_others(shown)
    w.others_unchanged()
    folder = w.dir_a / kind
    assert folder.is_dir() and any(folder.iterdir()), (kind, shown)


def _image_case(tool_name: str, **kwargs: Any) -> None:
    def run(w: World) -> None:
        from kazma_core.tools.image_backends import router

        class Backend:
            name = "stand-in"

            async def generate(self, *_a: Any, **_k: Any) -> bytes:
                return _PNG

        w.patch.setattr(router, "get_backend", lambda *_a, **_k: Backend())
        w.patch.setattr(router, "resolve_provider", lambda *_a, **_k: "stand-in", raising=False)
        shown = w.tool("a", tool_name, **kwargs)
        _made_in_a(w, shown, "images")
        _own_files_work(w)

    run.__name__ = f"image_tool_{tool_name}"
    case(f"tool:{tool_name}", ITEM)(_in_world(run))


_image_case("generate_image", prompt="a red square")
_image_case("generate_ui_mockup", prompt="a login page")


@case("tool:download_image", ITEM)
@_in_world
def download_image(w: World) -> None:
    from kazma_core.tools import image_download

    with w.as_who("a"):
        saved = image_download._save(_PNG, "picture", "png")
    assert saved.resolve().is_relative_to(w.files("a").resolve()), saved
    w.others_unchanged()


def _document_case(tool_name: str, **kwargs: Any) -> None:
    def run(w: World) -> None:
        from kazma_core.documents.service import DocumentService

        seen: list[Path] = []

        async def generate(self: Any, *_a: Any, export_dir: Any = None, **_k: Any) -> Any:
            seen.append(Path(export_dir))
            raise RuntimeError("stand-in: no document worker in the matrix")

        w.patch.setattr(DocumentService, "generate", generate)
        try:
            shown = w.tool("a", tool_name, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- the stand-in's error
            shown = str(exc)
        w.nothing_of_the_others(shown)
        assert seen and all(p.resolve() == (w.dir_a / "documents").resolve() for p in seen), seen
        w.others_unchanged()

    run.__name__ = f"document_tool_{tool_name}"
    case(f"tool:{tool_name}", ITEM)(_in_world(run))


_document_case("generate_xlsx", sheets=[{"name": "S", "rows": [["a", "b"]]}])
_document_case("generate_pptx", title="T", slides=[{"heading": "H", "bullets": ["x"]}])
_document_case("generate_markdown_doc", title="T", sections=[{"heading": "H", "body": "text"}])


# ── the browser: one page, the space's that opened it ────────────────────


class _Page:
    """A stand-in page: it shows whatever it was last sent to."""

    def __init__(self) -> None:
        self.shown = ""
        self.closed = False

    def goto(self, url: str, **_k: Any) -> None:
        self.shown = f"page of {url}"

    def title(self) -> str:
        return self.shown

    def evaluate(self, *_a: Any, **_k: Any) -> str:
        return self.shown

    def eval_on_selector_all(self, *_a: Any, **_k: Any) -> str:
        return self.shown

    def click(self, *_a: Any, **_k: Any) -> None:
        return None

    def fill(self, *_a: Any, **_k: Any) -> None:
        return None

    def screenshot(self, **_k: Any) -> bytes:
        return _PNG + self.shown.encode()

    def close(self) -> None:
        self.closed = True


def _browser_case(tool_name: str, **kwargs: Any) -> None:
    def run(w: World) -> None:
        from kazma_core.web_acquire import interactive
        from kazma_skills.native.browser_automation import tools as browser

        pages: list[_Page] = []

        def open_page() -> _Page:
            pages.append(_Page())
            return pages[-1]

        async def launchable() -> None:
            return None

        w.patch.setattr(interactive, "open_page", open_page)
        w.patch.setattr(browser, "_refuse_if_unlaunchable", launchable)
        w.patch.setitem(browser._state, "page", None)
        w.patch.setitem(browser._state, "space", None)
        try:
            # B leaves a page open that shows B's marker.
            w.tool("b", "browser_navigate", url=f"https://example.org/{w.marks.b}")
            assert len(pages) == 1 and w.marks.b in pages[0].shown
            try:
                shown = w.tool("a", tool_name, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- a stand-in page's limits
                shown = str(exc)
            w.nothing_of_the_others(shown)
            # A got a page of its own; B's was closed, never handed over.
            assert len(pages) == 2 and pages[0].closed, (len(pages), pages[0].closed)
            assert w.marks.b not in pages[1].shown
            if tool_name == "browser_screenshot":
                assert any((w.dir_a / "images").glob("browser_*.png")), shown
            w.others_unchanged()
        finally:
            browser._state.update(page=None, space=None)

    run.__name__ = f"browser_tool_{tool_name}"
    case(f"tool:{tool_name}", ITEM)(_in_world(run))


_browser_case("browser_navigate", url="https://example.org/a")
_browser_case("browser_click", selector="#go")
_browser_case("browser_fill_form", fields={"#q": "x"})
_browser_case("browser_extract_text")
_browser_case("browser_eval_js", expression="document.title")
_browser_case("browser_screenshot")


# ── research files ───────────────────────────────────────────────────────


@case("tool:list_research_papers", ITEM)
@_in_world
def list_research_papers(w: World) -> None:
    shown = w.tool("a", "list_research_papers")
    assert w.marks.a in shown, shown
    w.nothing_of_the_others(shown)
    w.others_unchanged()


def _research_writer_case(tool_name: str) -> None:
    def run(w: World) -> None:
        from kazma_core.tools import read_url, research_pipeline

        with w.as_who("a"):
            assert research_pipeline._get_ws_root() == w.files("a").resolve()
            assert read_url._workspace_root() == w.files("a").resolve()
            roots = research_pipeline._candidate_report_roots()
            # B's report is not a report of A's, by any name.
            b_report = f"research/reports/run-{w.marks.b}/report.md"
            assert research_pipeline.resolve_report_file(b_report) is None
            assert research_pipeline.resolve_report_file(
                str(w.files("b") / b_report), stored=True) is None
        assert roots == [w.files("a").resolve()], roots
        # A job of another space is not resumed from here: its id is unknown.
        if tool_name == "run_research_pipeline":
            shown = w.tool("a", tool_name, topic="x", resume_job_id="0" * 32)
        else:
            shown = w.tool("a", tool_name, start_url="http://127.0.0.1:9/", resume_job_id="0" * 32)
        w.nothing_of_the_others(shown)
        w.others_unchanged()

    run.__name__ = f"research_tool_{tool_name}"
    case(f"tool:{tool_name}", ITEM)(_in_world(run))


_research_writer_case("run_research_pipeline")
_research_writer_case("crawl_site")


# ── routes ───────────────────────────────────────────────────────────────


@contextmanager
def _app(w: World) -> Iterator[None]:
    from kazma_ui.research_panel.routes import create_research_router
    from kazma_ui.routes_chat_upload import router as upload_router

    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.include_router(upload_router)
    app.include_router(create_research_router())
    with TestClient(app, base_url="http://testserver") as client:
        w.client = client
        try:
            yield
        finally:
            w.client = None


def _route(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    def register(run: Callable[[World], None]) -> Callable[[Matrix], None]:
        def with_app(w: World) -> None:
            with _app(w):
                run(w)
                w.others_unchanged()

        with_app.__name__ = run.__name__
        return case(target, ITEM)(_in_world(with_app))

    return register


def _upload(w: World, who: str, mark: str) -> str:
    sent = w.call(who, "POST", "/api/chat/upload",
                  files={"file": (f"{mark}.txt", f"upload {mark}".encode(), "text/plain")})
    assert sent.status_code == 200, sent.text[:300]
    return str(sent.json()["id"])


@_route("POST /api/chat/upload")
def chat_upload(w: World) -> None:
    from kazma_ui import chat_attachments

    w.remember()
    id_b = _upload(w, "b", w.marks.b)
    w.remember()  # B's own upload is B's change
    id_a = _upload(w, "a", w.marks.a)
    with w.as_who("a"):
        assert chat_attachments._load_uploaded_attachment(id_a) == f"upload {w.marks.a}".encode()
        assert chat_attachments._load_uploaded_attachment(id_b) is None  # B's id names nothing here
    with w.as_who("b"):
        assert chat_attachments._load_uploaded_attachment(id_b) == f"upload {w.marks.b}".encode()
        assert chat_attachments._load_uploaded_attachment(id_a) is None
    with w.as_who("owner"):
        assert chat_attachments._load_uploaded_attachment(id_b) is None
    assert list((w.dir_a / "attachments").glob(f"{id_a}*"))


@_route("GET /api/chat/files/{file_id}")
def chat_shared_file(w: World) -> None:
    from kazma_core import chat_files

    with w.as_who("b"):
        shared_b = chat_files.share_file(w.files("b") / "note.md", thread_id="thread-of-b")
    w.remember()
    with w.as_who("a"):
        shared_a = chat_files.share_file(w.files("a") / "note.md", thread_id="thread-of-a")
        assert chat_files.load_shared(shared_b.id) is None
        assert chat_files.load_shared(shared_a.id) is not None
    got = w.call("a", "GET", f"/api/chat/files/{shared_b.id}")
    assert got.status_code == 404, got.status_code
    w.nothing_of_the_others(got.text)


@_route("GET /api/research/papers")
def research_papers(w: World) -> None:
    got = w.call("a", "GET", "/api/research/papers")
    assert got.status_code == 200, got.text[:300]
    assert w.marks.a in got.text, got.text[:400]
    w.nothing_of_the_others(got.text)


@_route("GET /api/research/papers/file")
def research_paper_file(w: World) -> None:
    own = w.call("a", "GET", "/api/research/papers/file",
                 params={"path": f"research/reports/run-{w.marks.a}/report.md"})
    assert own.status_code == 200 and w.marks.a in own.text, own.text[:300]
    for path in (f"research/reports/run-{w.marks.b}/report.md",
                 f"../../{Matrix.SPACE_B}/files/research/reports/run-{w.marks.b}/report.md",
                 str(w.files("b") / "research" / "reports" / f"run-{w.marks.b}" / "report.md"),
                 f"research/reports/run-{OWNER_MARK}/report.md"):
        got = w.call("a", "GET", "/api/research/papers/file", params={"path": path})
        assert got.status_code == 404, (path, got.status_code)
        w.nothing_of_the_others(got.text)


@_route("POST /api/research/papers/export")
def research_paper_export(w: World) -> None:
    for path in (f"research/reports/run-{w.marks.b}/report.md",
                 str(w.files("b") / "research" / "reports" / f"run-{w.marks.b}" / "report.md"),
                 f"research/reports/run-{OWNER_MARK}/report.md"):
        got = w.call("a", "POST", "/api/research/papers/export",
                     json={"report_path": path, "format": "markdown"})
        assert got.status_code == 404, (path, got.status_code, got.text[:200])
        w.nothing_of_the_others(got.text)


@_route("GET /api/research/eval")
def research_eval(w: World) -> None:
    for path in (f"research/reports/run-{w.marks.b}/report.md",
                 str(w.files("b") / "research" / "reports" / f"run-{w.marks.b}" / "report.md")):
        got = w.call("a", "GET", "/api/research/eval", params={"path": path})
        assert got.status_code in (400, 404), (path, got.status_code, got.text[:200])
        w.nothing_of_the_others(got.text)
    own = w.call("a", "GET", "/api/research/eval",
                 params={"path": f"research/reports/run-{w.marks.a}/report.md"})
    assert own.status_code == 200, own.text[:300]


@_route("GET /api/research/download")
def research_download(w: World) -> None:
    for who, mark in (("a", w.marks.a), ("b", w.marks.b)):
        with w.as_who(who):
            folder = space_files.space_folder("documents")
        (folder / f"export-{mark}.docx").write_text(f"export {mark}", encoding="utf-8")
    w.remember()
    own = w.call("a", "GET", "/api/research/download", params={"path": f"export-{w.marks.a}.docx"})
    assert own.status_code == 200 and w.marks.a in own.text
    for path in (f"export-{w.marks.b}.docx", f"../../{Matrix.SPACE_B}/documents/export-{w.marks.b}.docx"):
        got = w.call("a", "GET", "/api/research/download", params={"path": path})
        assert got.status_code in (400, 403), (path, got.status_code)
        w.nothing_of_the_others(got.text)


@_route("GET /api/research/acquisition")
def research_acquisition(w: World) -> None:
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.research_crawl import research_job

    async def start(who: str, mark: str) -> str:
        with w.as_who(who):
            options = dict(max_pages=1, max_depth=0, same_domain_only=True, path_prefix="",
                           delay_ms=0, save=True, respect_robots=True)
            async with research_job(f"https://example.org/{mark}", w.files(who), options) as owned:
                job_id = owned.job.job_id
                await owned.finish(partial=False)
            return str(job_id)

    job_b = asyncio.run(start("b", w.marks.b))
    job_a = asyncio.run(start("a", w.marks.a))
    try:
        w.remember()
        listed = w.call("a", "GET", "/api/research/acquisition")
        assert listed.status_code == 200, listed.text[:300]
        w.nothing_of_the_others(listed.text)
        assert job_b not in listed.text
        theirs = w.call("a", "GET", "/api/research/acquisition", params={"job_id": job_b})
        assert theirs.status_code == 503 and job_b not in theirs.text, theirs.text[:300]
        w.nothing_of_the_others(theirs.text)
        unknown = w.call("a", "GET", "/api/research/acquisition", params={"job_id": "f" * 32})
        assert theirs.status_code == unknown.status_code  # as an unknown job
        own = w.call("a", "GET", "/api/research/acquisition", params={"job_id": job_a})
        assert own.status_code == 200 and w.marks.a in own.text, own.text[:300]
    finally:
        for job_id in (job_a, job_b):
            try:
                crawl_jobs.call  # noqa: B018 -- the journal keeps finished jobs; nothing to stop
                crawl_jobs.forget_job(job_id) if hasattr(crawl_jobs, "forget_job") else None
            except Exception:  # noqa: BLE001, S110 -- best-effort clean-up of the test's own rows
                pass
