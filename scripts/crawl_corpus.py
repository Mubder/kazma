"""Record real sites for the crawl corpus, and lock what a crawl of each does.

    python scripts/crawl_corpus.py record [name ...]   # real network
    python scripts/crawl_corpus.py lock                 # replay, write expected.json
    python scripts/crawl_corpus.py show [name ...]      # replay, print, write nothing

``record`` crawls each site of ``tests/fixtures/crawl_corpus/sites.json`` (all,
or the names given) for real -- robots.txt honoured, as KazmaBot, Chromium off
-- in a private data folder, and keeps every HTTP response it received
(``tests/_crawl_replay.py``). ``lock`` replays every recording through the
product's crawl and writes the outcomes ``tests/test_crawl_corpus.py`` holds
the product to. Review the diff of ``expected.json`` before committing it:
a page that went from ingested to failed is a regression, not a new truth.

Env-free: it reads no ``.env`` and never opens the install's data. It sets a
temporary data folder and SQLite before Kazma is imported.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _isolate() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="kazma-crawl-corpus-"))
    os.environ["KAZMA_DATA_DIR"] = str(folder / "data")
    os.environ["KAZMA_DB_BACKEND"] = "sqlite"
    os.environ.pop("KAZMA_DATABASE_URL", None)
    os.environ["KAZMA_ACQUISITION_MODE"] = "local_only"
    os.environ["KAZMA_EMBEDDER_PREWARM"] = "0"
    sys.path.insert(0, str(ROOT))
    return folder


async def _record(site: dict, folder: Path) -> dict:
    import pytest
    from kazma_core.web_acquire import robots

    from tests import _crawl_replay as cr

    sink: dict = {}
    with pytest.MonkeyPatch.context() as mp, cr.recording_to(mp, sink):
        # Each site reads its own robots.txt: a rule kept from an earlier site
        # of the same host would leave it out of this recording.
        mp.setattr(robots, "_MANAGER", robots._RobotsManager())
        outcome = await cr.crawl(site, folder / site["name"], mp)
    return {"seed": site["seed"], "scope": site["scope"], "max_pages": site["max_pages"],
            "recorded_at": time.strftime("%Y-%m-%d"), "responses": sink, "outcome_when_recorded": outcome}


async def _replay(site: dict, folder: Path) -> dict:
    import pytest

    from tests import _crawl_replay as cr

    with pytest.MonkeyPatch.context() as mp, cr.replaying(mp, cr.load(site["name"])) as transport:
        outcome = await cr.crawl(site, folder / site["name"], mp)
    if transport.missing:
        raise SystemExit(f"{site['name']}: the recording lacks {transport.missing[:3]}; record it again")
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("record", "lock", "show"))
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    folder = _isolate()
    from tests import _crawl_replay as cr

    chosen = [s for s in cr.sites() if not args.names or s["name"] in args.names]
    if args.command == "record":
        for site in chosen:
            print(f"recording {site['name']} ...", flush=True)
            recording = asyncio.run(_record(site, folder))
            cr.save(site["name"], recording)
            print(f"  {len(recording['responses'])} responses; {recording['outcome_when_recorded']['status']}")
        return 0
    results = {site["name"]: asyncio.run(_replay(site, folder)) for site in chosen}
    if args.command == "show":
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    expected = cr.expected()
    expected.update(results)
    cr.EXPECTED.write_text(json.dumps(expected, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(f"locked {len(results)} site(s) into {cr.EXPECTED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
