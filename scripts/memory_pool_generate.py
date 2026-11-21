#!/usr/bin/env python3
"""Write the memory benchmark's unrelated chat, every turn different
(dataset v8, the benchmark audit's R9).

Until v7 the "large pool of unrelated chat" was 300 sessions drawn with
repeats from 74 hand-written turns: its 1,060 turns were 181 different ones,
about six copies each, and recall's background (the question's meaning ranks
4 to 15) was made of copies. This asks a model for chat a person might have
with an assistant about everything but themselves -- a subtopic at a time,
in English and Gulf Arabic -- and keeps each turn once. The benchmark's
build (``scripts/memory_bench.py build``) draws the pool and the scale run's
sessions from the file, without replacement.

    DEEPSEEK_API_KEY=... python scripts/memory_pool_generate.py --out tests/fixtures/memory_bench/pool_turns.json

It writes only ``--out``, after every reply, and continues a file it finds
there (a subtopic already answered is not asked again). ``--limit`` bounds
the calls. The key is read from the environment, never written. A turn is
kept only when it states nothing about the person asking -- no name, home,
job, family, pet, health, possession, date or preference: the benchmark's
unanswerable questions ask exactly those, and a pool turn that said one
would answer them -- and names nothing of the benchmark's persona.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "kazma-core" / "kazma_core" / "memory" / "benchmark_data" / "dataset.json"

#: Turns asked for per call.
PER_CALL = 15

#: (group, language, subtopic). The group is the weekly summary's topic; the
#: subtopic only steers the call. English and Gulf Arabic.
SUBTOPICS: list[tuple[str, str, str]] = [
    *[("code", "en", t) for t in (
        "Python lists, dictionaries and comprehensions", "JavaScript promises and the event loop",
        "SQL queries, joins and indexes", "git branching, rebasing and merge conflicts",
        "CSS layout with flexbox and grid", "Linux shell commands and permissions",
        "Docker images and containers", "REST APIs and HTTP status codes", "unit testing and mocking",
        "regular expressions", "algorithms: sorting, searching and complexity",
        "TypeScript types and generics", "Rust ownership and borrowing", "Go goroutines and channels",
        "debugging memory leaks and performance", "databases: transactions and isolation levels",
        "web security: XSS, CSRF and SQL injection", "data structures: trees, heaps and graphs",
        "React hooks and state management", "pandas and numpy data wrangling")],
    *[("food", "en", t) for t in (
        "baking bread and pastry", "quick weeknight dinners", "Middle Eastern and Gulf dishes",
        "Italian pasta and sauces", "Asian stir-fries, curries and noodles", "vegetarian and vegan cooking",
        "kitchen knife skills and equipment", "food safety and storage", "desserts and chocolate",
        "coffee and tea brewing", "grilling and barbecue", "soups and stews", "breakfast ideas",
        "spices and herbs", "meal prep and leftovers")],
    *[("travel", "en", t) for t in (
        "city breaks in Europe", "travel in Japan and Korea", "packing and luggage rules",
        "flights, layovers and jet lag", "visas and passports in general", "budget travel and hostels",
        "road trips and car rental", "beaches and islands", "hiking and national parks",
        "travel in the Gulf and the Levant", "train travel", "travel insurance and safety",
        "museums and historic sites", "travelling with children in general", "winter and ski trips")],
    *[("general", "en", t) for t in (
        "astronomy and space", "human biology", "world history", "geography and capitals",
        "physics in everyday life", "chemistry basics", "animals and wildlife", "weather and climate",
        "famous paintings and artists", "classic novels and authors", "inventions and inventors",
        "economics concepts", "philosophy and logic", "mathematics puzzles", "oceans and marine life",
        "ancient civilisations", "languages and etymology", "music history", "film history",
        "architecture and famous buildings")],
    *[("writing", "en", t) for t in (
        "rewriting emails to sound polite", "proofreading and grammar", "titles and headlines",
        "short poems", "cover letters and CVs in general", "summarising articles", "slogans and taglines",
        "translation of short phrases", "social media captions", "speeches and toasts",
        "formal letters", "story ideas")],
    *[("tools", "en", t) for t in (
        "running tests and builds", "checking servers and services", "files and folders",
        "disk, memory and CPU usage", "git status and logs", "backups and syncs",
        "calendar and reminder lookups", "network checks")],
    *[("health", "en", t) for t in (
        "sleep habits", "exercise and stretching", "nutrition and vitamins", "first aid basics",
        "posture and desk ergonomics", "stress and breathing exercises", "hydration and heat",
        "common colds and flu in general", "running and cycling training in general", "eye strain")],
    *[("money", "en", t) for t in (
        "budgeting methods", "interest rates and loans", "saving and emergency funds",
        "investing basics: index funds and bonds", "inflation", "credit scores", "taxes in general",
        "currencies and exchange rates", "small business accounting", "insurance types")],
    *[("home", "en", t) for t in (
        "cleaning tips and stains", "houseplants and gardening", "DIY repairs and tools",
        "energy saving at home", "organising and decluttering", "laundry and fabric care",
        "furniture and interior design", "pest control", "smart home devices", "moving house in general")],
    *[("hobbies", "en", t) for t in (
        "chess strategy", "photography and cameras", "guitar and piano practice", "football rules and history",
        "basketball and tennis", "board games and card games", "video games", "drawing and painting",
        "knitting and sewing", "fishing and camping", "reading recommendations", "podcasts and audiobooks")],
    *[("learning", "en", t) for t in (
        "learning a new language", "study techniques and memory", "exam preparation",
        "online courses and certificates", "public speaking", "time management and productivity",
        "note-taking methods", "teaching children to read in general")],
    *[("tech", "en", t) for t in (
        "smartphones and battery life", "laptops and keyboards", "Wi-Fi routers and networking",
        "passwords and two-factor authentication", "spreadsheets and formulas", "cloud storage",
        "printers and scanners", "AI assistants and chatbots", "email clients and spam", "video calls")],
    *[("arabic", "ar", t) for t in (
        "الطبخ الخليجي والحلويات", "السفر والسياحة", "البرمجة والتقنية", "الصحة والرياضة", "اللغة العربية والنحو",
        "التاريخ الإسلامي", "الفلك والفضاء", "المال والادخار", "البيت والزراعة", "السيارات والصيانة",
        "كرة القدم", "الدراسة والامتحانات", "الكتابة والرسائل", "الطقس والمناخ", "الحيوانات والطبيعة",
        "الأجهزة والجوالات", "الشعر والأدب", "الرمضانيات والعادات", "الأعمال والمشاريع الصغيرة", "القهوة والشاي")],
    *[("filler", "en", t) for t in (
        "greetings, thanks and short acknowledgements in English", "short replies like ok, sure, got it")],
    ("filler", "ar", "التحيات والشكر والردود القصيرة بالخليجي"),
]

PROMPT_EN = (
    "Write {n} different chat turns a person might send an AI assistant about {topic}, each with the "
    "assistant's short reply. Rules: every turn is a question, a request or a task about the world, "
    "never a statement about the person: no names, home, job, family, pets, health, possessions, dates, "
    "plans or preferences of the person asking. Vary the form: questions, commands, requests to explain, "
    "compare, write or check something; some short and casual, some longer. A user turn is 3 to 30 "
    "words; a reply is one or two sentences with a concrete answer. Reply with JSON only: "
    '{{"turns": [{{"user": "...", "assistant": "..."}}, ...]}}'
)
PROMPT_AR = (
    "Write {n} different chat turns in Gulf Arabic (Kuwaiti or Saudi everyday speech) that a person "
    "might send an AI assistant about {topic}, each with the assistant's short reply in Gulf Arabic. "
    "Rules: every turn is a question, a request or a task about the world, never a statement about the "
    "person: no names, home, job, family, pets, health, possessions, dates, plans or preferences of the "
    "person asking. Vary the form. A user turn is 3 to 30 words; a reply is one or two sentences. Reply "
    'with JSON only: {{"turns": [{{"user": "...", "assistant": "..."}}, ...]}}'
)
SMALLTALK_EN = (
    "Write {n} different very short chat turns a person sends an AI assistant: {topic}. Each with the "
    "assistant's one-line reply. Nothing about the person. JSON only: "
    '{{"turns": [{{"user": "...", "assistant": "..."}}, ...]}}'
)
SMALLTALK_AR = (
    "Write {n} different very short chat turns in Gulf Arabic a person sends an AI assistant: {topic}. "
    "Each with the assistant's one-line reply in Gulf Arabic. Nothing about the person. JSON only: "
    '{{"turns": [{{"user": "...", "assistant": "..."}}, ...]}}'
)

#: The person stating something about themself or their own people and
#: things, in English and Gulf Arabic: the benchmark asks such things and
#: expects nothing to answer them.
_SELF_EN = re.compile(
    r"\b(?:my|our)\s+(?:wife|husband|partner|son|daughter|kids?|child(?:ren)?|mother|mom|mum|father|dad|"
    r"brother|sister|family|parents?|cousin|uncle|aunt|friend|neighbou?r|dog|cat|pet|boss|manager|"
    r"colleague|doctor|dentist|car|house|home|flat|apartment|bank|salary|birthday|anniversary|passport|"
    r"blood|name|team|gym|university|school|wi-?fi|phone|address|landlord|teacher|coach|wedding|"
    r"cholesterol|height|weight|shoe|job|company|employer|office)\b"
    r"|\bI\s*(?:am|'m)\s+(?:allergic|married|from|a|an|\d)"
    r"|\bI\s+(?:live|work|study|drive|own|was born|moved|have (?:a|an|two|three)|got married)\b",
    re.IGNORECASE,
)
_SELF_AR = re.compile(
    r"(?:اسمي|أسكن|اسكن|ساكن|أشتغل|اشتغل|زوجتي|زوجي|ولدي|بنتي|عيالي|أمي|امي|أبوي|ابوي|أخوي|اخوي|أختي|اختي|"
    r"سيارتي|بيتي|راتبي|ميلادي|مديري|دكتوري|شغلي|وظيفتي|جامعتي|مدرستي|جوالي|عنواني|قطوتي|كلبي|فريقي)"
)


def _fold(text: str) -> str:
    """A turn's text for comparing: NFKC, casefolded, letters and digits."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def persona_words(dataset: dict[str, Any]) -> set[str]:
    """The proper names of the benchmark's persona and its near misses:
    every capitalised word inside a sentence of their turns, folded. A pool
    turn naming one could answer a question about them."""
    words: set[str] = set()
    for session in dataset["sessions"]:
        if session.get("kind") not in ("persona", "hard_negative", "dense"):
            continue
        for turn in session["turns"]:
            for text in (turn["user"], turn["assistant"]):
                for sentence in re.split(r"[.!?]\s+", text):
                    for word in sentence.split()[1:]:
                        core = word.strip(".,;:!?()'\"")
                        if core[:1].isupper() and core[1:].islower() and len(core) > 2:
                            words.add(core.casefold())
    return words


#: Capitalised words that are not names (they start a clause after a colon,
#: are days, months or common words).
_NOT_NAMES = frozenset({
    "the", "and", "you", "your", "this", "that", "they", "she", "his", "her", "how", "what", "when", "where",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
    "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "english", "arabic", "python", "linux", "google", "sure", "noted", "great", "good", "nice", "yes", "done",
})


def keep(turn: dict[str, Any], *, names: set[str]) -> str | None:
    """Why *turn* is not kept (None: it is)."""
    user = str(turn.get("user") or "").strip()
    reply = str(turn.get("assistant") or "").strip()
    if not user or not reply:
        return "empty"
    if len(user) > 240 or len(reply) > 480:
        return "too long"
    if _SELF_EN.search(user) or _SELF_AR.search(user):
        return "about the person"
    folded = set(_fold(f"{user} {reply}").split())
    if folded & (names - _NOT_NAMES):
        return "names the persona"
    return None


def _prompt_sha256() -> str:
    return hashlib.sha256("\n".join((PROMPT_EN, PROMPT_AR, SMALLTALK_EN, SMALLTALK_AR)).encode()).hexdigest()


def _ask(client: Any, *, base_url: str, model: str, key: str, prompt: str) -> tuple[list[dict[str, Any]], dict]:
    body: dict[str, Any] = {
        "model": model, "temperature": 1.0, "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
        # DeepSeek's own switch for hidden reasoning: it counts against max_tokens.
        "thinking": {"type": "disabled"},
    }
    for attempt in range(4):
        reply = client.post(f"{base_url.rstrip('/')}/chat/completions", json=body,
                            headers={"Authorization": f"Bearer {key}"}, timeout=180)
        if reply.status_code == 400 and "thinking" in body:
            body.pop("thinking")
            continue
        if reply.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(10 * (attempt + 1))
            continue
        reply.raise_for_status()
        data = reply.json()
        text = data["choices"][0]["message"]["content"]
        turns = json.loads(text).get("turns") or []
        return [t for t in turns if isinstance(t, dict)], data.get("usage") or {}
    raise RuntimeError("the model did not answer")


def _write(out: Path, record: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for attempt in range(10):
        try:
            os.replace(tmp, out)
            return
        except PermissionError:
            # Windows: a scanner or a reader holds the file a moment.
            if attempt == 9:
                raise
            time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    import httpx

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--limit", type=int, default=400, help="model calls at most")
    p.add_argument("--model", default=os.environ.get("KAZMA_POOL_MODEL", "deepseek-flash"))
    p.add_argument("--base-url", default=os.environ.get("KAZMA_POOL_BASE_URL", "https://api.deepseek.com"))
    p.add_argument("--rounds", type=int, default=2, help="askings per subtopic")
    p.add_argument("--workers", type=int, default=6, help="calls at once")
    args = p.parse_args(argv)
    # Progress names Arabic subtopics; the console may not be UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("set DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    names = persona_words(json.loads(DATASET.read_text(encoding="utf-8")))
    record: dict[str, Any] = {"version": 1, "model": args.model, "provider": args.base_url.split("//")[-1].split("/")[0],
                              "prompt_sha256": _prompt_sha256(), "asked": [], "usage": {}, "dropped": {}, "turns": []}
    if args.out.is_file():
        held = json.loads(args.out.read_text(encoding="utf-8"))
        if held.get("prompt_sha256") == record["prompt_sha256"]:
            record = held
    seen = {_fold(t["user"]) for t in record["turns"]}
    asked = set(record["asked"])
    todo = [(f"{round_}|{group}|{lang}|{topic}", group, lang, topic)
            for round_ in range(args.rounds) for group, lang, topic in SUBTOPICS
            if f"{round_}|{group}|{lang}|{topic}" not in asked][: args.limit]

    def one(job: tuple[str, str, str, str]) -> tuple[tuple[str, str, str, str], list[dict[str, Any]], dict]:
        _tag, group, lang, topic = job
        template = (SMALLTALK_AR if lang == "ar" else SMALLTALK_EN) if group == "filler" else (
            PROMPT_AR if lang == "ar" else PROMPT_EN)
        turns, usage = _ask(client, base_url=args.base_url, model=args.model, key=key,
                            prompt=template.format(n=PER_CALL, topic=topic))
        return job, turns, usage

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with httpx.Client(follow_redirects=False, trust_env=False) as client,             ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(one, job) for job in todo]):
            try:
                (tag, group, lang, _topic), turns, usage = future.result()
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
                print(f"a call failed ({type(exc).__name__}); run again to ask it again", flush=True)
                continue
            for k, v in usage.items():
                if isinstance(v, int):
                    record["usage"][k] = record["usage"].get(k, 0) + v
            for t in turns:
                why = keep(t, names=names)
                folded = _fold(str(t.get("user") or ""))
                if why is None and folded in seen:
                    why = "repeat"
                if why:
                    record["dropped"][why] = record["dropped"].get(why, 0) + 1
                    continue
                seen.add(folded)
                record["turns"].append({"group": group, "lang": lang, "user": t["user"].strip(),
                                        "assistant": t["assistant"].strip()})
            record["asked"].append(tag)
            record["calls"] = record.get("calls", 0) + 1
            record["made_on"] = datetime.now(UTC).date().isoformat()
            _write(args.out, record)
            print(f"{tag}: {len(record['turns'])} turns", flush=True)
    print(json.dumps({"turns": len(record["turns"]), "calls": record.get("calls"), "usage": record["usage"],
                      "dropped": record["dropped"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
