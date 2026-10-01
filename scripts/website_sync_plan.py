#!/usr/bin/env python
"""What kazma.ai must add, update, remove or translate to match this repository.

The website (``Mubder/KazmaAI``) mirrors this repository's documentation.
``docs/website-pages.json`` says where each page is published (or why it is
not); the site records, in ``src/data/docs-sync.json``, the exact content (git
blob) of each source it last copied. Comparing the two answers "what changed"
page by page, without trusting anyone's memory of the last sync.

Usage (from anywhere; both checkouts on disk, both pulled):
    python scripts/website_sync_plan.py --site ../KazmaAI
    python scripts/website_sync_plan.py --site ../KazmaAI --assume-synced-at <commit>
    python scripts/website_sync_plan.py --site ../KazmaAI --mark-synced <page> [...]
    python scripts/website_sync_plan.py --site ../KazmaAI --mark-synced-all
    python scripts/website_sync_plan.py --site ../KazmaAI --check     # exit 1 unless in sync

``--assume-synced-at`` is for a page with no record yet: it is taken to hold
the source as it was at that commit. ``--mark-synced`` records the current
content of the named pages (source path or site path) once both languages are
done; ``--mark-synced-all`` does it for every page present in both languages.
Recording refuses, all or nothing, a page whose English is built differently
from its source or whose Arabic is built differently from its English
(headings, code blocks, table rows, asides, and the code itself, which is
copied exactly), or whose Arabic prose is mostly English (a copy, never
translated): a record claims the page holds its source. A page marked ``adapted`` in the map is written for the site, not
copied, and is compared with its other language only.
The procedure around it: docs/docs/ops/website-sync.md.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = "docs/website-pages.json"
GITHUB_REPO_URL = "https://github.com/Mubder/kazma/"
RECORD_SCHEMA = 1

_LINK = re.compile(r"\]\(([^)\s]+)")
_SIDEBAR_SLUG = re.compile(r"""slug:\s*['"]([^'"]+)['"]""")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")

#: An Arabic page whose prose letters are less than half Arabic is the English
#: page, copied. Measured 2026-10-01: translated pages 66 % and up, the eight
#: copies 0-38 % (their structure matched, so the drift checks passed them).
ARABIC_MIN_SHARE = 0.5
#: Below this many letters of prose, a page is too short to judge.
_ARABIC_MIN_LETTERS = 100
_NOT_PROSE = re.compile(r"`[^`\n]*`|\]\([^)]*\)|<[^>\n]*>|\{#[^}]*\}")


class PlanError(RuntimeError):
    """The plan could not be made; the message says why."""


# ── Reading the two repositories ────────────────────────────────────────────


def _git(framework: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(framework), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def blob_at(framework: Path, ref: str, path: str) -> str | None:
    """The git blob id of *path* at *ref*, or None when it is not there."""
    out = _git(framework, "rev-parse", "--verify", "--quiet", f"{ref}:{path}")
    return out.stdout.strip() or None


def blob_text(framework: Path, blob: str) -> str:
    out = _git(framework, "cat-file", "-p", blob)
    return out.stdout if out.returncode == 0 else ""


def path_exists(framework: Path, path: str) -> bool:
    return _git(framework, "cat-file", "-e", f"HEAD:{path}").returncode == 0


def load_manifest(framework: Path) -> dict:
    try:
        return json.loads((framework / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanError(f"cannot read {MANIFEST_PATH} in {framework}: {exc}") from exc


def load_record(site: Path, manifest: dict) -> dict:
    path = site / manifest["site"]["sync_record"]
    if not path.is_file():
        return {"schema_version": RECORD_SCHEMA, "framework_commit": None, "pages": {}}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PlanError(f"{path} is not JSON: {exc}") from exc
    record.setdefault("pages", {})
    return record


def site_page(site: Path, root: str, slug: str) -> Path | None:
    for ext in (".md", ".mdx"):
        candidate = site / root / f"{slug}{ext}"
        if candidate.is_file():
            return candidate
    return None


def site_slugs(site: Path, root: str, exclude: str | None = None) -> set[str]:
    """Every page under *root*, as a site path (``ops/x``), minus *exclude*."""
    base = site / root
    slugs = set()
    for path in base.rglob("*"):
        if path.suffix not in (".md", ".mdx") or not path.is_file():
            continue
        rel = path.relative_to(base).with_suffix("").as_posix()
        if exclude and (rel == exclude or rel.startswith(exclude + "/")):
            continue
        slugs.add(rel)
    return slugs


# ── What the pages hold ─────────────────────────────────────────────────────


def _body(text: str) -> str:
    """The page without its frontmatter."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :]
    return text


def _lines(text: str):
    """``(line, kind)`` for each line of the page body: ``open`` and ``close``
    for a code fence, ``code`` inside one, ``prose`` otherwise.

    A backtick fence's info string holds no backtick, so a line opening with
    ```` ```plan ```` is inline code, not a fence; and a fence closes on its
    own character, at least as long, with nothing after it. Toggling on any
    line starting with three backticks took that inline code for a fence and
    checked none of the links after it (task-ledger, 2026-10-01).
    """
    fence = None
    for line in _body(text).splitlines():
        m = _FENCE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = (m.group(1)[0], len(m.group(1)))
                yield line, "open"
            else:
                yield line, "prose"
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not m.group(2).strip():
            fence = None
            yield line, "close"
        else:
            yield line, "code"


def arabic_share(text: str) -> float | None:
    """The share of right-to-left letters among the letters of a page's prose
    (code, inline code, link targets, tags and heading ids left out), by the
    Unicode bidi class, as ``kazma_core.documents.arabic`` decides direction.
    ``None`` for a page with too little prose to judge."""
    rtl = ltr = 0
    for line, kind in _lines(text):
        if kind != "prose":
            continue
        for ch in _NOT_PROSE.sub(" ", line):
            if ch.isalpha():
                bidi = unicodedata.bidirectional(ch)
                if bidi in ("R", "AL"):
                    rtl += 1
                elif bidi == "L":
                    ltr += 1
    if rtl + ltr < _ARABIC_MIN_LETTERS:
        return None
    return rtl / (rtl + ltr)


def untranslated(text: str) -> float | None:
    """The Arabic share of an Arabic page that is mostly not Arabic, else None."""
    share = arabic_share(text)
    return share if share is not None and share < ARABIC_MIN_SHARE else None


def structure(text: str) -> dict[str, int]:
    """Headings, code blocks, table rows and asides: a translation keeps them."""
    counts = {"headings": 0, "code blocks": 0, "table rows": 0, "asides": 0}
    for line, kind in _lines(text):
        if kind == "open":
            counts["code blocks"] += 1
        if kind != "prose":
            continue
        stripped = line.strip()
        if re.match(r"#{1,6}\s", stripped):
            counts["headings"] += 1
        elif stripped.startswith("|"):
            counts["table rows"] += 1
        elif stripped.startswith(":::") and len(stripped) > 3:
            counts["asides"] += 1
    return counts


def _differs(a: dict[str, int], b: dict[str, int], a_name: str, b_name: str) -> dict:
    """The counts two pages disagree on, as ``{kind: {a_name: n, b_name: m}}``."""
    return {k: {a_name: a[k], b_name: b[k]} for k in a if a[k] != b[k]}


def code_blocks(text: str) -> list[str]:
    """The text of each fenced code block, trailing spaces dropped."""
    blocks, current = [], []
    for line, kind in _lines(text):
        if kind == "code":
            current.append(line.rstrip())
        elif kind == "close":
            blocks.append("\n".join(current))
            current = []
    return blocks


def compare(a_text: str, b_text: str, a_name: str, b_name: str) -> dict:
    """What two versions of a page disagree on: their structure, and their code.

    Code is copied exactly into both languages, so a code block whose text
    changed is a difference even when every count agrees.
    """
    differs = _differs(structure(a_text), structure(b_text), a_name, b_name)
    a_code, b_code = code_blocks(a_text), code_blocks(b_text)
    if len(a_code) == len(b_code):
        changed = sum(1 for x, y in zip(a_code, b_code) if x != y)
        if changed:
            differs["code"] = {"blocks changed": changed}
    return differs


def counts_line(slug: str, differs: dict) -> str:
    """``page: headings source 9 / en 7, ...`` -- one line per drifting page."""
    return f"{slug}: " + ", ".join(
        f"{kind} " + " / ".join(f"{side} {n}" for side, n in sides.items())
        for kind, sides in differs.items()
    )


def links(text: str) -> list[str]:
    """Markdown link targets outside code blocks."""
    return [target for line, kind in _lines(text) if kind == "prose"
            for target in _LINK.findall(line)]


def headings(text: str, level: str = "## ") -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith(level)]


# ── The plan ────────────────────────────────────────────────────────────────


@dataclass
class Plan:
    framework_commit: str
    record_commit: str | None
    add: list[dict] = field(default_factory=list)
    update: list[dict] = field(default_factory=list)
    remove: list[dict] = field(default_factory=list)
    site_only: list[str] = field(default_factory=list)
    arabic_missing: list[str] = field(default_factory=list)
    arabic_orphans: list[str] = field(default_factory=list)
    arabic_drift: list[dict] = field(default_factory=list)
    arabic_untranslated: list[dict] = field(default_factory=list)
    english_drift: list[dict] = field(default_factory=list)
    sidebar_missing: list[str] = field(default_factory=list)
    sidebar_dead: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    watch: list[dict] = field(default_factory=list)

    def work(self) -> int:
        return sum(
            len(v) for k, v in self.__dict__.items() if isinstance(v, list)
        )


def make_plan(framework: Path, site: Path, assume_at: str | None = None) -> Plan:
    manifest = load_manifest(framework)
    record = load_record(site, manifest)
    layout = manifest["site"]
    docs_dir, ar_dir = layout["docs_dir"], layout["arabic_dir"]
    ar_prefix = Path(ar_dir).relative_to(docs_dir).as_posix()
    head = _git(framework, "rev-parse", "HEAD").stdout.strip()
    if not head:
        raise PlanError(f"{framework} is not a git checkout")
    plan = Plan(framework_commit=head, record_commit=record.get("framework_commit"))

    try:
        sidebar_text = (site / layout["sidebar_file"]).read_text(encoding="utf-8")
    except OSError:
        sidebar_text = ""
    sidebar = set(_SIDEBAR_SLUG.findall(sidebar_text))

    published: dict[str, str] = {}
    for entry in manifest["pages"]:
        source, slug = entry["source"], entry.get("site")
        current = blob_at(framework, "HEAD", source)
        recorded = (record["pages"].get(source) or {}).get("blob")
        if recorded is None and assume_at:
            recorded = blob_at(framework, assume_at, source)
        if slug:
            published[slug] = source
            en = site_page(site, docs_dir, slug)
            ar = site_page(site, ar_dir, slug)
            if en is None:
                plan.add.append({"site": slug, "source": source})
            elif recorded != current:
                plan.update.append(
                    {
                        "site": slug,
                        "source": source,
                        "from": recorded,
                        "to": current,
                        "diff": (
                            f"git -C {framework} diff {recorded} {current}"
                            if recorded
                            else "no record: compare the whole page with the source"
                        ),
                    }
                )
            if en is not None and ar is None:
                plan.arabic_missing.append(slug)
            if en is not None and current and not entry.get("adapted"):
                differs = compare(blob_text(framework, current), en.read_text(encoding="utf-8"), "source", "en")
                if differs:
                    plan.english_drift.append({"site": slug, "differs": differs})
            if en is not None and ar is not None:
                ar_text = ar.read_text(encoding="utf-8")
                differs = compare(en.read_text(encoding="utf-8"), ar_text, "en", "ar")
                if differs:
                    plan.arabic_drift.append({"site": slug, "differs": differs})
                share = untranslated(ar_text)
                if share is not None:
                    plan.arabic_untranslated.append({"site": slug, "share": share})
            wanted = "docs" if slug == "index" else f"docs/{slug}"
            if en is not None and wanted not in sidebar:
                plan.sidebar_missing.append(wanted)
        elif entry.get("watch"):
            if recorded != current:
                item = {"source": source, "watch": entry["watch"], "from": recorded, "to": current}
                if entry["watch"] == "news" and current:
                    before = set(headings(blob_text(framework, recorded))) if recorded else set()
                    item["new_entries"] = [
                        h for h in headings(blob_text(framework, current)) if h not in before
                    ]
                plan.watch.append(item)
        else:
            was = (record["pages"].get(source) or {}).get("site")
            if was and site_page(site, docs_dir, was) is not None:
                plan.remove.append({"site": was, "source": source, "why": entry.get("why", "")})

    # Recorded pages whose source left this repository or the map.
    mapped = {e["source"] for e in manifest["pages"]}
    for source, rec in record["pages"].items():
        was = (rec or {}).get("site")
        if source not in mapped and was and site_page(site, docs_dir, was) is not None:
            plan.remove.append({"site": was, "source": source, "why": "the source was removed or renamed"})
    removing = {item["site"] for item in plan.remove}

    english = site_slugs(site, docs_dir, exclude=ar_prefix)
    plan.site_only = sorted(english - set(published) - removing)
    plan.arabic_orphans = sorted(site_slugs(site, ar_dir) - english)

    for slug in sorted(sidebar):
        if slug == "docs":
            target = "index"
        elif slug.startswith("docs/"):
            target = slug[len("docs/") :]
        else:
            continue
        if site_page(site, docs_dir, target) is None:
            plan.sidebar_dead.append(slug)

    for root, lang in ((docs_dir, "en"), (ar_dir, "ar")):
        for slug in sorted(site_slugs(site, root, exclude=ar_prefix if lang == "en" else None)):
            page = site_page(site, root, slug)
            for target in links(page.read_text(encoding="utf-8")):
                problem = _link_problem(framework, site, docs_dir, ar_dir, target,
                                        from_arabic=lang == "ar")
                if problem:
                    plan.links.append({"page": f"{lang}:{slug}", "link": target, "problem": problem})
    return plan


def _link_problem(
    framework: Path, site: Path, docs_dir: str, ar_dir: str, target: str,
    *, from_arabic: bool = False,
) -> str | None:
    url = target.split("#", 1)[0].split("?", 1)[0]
    if url in ("/docs", "/ar/docs") or url.startswith(("/docs/", "/ar/docs/")):
        arabic = url.startswith("/ar/")
        rel = url[len("/ar/docs") :] if arabic else url[len("/docs") :]
        rel = rel.strip("/") or "index"
        if site_page(site, ar_dir if arabic else docs_dir, rel) is None:
            return "no such page on the site"
        # Translations copied the English links: 140 on 2026-10-01.
        if from_arabic and not arabic and site_page(site, ar_dir, rel) is not None:
            return f"an Arabic page leads to the English page: link /ar{target}"
        return None
    for kind in ("blob/main/", "tree/main/"):
        prefix = GITHUB_REPO_URL + kind
        if url.startswith(prefix):
            path = url[len(prefix) :].rstrip("/")
            if path and not path_exists(framework, path):
                return "no such path in Mubder/kazma"
            return None
    if not url.startswith(("http://", "https://", "mailto:", "/")) and re.search(r"\.mdx?$", url):
        return "a link left in the source's form: point it at /docs/<page>/ or the GitHub file"
    return None


# ── Output ──────────────────────────────────────────────────────────────────


def render(plan: Plan, framework: Path, site: Path) -> str:
    out = [f"kazma.ai sync plan: framework {plan.framework_commit[:8]}, site {site}"]
    out.append(
        f"Last recorded sync: {plan.record_commit[:8]}"
        if plan.record_commit
        else "No sync recorded yet (--assume-synced-at <commit> for a first run)."
    )

    def section(title: str, rows: list[str]) -> None:
        if rows:
            out.append(f"\n{title} ({len(rows)})")
            out.extend(f"  {row}" for row in rows)

    section("ADD: publish, in English and Arabic, with a sidebar entry",
            [f"{a['site']}  <-  {a['source']}" for a in plan.add])
    section("UPDATE: the source changed since the site copied it",
            [f"{u['site']}  <-  {u['source']}\n      {u['diff']}" for u in plan.update])
    section("REMOVE: delete both languages and the sidebar entry, add a redirect",
            [f"{r['site']}  ({r['why']})" for r in plan.remove])
    section("SITE-ONLY: no source in this repository; remove it, or ask for a source page first",
            plan.site_only)
    section("ARABIC MISSING", plan.arabic_missing)
    section("ARABIC ORPHANS: an Arabic page with no English one", plan.arabic_orphans)
    section("ENGLISH DRIFT: the English page is built differently from its source (not ported, or in part)",
            [counts_line(d["site"], d["differs"]) for d in plan.english_drift])
    section("ARABIC DRIFT: the Arabic page is built differently from the English one",
            [counts_line(d["site"], d["differs"]) for d in plan.arabic_drift])
    section("ARABIC UNTRANSLATED: the Arabic page's prose is mostly English; translate it",
            [f"{u['site']}  ({u['share']:.0%} Arabic)" for u in plan.arabic_untranslated])
    section("SIDEBAR MISSING (astro.config.mjs)", plan.sidebar_missing)
    section("SIDEBAR DEAD: points at no page", plan.sidebar_dead)
    section("LINKS", [f"{l['page']}: {l['link']}  ({l['problem']})" for l in plan.links])
    for w in plan.watch:
        what = {
            "claims": "re-check every claim on the site against it (docs/docs/ops/website-sync.md)",
            "news": "new entries the site may announce",
        }.get(w["watch"], w["watch"])
        rows = [f"git -C {framework} diff {w['from']} {w['to']}" if w["from"] else "no record: read it whole"]
        rows += w.get("new_entries", [])
        section(f"{w['watch'].upper()}: {w['source']} changed; {what}", rows)
    if not plan.work():
        out.append("\nNothing to do: the site matches this repository.")
    return "\n".join(out) + "\n"


def mark_synced(
    framework: Path, site: Path, names: list[str], every: bool = False
) -> tuple[list[str], str]:
    """Record the current content of the named pages.

    Returns the sources marked and the framework commit they were read at.
    Entries of pages that left the map or the site are dropped.
    """
    manifest = load_manifest(framework)
    record = load_record(site, manifest)
    layout = manifest["site"]
    head = _git(framework, "rev-parse", "HEAD").stdout.strip()
    by_name = {}
    for entry in manifest["pages"]:
        by_name[entry["source"]] = entry
        if entry.get("site"):
            by_name[entry["site"]] = entry
    if every:
        chosen = [
            e for e in manifest["pages"]
            if e.get("watch")
            or (e.get("site")
                and site_page(site, layout["docs_dir"], e["site"]) is not None
                and site_page(site, layout["arabic_dir"], e["site"]) is not None)
        ]
    else:
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise PlanError(f"not in {MANIFEST_PATH}: {', '.join(unknown)}")
        chosen = [by_name[n] for n in names]
        for entry in chosen:
            slug = entry.get("site")
            if slug and (
                site_page(site, layout["docs_dir"], slug) is None
                or site_page(site, layout["arabic_dir"], slug) is None
            ):
                raise PlanError(f"{slug} is not on the site in both languages yet")
    # A record says "this page holds its source"; refuse one that visibly does
    # not: an English page built differently from its source, or an Arabic
    # page built differently from the English one. All or nothing.
    refused = []
    for entry in chosen:
        slug = entry.get("site")
        if not slug:
            continue
        en_text = site_page(site, layout["docs_dir"], slug).read_text(encoding="utf-8")
        ar_text = site_page(site, layout["arabic_dir"], slug).read_text(encoding="utf-8")
        current = blob_at(framework, "HEAD", entry["source"])
        source_text = blob_text(framework, current) if current else ""
        checks = [compare(en_text, ar_text, "en", "ar")]
        if not entry.get("adapted"):
            checks.insert(0, compare(source_text, en_text, "source", "en"))
        for differs in checks:
            if differs:
                refused.append(counts_line(slug, differs))
        share = untranslated(ar_text)
        if share is not None:
            refused.append(f"{slug}: the Arabic page is {share:.0%} Arabic -- translate it")
    if refused:
        raise PlanError(
            "not recorded -- these pages do not match yet (headings, code blocks, "
            "table rows, asides, code, an untranslated Arabic page):\n  "
            + "\n  ".join(refused)
        )
    today = date.today().isoformat()
    for entry in chosen:
        record["pages"][entry["source"]] = {
            "site": entry.get("site"),
            "blob": blob_at(framework, "HEAD", entry["source"]),
            "synced_on": today,
        }
    mapped = {e["source"] for e in manifest["pages"]}
    record["pages"] = {
        source: rec
        for source, rec in record["pages"].items()
        if source in mapped
        and (not rec.get("site") or site_page(site, layout["docs_dir"], rec["site"]) is not None)
    }
    record["schema_version"] = RECORD_SCHEMA
    record["framework_commit"] = head
    path = site / layout["sync_record"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return [entry["source"] for entry in chosen], head


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--site", type=Path, required=True, help="the KazmaAI checkout")
    parser.add_argument("--framework", type=Path, default=REPO_ROOT, help="the kazma checkout (default: this one)")
    parser.add_argument("--assume-synced-at", metavar="COMMIT", help="for pages with no record: the commit the site copied")
    parser.add_argument("--mark-synced", nargs="+", metavar="PAGE", help="record these pages (source or site path) as synced")
    parser.add_argument("--mark-synced-all", action="store_true", help="record every page present in both languages")
    parser.add_argument("--json", action="store_true", help="print the plan as JSON")
    parser.add_argument("--check", action="store_true", help="exit 1 unless the site is in sync")
    args = parser.parse_args(argv)
    try:
        if args.mark_synced or args.mark_synced_all:
            marked, head = mark_synced(
                args.framework, args.site, args.mark_synced or [], every=args.mark_synced_all
            )
            print(f"recorded {len(marked)} source(s) as synced at framework {head[:8]}")
            return 0
        plan = make_plan(args.framework, args.site, assume_at=args.assume_synced_at)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(plan.__dict__, indent=2, ensure_ascii=False))
    else:
        print(render(plan, args.framework, args.site), end="")
    return 1 if args.check and plan.work() else 0


if __name__ == "__main__":
    raise SystemExit(main())
