"""LLM synthesis over saved research digests / extracts."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

__all__ = ["synthesize_from_digests"]

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _workspace_root() -> Path:
    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace().resolve()
    except Exception:
        return (Path.cwd() / "kazma-data" / "workspace").resolve()


def _resolve_under_workspace(path: str) -> Path | str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return "Error: empty path"
    p = Path(raw)
    if not p.is_absolute():
        p = _workspace_root() / p
    try:
        resolved = p.resolve()
        root = _workspace_root()
        resolved.relative_to(root)
    except Exception:
        return f"Error: path must be under workspace ({_workspace_root()})"
    if not resolved.is_file():
        return f"Error: file not found: {path}"
    return resolved


def _load_body(path: str) -> str | None:
    resolved = _resolve_under_workspace(path)
    if isinstance(resolved, str):
        return None
    try:
        return resolved.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


async def synthesize_from_digests(
    paths: str | list[str],
    question: str,
    outline: str = "",
    max_chars: int = 20_000,
) -> str:
    """Synthesize a multi-source analysis from saved research files.

    Nested LLM call (reduce step). Paths are workspace-relative research
    extracts/digests. Returns markdown with sections and URL citations when
    present in the sources.
    """
    q = (question or "").strip()
    if not q:
        return "Error: question is required."

    if isinstance(paths, str):
        # comma or newline separated
        path_list = [p.strip() for p in re_split_paths(paths) if p.strip()]
    else:
        path_list = [str(p).strip() for p in (paths or []) if str(p).strip()]
    if not path_list:
        return "Error: provide one or more research file paths."

    cap_in = _env_int("KAZMA_RESEARCH_SYNTH_MAX_IN", 48_000, lo=4000, hi=200_000)
    cap_out = max(2000, min(50_000, int(max_chars or 20_000)))

    blocks: list[str] = []
    used = 0
    loaded = 0
    for i, pth in enumerate(path_list[:20], 1):
        body = _load_body(pth)
        if body is None:
            blocks.append(f"### Source {i}: {pth}\n_(failed to load)_\n")
            continue
        # Prefer existing digest if huge raw file
        if len(body) > 20_000:
            try:
                from kazma_core.tools.read_url import digest_research_file

                dig = await digest_research_file(pth)
                if dig and not dig.startswith("Error:"):
                    body = dig
            except Exception:
                body = body[:16_000]
        piece = f"### Source {i}: `{pth}`\n\n{body.strip()}\n"
        if used + len(piece) > cap_in:
            remain = max(0, cap_in - used - 200)
            if remain > 500:
                blocks.append(piece[:remain] + "\n… [truncated]\n")
            break
        blocks.append(piece)
        used += len(piece)
        loaded += 1

    if loaded == 0:
        return "Error: could not load any source files under the workspace."

    outline_block = f"\nTarget outline:\n{outline.strip()}\n" if outline.strip() else ""
    system = (
        "You are a research analyst. Using ONLY the source materials provided, "
        "write a rigorous multi-section analysis. Requirements:\n"
        "- Use clear markdown headings (## / ###).\n"
        "- Cite sources inline with URLs or file paths from the materials "
        "(prefer http URLs when present).\n"
        "- Prefer claims listed under Evidence claims when present; "
        "do not invent facts not supported by materials.\n"
        "- Call out conflicts between sources.\n"
        "- End with a **Sources** section listing files/URLs used.\n"
        f"- Keep under ~{cap_out} characters."
    )
    user = (
        f"Research question:\n{q}\n{outline_block}\n"
        f"## Source materials\n\n{''.join(blocks)}"
    )

    try:
        from kazma_core.model_registry import get_model_registry

        reg = get_model_registry()
        client = reg.get_client()  # active model+provider (not a free function)
        model = None
        try:
            profile = reg.get_active_profile() or {}
            model = profile.get("model")
        except Exception:
            pass
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # Resilient path (audit): transient retries + failover + ledger.
        from kazma_core.agent.resilient_chat import resilient_chat

        result = await resilient_chat(
            client,
            messages=messages,
            tools=None,
            model=model,
            max_attempts=3,
            max_tokens=min(8000, max(1024, cap_out // 3)),
            label="research-synthesize",
        )
        text = ""
        if hasattr(result, "content"):
            text = str(result.content or "")
        elif isinstance(result, dict):
            text = str(result.get("content") or "")
        else:
            text = str(result or "")
        text = text.strip()
        if not text:
            return "Error: synthesis returned empty content."
        if len(text) > cap_out:
            text = text[: cap_out - 40] + "\n\n… [synthesis truncated]"
        header = (
            f"# Research synthesis\n\n"
            f"**Question:** {q}\n"
            f"**Sources loaded:** {loaded}/{len(path_list)}\n\n"
        )
        return header + text
    except Exception as exc:
        logger.warning("[synthesize_from_digests] failed: %s", exc)
        return f"Error: synthesis failed — {type(exc).__name__}: {exc}"


def re_split_paths(s: str) -> list[str]:
    out: list[str] = []
    for line in s.replace(",", "\n").splitlines():
        t = line.strip().strip('"').strip("'")
        if t:
            out.append(t)
    return out
