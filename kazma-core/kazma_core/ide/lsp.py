"""Workspace-scoped language intelligence for the Web IDE.

This is **not** a long-lived pylsp/tsserver process (Windows SelectorEventLoop
cannot host asyncio subprocesses — AGENTS.md §23). It is a request/response
façade over the existing code index + in-process parsers:

  * completion / hover / definition / document symbols from the symbol index
    and the unsaved buffer
  * diagnostics via ``ast.parse`` (Python) / ``json.loads`` (JSON)

All paths go through ``IdeService.resolve`` (workspace + path grants). Read-only.
Kill-switch: ``KAZMA_IDE_LSP=0``.
"""

from __future__ import annotations

import ast
import json
import keyword
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_CONTENT = 400_000
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_PY_KW = frozenset(keyword.kwlist)
_JS_KW = frozenset({
    "async", "await", "break", "case", "catch", "class", "const", "continue",
    "debugger", "default", "delete", "do", "else", "export", "extends",
    "finally", "for", "function", "if", "import", "in", "instanceof", "let",
    "new", "return", "static", "super", "switch", "this", "throw", "try",
    "typeof", "var", "void", "while", "with", "yield",
})

_KIND_MONACO = {
    "function": 1,  # CompletionItemKind.Function
    "method": 0,    # Method
    "class": 5,     # Class
    "keyword": 17,  # Keyword
    "variable": 4,  # Variable
    "text": 18,
}


def lsp_enabled() -> bool:
    raw = (os.environ.get("KAZMA_IDE_LSP") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def word_at(content: str, line: int, character: int) -> tuple[str, int, int]:
    """Return ``(word, start_col, end_col)`` at 0-based line/character."""
    rows = content.splitlines() or [""]
    if line < 0 or line >= len(rows):
        return "", 0, 0
    row = rows[line]
    i = min(max(int(character), 0), len(row))
    start = i
    while start > 0 and (row[start - 1].isalnum() or row[start - 1] == "_"):
        start -= 1
    end = i
    while end < len(row) and (row[end].isalnum() or row[end] == "_"):
        end += 1
    return row[start:end], start, end


def handle_lsp(
    method: str,
    *,
    path: str = "",
    line: int = 0,
    character: int = 0,
    content: str | None = None,
    prefix: str = "",
) -> dict[str, Any]:
    """Dispatch one LSP-shaped request. Never raises."""
    method = (method or "").strip()
    if method in ("initialize", "status", ""):
        return _status()
    if not lsp_enabled():
        return {"ok": False, "error": "LSP disabled (KAZMA_IDE_LSP=0)", "enabled": False}
    try:
        resolved = _resolve(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.debug("[ide.lsp] resolve failed", exc_info=True)
        return {"ok": False, "error": str(exc)}

    buf = _buffer(resolved, content)
    line = max(0, int(line or 0))
    character = max(0, int(character or 0))
    word, start, end = word_at(buf, line, character)
    prefix = (prefix or word).strip()

    if method in ("textDocument/completion", "completion", "complete"):
        return {"ok": True, "items": _complete(resolved, buf, prefix)}
    if method in ("textDocument/hover", "hover"):
        return {"ok": True, "hover": _hover(resolved, buf, word)}
    if method in ("textDocument/definition", "definition"):
        return {"ok": True, "locations": _definition(resolved, buf, word)}
    if method in ("textDocument/documentSymbol", "documentSymbol", "symbols"):
        return {"ok": True, "symbols": _document_symbols(resolved, buf)}
    if method in ("textDocument/diagnostic", "diagnostics", "diagnostic"):
        return {"ok": True, "diagnostics": _diagnostics(resolved, buf)}
    return {"ok": False, "error": f"Unknown LSP method {method!r}"}


def _status() -> dict[str, Any]:
    st: dict[str, Any] = {
        "ok": True,
        "enabled": lsp_enabled(),
        "languages": ["python", "javascript", "typescript", "go", "rust", "json"],
        "features": ["completion", "hover", "definition", "documentSymbol", "diagnostics"],
    }
    if not lsp_enabled():
        return st
    try:
        from kazma_core.code_index.indexer import status as index_status

        st["index"] = index_status()
    except Exception:
        st["index"] = {}
    return st


def _resolve(rel: str) -> Path:
    from kazma_core.ide.service import get_ide_service

    svc = get_ide_service()
    svc.refresh_root()
    return svc.resolve(rel or "")


def _buffer(path: Path, content: str | None) -> str:
    if content is not None:
        return str(content)[:_MAX_CONTENT]
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_MAX_CONTENT]
    except OSError:
        return ""


def _rel(path: Path) -> str:
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root().resolve()
        return path.resolve().relative_to(root).as_posix()
    except Exception:
        return path.name


def _complete(path: Path, content: str, prefix: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label: str, kind: str, detail: str = "") -> None:
        if not label or label in seen:
            return
        if prefix and not label.lower().startswith(prefix.lower()):
            return
        seen.add(label)
        items.append({
            "label": label,
            "kind": kind,
            "kindId": _KIND_MONACO.get(kind, 18),
            "detail": detail[:200],
            "insertText": label,
        })

    lang = _lang(path)
    if lang == "python":
        for kw in sorted(_PY_KW):
            add(kw, "keyword")
    elif lang in ("javascript", "typescript"):
        for kw in sorted(_JS_KW):
            add(kw, "keyword")

    for sym in _local_symbols(path, content):
        add(sym["name"], str(sym.get("kind") or "function"), str(sym.get("signature") or ""))

    for ident in _idents(content):
        add(ident, "variable")

    if len(prefix) >= 2:
        for row in _index_symbols(prefix, limit=40):
            add(
                str(row.get("name") or ""),
                str(row.get("kind") or "function"),
                f"{row.get('path')}:{row.get('line')}  {row.get('signature') or ''}".strip(),
            )
    return items[:60]


def _hover(path: Path, content: str, word: str) -> dict[str, Any] | None:
    if not word:
        return None
    for sym in _local_symbols(path, content):
        if str(sym.get("name")) == word:
            doc = ""
            if _lang(path) == "python":
                doc = _python_docstring(content, int(sym.get("line") or 0))
            sig = str(sym.get("signature") or word)
            parts = [f"```{_lang(path) or 'text'}\n{sig}\n```"]
            if doc:
                parts.append(doc)
            return {"contents": "\n\n".join(parts), "kind": sym.get("kind"), "line": sym.get("line")}
    for row in _index_symbols(word, limit=5, exact=True):
        if str(row.get("name")) != word:
            continue
        sig = str(row.get("signature") or word)
        loc = f"{row.get('path')}:{row.get('line')}"
        return {
            "contents": f"```{_lang(path) or 'text'}\n{sig}\n```\n\n{loc}",
            "kind": row.get("kind"),
            "line": row.get("line"),
            "path": row.get("path"),
        }
    return None


def _definition(path: Path, content: str, word: str) -> list[dict[str, Any]]:
    if not word:
        return []
    rel = _rel(path)
    for sym in _local_symbols(path, content):
        if str(sym.get("name")) == word:
            return [{
                "path": rel,
                "line": int(sym.get("line") or 1),
                "character": 1,
                "kind": sym.get("kind"),
            }]
    out: list[dict[str, Any]] = []
    for row in _index_symbols(word, limit=8, exact=True):
        if str(row.get("name")) != word:
            continue
        out.append({
            "path": str(row.get("path") or ""),
            "line": int(row.get("line") or 1),
            "character": 1,
            "kind": row.get("kind"),
        })
    return out


def _document_symbols(path: Path, content: str) -> list[dict[str, Any]]:
    return _local_symbols(path, content)


def _diagnostics(path: Path, content: str) -> list[dict[str, Any]]:
    lang = _lang(path)
    if lang == "python":
        return _py_diagnostics(content)
    if lang == "json" or path.suffix.lower() == ".json":
        return _json_diagnostics(content)
    return []


def _py_diagnostics(content: str) -> list[dict[str, Any]]:
    try:
        ast.parse(content)
        return []
    except SyntaxError as exc:
        line = int(exc.lineno or 1)
        col = int(exc.offset or 1)
        msg = exc.msg or "invalid syntax"
        return [{
            "severity": "error",
            "line": max(1, line),
            "character": max(1, col),
            "message": msg,
            "source": "python",
        }]


def _json_diagnostics(content: str) -> list[dict[str, Any]]:
    text = (content or "").strip()
    if not text:
        return []
    try:
        json.loads(content)
        return []
    except json.JSONDecodeError as exc:
        return [{
            "severity": "error",
            "line": max(1, int(exc.lineno or 1)),
            "character": max(1, int(exc.colno or 1)),
            "message": exc.msg,
            "source": "json",
        }]


def _local_symbols(path: Path, content: str) -> list[dict[str, Any]]:
    try:
        from kazma_core.code_index.symbols import extract_symbols

        return [
            {"name": s.name, "kind": s.kind, "line": s.line, "signature": s.signature}
            for s in extract_symbols(path, content)
        ]
    except Exception:
        return []


def _index_symbols(query: str, *, limit: int = 20, exact: bool = False) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        from kazma_core.code_index.indexer import code_index_enabled, ensure_index
        from kazma_core.code_index.store import connect, search_symbols
        from kazma_core.workspace.binding import resolve_active_root

        if not code_index_enabled():
            return []
        root = resolve_active_root()
        ensure_index(root)
        conn = connect(root)
        try:
            rows = search_symbols(conn, q, limit=limit)
            out: list[dict[str, Any]] = []
            for row in rows:
                name = str(row["name"])
                if exact and name != q:
                    continue
                out.append({
                    "path": str(row["path"]),
                    "name": name,
                    "kind": str(row["kind"]),
                    "line": int(row["line"]),
                    "signature": str(row["signature"] or ""),
                })
            return out
        finally:
            conn.close()
    except Exception:
        logger.debug("[ide.lsp] index lookup failed", exc_info=True)
        return []


def _idents(content: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _IDENT.finditer(content or ""):
        name = m.group(0)
        if name in seen or len(name) < 2:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= 80:
            break
    return out


def _lang(path: Path) -> str:
    try:
        from kazma_core.code_index.walk import lang_for_path

        return lang_for_path(path) or ""
    except Exception:
        ext = path.suffix.lower()
        return {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".go": "go", ".rs": "rust", ".json": "json",
        }.get(ext, "")


def _python_docstring(source: str, line_1based: int) -> str:
    if not line_1based:
        return ""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if int(getattr(node, "lineno", 0) or 0) != line_1based:
            continue
        doc = ast.get_docstring(node) or ""
        return doc.strip()[:1500]
    return ""
