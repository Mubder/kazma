"""Browser Automation Native Skill — Playwright headless browser control.

Maintains a single shared Playwright browser instance + a persistent page
across calls for efficiency. The first ``browser_navigate`` boots Playwright;
subsequent tools reuse the live page.

Uses the bounded acquisition browser owner and CDP/HTTPX broker.
Its async Playwright objects stay on a dedicated browser loop; the compatibility
facade runs from joined worker calls. No global event-loop policy switch or
unrestricted sync driver is used. Calls have one owner and eight queued slots;
HTTP uses bounded public DNS, peer checks, origin scheduling and policy-gated
proxy accounting. Browser lifetime/resource limits are shared with acquisition.
Mutating HTTP is admitted only on this native automation path, through its
existing tool/HITL gates; crawler rendering remains read-only.

Requires ``playwright`` (``pip install playwright && playwright install``).
All tool functions return a friendly install-hint string if Playwright is
missing, so the skill always loads.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Shared Playwright state (lazy-initialized on first navigate). All access
# goes through worker threads; the lock serializes boot + page operations.
_state: dict[str, Any] = {"playwright": None, "browser": None, "page": None}
_state_lock = threading.Lock()

MAX_TEXT_CHARS = 8000
from kazma_core.paths import data_dir as _data_dir

#: Resolved via paths so KAZMA_DATA_DIR is honoured.
SCREENSHOT_DIR = _data_dir() / "images"

_IS_WINDOWS = sys.platform.startswith("win")


def _install_hint() -> str:
    return (
        "Error: Playwright not installed. Run: "
        "pip install playwright && playwright install chromium"
    )


@contextmanager
def _proactor_policy_for_boot():
    """Boot Playwright under a Proactor event-loop policy (Windows).

    Playwright's sync API creates its own loop via ``asyncio.new_event_loop()``
    on start. Kazma forces a GLOBAL ``WindowsSelectorEventLoopPolicy`` (see
    ``kazma_core/eventloop.py`` — psycopg refuses Proactor), and the selector
    loop does not implement subprocess transports, so the Node-driver spawn
    (``create_subprocess_exec`` in Playwright's transport) raised
    ``NotImplementedError`` and every browser tool silently returned nothing
    (incident 2026-08-16). Installing the Proactor policy for the boot makes
    the loop Playwright creates able to spawn its driver. Held only for the
    boot under ``_state_lock`` (single-flight); the already-created loop keeps
    running after the policy is restored. Non-Windows is a pass-through.
    """
    if not _IS_WINDOWS:
        yield
        return
    prev = asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  # type: ignore[attr-defined]
        yield
    finally:
        asyncio.set_event_loop_policy(prev)


from kazma_core.web_acquire.resources import _CapacityPool
_NATIVE_CALLS = _CapacityPool('interactive browser calls', 1, 8)


async def _run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Serialize bounded native operations; join cleanup under cancellation."""
    from kazma_core.playwright_loop import finish_owned
    from kazma_core.web_acquire.crawl_jobs import call
    lease = await _NATIVE_CALLS.acquire()
    try:
        work = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        receipt = asyncio.gather(work, return_exceptions=True)
        try:
            result, = await asyncio.shield(receipt)
        except asyncio.CancelledError:
            page = _state.get('page')
            if page is not None and hasattr(page, 'interrupt'):
                page.interrupt()
            await finish_owned(receipt)
            await finish_owned(call(_close_sync))
            raise
        if isinstance(result, BaseException):
            raise result
        return result
    finally:
        lease.release()


def _ensure_page_sync() -> Any:
    """Compatibility facade; all HTTP uses the owned CDP/HTTPX broker."""
    with _state_lock:
        if _state['page'] is not None:
            return _state['page']
        from kazma_core.web_acquire.interactive import open_page
        page = open_page()
        _state.update(page=page, browser=None, playwright=None)
        return page


def _close_sync() -> None:
    """Tear down the shared browser (call on errors to force a fresh boot)."""
    with _state_lock:
        page = _state.get("page")
        browser = _state.get("browser")
        pw = _state.get("playwright")
        if page is not None:
            from kazma_core.web_acquire.interactive import PageFacade
            if isinstance(page, PageFacade):
                # Preserve the owner/handles on uncertain cleanup; do not open
                # another native browser over a quarantined process tree.
                page.close()
                _state.update(playwright=None, browser=None, page=None)
                return
            try:
                page.close()
            except Exception:
                logger.debug("[browser] page close error", exc_info=True)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                logger.debug("[browser] browser close error", exc_info=True)
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                logger.debug("[browser] playwright stop error", exc_info=True)
        _state.update(playwright=None, browser=None, page=None)


# ── per-tool sync ops (run on worker threads, serialized by the lock) ──────


def _navigate_sync(url: str) -> tuple[str, str]:
    page = _ensure_page_sync()
    with _state_lock:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = page.title()
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    return title or "", text or ""


def _click_sync(selector: str) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        page.click(selector, timeout=10000)
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    return text or ""


def _extract_sync(selector: str) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        if selector:
            text = page.eval_on_selector_all(
                selector, "(els) => els.map(e => e.innerText).join('\\n---\\n')"
            )
        else:
            text = page.evaluate("() => document.body ? document.body.innerText : ''")
    return text or ""


def _screenshot_sync(full_page: bool) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        import uuid
        from kazma_core.web_acquire.output_allowance import reserve
        dest = SCREENSHOT_DIR / f'browser_{uuid.uuid4().hex}.png'
        image = page.screenshot(full_page=full_page)
        reserve(dest, len(image))
        with dest.open('xb') as stream:
            stream.write(image)
    return str(dest)


def _mouse_click_sync(x: int, y: int) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        page.mouse.click(int(x), int(y))
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    return f"clicked ({int(x)},{int(y)})\n{(text or '')[:MAX_TEXT_CHARS]}"


def _type_text_sync(text: str) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        page.keyboard.type(str(text or ""), delay=20)
    return f"typed {len(text or '')} chars"


def _key_press_sync(key: str) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        page.keyboard.press(str(key or "Enter"))
    return f"pressed {key or 'Enter'}"


def _scroll_sync(dy: int) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        page.mouse.wheel(0, int(dy))
    return f"scrolled dy={int(dy)}"


def _wait_sync(seconds: float) -> str:
    time.sleep(max(0.0, min(float(seconds), 5.0)))
    return f"waited {seconds}s"


def _fill_form_sync(fields: dict[str, str], submit_selector: str) -> str:
    page = _ensure_page_sync()
    with _state_lock:
        for sel, val in fields.items():
            page.fill(sel, str(val), timeout=10000)
        result = f"Filled {len(fields)} field(s)."
        if submit_selector:
            page.click(submit_selector, timeout=10000)
            text = page.evaluate("() => document.body ? document.body.innerText : ''")
            result += f"\n\nSubmitted. Result text:\n{(text or '')[:MAX_TEXT_CHARS]}"
    return result


def _eval_js_sync(expression: str) -> Any:
    page = _ensure_page_sync()
    with _state_lock:
        return page.evaluate(expression)


# ── tool surface (unchanged signatures) ────────────────────────────────────


def _fence_page_text(text: str, source: str) -> str:
    """Wrap browser-extracted page text in the untrusted prompt fence.

    Page content is the classic prompt-injection vector; every other web
    path (read_url, web_search, MCP resources) fences — the browser tools
    used to be the raw exception (audit M-P8). Best-effort: if the fence
    helper is unavailable the text still returns (bounded by MAX_TEXT_CHARS).
    """
    try:
        from kazma_core.safety.prompt_fence import fence_untrusted

        return fence_untrusted(text, source=source)
    except Exception:
        logger.debug("prompt fence unavailable for browser text", exc_info=True)
        return text


async def browser_navigate(url: str) -> str:
    """Open *url* in the headless browser and return title + visible text.

    Use for JavaScript-rendered pages a plain HTTP fetch cannot read.
    """
    if not url or not url.strip():
        return "Error: No URL provided."
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from kazma_core.security.ssrf import validate_url

        from kazma_core.web_acquire.dns_worker import validate_public_url
        await validate_public_url(url, validator=validate_url)
    except Exception as exc:
        return f"Error: URL blocked by security policy — {exc}"

    try:
        title, text = await _run_sync(_navigate_sync, url)
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: navigation failed — {type(exc).__name__}: {exc}"
    return (
        f"Navigated to {url}\nTitle: {title}\n\n"
        + _fence_page_text(text[:MAX_TEXT_CHARS], source=f"browser:{url}")
    )


async def browser_click(selector: str) -> str:
    """Click the first element matching *selector* (CSS) and return updated text."""
    if not selector or not selector.strip():
        return "Error: No CSS selector provided."
    try:
        text = await _run_sync(_click_sync, selector.strip())
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: click failed — {type(exc).__name__}: {exc}"
    return f"Clicked '{selector}'.\n\n" + _fence_page_text(
        text[:MAX_TEXT_CHARS], source="browser:click"
    )


async def browser_extract_text(selector: str = "") -> str:
    """Extract text from elements matching *selector*, or full body if empty."""
    try:
        text = await _run_sync(_extract_sync, selector.strip())
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: extraction failed — {type(exc).__name__}: {exc}"
    return _fence_page_text(text[:MAX_TEXT_CHARS], source="browser:extract")


async def browser_screenshot(full_page: bool = True) -> str:
    """Capture a full-page screenshot and save it to kazma-data/images/."""
    try:
        dest = await _run_sync(_screenshot_sync, bool(full_page))
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: screenshot failed — {type(exc).__name__}: {exc}"
    return f"Screenshot saved to {dest}"


async def browser_fill_form(
    fields: dict[str, str],
    submit_selector: str = "",
) -> str:
    """Fill form inputs from a {css_selector: value} mapping.

    If *submit_selector* is set, clicks it after filling and returns the
    resulting page text.
    """
    if not fields or not isinstance(fields, dict):
        return "Error: 'fields' must be a non-empty {selector: value} mapping."
    try:
        result = await _run_sync(
            _fill_form_sync, fields, (submit_selector or "").strip()
        )
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: form fill failed — {type(exc).__name__}: {exc}"
    return result


async def browser_eval_js(expression: str) -> str:
    """Evaluate a JavaScript *expression* on the page and return the result.

    Executes arbitrary page-side code — use with care.
    """
    if not expression or not expression.strip():
        return "Error: No JavaScript expression provided."
    try:
        result = await _run_sync(_eval_js_sync, expression.strip())
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: JS evaluation failed — {type(exc).__name__}: {exc}"
    return f"Result: {result}"
