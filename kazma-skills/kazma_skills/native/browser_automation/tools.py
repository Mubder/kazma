"""Browser Automation Native Skill — Playwright headless browser control.

Maintains a single shared Playwright browser instance + a persistent page
across calls for efficiency. The first ``browser_navigate`` boots Playwright;
subsequent tools reuse the live page.

Requires ``playwright`` (``pip install playwright && playwright install``).
All tool functions return a friendly install-hint string if Playwright is
missing, so the skill always loads.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Shared Playwright state (lazy-initialized on first navigate).
_state: dict[str, object | None] = {"playwright": None, "browser": None, "page": None}

MAX_TEXT_CHARS = 8000
SCREENSHOT_DIR = Path("kazma-data/images")


def _install_hint() -> str:
    return (
        "Error: Playwright not installed. Run: "
        "pip install playwright && playwright install chromium"
    )


async def _ensure_page():
    """Boot Playwright (if needed) and return the shared page, or raise."""
    if _state["page"] is not None:
        return _state["page"]  # type: ignore[return-value]
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(_install_hint())

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    _state.update(playwright=pw, browser=browser, page=page)
    return page


async def _close() -> None:
    """Tear down the shared browser (call on errors to force a fresh boot)."""
    page = _state.get("page")
    browser = _state.get("browser")
    pw = _state.get("playwright")
    try:
        if page is not None:
            await page.close()  # type: ignore[union-attr]
        if browser is not None:
            await browser.close()  # type: ignore[union-attr]
        if pw is not None:
            await pw.stop()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        logger.debug("[browser] teardown error", exc_info=True)
    finally:
        _state.update(playwright=None, browser=None, page=None)


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
        page = await _ensure_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)  # type: ignore[union-attr]
        title = await page.title()  # type: ignore[union-attr]
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")  # type: ignore[union-attr]
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        await _close()
        return f"Error: navigation failed — {type(exc).__name__}: {exc}"
    snippet = (text or "")[:MAX_TEXT_CHARS]
    return f"Navigated to {url}\nTitle: {title}\n\n{snippet}"


async def browser_click(selector: str) -> str:
    """Click the first element matching *selector* (CSS) and return updated text."""
    if not selector or not selector.strip():
        return "Error: No CSS selector provided."
    try:
        page = await _ensure_page()
        await page.click(selector.strip(), timeout=10000)  # type: ignore[union-attr]
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")  # type: ignore[union-attr]
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"Error: click failed — {type(exc).__name__}: {exc}"
    return f"Clicked '{selector}'.\n\n{(text or '')[:MAX_TEXT_CHARS]}"


async def browser_extract_text(selector: str = "") -> str:
    """Extract text from elements matching *selector*, or full body if empty."""
    try:
        page = await _ensure_page()
        if selector and selector.strip():
            sel = selector.strip()
            text = await page.eval_on_selector_all(  # type: ignore[union-attr]
                sel, "(els) => els.map(e => e.innerText).join('\\n---\\n')"
            )
        else:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")  # type: ignore[union-attr]
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"Error: extraction failed — {type(exc).__name__}: {exc}"
    return f"{(text or '')[:MAX_TEXT_CHARS]}"


async def browser_screenshot(full_page: bool = True) -> str:
    """Capture a full-page screenshot and save it to kazma-data/images/."""
    try:
        page = await _ensure_page()
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        dest = SCREENSHOT_DIR / f"browser_{int(time.time())}.png"
        await page.screenshot(path=str(dest), full_page=full_page)  # type: ignore[union-attr]
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
        page = await _ensure_page()
        for sel, val in fields.items():
            await page.fill(sel, str(val), timeout=10000)  # type: ignore[union-attr]
        result = f"Filled {len(fields)} field(s)."
        if submit_selector and submit_selector.strip():
            await page.click(submit_selector.strip(), timeout=10000)  # type: ignore[union-attr]
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")  # type: ignore[union-attr]
            result += f"\n\nSubmitted. Result text:\n{(text or '')[:MAX_TEXT_CHARS]}"
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
        page = await _ensure_page()
        result = await page.evaluate(expression.strip())  # type: ignore[union-attr]
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"Error: JS evaluation failed — {type(exc).__name__}: {exc}"
    return f"Result: {result}"
