"""Query the symbol index + live ripgrep. Read-only."""

from __future__ import annotations

from pathlib import Path

from kazma_core.code_index.indexer import code_index_enabled, ensure_index, status
from kazma_core.code_index.ripgrep import search_text
from kazma_core.code_index.store import connect, search_symbols

__all__ = ["format_search", "search_codebase"]


def search_codebase(
    query: str,
    *,
    root: Path | None = None,
    mode: str = "auto",
    glob: str = "",
    limit: int = 20,
) -> dict[str, object]:
    """Return ``{symbols, text, stats}``. Never raises."""
    q = (query or "").strip()
    empty: dict[str, object] = {"symbols": [], "text": [], "stats": {}, "query": q}
    if not q or not code_index_enabled():
        empty["stats"] = status(root)
        return empty
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = (root or resolve_active_root()).resolve()
    except Exception:
        empty["stats"] = status(root)
        return empty
    st = ensure_index(root)
    mode = (mode or "auto").strip().lower()
    want_sym = mode in ("auto", "symbol", "both")
    want_text = mode in ("auto", "text", "both")
    # Bare identifiers prefer symbols; otherwise include text.
    if mode == "auto" and not _looks_like_identifier(q):
        want_sym = True
        want_text = True
    symbols: list[dict[str, object]] = []
    if want_sym:
        conn = connect(root)
        try:
            for row in search_symbols(conn, q, limit=limit):
                symbols.append(
                    {
                        "path": row["path"],
                        "name": row["name"],
                        "kind": row["kind"],
                        "line": int(row["line"]),
                        "signature": row["signature"],
                    }
                )
        finally:
            conn.close()
    text: list[str] = []
    if want_text:
        text = search_text(root, q, glob=glob, limit=limit)
    return {"query": q, "symbols": symbols, "text": text, "stats": st}


def format_search(result: dict[str, object]) -> str:
    q = str(result.get("query") or "")
    symbols = list(result.get("symbols") or [])
    text = list(result.get("text") or [])
    stats = dict(result.get("stats") or {})
    if not symbols and not text:
        extra = ""
        if stats:
            extra = f" (index: {stats.get('files', 0)} files, {stats.get('symbols', 0)} symbols)"
        return f"No codebase hits for {q!r}{extra}."
    lines: list[str] = []
    if symbols:
        lines.append("## Symbols")
        for s in symbols:
            if not isinstance(s, dict):
                continue
            sig = f"  {s.get('signature')}" if s.get("signature") else ""
            lines.append(
                f"{s.get('path')}:{s.get('line')}  {s.get('kind')}  {s.get('name')}{sig}"
            )
    if text:
        lines.append("## Text")
        lines.extend(str(t) for t in text)
    n_files = stats.get("files")
    if n_files:
        lines.append(f"(index {n_files} files / {stats.get('symbols', 0)} symbols)")
    return "\n".join(lines)


def _looks_like_identifier(q: str) -> bool:
    if not q or len(q) > 80 or " " in q:
        return False
    return q.replace("_", "").replace(".", "").isalnum()
