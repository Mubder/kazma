"""Apply a surgical edit to a workspace file (Aider/Morph-style).

Prefer this over ``file_write`` for edits to existing files: the model
sends a unique ``old_string`` (or a unified diff) instead of rewriting
the whole file. HITL-gated like ``file_write``.

Two input shapes (one required):

* Search-replace: ``old_string`` + ``new_string`` (optional ``replace_all``).
* Unified diff: ``patch`` starting with ``---`` / ``+++`` / ``@@``, or a
  Morph-style ``*** Begin Patch`` block.
"""

from __future__ import annotations

from pathlib import Path

from kazma_core.workspace.path_policy import check_path_access, denied_message

__all__ = [
    "PatchError",
    "apply_search_replace",
    "apply_unified_diff",
    "file_apply_patch",
]


class PatchError(ValueError):
    """The patch did not apply cleanly."""


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text and text.count("\r\n") >= max(1, text.count("\n") // 2) else "\n"


def apply_search_replace(
    original: str,
    old: str,
    new: str,
    *,
    replace_all: bool = False,
) -> str:
    """Replace ``old`` with ``new`` in ``original``. Raises :class:`PatchError`."""
    if old == new:
        raise PatchError("old_string and new_string are identical — nothing to change")
    nl = _newline_of(original)
    src = original.replace("\r\n", "\n")
    needle = (old or "").replace("\r\n", "\n")
    repl = (new or "").replace("\r\n", "\n")
    if not needle:
        raise PatchError("old_string is empty — use file_write to create a new file")
    count = src.count(needle)
    if count == 0:
        preview = src[:240].replace("\n", "\\n")
        raise PatchError(
            "old_string was not found in the file. Include more unique context. "
            f"File starts: {preview!r}"
        )
    if count > 1 and not replace_all:
        raise PatchError(
            f"old_string matched {count} times. Add more surrounding context "
            "so the match is unique, or set replace_all=true."
        )
    out = src.replace(needle, repl) if replace_all else src.replace(needle, repl, 1)
    return out.replace("\n", nl) if nl == "\r\n" else out


def _hunk_to_pair(hunk_lines: list[str]) -> tuple[str, str]:
    old_lines: list[str] = []
    new_lines: list[str] = []
    for raw in hunk_lines:
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        if raw.startswith("-"):
            old_lines.append(raw[1:])
        elif raw.startswith("+"):
            new_lines.append(raw[1:])
        elif raw.startswith(" "):
            old_lines.append(raw[1:])
            new_lines.append(raw[1:])
        elif raw.startswith("@@") or raw.startswith("---") or raw.startswith("+++"):
            continue
        else:
            old_lines.append(raw)
            new_lines.append(raw)
    return "\n".join(old_lines), "\n".join(new_lines)


def _iter_hunks(patch: str) -> list[tuple[str, str]]:
    """Yield (old, new) pairs from a unified diff or Morph Begin Patch block."""
    text = (patch or "").replace("\r\n", "\n")
    if "*** Begin Patch" in text or "*** Update File:" in text or "*** Add File:" in text:
        text = _morph_to_unified(text)
    hunks: list[tuple[str, str]] = []
    current: list[str] = []
    in_hunk = False
    for line in text.split("\n"):
        if line.startswith("@@"):
            if in_hunk and current:
                hunks.append(_hunk_to_pair(current))
            current = []
            in_hunk = True
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if in_hunk:
            if line.startswith("diff ") or line.startswith("index "):
                if current:
                    hunks.append(_hunk_to_pair(current))
                    current = []
                in_hunk = False
                continue
            current.append(line)
    if current:
        hunks.append(_hunk_to_pair(current))
    return [(o, n) for o, n in hunks if o or n]


def _morph_to_unified(text: str) -> str:
    """Turn Morph/Codex ``*** Begin Patch`` into @@ hunks we already parse."""
    out: list[str] = []
    in_file = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("*** End Patch"):
            in_file = False
            continue
        if line.startswith("*** Update File:") or line.startswith("*** Add File:"):
            in_file = True
            out.append("@@")
            continue
        if line.startswith("***"):
            continue
        if in_file:
            out.append(line)
    return "\n".join(out)


def apply_unified_diff(original: str, patch: str) -> str:
    """Apply unified-diff (or Morph) hunks in order. Raises :class:`PatchError`."""
    hunks = _iter_hunks(patch)
    if not hunks:
        raise PatchError("patch contained no hunks")
    current = original
    for old, new in hunks:
        if not old and new:
            # Add-file style hunk: append (or fill empty file).
            if current.strip():
                current = current.rstrip("\n") + "\n" + new
                if not current.endswith("\n") and original.endswith("\n"):
                    current += "\n"
            else:
                current = new if new.endswith("\n") else new + "\n"
            continue
        current = apply_search_replace(current, old, new, replace_all=False)
    return current


async def file_apply_patch(
    path: str,
    old_string: str = "",
    new_string: str = "",
    patch: str = "",
    replace_all: bool = False,
) -> str:
    """Apply a surgical edit to ``path``. Workspace + path-policy gated."""
    if not path or not path.strip():
        return "Error: No path provided."

    p = Path(path).expanduser().resolve()
    access = check_path_access(p, "write")
    if not access.allowed:
        return denied_message(path, "write", result=access)

    patch_text = (patch or "").strip()
    old = old_string or ""
    new = new_string or ""
    if not patch_text and not old and not new:
        return "Error: Provide old_string+new_string or a unified diff in patch."

    existed = p.is_file()
    if not existed:
        if patch_text or old:
            return f"Error: File not found: {path}. Use file_write to create it, then patch."
        # empty old_string + new_string on a missing file → create
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new, encoding="utf-8")
        except OSError as exc:
            return f"Error: Could not create {path} — {exc}"
        _touch_index(p)
        return f"Created {path} ({len(new.encode('utf-8'))} bytes) via file_apply_patch"

    try:
        original = p.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Error: Could not read {path} — {exc}"

    try:
        if patch_text:
            updated = apply_unified_diff(original, patch_text)
        else:
            updated = apply_search_replace(
                original, old, new, replace_all=bool(replace_all)
            )
    except PatchError as exc:
        return f"Error: {exc}"

    if updated == original:
        return f"No changes applied to {path}"

    try:
        p.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"Error: Could not write {path} — {exc}"

    old_lines = original.count("\n") + (1 if original and not original.endswith("\n") else 0)
    new_lines = updated.count("\n") + (1 if updated and not updated.endswith("\n") else 0)
    delta = new_lines - old_lines
    sign = f"+{delta}" if delta >= 0 else str(delta)
    _touch_index(p)
    return (
        f"Patched {path} ({old_lines} → {new_lines} lines, {sign}). "
        "Prefer this tool over file_write for further edits."
    )


def _touch_index(path: Path) -> None:
    try:
        from kazma_core.code_index.indexer import notify_file_changed

        notify_file_changed(path)
    except Exception:
        pass
