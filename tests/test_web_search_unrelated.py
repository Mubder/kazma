"""Search answers that are not about the query are not answers.

Live 2026-10-31, a deep research on the history of Kuwait after a day of
runs: the local SearXNG had its engines suspended (rate-limited), and the
backend chain stopped at the first non-empty answer -- Bing's. Bing answered
"Kuwait independence 1961 Iraqi claim Qasim British withdrawal" with
Telegram's download pages, the protectorate question with a browser game, and
others with a perfume shop, a sports channel and Windows help. The research
read nine of those as its sources. Underneath, two older defects: Bing's
result links were decoded with the wrong base64 alphabet (an address ending
in control characters, or the Bing redirect itself kept as a source), and the
Wikipedia fallback -- a title-prefix search, sent with a User-Agent Wikimedia
refuses -- had never answered a research query.
"""

from __future__ import annotations

import base64
import importlib

import pytest

ws = importlib.import_module("kazma_core.tools.web_search")
from kazma_core.web_acquire import search_http  # noqa: E402

# Run 5's answers, as recorded in its search effects (title and snippet).
TELEGRAM = [
    {"title": "تيليجرام - Telegram", "href": "https://telegram.org/?setln=ar",
     "body": "محرر النصوص الغني، الرسائل سريعة الزوال في المجموعات، المجتمعات"},
    {"title": "Telegram Messenger", "href": "https://telegram.org/",
     "body": "Rich Text Editor, Communities, Ephemeral Messages in Groups, 350 Million GIFs"},
    {"title": "Telegram - التطبيقات على Google Play",
     "href": "https://play.google.com/store/apps/details?id=org.telegram.messenger&hl=ar",
     "body": "برنامج مراسلة فوريّة نقيّة. بسيط، سريع، آمن"},
    {"title": "تيليجرام سطح المكتب", "href": "https://desktop.telegram.org/",
     "body": "هذا البرنامج متوفر تحت ترخيص GPL v3"},
]
PERFUME = [
    {"title": "جوهر العطور الفاخرة العالمية – أطياب المرشود", "href": "https://www.atyabalmarshoud.com/ar/",
     "body": "اكتشف الرائد العالمي في العطور الفاخرة"},
    {"title": "اطياب المرشود", "href": "https://atyabalmarshoud.com.sa/", "body": "السجل التجاري 2050116845"},
    {"title": "Shop Perfumes,Fragrances,Hair Mists & More", "href": "https://www.atyabalmarshoud.com/",
     "body": "Atyab Al Marshoud is a multi-generation company that was established in 1925, "
             "one of the oldest in the perfume industry in Kuwait"},
    {"title": "العطور | اطياب المرشود", "href": "https://atyabalmarshoud.com.sa/c645736006", "body": "من نحن ؟"},
]
# What the Wikipedia fallback now answers to the same questions.
WIKIPEDIA = [
    {"title": "Operation Vantage", "href": "https://en.wikipedia.org/wiki/Operation_Vantage",
     "body": "Vantage was a British military operation in 1961 to support Kuwait"},
    {"title": "Iraqi invasion of Kuwait", "href": "https://en.wikipedia.org/wiki/Iraqi_invasion_of_Kuwait",
     "body": "early 1990, Iraq accused Kuwait of slant drilling"},
    {"title": "History of Kuwait", "href": "https://en.wikipedia.org/wiki/History_of_Kuwait",
     "body": "Iraqi prime minister Abd al-Karim Qasim was killed"},
]
INDEPENDENCE = "Kuwait independence 1961 Iraqi claim Qasim British withdrawal"
VISION = "Kuwait Vision 2035 contemporary politics emir parliament reform"


def test_an_answer_about_something_else_is_not_an_answer():
    assert not ws._answers_the_query(INDEPENDENCE, TELEGRAM)
    # One result mentions Kuwait (a perfume company "in Kuwait"): still not an answer.
    assert not ws._answers_the_query(VISION, PERFUME)
    assert ws._answers_the_query(INDEPENDENCE, WIKIPEDIA)
    # An Arabic question is judged in Arabic.
    assert ws._answers_the_query("تاريخ الكويت", [
        {"title": "تاريخ الكويت - ويكيبيديا", "href": "https://ar.wikipedia.org/wiki/x", "body": ""},
    ])
    # A query with no content word is not judged.
    assert ws._answers_the_query("what is the", TELEGRAM)


def _chain(monkeypatch, *, judge=None):
    calls = []

    def backend(name, rows):
        def run(query, count):
            calls.append(name)
            return (rows, f"{name}:ok") if rows else (None, f"{name}:empty")
        return run

    monkeypatch.setattr(ws, "_searxng_search", backend("searxng", None))
    monkeypatch.setattr(ws, "_ddg_search", backend("duckduckgo", None))
    monkeypatch.setattr(ws, "_bing_search", backend("bing", TELEGRAM))
    monkeypatch.setattr(ws, "_wikipedia_search", backend("wikipedia", WIKIPEDIA))
    if judge is not None:
        monkeypatch.setattr(ws, "_answers_the_query", judge)
    return calls


def test_the_chain_passes_an_unrelated_answer_to_the_next_backend(monkeypatch):
    calls = _chain(monkeypatch)
    results, attempts, backend = ws._run_search(INDEPENDENCE, 6)
    assert backend == "wikipedia" and results == WIKIPEDIA
    assert "bing:unrelated" in attempts
    assert calls == ["searxng", "duckduckgo", "bing", "wikipedia"]


def test_negative_control_without_the_check_bing_s_junk_is_the_answer(monkeypatch):
    _chain(monkeypatch, judge=lambda query, results: True)
    results, _attempts, backend = ws._run_search(INDEPENDENCE, 6)
    assert backend == "bing" and results == TELEGRAM


# A result link from run 5's Bing answer: "_" in its URL-safe base64.
BING_LINK = ("https://www.bing.com/ck/a?!&&p=3a54db04f176&ptn=3&ver=2&hsh=4&fclid=256e"
             "&u=a1aHR0cHM6Ly9zdXBwb3J0Lmdvb2dsZS5jb20vZWFydGgvYW5zd2VyLzYyNDYyODk_aGw9aXQ&ntb=1")


def _bing_page(href):
    return (f'<li class="b_algo"><h2><a href="{href}">Title</a></h2>'
            '<div class="b_caption">snippet</div></li>')


def test_a_bing_result_link_decodes_to_its_address():
    rows = search_http._bing(_bing_page(BING_LINK))
    assert rows == [{"title": "Title", "href": "https://support.google.com/earth/answer/6246289?hl=it",
                     "body": "snippet"}]
    # Negative control: the old decoder. The standard alphabet drops "_" and
    # "-": this link fails to decode and the Bing redirect stays the "source".
    assert _old_decode(BING_LINK) == BING_LINK
    # Another of run 5's links decoded to an address ending in control characters.
    garbled = _old_decode("https://www.bing.com/ck/a?!&&p=1&u=a1"
                          + base64.urlsafe_b64encode(b"https://support.google.com/accounts/answer/3463280?hl=ar&x=y").decode().rstrip("="))
    assert any(ord(ch) < 32 for ch in garbled)


def _old_decode(b_url):
    """The decoder Bing links went through until 2026-10-31."""
    import urllib.parse

    u_param = urllib.parse.parse_qs(urllib.parse.urlparse(b_url).query).get("u", [None])[0]
    b64_str = u_param[2:] if u_param.startswith("a1") else u_param
    try:
        return base64.b64decode(b64_str + "=" * (4 - len(b64_str) % 4)).decode("utf-8", errors="ignore")
    except ValueError:
        return b_url


@pytest.mark.parametrize("href", [
    "https://www.bing.com/ck/a?!&&p=1&ntb=1",                         # no address in it
    "https://www.bing.com/ck/a?!&&p=1&u=a1bm90IGEgdXJs&ntb=1",       # "not a url"
    "https://www.bing.com/ck/a?!&&p=1&u=a1aHR0cHM6Ly9hG2xvY2s&ntb=1",  # a control character
])
def test_a_bing_redirect_that_does_not_decode_is_no_source(href):
    assert search_http._bing(_bing_page(href)) == []


def test_a_direct_result_link_is_kept_as_it_is():
    rows = search_http._bing(_bing_page("https://example.org/page?u=a1aHR0cHM6Ly9ldmls"))
    assert rows[0]["href"] == "https://example.org/page?u=a1aHR0cHM6Ly9ldmls"


def test_wikipedia_full_text_rows():
    payload = {"query": {"search": [
        {"title": "History of Kuwait", "snippet": 'the <span class="searchmatch">Bani</span> Utub &amp; trade'},
        {"title": "Kuwait City", "snippet": ""},
    ]}}
    assert search_http._wikipedia(payload, 5) == [
        {"title": "History of Kuwait", "href": "https://en.wikipedia.org/wiki/History_of_Kuwait",
         "body": "the Bani Utub & trade"},
        {"title": "Kuwait City", "href": "https://en.wikipedia.org/wiki/Kuwait_City", "body": ""},
    ]
    assert search_http._wikipedia({"query": {"search": []}}, 5) == []
    for bad in (["q", [], [], []], {"query": {"search": [{"snippet": "no title"}]}}, {"error": {}}):
        with pytest.raises(ValueError):
            search_http._wikipedia(bad, 5)


def test_the_search_user_agent_names_a_contact():
    # Wikimedia answers 403 to a User-Agent with no contact; the old one had none.
    assert "+https://github.com/Mubder/kazma" in search_http.USER_AGENT
    assert "+https://" not in "KazmaSearch/0.6"
