"""How in-process mouths find the running Kazma HTTP API.

TUI / CLI are separate processes. They are mouths of the *same* brain
only if they talk to the server the operator already started — not a
second LLM loop.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

__all__ = [
    "auth_headers",
    "candidate_api_bases",
    "request_json",
    "request_json_async",
]


def _is_loopback(url: str) -> bool:
    low = (url or "").lower()
    return (
        "://127.0.0.1" in low
        or "://localhost" in low
        or "://[::1]" in low
    )


def candidate_api_bases() -> list[str]:
    """Deduped API roots for in-process mouths (TUI / CLI).

    Loopback first. ``KAZMA_BASE_URL`` is an explicit override (may be remote).
    ``KAZMA_PUBLIC_URL`` is *not* used — that host is for browsers/OAuth and
    typically 302s to a login HTML page.
    """
    found: list[str] = []
    port = (os.environ.get("KAZMA_PORT") or "").strip()
    if port:
        found.append(f"http://127.0.0.1:{port}")
    found.append("http://127.0.0.1:9090")
    found.append("http://127.0.0.1:8000")
    raw = (os.environ.get("KAZMA_BASE_URL") or "").strip().rstrip("/")
    if raw:
        found.append(raw)
    loopback = [u for u in found if _is_loopback(u)]
    remote = [u for u in found if not _is_loopback(u)]
    out: list[str] = []
    for u in loopback + remote:
        if u not in out:
            out.append(u)
    return out


def _secret_from_dotenv() -> str:
    """Read KAZMA_SECRET from cwd/.env when the process env is empty."""
    import sys

    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    try:
        from pathlib import Path

        env_path = Path.cwd() / ".env"
        if not env_path.is_file():
            return ""
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            if key.strip() == "KAZMA_SECRET":
                return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def auth_headers() -> dict[str, str]:
    secret = (os.environ.get("KAZMA_SECRET") or "").strip()
    if not secret:
        secret = _secret_from_dotenv()
    if not secret:
        return {}
    return {"X-Kazma-Secret": secret}


def first_reachable(bases: Iterable[str] | None = None) -> list[str]:
    """Return candidate list (reachability is checked by the caller)."""
    return list(bases) if bases is not None else candidate_api_bases()


def _json_url(base: str, path: str) -> str:
    p = path if path.startswith("/") else f"/{path}"
    return f"{base.rstrip('/')}{p}"


def request_json(
    method: str,
    path: str,
    *,
    payload: Any | None = None,
    timeout: float = 8.0,
) -> Any:
    """GET/POST JSON against the first reachable loopback API.

    Raises ``RuntimeError`` when no candidate accepts a JSON response.
    """
    import httpx

    headers = {"Accept": "application/json", **auth_headers()}
    errors: list[str] = []
    for base in candidate_api_bases():
        url = _json_url(base, path)
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=2.0)) as client:
                resp = client.request(method.upper(), url, json=payload, headers=headers)
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"{url} returned {resp.status_code} — set KAZMA_SECRET "
                    "to the same secret the server is using (cwd .env)."
                )
            if resp.status_code >= 400:
                errors.append(f"{url} -> {resp.status_code}")
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                errors.append(f"{url} returned {resp.status_code} ({ctype or 'no content-type'})")
                continue
            return resp.json()
        except httpx.ConnectError:
            errors.append(f"nothing listening at {base}")
            continue
        except httpx.TimeoutException:
            errors.append(f"timeout talking to {base}")
            continue
    detail = "; ".join(errors) if errors else "no API candidates"
    raise RuntimeError(
        "Kazma server is not running on this machine "
        f"(tried {', '.join(candidate_api_bases())}). {detail} "
        "Start it yourself — the TUI is only a mouth."
    )


async def request_json_async(
    method: str,
    path: str,
    *,
    payload: Any | None = None,
    timeout: float = 8.0,
) -> Any:
    """Async variant of :func:`request_json` for TUI inspector commands."""
    import httpx

    headers = {"Accept": "application/json", **auth_headers()}
    errors: list[str] = []
    for base in candidate_api_bases():
        url = _json_url(base, path)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=2.0)) as client:
                resp = await client.request(
                    method.upper(), url, json=payload, headers=headers
                )
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"{url} returned {resp.status_code} — set KAZMA_SECRET "
                    "to the same secret the server is using (cwd .env)."
                )
            if resp.status_code >= 400:
                snippet = ""
                try:
                    body = resp.json()
                    snippet = str(body.get("error") or body.get("detail") or "")[:160]
                except Exception:
                    snippet = (resp.text or "")[:160]
                errors.append(f"{url} -> {resp.status_code}" + (f" {snippet}" if snippet else ""))
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                errors.append(f"{url} returned {resp.status_code} ({ctype or 'no content-type'})")
                continue
            return resp.json()
        except httpx.ConnectError:
            errors.append(f"nothing listening at {base}")
            continue
        except httpx.TimeoutException:
            errors.append(f"timeout talking to {base}")
            continue
    detail = "; ".join(errors) if errors else "no API candidates"
    raise RuntimeError(
        "Kazma server is not running on this machine "
        f"(tried {', '.join(candidate_api_bases())}). {detail} "
        "Start it yourself — the TUI is only a mouth."
    )
