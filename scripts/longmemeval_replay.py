#!/usr/bin/env python3
"""The facts pilot's stores written again from its kept replies, through this
checkout's own write path (the memory plan's W6, step 5): no model call.

A change of how Kazma applies what its deep pass says (which facts the pass is
shown, the names facts are kept under, what replaces what) is measured from
cache: each question's history is written round by round as
``longmemeval_facts_pilot.py extract`` writes it -- the episode, the quick
pass, the product's deep-pass task -- and the model's reply for the round is
the one the extraction kept. Its map (``<run>/map/<id>.json``, one entry a
round: the reply, and the facts the round's pass was shown) says what each
round said; ``extract`` writes it.

The reply's "replaces f2" names the second fact the pass was SHOWN then. This
code may number and choose its facts otherwise, so the replay finds that fact
by what it was (subject and value, the name first) among the facts this code
shows; a fact this code holds but did not show is handed to the pass as one
more numbered fact, so the stores differ only by how they are written, never
by what the model saw (``refs_added`` counts those; ``refs_lost`` a fact this
code no longer holds). A round whose reply was not kept is counted
(``no_reply``) and never asked::

    python scripts/longmemeval_replay.py --data DIR --map RUN --label LABEL [--questions pilot|dev] [--procs N]

The stores land in ``DIR/cache/facts-pilot/<prompt>-LABEL`` with a ``done``
file a question (its counts and what wrote it), where ``longmemeval_written.py``
and the blocks read them. Everything holding the dataset's text stays in the
data folder, never in the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import multiprocessing as mp
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import longmemeval_facts_pilot as pilot  # noqa: E402

#: The round the running task replays: its map entry and the numbers this
#: code gave the facts the reply names.
_ROUND: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("replay_round", default=None)


class NoReplyKeptError(RuntimeError):
    """The map holds no reply for this round: it is never asked."""


def parse_reply(content: str | None) -> dict[str, Any] | None:
    """A kept reply's JSON (``{"beliefs": [...]}``), its code fence taken
    off; None for a reply the deep pass could not read either."""
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"(?<!\s)\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("beliefs"), list) else None


def read_map(folder: Path, qid: str) -> dict[str, dict[str, Any]]:
    """Episode id -> its round in *qid*'s map. A session the dataset holds
    twice gives an episode two entries; the second has no reply (its pass
    was done): the first's is kept."""
    out: dict[str, dict[str, Any]] = {}
    for entry in json.loads((folder / f"{qid}.json").read_text(encoding="utf-8"))["rounds"]:
        if entry.get("content") is not None or entry["episode"] not in out:
            out[entry["episode"]] = entry
    return out


def renumber(entry: dict[str, Any], shown: list[dict[str, Any]], conn: Any, tenant_id: str,
             counts: dict[str, int]) -> dict[str, str]:
    """The kept reply's fact numbers in this code's numbering: each fact the
    reply says it replaces, found by what it was (subject and value, the
    same name first) among *shown*, else among the facts held now -- added
    to *shown* as one more numbered fact."""
    data = parse_reply(entry.get("content"))
    was = {str(h.get("ref")): h for h in entry.get("held") or []}
    refs: dict[str, str] = {}
    for belief in (data or {}).get("beliefs") or []:
        ref = str((belief or {}).get("replaces") or "").strip().lower() if isinstance(belief, dict) else ""
        old = was.get(ref)
        if not old or ref in refs:
            continue
        same = sorted((h for h in shown if h["subject"] == old["subject"] and h["object"] == old["object"]),
                      key=lambda h: h["predicate"] != old["predicate"])
        if same:
            refs[ref] = same[0]["ref"]
            continue
        row = conn.execute(
            "SELECT id, subject, predicate, predicate_type, object, extraction_method FROM beliefs WHERE "
            "tenant_id = ? AND subject = ? AND object = ? AND valid_until IS NULL AND invalidated_at IS NULL "
            "ORDER BY predicate != ?, valid_from DESC LIMIT 1",
            (tenant_id, old["subject"], old["object"], old["predicate"])).fetchone()
        if row is None:
            continue
        new = f"f{len(shown) + 1}"
        shown.append({"ref": new, "id": row[0], "subject": row[1], "shown": row[1], "predicate": row[2],
                      "predicate_type": row[3] or "set", "object": row[4], "method": row[5]})
        refs[ref] = new
        counts["refs_added"] += 1
    return refs


class ReplayModel:
    """The deep pass's model: the round's kept reply, its "replaces" numbers
    in this code's numbering (a number this code cannot place is dropped and
    counted)."""

    provider_name = "replay"

    def __init__(self, model: str, counts: dict[str, int]) -> None:
        self.config = type("Config", (), {"model": model})()
        self.counts = counts

    async def chat(self, messages: Any, **_kw: Any) -> Any:
        from kazma_core.llm_provider import LLMResponse

        rec = _ROUND.get()
        if rec is None or rec["entry"].get("content") is None:
            raise NoReplyKeptError("no reply was kept for this round")
        data = parse_reply(rec["entry"]["content"])
        if data is None:
            return LLMResponse(content=rec["entry"]["content"], model=self.config.model)
        for belief in data["beliefs"]:
            if isinstance(belief, dict) and belief.get("replaces"):
                ref = rec["refs"].get(str(belief["replaces"]).strip().lower())
                if ref:
                    belief["replaces"] = ref
                else:
                    belief.pop("replaces")
                    self.counts["refs_lost"] += 1
        return LLMResponse(content=json.dumps(data, ensure_ascii=False), model=self.config.model)


def _install(model: str, counts: dict[str, int], embedder: Any) -> None:
    """This process's routing: the question's store, the vectors, the
    replayed model, the facts shown renumbered for the kept reply."""
    import kazma_core.memory.belief_extractor as extractor
    import kazma_core.memory.embedder as embedding
    import kazma_core.memory.held_facts as where
    import kazma_core.model_registry as registry

    pilot._route_memory_paths()
    embedding.get_embedder = lambda *_a, **_k: embedder  # type: ignore[assignment]
    replayed = ReplayModel(model, counts)
    registry.get_model_registry = lambda: type(  # type: ignore[assignment]
        "Registry", (), {"get_client": lambda self, *_a, **_k: replayed})()
    extractor._turn_zone = lambda: UTC  # type: ignore[assignment]
    held = where.held_facts

    def held_facts(conn: Any, **kw: Any) -> list[dict[str, Any]]:
        shown = held(conn, **kw)
        rec = _ROUND.get()
        if rec is not None:
            rec["refs"] = renumber(rec["entry"], shown, conn, kw["tenant_id"], counts)
        return shown

    where.held_facts = held_facts  # type: ignore[assignment]


_STATE: dict[str, Any] = {}


def _init(data: Path, source: Path, folder: Path, model: str) -> None:
    pilot.bench._private_data_folder()
    pilot.bench._limit_cpu_threads()
    import logging

    logging.disable(logging.WARNING)
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    embedder = pilot.PilotEmbedder(get_embedder(), data / "cache" / "kazma_vectors.npz",
                                   get_embedding_model_name(), folder / "vectors-extra.npz")
    counts = {"refs_added": 0, "refs_lost": 0}
    _install(model, counts, embedder)
    _STATE.update(data=data, source=source, folder=folder, counts=counts, embedder=embedder,
                  written_with=written_with(source))


async def replay_question(item: dict[str, Any], rounds: dict[str, dict[str, Any]], folder: Path,
                          counts: dict[str, int]) -> dict[str, Any]:
    """*item*'s store written again from *rounds* (its map), as the pilot's
    ``extract`` writes it; its counts."""
    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.belief_extractor import is_filler_turn

    qid = str(item["question_id"])
    db, ops = folder / "stores" / f"{qid}.db", folder / "stores" / f"{qid}.ops.db"
    for path in (db, ops):
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    pilot._STORE.set((str(db), str(ops)))
    rows = await asyncio.to_thread(pilot._seed, item, db, ops)
    counts.update(refs_added=0, refs_lost=0)
    stats = {"rounds": len(rows), "quick": 0, "deep": 0, "deep_failed": 0, "no_reply": 0}
    started = time.monotonic()
    for row in rows:
        user = (row["user_text"] or "").strip()
        if not user or user.startswith("/"):
            continue  # the product writes no turn memory for these
        await asyncio.to_thread(pilot._quick_pass, str(db), str(ops), row)
        stats["quick"] += 1
        if is_filler_turn(user):
            continue  # small talk queues no deep pass
        entry = rounds.get(row["id"])
        if entry is None or entry.get("content") is None:
            stats["no_reply"] += 1
            continue
        token = _ROUND.set({"entry": entry, "refs": {}})
        try:
            ok = await worker_bootstrap._handle_micro_consolidation({"episode_id": row["id"]})
        finally:
            _ROUND.reset(token)
        stats["deep" if ok else "deep_failed"] += 1
    stats.update(await asyncio.to_thread(pilot.store_counts, db))
    stats.update(counts, seconds=round(time.monotonic() - started))
    return stats


def _one(qid: str) -> tuple[str, dict[str, Any]]:
    folder, source = _STATE["folder"], _STATE["source"]
    done = folder / "done" / f"{qid}.json"
    if done.is_file():
        return qid, {"held": True}
    if "items" not in _STATE:
        _STATE["items"] = {str(x["question_id"]): x for x in pilot.bench.load(_STATE["data"])}
    try:
        stats = asyncio.run(replay_question(_STATE["items"][qid], read_map(source, qid), folder,
                                            _STATE["counts"]))
    except Exception as exc:  # noqa: BLE001 - said in the run's output, the question left unwritten
        return qid, {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        _STATE["embedder"].save()
    stats["written_with"] = _STATE["written_with"]
    done.write_text(json.dumps(stats) + "\n", encoding="utf-8")
    return qid, stats


def written_with(source: Path) -> dict[str, Any]:
    """What a replayed store's facts were written with: the replies' run and
    this checkout's code."""
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True,
                            check=False).stdout.strip()
    return {"facts": "replayed", "replies": source.parent.name, "code": commit, "prompt": pilot.prompt_tag()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", required=True, type=Path, help="the folder LongMemEval was downloaded into")
    p.add_argument("--map", required=True, help="the run whose replies are replayed (its map/ folder)")
    p.add_argument("--label", required=True, help="the replayed run's name (its stores are kept apart)")
    p.add_argument("--model", default="replayed", help="the model the replies came from, for the store's records")
    p.add_argument("--vectors", default="", help="a run whose fact vectors are taken as a start (no model call)")
    p.add_argument("--questions", choices=("pilot", "dev"), default="pilot")
    p.add_argument("--procs", type=int, default=3, help="questions written at once, a process each")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    runs = args.data / "cache" / "facts-pilot"
    source = runs / args.map / "map"
    if not source.is_dir():
        print(f"{source}: no map; the run's extract writes one", file=sys.stderr)
        return 2
    folder = pilot.work_folder(args.data, args.label)
    for sub in ("stores", "done"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    start = runs / args.vectors / "vectors-extra.npz" if args.vectors else None
    if start is not None and start.is_file() and not (folder / "vectors-extra.npz").exists():
        shutil.copy2(start, folder / "vectors-extra.npz")
    ids = [q for q in pilot.pilot_ids(args.questions) if (source / f"{q}.json").is_file()][: args.limit or None]
    print(f"{len(ids)} questions replayed from {args.map} into {folder.name}, {args.procs} at once, "
          f"{datetime.now(UTC).strftime('%H:%M')} UTC", flush=True)
    failed = 0
    with mp.Pool(args.procs, initializer=_init, initargs=(args.data, source, folder, args.model)) as pool:
        for n, (qid, stats) in enumerate(pool.imap_unordered(_one, ids), 1):
            failed += "error" in stats
            print(n, qid, {k: v for k, v in stats.items() if k != "written_with"}, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
