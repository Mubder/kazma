"""``download_image``: save a picture from the web as a file in the workspace.

A document names its pictures by file (``documents.image_inputs``), never by
address, so no fetch is hidden inside a document call. This is the step that
brings a web picture in: one GET through the acquisition layer
(``web_acquire.http.fetch_public``: every hop of the redirect chain validated
as public and the connection pinned to the address that was validated, the
body bounded), the bytes judged by what they are -- a PNG, JPEG, GIF or WebP,
whatever the server called them -- and saved under
``<workspace>/downloads/images``. The file is then named in a document like
any other picture.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from kazma_core.agent.tool_refusal import KnownEffectError, RefusedBeforeEffect

__all__ = ["MAX_DOWNLOAD_BYTES", "download_image"]

#: One picture (the document tools take at most this much a picture).
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
_TIMEOUT_S = 60.0
_USER_AGENT = "KazmaBot/1.0 (image download)"
_NAME_CHARS = re.compile(r"[^\w\-]+", re.UNICODE)


def _file_stem(name: str, url: str) -> str:
    """A plain file name: the caller's, else the address's last segment."""
    stem = name.strip() or Path(unquote(urlsplit(url).path or "")).stem or "image"
    stem = Path(stem).stem if Path(stem).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else stem
    stem = _NAME_CHARS.sub("-", stem).strip("-_.")[:60]
    return stem or "image"


def _save(data: bytes, stem: str, kind: str) -> Path:
    """Write the picture under a name not yet taken (blocking: a thread)."""
    from kazma_core.workspace.binding import resolve_active_root

    folder = resolve_active_root() / "downloads" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stem}.{kind}"
    n = 2
    while target.exists():
        target = folder / f"{stem}-{n}.{kind}"
        n += 1
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    partial.replace(target)
    return target


async def download_image(url: str, name: str = "") -> str:
    """Save a picture from the web as a file in the workspace, to put in a document.

    url: the picture's http or https address
    name: the file name to save it under, without extension (default: the address's)

    Saves a PNG, JPEG, GIF or WebP picture (at most 20 MB) under the
    workspace's downloads/images folder and answers with its path; name that
    path in a document (``![caption](path)`` in generate_pdf or generate_docx,
    a slide's image in generate_pptx) or send it with send_file.
    """
    from kazma_core.documents.image_inputs import image_type
    from kazma_core.security.ssrf import SSRFError
    from kazma_core.web_acquire.http import fetch_public

    address = str(url or "").strip()
    if urlsplit(address).scheme.lower() not in {"http", "https"} or not urlsplit(address).hostname:
        raise RefusedBeforeEffect("Error: give the picture's http or https address; nothing was saved.")
    try:
        got = await fetch_public(
            address, max_bytes=MAX_DOWNLOAD_BYTES, timeout=_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
        )
    except SSRFError as exc:
        raise KnownEffectError(f"Error: the address was refused ({exc}); nothing was saved.") from exc
    except (TimeoutError, OSError) as exc:
        raise KnownEffectError(
            f"Error: the picture could not be fetched ({type(exc).__name__}); nothing was saved."
        ) from exc
    except Exception as exc:  # httpx and the acquisition layer's own errors
        raise KnownEffectError(
            f"Error: the picture could not be fetched ({type(exc).__name__}); nothing was saved."
        ) from exc
    status = got.response.status_code
    if status >= 400:
        raise KnownEffectError(f"Error: the server answered HTTP {status}; nothing was saved.")
    if got.truncated:
        raise KnownEffectError(
            f"Error: the picture is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB; nothing was saved."
        )
    data = got.response.content
    kind = image_type(data[:16])
    if kind is None:
        what = (got.response.headers.get("content-type") or "unknown").split(";")[0].strip()
        raise KnownEffectError(
            f"Error: the address gave no PNG, JPEG, GIF or WebP picture (it sent {what[:60]}); nothing was "
            "saved. An SVG or a web page cannot go into a document."
        )
    path = await asyncio.to_thread(_save, data, _file_stem(name, got.url), kind)
    return (
        f"Picture saved: {path} ({max(1, len(data) // 1024)} KB, {kind.upper()}).\n"
        f"Put it in a document with ![caption]({path}) on a line of its own, or as a slide's image."
    )
