"""Pure local article extraction; production callers use the bounded parser child."""
import re


def strip_html_fallback(html: str) -> str:
    """stdlib-only extract when trafilatura is missing or returns nothing."""
    # Drop script/style blocks first
    cleaned = re.sub(
        r"(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>",
        " ",
        html,
    )
    # Prefer body if present
    body_m = re.search(r"(?is)<body[^>]*>(.*)</body>", cleaned)
    chunk = body_m.group(1) if body_m else cleaned
    text = re.sub(r"(?is)<br\s*/?>", "\n", chunk)
    text = re.sub(r"(?is)</(p|div|h[1-6]|li|tr|section|article)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    # Unescape a few common entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_article(html: str, limit: int = 100_000) -> tuple[str, bool]:
    text = ''
    try:
        import trafilatura

        text = trafilatura.extract(html, include_links=True, include_tables=True,
            favor_recall=True, include_comments=False) or ''
    except ImportError:
        pass
    except Exception:
        pass  # Bounded child falls back to static HTML text.
    text = text.strip()
    if len(text) < 80:
        fallback = strip_html_fallback(html)
        if len(fallback) > len(text):
            text = fallback
    return text[:limit].strip(), len(text) > limit
