"""Conservative document signals; do not infer failure from extracted prose."""

from __future__ import annotations

import re
from html.parser import HTMLParser


_LOGIN_TITLE = re.compile(
    r"(?:log\s?in|sign\s?in|تسجيل\s+(?:الدخول|دخول))"
    r"(?:\s*[|:—–-]\s*[^<>]{1,100})?", re.IGNORECASE)


class LoginRequiredError(ValueError):
    """An observed login document must not be indexed or recovered remotely."""


class _LoginDocument(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.password = False
        self.in_title = False
        self.title: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "input" and (dict(attrs).get("type") or "").lower() == "password":
            self.password = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def is_login_document(html: str) -> bool:
    """Require BOTH a real password input and an explicit login page title.

    A login link/widget, prose about authentication and escaped code examples
    are insufficient. Unknown/SSO/paywall layouts remain unclassified; this
    signal makes no claim to identify every authentication screen.
    """
    if "password" not in html.lower():
        return False
    document = _LoginDocument()
    document.feed(html)
    title = " ".join("".join(document.title).split())
    return document.password and bool(_LOGIN_TITLE.fullmatch(title))
