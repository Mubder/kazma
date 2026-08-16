"""Browser Automation Native Skill — Playwright headless browser control.

Maintains a single shared Playwright browser instance + a persistent page
across calls for efficiency. The first ``browser_navigate`` boots Playwright;
subsequent tools reuse the live page.

Runs on Playwright's **sync API inside ``asyncio.to_thread`` worker threads**.
Kazma's server runs a Windows ``SelectorEventLoop`` (forced by psycopg — see
``kazma_core/eventloop.py``), on which ``asyncio.create_subprocess_exec``
raises ``NotImplementedError``. Playwright's transport needs exactly that call
to spawn its Node driver — even the sync API creates its own loop via
``asyncio.new_event_loop()``, which would inherit the selector policy. So the
boot temporarily installs a Proactor policy (``_proactor_policy_for_boot``)
while ``sync_playwright().start()`` creates its loop; the loop survives the
policy restore and drives all later page operations. All page operations are
serialized by a module lock (one page = one actor).

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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Shared Playwright state (lazy-initialized on first navigate). All access
# goes through worker threads; the lock serializes boot + page operations.
_state: dict[str, Any] = {"playwright": None, "browser": None, "page": None}
_state_lock = threading.Lock()

MAX_TEXT_CHARS = 8000
SCREENSHOT_DIR = Path("kazma-data/images")

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


async def _run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking Playwright op on a worker thread.

    ``asyncio.to_thread`` keeps the server's SelectorEventLoop out of the
    subprocess-spawning path entirely (see module docstring).
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


def _ensure_page_sync() -> Any:
    """Boot Playwright (if needed) and return the shared page, or raise."""
    with _state_lock:
        if _state["page"] is not None:
            return _state["page"]
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(_install_hint()) from None
        with _proactor_policy_for_boot():
            pw = sync_playwright().start()
            try:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
            except Exception:
                # Don't leak a half-booted driver if launch/new_page fails.
                try:
                    pw.stop()
                except Exception:
                    logger.debug("[browser] stop after failed launch", exc_info=True)
                raise
        _state.update(playwright=pw, browser=browser, page=page)
        return page


def _close_sync() -> None:
    """Tear down the shared browser (call on errors to force a fresh boot)."""
    with _state_lock:
        page = _state.get("page")
        browser = _state.get("browser")
        pw = _state.get("playwright")
        try:
            if page is not None:
                page.close()
            if browser is not None:
                browser.close()
            if pw is not None:
                pw.stop()
        except Exception:
            logger.debug("[browser] teardown error", exc_info=True)
        finally:
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
        dest = SCREENSHOT_DIR / f"browser_{int(time.time())}.png"
        page.screenshot(path=str(dest), full_page=full_page)
    return str(dest)


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
        title, text = await _run_sync(_navigate_sync, url)
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _run_sync(_close_sync)
        return f"Error: navigation failed — {type(exc).__name__}: {exc}"
    return f"Navigated to {url}\nTitle: {title}\n\n{text[:MAX_TEXT_CHARS]}"


async def browser_click(selector: str) -> str:
    """Click the first element matching *selector* (CSS) and return updated text."""
    if not selector or not selector.strip():
        return "Error: No CSS selector provided."
    try:
        text = await _run_sync(_click_sync, selector.strip())
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"Error: click failed — {type(exc).__name__}: {exc}"
    return f"Clicked '{selector}'.\n\n{text[:MAX_TEXT_CHARS]}"


async def browser_extract_text(selector: str = "") -> str:
    """Extract text from elements matching *selector*, or full body if empty."""
    try:
        text = await _run_sync(_extract_sync, selector.strip())
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"Error: extraction failed — {type(exc).__name__}: {exc}"
    return text[:MAX_TEXT_CHARS]


async def browser_screenshot(full_page: bool = True) -> str:
    """Capture a full-page screenshot and save it to kazma-data/images/."""
    try:
        dest = await _run_sync(_screenshot_sync, bool(full_page))
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
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
        return f"Error: JS evaluation failed — {type(exc).__name__}: {exc}"
    return f"Result: {result}"
