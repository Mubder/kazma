#!/usr/bin/env python3
"""Knowledge Library scale and recovery measurement, and its multi-day soak.

Tracker row KB-Q01 (``docs/plans/KNOWLEDGE_INDUSTRIAL_HARDENING_GOAL.md``)
asks for Knowledge scale and recovery measured on named hardware, with
declared ceilings and no unbounded growth. This is the harness for it, not
the result. It runs the product's own Knowledge code on PRIVATE stores (a
temporary data directory unless ``--data-dir`` names one; never an
install's), forced to SQLite, one child process per step or cycle, and
nothing leaves the machine: a crawl's pages come from a generated English,
Arabic or mixed site served through the fetch seam the Knowledge tests use,
and any other connection is refused and reported.

scale
  For each size (``--chunks``) and language (``--languages``: en, ar,
  mixed), a fresh store is built through the publication path
  (``KnowledgeIndex.index`` -> ``KnowledgeStore.publish_page``, the store's
  one index from ``knowledge_index_for``) and measured: ingest throughput;
  keyword, meaning and hybrid search p50/p95/p99 over a fixed question set
  with planted answers the search must return (English and Arabic; the
  Arabic keyword query is vocalized, so the fold is exercised); the repair
  sweep's cost for a pass with nothing to do (seconds, rows listed); a
  backup of knowledge.db with the product's snapshot helper. Then the
  machine is "lost" (vectors and database removed: vectors are not backed
  up), the backup restored, and the re-embed RTO timed: ``run_repair_sweep``
  until every chunk has a vector of the active embedder again. Last, the
  library delete (``knowledge_lifecycle.delete_library``) is timed and must
  leave nothing. A store that reaches knowledge.db's 1 GiB quota stops
  ingesting there and says so (a declared ceiling, not a failure). A step
  that fails is recorded and the next one still runs.

breadth
  How wide the vector index must search as a library grows. For each size
  and language, one library's collection is built through the product's
  vector store alone (``VectorStore.index_many``: the scale step's corpus
  and planted questions, the scale step's library name, Kazma's HNSW
  graph), and the exact rank of every planted answer is computed over every
  vector. Then, for each search breadth (``--breadths``; ``auto`` is the
  breadth the product sets for the collection's size), a fresh process sets
  it before the index loads (Chroma applies a breadth when it loads an
  index) and asks the planted questions as a library search's meaning layer
  does (``--top-k`` x 4 candidates): answers found, answers ranked first,
  latency p50/p95/p99, and the load time. ``--keep-data`` keeps the stores
  and ``--reuse-builds`` measures them again with other breadths. Sizes
  that are multiples of 1,000 keep every process on one graph: Chroma
  writes an index to disk every 1,000 records and inserts the rest again,
  in parallel, at every load.

soak
  Cycle after cycle, each a fresh process on the same stores (a restart):
  crawls of new libraries and refreshes of old ones through the durable
  crawl journal (pages changed, added and removed from the site -- a crawl
  never deletes what it did not find -- and pages that fail and keep their
  previous version); document publications through the Documents adapter
  (``DocumentKnowledgeAdapter.index_document_ir``; a new version replaces
  the old one); library deletes; an embedding-model switch every
  ``--switch-every`` cycles (a new embedder identity, every second switch a
  new vector size too); a crash inside a crawl (the next start resumes it);
  the repair sweep; searches. Each of these happens within the first five
  cycles, so a short run covers them all. A last start resumes what is
  pending and checks everything.

Violations, from the stores (after every soak cycle that was not killed,
and for every scale step): a published chunk that is not active, or an
active one nobody published (lost, doubled, a stale page or document
version); a keyword index that does not hold exactly the active chunks
before the sweep repairs anything; an active chunk without a vector of the
active embedder, or a vector without a chunk, once the sweep had its budget
(after every change and after a model switch); a deleted library that left
a chunk, keyword row, coverage record, collection, fetched page
(``<data dir>/knowledge_provenance/<lib>``), running crawl or Documents
index record, or whose answers are still found; a planted answer the
keyword index or the hybrid search does not return (save the ranking case
below), a replaced one they still return, or one whose chunk has no
vector; a crawl that did not end as
planned; growth past a declared bound (the crawl journal's 24 jobs and 16
MiB, 5,000 fetched pages a library) and ``unified_items`` (retired by the
Memory audit) in any database; a vector-store folder no collection owns --
what a dropped Chroma collection leaves on disk (with
``--tolerate-orphan-vector-folders`` a counted finding instead); a deleted
library's words still in the keyword index's FTS5 segments (unsearchable,
but on disk); a restored database's coverage record claiming vectors that
do not exist yet; any connection off the machine. The soak stops at the
first violation. (The deleted words and the coverage record were findings
until the fixes of 2026-10-15, KB-Q01: the store now removes dropped
collections' folders, purges a deleted library's words and records which
vector store a coverage was measured on.)

Findings, recorded and counted, not violations: meaning-search recall below
100 % when every missed vector was present (the approximate HNSW index
passed over it), and the hybrid top-k then leaving out such an answer that
the keyword index returned (reciprocal rank fusion prefers chunks both
layers return: ranking, KQ01's ground); a meaning query that failed once
and answered when asked again; an index Chroma lost and the product
rebuilt from Chroma's log (``index_reloads``: the recovery worked, and it
is counted); a process that could not read a library's collection at all
(a Chroma internal error: "Error creating hnsw segment reader: Nothing
found on disk") when the next process reads it -- the next start is made
to ask that library, and a failure that survives the restart is a
violation, or with ``--tolerate-vector-read-failures`` a counted finding
(in ``scale``, the restore is run once more in a fresh process). The
product's log of a cycle with a violation or a read failure is kept beside
the plans (``cycle-<n>.log``).

Embedder (``--embedder``): ``hash`` (the default) is a deterministic local
embedder of 1,024 dimensions (bge-m3's size) made of hashed words, so the
numbers measure the store and the vector index, not a model.
``configured`` uses the install-style configured embedder only when it is
already on this machine (nothing is downloaded); its model switch
alternates with the hash embedder.

The report (``--report``, JSON) is rewritten whole after every cycle or
step (``status: running``), then once more (``passed`` or ``failed``). It
names the hardware (CPU, cores, RAM, the data directory's disk, OS, Python,
SQLite, Chroma) and the embedder, and holds every measurement, the ceilings
observed (rows per table, file sizes), every violation and finding, and in
a soak the operations done, growth samples and search latency. Exit 0 when
healthy, else 1.

Usage (``--hardware-name`` names the machine in the report):
  python scripts/knowledge_soak.py scale --chunks 10000,100000,1000000 --report kb-scale.json
  python scripts/knowledge_soak.py breadth --chunks 10000,50000,100000,200000 --breadths 256,1024,4096,auto \
      --data-dir kb-breadth --keep-data --report kb-breadth.json
  python scripts/knowledge_soak.py soak --duration 259200 --report kb-soak.json
  python scripts/knowledge_soak.py soak --duration 300 --cycle-seconds 2 --switch-every 10 --report short.json
  (--tolerate-orphan-vector-folders --tolerate-vector-read-failures count those two Chroma defects instead
  of judging them: for measuring a build from before 2026-10-15, which did not work around them)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import hashlib
import json
import math
import os
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # the shared report helpers sit beside this file
from soak_report import RUNNING, finished, latency_summary, process_tree_sample, utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

_ROOT = _HERE.parent
_CORE = str(_ROOT / "kazma-core")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)  # this checkout's code, also in the children

_KILLED = 137
_OP = "KB-SOAK-OP "
_RESULT = "KB-SOAK-RESULT "
_TENANT = "default"
_ACTOR = "kb-soak"
#: Bounds the soak holds the stores to (the product's own constants).
_JOURNAL_MAX_JOBS = 24
_JOURNAL_MAX_BYTES = 16 * 1024**2
_PROVENANCE_MAX_FILES = 5000
#: The maintenance cadence a repair pass waits for in the product.
_CADENCE_S = 900.0
_STAGE_BUDGET = 5  # cycles within which every kind of operation has run once

# ── the generated corpus (English, Arabic, mixed) ───────────────────────────

_EN_WORDS = (
    "system service request response cluster node replica backup archive message queue worker "
    "scheduler storage volume network gateway router endpoint client server session cache index "
    "search record table column schema migration release version build deploy pipeline monitor "
    "metric alert trace event stream batch job task thread process memory disk file folder path "
    "setting option feature module package library plugin adapter provider document page section "
    "chapter paragraph summary detail example sample default value range field report dashboard "
    "panel button screen window user operator admin team project workspace repository branch commit "
    "change review patch issue ticket support guide manual tutorial reference overview install "
    "update upgrade restart reload shutdown startup health status ready check probe timeout retry "
    "backoff quota budget capacity throughput latency error warning failure success outcome result "
    "answer payload header body query filter sort order cursor offset region zone tenant account "
    "profile policy rule permission role audit journal ledger snapshot restore recovery mirror shard "
    "partition segment block buffer socket protocol format encoding parser renderer template layout "
    "theme locale"
).split()
_AR_WORDS = (
    "نظام خدمة طلب استجابة عقدة نسخة أرشيف رسالة طابور عامل جدولة تخزين شبكة بوابة موجه عميل خادم "
    "جلسة ذاكرة قرص ملف مجلد مسار إعداد خيار ميزة وحدة حزمة مكتبة مزود وثيقة صفحة قسم فصل فقرة جملة "
    "ملخص تفاصيل مثال قيمة نطاق حقل سجل تقرير لوحة زر شاشة نافذة مستخدم مشغل مدير مشروع مستودع فرع "
    "تغيير مراجعة تذكرة دعم دليل مرجع مقدمة تثبيت تحديث ترقية تشغيل إيقاف حالة جاهز فحص مهلة محاولة "
    "سعة إنتاجية خطأ تحذير فشل نجاح نتيجة جواب سؤال بيانات مؤشر بحث جدول عمود مخطط ترحيل إصدار بناء "
    "نشر مراقبة تنبيه حدث دفعة مهمة عملية سياسة قاعدة صلاحية دور تدقيق لقطة استعادة مرآة قطعة مقطع "
    "كتلة بروتوكول تنسيق ترميز قالب تخطيط لغة منطقة حساب"
).split()
_WORDS = {"en": _EN_WORDS, "ar": _AR_WORDS}
#: (statement, question) templates; their words are not in the filler vocabulary.
_FACTS = {
    "en": (
        ("The access code for {t} is {v}.", "What is the access code for {t}?"),
        ("The rotation interval of {t} is {v} days.", "What is the rotation interval of {t}?"),
        ("The owner of {t} is desk {v}.", "Who is the owner of {t}?"),
        ("{t} listens on port {v}.", "Which port does {t} listen on?"),
    ),
    "ar": (
        ("رمز الدخول الخاص بـ {t} هو {v}.", "ما هو رمز الدخول الخاص بـ {t}؟"),
        ("فترة تدوير {t} هي {v} يوما.", "ما هي فترة تدوير {t}؟"),
        ("مالك {t} هو المكتب {v}.", "من هو مالك {t}؟"),
        ("يستمع {t} على المنفذ {v}.", "على أي منفذ يستمع {t}؟"),
    ),
}
_EN_LETTERS = "bcdfghjklmnpqrstvwx"
#: Letters the search fold leaves as they are (no alef, hamza, taa marbuta or yaa forms).
_AR_LETTERS = "بتثجحخدذرزسشصضطظعغفقكلمن"
_HARAKAT = ("َ", "ِ", "ُ")  # fatha, kasra, damma


def _rng(*parts: Any) -> random.Random:
    """A generator seeded by *parts*: the same text on every machine and run."""
    return random.Random("|".join(str(p) for p in parts))


def _token(rng: random.Random, lang: str) -> str:
    if lang == "ar":
        return "".join(rng.choice(_AR_LETTERS) for _ in range(7))
    return "zq" + "".join(rng.choice(_EN_LETTERS) for _ in range(8))


def _vocalized(token: str) -> str:
    """The token with harakat on every other letter: folds back to itself."""
    return "".join(ch + (_HARAKAT[i % 3] if i % 2 == 0 else "") for i, ch in enumerate(token))


def _fact(rng: random.Random, lang: str) -> dict[str, str]:
    token, value = _token(rng, lang), str(rng.randint(1000, 9999))
    statement, question = rng.choice(_FACTS[lang])
    fact = {"lang": lang, "token": token, "statement": statement.format(t=token, v=value),
            "question": question.format(t=token)}
    if lang == "ar":
        fact["variant"] = question.format(t=_vocalized(token))
    return fact


def _pick(lang: str, rng: random.Random) -> str:
    return lang if lang != "mixed" else rng.choice(("en", "ar"))


def _sentence(rng: random.Random, lang: str) -> str:
    words = [rng.choice(_WORDS[lang]) for _ in range(rng.randint(6, 12) if lang == "en" else rng.randint(5, 10))]
    if lang == "en":
        words[0] = words[0].capitalize()
    return " ".join(words) + "."


def _heading(rng: random.Random, lang: str) -> str:
    words = [rng.choice(_WORDS[lang]) for _ in range(rng.randint(2, 3))]
    return " ".join(w.capitalize() for w in words) if lang == "en" else " ".join(words)


def scale_chunk(seed: int, lib: str, lang: str, n: int, fact: dict[str, str] | None = None) -> tuple[str, str]:
    """(section header, content) of the *n*-th chunk of a scale corpus: a
    short reference entry holding one planted fact, or a section of
    several sentences."""
    rng = _rng(seed, lib, "chunk", n)
    heading = _heading(rng, _pick(lang, rng) if fact is None else fact["lang"])
    if fact is not None:
        return heading, fact["statement"]
    body = " ".join(_sentence(rng, _pick(lang, rng)) for _ in range(rng.randint(3, 7)))
    return heading, f"## {heading}\n\n{body}"


def page_doc(seed: int, lib: str, lang: str, page_no: int, version: int) -> tuple[str, dict[str, str]]:
    """(markdown, its planted fact) of one version of one page of a soak
    site; the fact is a reference section of its own (a chunk of its own)."""
    rng = _rng(seed, lib, "page", page_no, version)
    fact = _fact(rng, _pick(lang, rng))
    title = _heading(rng, _pick(lang, rng))
    sections = []
    for _ in range(rng.randint(2, 4)):
        sl = _pick(lang, rng)
        paras = [" ".join(_sentence(rng, sl) for _ in range(rng.randint(2, 4))) for _ in range(rng.randint(1, 3))]
        sections.append(f"## {_heading(rng, sl)}\n\n" + "\n\n".join(paras))
    sections.insert(rng.randrange(len(sections) + 1),
                    f"## {_heading(rng, fact['lang'])}\n\n{fact['statement']}")
    return (f"# {title} {page_no}\n\n" + "\n\n".join(sections)).strip(), fact


def doc_doc(seed: int, lib: str, key: str, version: int, lang: str) -> tuple[list[list[str]], dict[str, str]]:
    """(pages of paragraphs, planted fact) of one version of one soak
    document; the fact is a page of its own (a chunk of its own)."""
    rng = _rng(seed, lib, "doc", key, version)
    fact = _fact(rng, _pick(lang, rng))
    pages = [[" ".join(_sentence(rng, _pick(lang, rng)) for _ in range(rng.randint(2, 4)))
              for _ in range(rng.randint(2, 3))] for _ in range(rng.randint(2, 3))]
    pages.insert(rng.randrange(len(pages) + 1), [fact["statement"]])
    return pages, fact


def page_url(seed_url: str, page_no: int) -> str:
    return f"{seed_url}page-{page_no:04d}"


def seed_url_for(lib: str) -> str:
    return f"https://{lib}.soak.example.test/docs/"


# ── the hash embedder ───────────────────────────────────────────────────────

_STOPWORDS = (
    "the is a an of for on what which who does do to in and it its be are with by at as this that "
    "from ما هو هي من في على أي الى إلى عن هذا هذه التي الذي كل بـ ب و لا"
).split()


class SoakHashEmbedder:
    """A deterministic local embedder, made of hashed words. No model, no
    download: the soak measures the Knowledge store and the vector index,
    not an embedding model.

    A vector has two halves of equal weight: a 32-dimension topic part (the
    sum of each word's fixed pseudo-random direction) and a lexical part
    (signed feature hashing of the words over the remaining dimensions).
    Lexical hashing alone gives an approximate nearest-neighbour index no
    structure to navigate -- every vector is nearly orthogonal to every
    other -- and the HNSW index then misses answers no real embedder would
    make it miss; the low-dimensional topic part gives it the clustered
    geometry real embeddings have, while the lexical part keeps a rare word
    decisive. Words are read through the product's search fold, so a
    vocalized Arabic word lands where its plain spelling does.

    Each version hashes with its own salt and names itself
    (``kazma-soak-hash-v<n>``): a model switch is a new embedder identity,
    and versions 3-4, 7-8, ... make 768-dimension vectors instead of 1,024,
    so every second switch changes the vector size too.
    """

    _TOPIC = 32

    def __init__(self, version: int = 1) -> None:
        self.version = int(version)
        self.dim = 1024 if ((self.version - 1) // 2) % 2 == 0 else 768
        self._model_name = f"kazma-soak-hash-v{self.version}"
        from kazma_core.documents.arabic import fold_for_search

        self._fold = fold_for_search
        self._stop = {fold_for_search(w) for w in _STOPWORDS}
        self._words: dict[str, tuple[list[float], int, float]] = {}

    def _word(self, word: str) -> tuple[list[float], int, float]:
        known = self._words.get(word)
        if known is None:
            h = zlib.crc32(f"{self.version}:{word}".encode())
            rng = random.Random(h)
            known = ([rng.gauss(0.0, 1.0) for _ in range(self._TOPIC)],
                     self._TOPIC + h % (self.dim - self._TOPIC), 1.0 if (h >> 20) & 1 else -1.0)
            if len(self._words) >= 200_000:
                self._words.clear()
            self._words[word] = known
        return known

    def encode(self, text: str) -> list[float]:
        counts: dict[str, int] = {}
        for word in re.findall(r"\w+", self._fold(text or "")):
            if len(word) > 1 and word not in self._stop:
                counts[word] = counts.get(word, 0) + 1
        if not counts:
            counts[(text or "").strip() or "-"] = 1
        topic = [0.0] * self._TOPIC
        vec = [0.0] * self.dim
        for word, n in counts.items():
            weight = 1.0 + math.log(n)
            direction, slot, sign = self._word(word)
            for k, value in enumerate(direction):
                topic[k] += weight * value
            vec[slot] += sign * weight
        topic_norm = math.sqrt(sum(t * t for t in topic)) or 1.0
        lexical_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        half = math.sqrt(2.0)
        out = [x / (lexical_norm * half) for x in vec]
        for k, value in enumerate(topic):
            out[k] = value / (topic_norm * half)
        return out

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


# ── environment, hardware ───────────────────────────────────────────────────


def _env(data_dir: Path) -> dict[str, str]:
    """A child's environment: private stores, SQLite, no alerts, no downloads."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("KAZMA_") and k not in ("DATABASE_URL",)}
    env.update({
        "KAZMA_DATA_DIR": str(data_dir),
        "KAZMA_VECTOR_PATH": str(data_dir / "vector_memory"),
        "KAZMA_BACKUPS_DIR": str(data_dir / "backups"),
        "KAZMA_USER_HOME": str(data_dir / "home"),
        "KAZMA_SKILLS_HOME": str(data_dir / "home"),
        "KAZMA_DB_BACKEND": "sqlite",
        "KAZMA_DOCUMENTS_METADATA_BACKEND": "sqlite",
        "KAZMA_DOCUMENTS_JOBS_BACKEND": "sqlite",
        "KAZMA_PROCESS_PRIORITY": "keep",
        "KAZMA_KB_DELAY_MS": "0",
        "KAZMA_OPS_ALERTS": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONIOENCODING": "utf-8",
        # A native crash in a child (chromadb's Rust bindings, 2026-10-17:
        # 0xc0000005 with nothing in the log) still names its Python stack
        # in the kept failed-<tag>.log.
        "PYTHONFAULTHANDLER": "1",
    })
    return env


def _cpu_model() -> str:
    try:
        if os.name == "nt":
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.is_file():
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _disk_model(path: Path) -> dict[str, Any]:
    """The physical disk holding *path*, best effort (unknown when not found)."""
    try:
        if os.name == "nt":
            letter = path.resolve().drive[:1]
            if not letter.isalpha():
                return {}
            command = (f"$n=(Get-Partition -DriveLetter {letter}).DiskNumber; Get-PhysicalDisk | "
                       "Where-Object { $_.DeviceId -eq \"$n\" } | "
                       "Select-Object FriendlyName,MediaType,BusType | ConvertTo-Json -Compress")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                 capture_output=True, text=True, timeout=30, check=False)
            data = json.loads(out.stdout or "{}")
            data = data[0] if isinstance(data, list) and data else data
            return {"model": data.get("FriendlyName"), "media": data.get("MediaType"),
                    "bus": data.get("BusType")} if isinstance(data, dict) else {}
        import psutil

        best = None
        for part in psutil.disk_partitions(all=False):
            if str(path.resolve()).startswith(part.mountpoint) and (
                    best is None or len(part.mountpoint) > len(best.mountpoint)):
                best = part
        match = re.match(r"/dev/(nvme\d+n\d+|mmcblk\d+|[a-z]+)", best.device if best else "")
        if match:
            block = Path("/sys/block") / match.group(1)
            model = (block / "device" / "model")
            rotational = (block / "queue" / "rotational")
            return {"model": model.read_text().strip() if model.is_file() else None,
                    "media": ("HDD" if rotational.read_text().strip() == "1" else "SSD")
                    if rotational.is_file() else None}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return {}


def _hardware(path: Path, name: str | None) -> dict[str, Any]:
    """The machine the numbers were measured on."""
    import psutil

    disk: dict[str, Any] = {"data_dir": str(path)}
    try:
        usage = psutil.disk_usage(str(path))
        disk.update(total_gib=round(usage.total / 2**30, 1), free_gib=round(usage.free / 2**30, 1))
    except OSError:
        pass
    for part in psutil.disk_partitions(all=False):
        if str(path.resolve()).lower().startswith(part.mountpoint.lower()):
            disk.setdefault("filesystem", part.fstype)
    disk.update(_disk_model(path))
    try:
        import chromadb

        chroma = str(chromadb.__version__)
    except ImportError:
        chroma = "not installed"
    # The index pool Kazma starts Chroma with, sized from the open-file
    # limit this machine gives a process (a build from before 2026-10-15
    # left Chroma's own).
    from kazma_core.memory import chroma_local

    pool_size = getattr(chroma_local, "index_pool_size", None)
    ceiling = getattr(chroma_local, "library_ceiling", None)
    return {
        "name": name or "unnamed (pass --hardware-name)",
        "vector_index_pool": pool_size() if pool_size else "chroma's default",
        "library_ceiling": ceiling() if ceiling else None,
        "cpu": _cpu_model(),
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "ram_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "disk": disk,
        "os": platform.platform(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "sqlite": sqlite3.sqlite_version,
        "chromadb": chroma,
    }


# ── the child: shared pieces ────────────────────────────────────────────────


def _emit(prefix: str, payload: dict[str, Any]) -> None:
    print(prefix + json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _peak_rss_mib() -> float | None:
    try:
        import psutil

        info = psutil.Process().memory_info()
        if hasattr(info, "peak_wset"):
            return round(info.peak_wset / 2**20, 1)
        import resource  # POSIX

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (2**20 if sys.platform == "darwin" else 1024), 1)
    except (ImportError, OSError):
        return None


def _guard_network(refused: list[str]) -> None:
    """Refuse every connection that is not to this machine, and record it:
    the soak's site is served in-process, and nothing may leave."""
    import socket

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def local(host: Any) -> bool:
        if host is None:
            return True
        name = (host.decode() if isinstance(host, bytes) else str(host)).strip("[]").lower()
        return name in ("", "localhost", "::1", "0.0.0.0") or name.startswith("127.")

    def check(sock: Any, address: Any) -> None:
        if (sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple)
                and not local(address[0])):
            refused.append(str(address[0]))
            raise OSError(f"knowledge soak: no network ({address[0]})")

    def connect(self: Any, address: Any) -> Any:
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self: Any, address: Any) -> Any:
        check(self, address)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not local(host):
            refused.append(str(host))
            raise socket.gaierror(socket.EAI_NONAME, f"knowledge soak: no network ({host})")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo


def _install_embedder(spec: dict[str, Any]) -> Any:
    """Point the vector store at the embedder the plan names, before any
    store opens (the seam the Knowledge tests use: ``get_encoder``)."""
    from kazma_core.memory import vector_store_global as vsg

    if spec["mode"] == "hash":
        model: Any = SoakHashEmbedder(spec["version"])
    else:
        from kazma_core.memory.embedder import get_embedder

        model = get_embedder()
        probe = model.encode("knowledge soak probe") if model is not None else None
        if not probe:
            raise SystemExit("the configured embedder is not available on this machine "
                             "(nothing is downloaded); use --embedder hash")
    vsg.get_encoder = lambda model_name=None: model
    return model


def _open() -> tuple[Any, Any]:
    """The install-style store and its one index (``knowledge_index_for``)."""
    from kazma_core.stores.knowledge import get_knowledge_store
    from kazma_core.stores.knowledge_index import knowledge_index_for

    store = get_knowledge_store()
    return store, knowledge_index_for(store)


def _wrap(obj: Any, name: str, cell: dict[str, Any], count: bool = False) -> None:
    """Time (and count the rows of) a method of *obj*, on the instance."""
    real = getattr(obj, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            result = real(*args, **kwargs)
        finally:
            cell["seconds"] = cell.get("seconds", 0.0) + time.perf_counter() - start
            cell["calls"] = cell.get("calls", 0) + 1
        if count:
            cell["rows"] = cell.get("rows", 0) + len(result)
        cell["last"] = result if not count else None
        return result

    setattr(obj, name, wrapper)


def _unwrap(obj: Any, *names: str) -> None:
    for name in names:
        with contextlib.suppress(AttributeError):
            delattr(obj, name)


def _sweep_until_full(budget: float, max_passes: int) -> tuple[list[dict[str, Any]], str | None]:
    """Repair passes until every chunk has a vector of the active embedder.

    Returns the passes and an error (None when coverage is full): the pass
    limit, three passes in a row with no progress, an unavailable vector
    store or a library the sweep could not repair (named with its error:
    ``failed <library>: <error>``).
    """
    from kazma_core.stores import knowledge_index

    passes: list[dict[str, Any]] = []
    idle = 0
    while True:
        start = time.perf_counter()
        try:
            report = knowledge_index.run_repair_sweep(time_budget_s=budget)
        except RuntimeError as exc:
            failed = (knowledge_index._last_repair_report() or {}).get("failed") or {}
            return passes, ("the repair sweep failed: "
                            + ("; ".join(f"failed {lib}: {err}" for lib, err in sorted(failed.items())) or str(exc)))
        passes.append({"seconds": round(time.perf_counter() - start, 3), "embedded": report["embedded"],
                       "removed": report["removed"], "missing": report["missing"],
                       "complete": report["complete"]})
        if report["unavailable"]:
            return passes, "the vector store is unavailable"
        if report["complete"] and not report["missing"]:
            return passes, None
        idle = idle + 1 if not (report["embedded"] or report["removed"]) else 0
        if idle >= 3:
            return passes, "three repair passes in a row made no progress"
        if len(passes) >= max_passes:
            return passes, f"coverage not full after {max_passes} passes"


def _vector_facts(store: Any, index: Any, libraries: list[str]) -> dict[str, Any]:
    """Each library's vectors as the vector store holds them (not the
    coverage record): chunks with a vector of the active embedder, chunks
    without one, vectors with no chunk, vectors another embedder made."""
    out: dict[str, Any] = {}
    for lib in libraries:
        vs = index._vector_store_for(lib)
        if not vs.available:
            out[lib] = {"available": False}
            continue
        identity = vs.identity
        try:
            tags = vs.models()
        except Exception as exc:  # noqa: BLE001 - recorded, the soak decides
            out[lib] = {"available": False, "error": f"listing the vectors failed: {exc}"[:300]}
            continue
        active = store.active_chunk_ids(lib)
        current = {cid for cid, made_by in tags.items() if made_by == identity}
        health = index.health(lib)
        out[lib] = {"available": True, "identity": identity, "active": len(active),
                    "current": len(current & active), "missing": len(active - current),
                    "orphans": len(set(tags) - active), "foreign": len(set(tags) - current),
                    "health_state": health["state"], "health_vectors": health["vector_chunks"],
                    "health_chunks": health["sqlite_chunks"]}
    return out


def _collections(index: Any) -> list[str]:
    from kazma_core.memory import vector_store_global as vsg

    client = vsg._shared_client(index._persist_dir)
    return sorted(str(getattr(c, "name", c)) for c in client.list_collections())


def _ask(store: Any, index: Any, fact: dict[str, Any], top_k: int) -> dict[str, Any]:
    """One planted question through the hybrid search, the keyword index and
    the meaning index; found = a returned chunk holds the planted token."""
    lib, token, question = fact["library"], fact["token"], fact["question"]
    out: dict[str, Any] = {"token": token, "lang": fact["lang"], "library": lib}
    start = time.perf_counter()
    hits = index.search_sync(question, lib, top_k=top_k)
    out["hybrid_ms"] = _ms(start)
    out["hybrid"] = any(token in h.content for h in hits)
    if fact.get("chunk_id"):
        out["hybrid_rank"] = next((i for i, h in enumerate(hits, 1) if h.chunk_id == fact["chunk_id"]), None)
    start = time.perf_counter()
    rows = store.fts_search(fact.get("variant") or question, lib, limit=top_k * 4)
    out["keyword_ms"] = _ms(start)
    found = store.get_chunks_by_ids([r[0] for r in rows])
    out["keyword"] = any(token in row["content"] for row in found.values())
    keyword_ids = [cid for cid, row in found.items() if token in row["content"]]
    out["keyword_rank"] = next((i for i, r in enumerate(rows, 1) if r[0] in keyword_ids), None)
    vs = index._vector_store_for(lib)
    out["meaning"] = None
    if vs.available:
        raw, out["meaning_ms"], failure = _meaning_query(vs, question, lib, top_k)
        if failure:
            # The product logs the failed query and answers nothing; ask once
            # more, as the next search would.
            out["meaning_transient_failure"] = failure
            time.sleep(0.5)
            raw, _again, failure = _meaning_query(vs, question, lib, top_k)
            if failure:
                del out["meaning_transient_failure"]  # not transient: it failed again
                out["meaning"] = False
                out["meaning_miss"] = {"kind": "query_failed", "detail": f"failed twice: {failure}"}
                return out
        found = store.get_chunks_by_ids([r[0] for r in raw])
        out["meaning"] = any(token in row["content"] for row in found.values())
        if not out["meaning"]:
            out["meaning_miss"] = _classify_miss(vs, question, [fact["chunk_id"]] if fact.get("chunk_id")
                                                 else keyword_ids, raw)
    return out


#: Warnings the product's vector store logged in this child (a failed query
#: is logged and answered with nothing): the soak reads them to tell a
#: failed meaning search from an empty one.
_VECTOR_LOG: list[str] = []


def _meaning_query(vs: Any, question: str, lib: str, top_k: int) -> tuple[list[tuple[str, float]], float, str | None]:
    """The meaning layer as ``search_sync`` queries it -- with the filter on
    the embedder's tag only while the collection holds another embedder's
    vectors (``KnowledgeIndex._model_filter``): (results, ms, the failure
    the product logged, if any)."""
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    _VECTOR_LOG.clear()
    start = time.perf_counter()
    model_filter = getattr(KnowledgeIndex, "_model_filter", None)  # a build from before 2026-10-15 has none
    where = (model_filter(vs, vs.identity) if model_filter is not None
             else {"$and": [{"library_id": lib}, {"model": vs.identity}]})
    raw = vs.query(question, limit=top_k * 4, where=where)
    elapsed = _ms(start)
    failure = next((m for m in _VECTOR_LOG if "Query failed" in m), None)
    return raw, elapsed, failure[:300] if failure else None


def _classify_miss(vs: Any, question: str, chunk_ids: list[str], raw: list[tuple[str, float]]) -> dict[str, Any]:
    """Why meaning search did not return a planted answer: ``no_vector``
    (its chunk has no vector of the active embedder: a violation), or the
    vector is there and the approximate index (HNSW) passed over it --
    ``index`` when it was nearer than everything returned, ``farther`` when
    other chunks were nearer."""
    if not chunk_ids:
        return {"kind": "no_vector", "detail": "no chunk holds the answer"}
    try:
        held = vs._collection.get(ids=chunk_ids, include=["embeddings", "metadatas"])
    except Exception as exc:  # noqa: BLE001 - recorded, the soak decides
        return {"kind": "query_failed", "detail": f"reading the answer's vector failed: {exc}"[:300]}
    ids = list(held.get("ids") or [])
    if not ids:
        return {"kind": "no_vector", "detail": "the answer's chunk has no vector"}
    meta = (held.get("metadatas") or [{}])[0] or {}
    if meta.get("model") != vs.identity:
        return {"kind": "no_vector", "detail": f"the answer's vector was made by {meta.get('model')!r}"}
    query = vs._model.encode(question)
    vector = [float(x) for x in held["embeddings"][0]]
    norm = math.sqrt(sum(x * x for x in query)) * math.sqrt(sum(x * x for x in vector)) or 1.0
    similarity = sum(a * b for a, b in zip(query, vector)) / norm
    best = max((s for _i, s in raw), default=None)
    kind = "index" if best is None or similarity > best else "farther"
    return {"kind": kind, "similarity": round(similarity, 4), "best_returned": None if best is None else round(best, 4)}


def _absent(store: Any, index: Any, fact: dict[str, Any], live: list[str], top_k: int) -> dict[str, Any]:
    """A replaced or deleted answer: no search may return it any more."""
    lib, token, question = fact["library"], fact["token"], fact["question"]
    out: dict[str, Any] = {"token": token, "library": lib, "found_by": [], "problem": None}
    exists = store.get_library(lib) is not None
    if fact.get("deleted"):
        if exists:
            out["problem"] = "the deleted library still exists"
        elif live and any(token in h.content for h in index.search_all_sync(question, live, top_k=top_k)):
            out["found_by"].append("hybrid across the live libraries")
    elif not exists:
        out["problem"] = "its library is gone although it was not deleted"
    else:
        if any(token in h.content for h in index.search_sync(question, lib, top_k=top_k)):
            out["found_by"].append("hybrid")
        rows = store.fts_search(fact.get("variant") or question, lib, limit=top_k * 4)
        if any(token in r["content"] for r in store.get_chunks_by_ids([x[0] for x in rows]).values()):
            out["found_by"].append("keyword")
    return out


# ── the child: scale steps ──────────────────────────────────────────────────


def _scale_pages(plan: dict[str, Any]):
    """The corpus, page by page: (url, chunk dicts, planted facts of the page)."""
    from kazma_core.stores.knowledge_chunker import make_chunk_id

    seed, lib, lang, size = plan["seed"], plan["library"], plan["language"], plan["size"]
    per_page = plan["page_chunks"]
    planted = {int(p["chunk_no"]): p["fact"] for p in plan["planted"]}
    seed_url = seed_url_for(lib)
    for page in range(math.ceil(size / per_page)):
        url = f"{seed_url}p{page:06d}"
        chunks, facts = [], []
        for n in range(page * per_page, min(size, (page + 1) * per_page)):
            fact = planted.get(n)
            header, content = scale_chunk(seed, lib, lang, n, fact)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            index_in_page = n - page * per_page
            chunks.append({"content": content, "content_hash": digest, "source_url": url,
                           "document_title": f"Scale page {page}", "section_header": header,
                           "chunk_index": index_in_page, "has_code": False, "char_count": len(content),
                           "library_id": lib})
            if fact is not None:
                facts.append({**fact, "library": lib, "chunk_no": n,
                              "chunk_id": make_chunk_id(lib, url, digest, index_in_page)})
        yield url, chunks, facts


def _search_block(store: Any, index: Any, facts: list[dict[str, Any]], repeats: int, top_k: int,
                  violations: list[str], where: str) -> dict[str, Any]:
    lat: dict[str, list[float]] = {"hybrid": [], "keyword": [], "meaning": []}
    found = {"hybrid": 0, "keyword": 0, "meaning": 0}
    index_misses: list[dict[str, Any]] = []
    transient: list[str] = []
    unreadable: list[str] = []
    for r in range(repeats):
        for fact in facts:
            res = _ask(store, index, fact, top_k)
            for layer in lat:
                if res.get(f"{layer}_ms") is not None:
                    lat[layer].append(res[f"{layer}_ms"])
            if res.get("meaning_transient_failure"):
                transient.append(res["meaning_transient_failure"])
            if r:
                continue
            for layer in found:
                found[layer] += bool(res[layer])
            miss = res.get("meaning_miss") or {}
            if miss.get("kind") == "query_failed" and _internal(miss.get("detail", "")):
                unreadable.append(miss["detail"])  # this process could not read the collection
                violations += [v for v in _ask_violations(res, where) if "meaning search:" not in v]
                continue
            violations += _ask_violations(res, where)
            if _passed_over(res):
                index_misses.append({"token": res["token"], "hybrid": res["hybrid"],
                                     "keyword_rank": res.get("keyword_rank"), **res["meaning_miss"]})
    return {"hybrid_ms": latency_summary(lat["hybrid"]), "keyword_ms": latency_summary(lat["keyword"]),
            "meaning_ms": latency_summary(lat["meaning"]),
            "found": {layer: [n, len(facts)] for layer, n in found.items()},
            "meaning_index_misses": index_misses, "meaning_transient_failures": len(transient),
            "meaning_transient_failure_last": transient[-1] if transient else None,
            "vector_read_failure": unreadable[0] if unreadable else None,
            "questions": len(facts), "repeats": repeats}


def _internal(text: str) -> bool:
    """A Chroma internal error (seen: "Error creating hnsw segment reader:
    Nothing found on disk"): the process could not read a collection."""
    return "internal error" in (text or "").lower()


def _passed_over(res: dict[str, Any]) -> bool:
    """The answer's vector was there and the approximate index did not
    return it (:func:`_classify_miss`)."""
    return (res.get("meaning_miss") or {}).get("kind") in ("index", "farther")


def _rank_dropped(res: dict[str, Any]) -> bool:
    """The hybrid top-k left out an answer the keyword index returned, when
    the approximate meaning index passed over it: reciprocal rank fusion
    prefers chunks both layers return to one only the keyword layer does. A
    ranking measurement (KQ01's ground), not damage to the store."""
    return not res.get("hybrid") and bool(res.get("keyword")) and _passed_over(res)


def _ask_violations(res: dict[str, Any], where: str) -> list[str]:
    """A planted answer the keyword index did not return, the hybrid search
    did not return (unless :func:`_rank_dropped`), or whose chunk has no
    vector of the active embedder. A vector the approximate index passed
    over is a measurement (recall), not a violation."""
    out = [f"{where}: planted answer {res['token']} ({res['lang']}, {res['library']}) not found by {layer}"
           for layer in ("hybrid", "keyword") if not res.get(layer)
           and not (layer == "hybrid" and _rank_dropped(res))]
    if res.get("meaning") is None:
        out.append(f"{where}: meaning search unavailable for {res['library']}")
    elif res.get("meaning_miss", {}).get("kind") in ("no_vector", "query_failed"):
        out.append(f"{where}: planted answer {res['token']} ({res['library']}), meaning search: "
                   f"{res['meaning_miss']['detail']}")
    return out


def _db_ceilings(db: Path) -> dict[str, Any]:
    """Rows of every table in a SQLite file, and its size on disk."""
    out: dict[str, Any] = {"bytes": _files_bytes(db)}
    if not db.is_file():
        return out
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        out["rows"] = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}
    finally:
        conn.close()
    return out


def _files_bytes(path: Path) -> int:
    """A file and its -wal/-shm, or a folder's files, in bytes."""
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return sum(p.stat().st_size for p in path.parent.glob(path.name + "*") if p.is_file())


def _scale_build(plan: dict[str, Any]) -> dict[str, Any]:
    from kazma_core.db.sqlite_snapshot import snapshot_copy
    from kazma_core.stores.knowledge import KnowledgeStorageFull
    from kazma_core.stores.knowledge_index import KnowledgePublishError, run_repair_sweep

    store, index = _open()
    lib = plan["library"]
    violations: list[str] = []
    store.create_library(lib, f"Scale {plan['size']} {plan['language']}", seed_url=seed_url_for(lib))
    publish: dict[str, Any] = {}
    _wrap(store, "publish_page", publish)
    facts: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    pending_facts: list[dict[str, Any]] = []
    capped: dict[str, Any] | None = None
    pages = 0
    start = time.perf_counter()

    def flush() -> bool:
        nonlocal capped
        try:
            index.index(lib, batch)
        except KnowledgePublishError as exc:
            full = [url for url, err in exc.failures if isinstance(err, KnowledgeStorageFull)]
            if len(full) == len(exc.failures):
                capped = {"reason": "knowledge.db reached its quota", "first_refused_page": full[0]}
            else:
                violations.append(f"ingest: {len(exc.failures)} page(s) not stored: {exc}")
            return False
        return True

    for _url, chunks, page_facts in _scale_pages(plan):
        batch += chunks
        pending_facts += page_facts
        pages += 1
        if pages % plan["batch_pages"] == 0:
            ok = flush()
            facts += pending_facts
            batch, pending_facts = [], []
            if not ok:
                break
    if batch and capped is None and not violations:
        flush()
        facts += pending_facts
    ingest_s = time.perf_counter() - start
    _unwrap(store, "publish_page")
    stored = store.count_chunks(lib)
    held = store.get_chunks_by_ids([f["chunk_id"] for f in facts])
    if capped is None and len(held) < len(plan["planted"]):
        violations.append(f"ingest: {len(plan['planted']) - len(held)} planted chunk(s) missing after publication")
    facts = [f for f in facts if f["chunk_id"] in held]
    lost_to_cap = len(plan["planted"]) - len(facts)  # never stored: past the quota
    if capped is None and stored != plan["size"]:
        violations.append(f"ingest: {stored} chunks active, {plan['size']} published")
    ingest = {"chunks": stored, "pages": pages, "seconds": round(ingest_s, 3),
              "chunks_per_s": round(stored / ingest_s, 1) if ingest_s else None,
              "publish_seconds": round(publish.get("seconds", 0.0), 3),
              "vector_and_other_seconds": round(ingest_s - publish.get("seconds", 0.0), 3),
              "capped": capped}

    # A process that cannot read the collection (a Chroma internal error)
    # is recorded, not judged: the restore step reads the vectors again in a
    # fresh process, and that one must succeed.
    unreadable: str | None = None
    settle, error = _sweep_until_full(plan["sweep_budget"], plan["max_sweep_passes"])
    if error and _internal(error):
        unreadable = error
    elif error:
        violations.append(f"after ingest: {error}")
    # The listing cost: a pass with nothing to do, with what it read.
    vs = index._vector_store_for(lib)
    chunk_rows: dict[str, Any] = {}
    vector_rows: dict[str, Any] = {}
    fts: dict[str, Any] = {}
    _wrap(store, "active_chunk_sizes", chunk_rows, count=True)
    _wrap(vs, "models", vector_rows, count=True)
    _wrap(store, "fts_coverage", fts)
    start = time.perf_counter()
    try:
        idle = run_repair_sweep(time_budget_s=plan["sweep_budget"])
    except RuntimeError as exc:
        idle = {"embedded": 0, "removed": 0, "missing": 0}
        unreadable = unreadable or f"the idle repair pass failed: {exc}"
    idle_s = time.perf_counter() - start
    _unwrap(store, "active_chunk_sizes", "fts_coverage")
    _unwrap(vs, "models")
    if (idle["embedded"] or idle["removed"] or idle["missing"]) and not unreadable:
        violations.append(f"an idle repair pass did work: {idle['embedded']} embedded, "
                          f"{idle['removed']} removed, {idle['missing']} missing")
    coverage = fts.get("last") or {}
    sweep_idle = {"ms": round(idle_s * 1000, 3), "keyword_coverage_ms": round(fts.get("seconds", 0.0) * 1000, 3),
                  "vector_listing_ms": round(vector_rows.get("seconds", 0.0) * 1000, 3),
                  "chunk_listing_ms": round(chunk_rows.get("seconds", 0.0) * 1000, 3),
                  "rows_listed": {"active_chunks": chunk_rows.get("rows", 0),
                                  "vector_ids": vector_rows.get("rows", 0),
                                  "keyword_index_rows_counted": coverage.get("indexed"),
                                  "chunks_counted": coverage.get("chunks")}}
    if not coverage.get("ok", False):
        violations.append(f"the keyword index does not match the chunks: {coverage}")

    search = _search_block(store, index, facts, plan["search_repeats"], plan["top_k"], violations,
                           "search after ingest")
    vectors = _vector_facts(store, index, [lib])[lib]
    unreadable = unreadable or search["vector_read_failure"] or (
        vectors.get("error") if _internal(vectors.get("error", "")) else None)
    if (vectors.get("missing") or vectors.get("orphans") or not vectors.get("available")) and not unreadable:
        violations.append(f"vectors after ingest: {vectors}")
    storage = store.storage_status()
    backup = Path(plan["backup"])
    backup.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    snapshot_copy(store.path, backup)
    backup_s = time.perf_counter() - start
    store.close()
    data = Path(os.environ["KAZMA_DATA_DIR"])
    return {"ingest": ingest, "planted": facts, "planted_lost_to_cap": lost_to_cap,
            "sweep_after_ingest": {"passes": settle, "error": error}, "sweep_idle": sweep_idle,
            "search": search, "vectors": vectors, "identity": vectors.get("identity"),
            "storage": storage, "backup": {"seconds": round(backup_s, 3), "bytes": backup.stat().st_size},
            "ceilings": {"knowledge_db": _db_ceilings(data / "knowledge.db"),
                         "vector_dir_bytes": _files_bytes(data / "vector_memory"),
                         "settings_db_bytes": _files_bytes(data / "settings.db")},
            "vector_read_failure": unreadable, "violations": violations}


def _scale_restore(plan: dict[str, Any]) -> dict[str, Any]:
    from kazma_core.db.sqlite_snapshot import snapshot_copy
    from kazma_core.paths import knowledge_db
    from kazma_core.stores.knowledge_lifecycle import delete_library

    lib, facts = plan["library"], plan["planted"]
    violations: list[str] = []
    start = time.perf_counter()
    snapshot_copy(plan["backup"], knowledge_db())
    copy_s = time.perf_counter() - start
    start = time.perf_counter()
    store, index = _open()
    chunks = store.count_chunks(lib)
    open_s = time.perf_counter() - start
    if chunks != plan["chunks"]:
        violations.append(f"restore: {chunks} chunks restored, {plan['chunks']} backed up")
    # Keyword search answers at once; meaning search waits for the sweep.
    kw_lat, kw_found = [], 0
    for fact in facts:
        start = time.perf_counter()
        rows = store.fts_search(fact.get("variant") or fact["question"], lib, limit=plan["top_k"] * 4)
        kw_lat.append(_ms(start))
        kw_found += fact["chunk_id"] in {r[0] for r in rows}
    if kw_found != len(facts):
        violations.append(f"restore: keyword search found {kw_found} of {len(facts)} planted answers "
                          "before the repair sweep")
    record = index.health(lib)
    start = time.perf_counter()
    passes, error = _sweep_until_full(plan["sweep_budget"], plan["max_sweep_passes"])
    sweep_s = time.perf_counter() - start
    # A process that cannot read the collection is retried by the parent in
    # a fresh one (see ``_scale_build``); every other failure is judged here.
    unreadable = error if error and _internal(error) else None
    if error and not unreadable:
        violations.append(f"restore: {error}")
    vectors = _vector_facts(store, index, [lib])[lib]
    search = _search_block(store, index, facts, 1, plan["top_k"], violations, "search after restore")
    unreadable = unreadable or search["vector_read_failure"] or (
        vectors.get("error") if _internal(vectors.get("error", "")) else None)
    if (vectors.get("missing") or vectors.get("orphans") or vectors.get("foreign")
            or not vectors.get("available")) and not unreadable:
        violations.append(f"restore: vectors after the sweep: {vectors}")
    collection = f"kazma_kb_{lib}"
    start = time.perf_counter()
    result = delete_library(lib)
    delete_s = time.perf_counter() - start
    after = _after_delete(store, index, lib, [f["token"] for f in facts])
    if not result.get("deleted") or not result.get("provenance_removed"):
        violations.append(f"delete: {result}")
    violations += [f"delete: {p}" for p in after["problems"]]
    rto = copy_s + open_s + sweep_s
    waits = max(0, len(passes) - 1) * _CADENCE_S
    return {"restore": {"copy_seconds": round(copy_s, 3), "open_seconds": round(open_s, 3),
                        "keyword_rto_seconds": round(copy_s + open_s, 3),
                        "keyword_before_sweep": {"found": [kw_found, len(facts)], "ms": latency_summary(kw_lat)},
                        "coverage_record_before_sweep": {k: record.get(k) for k in
                                                         ("state", "vector_chunks", "sqlite_chunks")},
                        "sweep": {"passes": passes, "seconds": round(sweep_s, 3), "error": error},
                        "reembed_rto_seconds": round(rto, 3),
                        "reembed_rto_at_cadence_seconds": round(rto + waits, 3),
                        "vectors_after": vectors, "search_after": search},
            "delete": {"seconds": round(delete_s, 3), "result": result, "collection": collection,
                       "after": after},
            "vector_read_failure": unreadable, "violations": violations}


# ── the child: search breadth ───────────────────────────────────────────────


def _breadth_build(plan: dict[str, Any]) -> dict[str, Any]:
    """One library's collection, built through the product's vector store
    alone (``VectorStore.index_many``: the scale step's corpus, Kazma's HNSW
    graph), and the exact rank of each planted answer over every vector."""
    import numpy as np
    from kazma_core.memory import vector_store_global as vsg
    from kazma_core.stores.knowledge_chunker import make_chunk_id
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    _store, index = _open()
    lib = plan["library"]
    vs = index._vector_store_for(lib)
    violations: list[str] = []
    if not vs.available:
        return {"violations": ["the vector store is unavailable"], "planted": [], "exact": {}}
    identity = vs.identity
    model = vsg.get_encoder()
    vectors: list[list[float]] = []
    real_batch = model.encode_batch

    def recording(texts: list[str]) -> list[list[float]]:
        out = real_batch(texts)
        vectors.extend(out)
        return out

    model.encode_batch = recording  # index_many's own embedding call feeds the exact search
    planted = {int(p["chunk_no"]): p["fact"] for p in plan["planted"]}
    url = seed_url_for(lib)
    ids: list[str] = []
    facts: list[dict[str, Any]] = []
    batch: list[tuple[str, str, dict[str, Any]]] = []
    start = time.perf_counter()
    for n in range(plan["size"]):
        fact = planted.get(n)
        _header, content = scale_chunk(plan["seed"], lib, plan["language"], n, fact)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunk_id = make_chunk_id(lib, f"{url}c{n:07d}", digest, 0)
        if fact is not None:
            facts.append({**fact, "library": lib, "chunk_no": n, "chunk_id": chunk_id})
        batch.append((chunk_id, content, KnowledgeIndex._chunk_vector_metadata(lib, {}, identity)))
        ids.append(chunk_id)
        if len(batch) >= plan["batch"] or n == plan["size"] - 1:
            done = vs.index_many(batch)
            if done != len(batch):
                violations.append(f"build: {len(batch) - done} of a batch of {len(batch)} not stored")
            batch = []
    build_s = time.perf_counter() - start
    model.encode_batch = real_batch
    held = vs.count()
    if held != plan["size"] or len(vectors) != len(ids):
        violations.append(f"build: {held} vectors held, {len(vectors)} embedded, {plan['size']} published")
    matrix = np.asarray(vectors, dtype=np.float32)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    row = {cid: i for i, cid in enumerate(ids)}
    candidates = plan["top_k"] * 4  # the meaning layer's candidates (KnowledgeIndex._gather)
    ranks = []
    for fact in facts:
        query = np.asarray(model.encode(fact["question"]), dtype=np.float32)
        query /= max(float(np.linalg.norm(query)), 1e-12)
        sims = matrix @ query
        mine = sims[row[fact["chunk_id"]]]
        fact["exact_rank"] = 1 + int((sims > mine).sum())
        fact["similarity"] = round(float(mine), 4)
        ranks.append(fact["exact_rank"])
    return {"build": {"vectors": held, "seconds": round(build_s, 3),
                      "vectors_per_s": round(held / build_s, 1) if build_s else None,
                      "hnsw": vs._hnsw()},
            "exact": {"questions": len(facts), "rank_1": sum(r == 1 for r in ranks),
                      "in_candidates": sum(r <= candidates for r in ranks), "worst_rank": max(ranks, default=None)},
            "planted": facts, "identity": identity, "violations": violations}


def _set_breadth(vs: Any, breadth: int) -> None:
    """Set the collection's search breadth in Chroma's catalog. Chroma's
    own work on its catalog right after a start can hold it for a moment
    ("database is locked", seen once in five runs): asked again for up to
    ten seconds."""
    deadline = time.monotonic() + 10.0
    while True:
        try:
            vs._collection.modify(configuration={"hnsw": {"ef_search": breadth}})
            return
        except Exception as exc:  # noqa: BLE001 - chromadb's InternalError; anything else raises below
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def _breadth_search(plan: dict[str, Any]) -> dict[str, Any]:
    """The planted questions through the meaning layer of a library search
    (``top_k`` x 4 candidates), at one search breadth: a number set on the
    collection before this process loads its index (Chroma applies a breadth
    when it loads one), or ``auto``, the breadth the product sets for the
    collection's size when it opens it."""
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    _store, index = _open()
    lib, facts = plan["library"], plan["planted"]
    vs = index._vector_store_for(lib)
    if not vs.available:
        return {"violations": ["the vector store is unavailable"]}
    breadth = plan["breadth"]
    violations: list[str] = []
    size = rule = None
    if breadth != "auto":
        _set_breadth(vs, int(breadth))
    else:
        # As the product opens a collection made with the base breadth (an
        # earlier step of this run left another one in its catalog).
        from kazma_core.memory import vector_store_global as vsg

        _set_breadth(vs, vsg._HNSW["ef_search"])
        vs._fit_breadth()
        size = vs._size()
        rule = vsg.search_breadth(size) if size is not None else None
    held = vs._client.get_collection(vs._collection_name).configuration_json["hnsw"]["ef_search"]
    if breadth == "auto" and held != rule:
        violations.append(f"the product set a search breadth of {held} for {size} vectors, its rule says {rule}")
    where = KnowledgeIndex._model_filter(vs, vs.identity)
    limit = plan["top_k"] * 4
    start = time.perf_counter()
    vs.query(facts[0]["question"], limit=limit, where=where)  # loads the index
    load_ms = _ms(start)
    lat: list[float] = []
    found = top1 = 0
    misses: list[dict[str, Any]] = []
    for r in range(plan["repeats"]):
        for fact in facts:
            start = time.perf_counter()
            raw = vs.query(fact["question"], limit=limit, where=where)
            lat.append(_ms(start))
            if r:
                continue
            got = [cid for cid, _score in raw]
            found += fact["chunk_id"] in got
            top1 += bool(got) and got[0] == fact["chunk_id"]
            if fact["chunk_id"] not in got:
                misses.append({"token": fact["token"], "exact_rank": fact.get("exact_rank"),
                               "similarity": fact.get("similarity"),
                               "best_returned": round(raw[0][1], 4) if raw else None})
    return {"breadth": breadth, "breadth_held": held, "load_ms": load_ms, "found": [found, len(facts)],
            "top1": [top1, len(facts)], "ms": latency_summary(lat), "misses": misses, "violations": violations}


def _varint(blob: bytes, i: int) -> tuple[int, int]:
    """SQLite's variable-length integer, as FTS5 writes it."""
    value = 0
    for k in range(8):
        byte = blob[i + k]
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i + k + 1
    return (value << 8) | blob[i + 8], i + 9


def _leaf_terms(page: bytes) -> list[bytes]:
    """The words of one leaf page of an FTS5 segment: the page footer gives
    each word's offset; the first is stored whole, each next one after the
    prefix it shares with the word before it."""
    footer = int.from_bytes(page[2:4], "big")
    offsets, i, at = [], footer, 0
    while i < len(page):
        delta, i = _varint(page, i)
        at += delta
        offsets.append(at)
    terms: list[bytes] = []
    previous = b""
    for n, offset in enumerate(offsets):
        if n == 0:
            size, j = _varint(page, offset)
            term = page[j:j + size]
        else:
            shared, j = _varint(page, offset)
            size, j = _varint(page, j)
            term = previous[:shared] + page[j:j + size]
        terms.append(term)
        previous = term
    return terms


def _segment_terms(conn: sqlite3.Connection) -> set[str]:
    """Every word the keyword index's segments hold, read from their leaf
    pages: a deleted row's words too, until a merge drops them."""
    out: set[str] = set()
    for rowid, block in conn.execute("SELECT id, block FROM knowledge_chunks_fts_data WHERE id > 10"):
        # b-tree node or doclist index (height, flag), a tombstone segment, an empty block
        if (rowid >> 31) & 0x3F or rowid >> 37 >= 1 << 16 or not block:
            continue
        out.update(t[1:].decode("utf-8", "replace") for t in _leaf_terms(bytes(block)) if t[:1] == b"0")
    return out


def _segment_residue(conn: sqlite3.Connection, tokens: list[str]) -> int:
    """How many *tokens* the keyword index's segments hold as words no live
    row holds.

    FTS5 deletes by writing a delete marker; the deleted row's words stay in
    the segment blobs (``knowledge_chunks_fts_data``) until a merge rewrites
    them -- unsearchable, but on disk. Read word by word: the test this
    replaced looked for each token's last 4 (Arabic) or 6 (English) letters
    anywhere in the blobs, and another word holding those letters counted --
    6 of 20,000 random Arabic tokens in the store the first multi-day run
    stopped on, a leak that is not there for a run checking every cycle.
    """
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.kb_soak_vocab "
                 "USING fts5vocab(main, knowledge_chunks_fts, 'row')")
    live = {str(r[0]) for r in conn.execute("SELECT term FROM temp.kb_soak_vocab")}
    held = _segment_terms(conn) - live
    return sum(1 for t in tokens if t in held)


def _after_delete(store: Any, index: Any, lib: str, tokens: list[str]) -> dict[str, Any]:
    """What a deleted library left, read from every store it used."""
    from kazma_core.paths import data_dir, knowledge_db
    from kazma_core.web_acquire.crawl_jobs import inspect_jobs

    conn = sqlite3.connect(f"file:{Path(knowledge_db()).as_posix()}?mode=ro", uri=True)
    try:
        left = {
            "library_rows": conn.execute("SELECT COUNT(*) FROM knowledge_libraries WHERE id = ?", (lib,)).fetchone()[0],
            "chunk_rows": conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE library_id = ?", (lib,)).fetchone()[0],
            "keyword_rows": conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts WHERE library_id = ?",
                                         (lib,)).fetchone()[0],
            "coverage_rows": conn.execute("SELECT COUNT(*) FROM knowledge_vector_status WHERE library_id = ?",
                                          (lib,)).fetchone()[0],
        }
        residue = _segment_residue(conn, tokens)
    finally:
        conn.close()
    left["provenance_dir"] = (data_dir() / "knowledge_provenance" / _safe(lib)).exists()
    left["collection"] = f"kazma_kb_{lib}" in _collections(index)
    left["running_crawls"] = sum(1 for row in inspect_jobs() if row.get("library_id") == lib
                                 and row.get("status") == "running")
    problems = [f"{name} left: {value}" for name, value in left.items() if value]
    return {**left, "keyword_segment_residue": [residue, len(tokens)], "problems": problems}


# ── the child: a soak cycle ─────────────────────────────────────────────────

#: The generated site the fetch seam serves: url -> page, seed -> urls.
_SITE: dict[str, dict[str, Any]] = {}
_SEEDS: dict[str, list[str]] = {}


def _register_site(seed: int, spec: dict[str, Any], crash_page: int | None) -> None:
    urls = []
    for i, page in enumerate(spec["pages"]):
        url = page_url(spec["seed_url"], page["page_no"])
        text, _fact = page_doc(seed, spec["library"], spec["lang"], page["page_no"], page["version"])
        _SITE[url] = {"text": text, "fail": bool(page["fail"]), "crash": crash_page == i}
        urls.append(url)
    _SEEDS[spec["seed_url"]] = urls


def _patch_crawl_seams() -> None:
    """The seams the Knowledge crawl tests use: discovery and acquisition
    answer from the generated site, the SSRF check is skipped for it (its
    hosts resolve nowhere), and no browser is started."""
    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.web_acquire.result import FetchErrorCode, FetchResult

    async def discover(seed: str, *args: Any, **kwargs: Any) -> list[str]:
        return list(_SEEDS.get(seed, []))

    async def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
        page = _SITE.get(url)
        if page is None:
            return FetchResult(ok=False, url=url, error="the soak site has no such page", http_status=404,
                               error_code=FetchErrorCode.HTTP, backend="kb-soak-site")
        if page["crash"]:
            sys.stdout.flush()
            os._exit(_KILLED)  # a crash mid-crawl: the next start resumes it
        if page["fail"]:
            return FetchResult(ok=False, url=url, error="planned failure (HTTP 503)", http_status=503,
                               error_code=FetchErrorCode.HTTP, backend="kb-soak-site")
        text = page["text"]
        return FetchResult(ok=True, url=url, final_url=url, text=text, http_status=200, char_count=len(text),
                           backend="kb-soak-site", quality="static")

    ssrf.validate_url = lambda *a, **k: ()
    ki.kb_discover_pages = discover
    ki._web_fetch_text = fetch
    ki._pw_browser_scope = contextlib.nullcontext


async def _crawl(store: Any, spec: dict[str, Any], *, resume: bool) -> dict[str, Any]:
    """A crawl as the Knowledge API runs one (``kb_api._start_site``)."""
    from contextlib import aclosing

    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.result import AcquisitionError

    lib, seed, job_id, kind = spec["library"], spec["seed_url"], spec["job_id"], spec["kind"]
    prepared = await ki._prepare_ingest_site(lib, seed, max_pages=None, job_id=job_id, resume=resume)
    last = None
    try:
        if not resume:
            await crawl_jobs.call(prepared[0].update, lambda row: row.update(kind=kind))

        def ensure() -> None:
            if store.get_library(lib) is None:
                store.create_library(lib, lib, seed_url=seed)
            elif kind == "site" and not resume:
                store.update_library(lib, seed_url=seed)

        await asyncio.to_thread(ensure)
        async with aclosing(ki.ingest_site(lib, seed, max_pages=None, job_id=job_id, resume=resume,
                                           _prepared=prepared)) as updates:
            async for update in updates:
                last = update
    finally:
        def release() -> None:
            job = prepared[0]
            try:
                if job.read()["status"] == "running":
                    job.finish("interrupted")
            except AcquisitionError as exc:
                if exc.code != "policy_error":
                    raise

        await crawl_jobs.call(release)
    row = await asyncio.to_thread(crawl_jobs.inspect_jobs, job_id) or {}
    return {"job_id": job_id, "status": row.get("status"),
            "pages": {p["url"]: p["status"] for p in row.get("pages", [])},
            "phase": getattr(last, "phase", None), "fetched": getattr(last, "fetched", 0),
            "failed": getattr(last, "failed", 0), "message": str(getattr(last, "message", ""))[:300]}


def _publish_document(seed: int, op: dict[str, Any]) -> dict[str, Any]:
    """A document version published into a library through the Documents
    platform's Knowledge adapter (its repository holds the version)."""
    from kazma_core.documents.ingestion import get_ingestion_service
    from kazma_core.documents.models import (
        BlockType,
        DocumentBlock,
        DocumentId,
        DocumentIR,
        DocumentPage,
        Provenance,
    )

    service = get_ingestion_service()
    repo, adapter = service.repository, service.knowledge_adapter
    if adapter is None:
        return {"ok": False, "code": "documents_adapter_unavailable"}
    lib, key, version = op["library"], op["doc_key"], op["version"]
    if op.get("document_id"):
        doc_id = DocumentId(op["document_id"])
    else:
        doc_id = repo.create_document(tenant_id=_TENANT, owner_id=_ACTOR, title=f"{lib} {key}").id
    paged, _fact = doc_doc(seed, lib, key, version, op["lang"])
    body = "\n\n".join(p for page in paged for p in page)
    digest = hashlib.sha256(f"{lib}|{key}|{version}|{body}".encode()).hexdigest()
    blob = repo.register_blob(tenant_id=_TENANT, sha256=digest, byte_size=len(body.encode()),
                              storage_kind="originals")
    record = repo.create_version(tenant_id=_TENANT, document_id=doc_id, actor_id=_ACTOR, source_blob_id=blob.id,
                                 source_sha256=digest, original_filename=f"{key}.md", mime_type="text/markdown")
    pages = tuple(
        DocumentPage(page_number=n + 1, blocks=tuple(
            DocumentBlock(f"p{n + 1}-b{i}", BlockType.PARAGRAPH, text) for i, text in enumerate(paras)))
        for n, paras in enumerate(paged))
    ir = DocumentIR(document_id=doc_id, version_id=record.id, pages=pages,
                    provenance=Provenance(source=f"{key}.md", parser="kazma-knowledge-soak", parser_version="1",
                                          metadata={"sha256": digest}),
                    metadata={"source_sha256": digest, "language": op["lang"]})
    result = adapter.index_document_ir(ir, tenant_id=_TENANT, actor_id=_ACTOR, library_id=lib)
    return {"ok": bool(result.ok), "code": result.code, "document_id": str(doc_id), "version_id": str(record.id),
            "chunks": result.data.chunk_count if result.data else 0,
            "published": bool(result.data and result.data.published)}


def _delete(library: str) -> dict[str, Any]:
    from kazma_core.stores.knowledge_lifecycle import delete_library

    start = time.perf_counter()
    result = delete_library(library)
    return {**result, "seconds": round(time.perf_counter() - start, 3)}


def _journal_facts(tokens: list[str]) -> dict[str, Any]:
    """The crawl journal as its own reader sees it (this install's jobs)."""
    from kazma_core.web_acquire.crawl_jobs import inspect_jobs

    rows = inspect_jobs()
    dump = json.dumps(rows, ensure_ascii=False, default=str)
    return {"rows": len(rows), "bytes": sum(len(json.dumps(r, ensure_ascii=True, default=str)) for r in rows),
            "running": sorted(r["job_id"] for r in rows if r.get("status") == "running"),
            "text_leaks": sorted(t for t in tokens if t in dump)}


def _document_index_leaks(deleted_documents: list[dict[str, str]]) -> list[str]:
    """Documents of deleted libraries the Documents platform still records
    as searchable there (KB-E08)."""
    if not deleted_documents:
        return []
    from kazma_core.documents.ingestion import get_ingestion_service
    from kazma_core.documents.models import DocumentId

    repo = get_ingestion_service().repository
    leaks = []
    for item in deleted_documents:
        held = repo.list_indexed_libraries(tenant_id=_TENANT, document_id=DocumentId(item["document_id"]))
        if item["library"] in held:
            leaks.append(f"{item['library']}/{item['document_id']}")
    return leaks


def _read_failures(out: dict[str, Any]) -> dict[str, str]:
    """Libraries whose collection Chroma could not read in this process
    (:func:`_internal`), from the searches, the repair sweep and the listing."""
    found: dict[str, str] = {}
    for ask in out.get("asked") or []:
        miss = ask.get("meaning_miss") or {}
        if miss.get("kind") == "query_failed" and _internal(miss.get("detail", "")):
            found.setdefault(ask["library"], miss["detail"])
    for lib, error in ((out.get("sweep") or {}).get("failed") or {}).items():
        if _internal(error):
            found.setdefault(lib, error)
    for lib, v in (out.get("vectors") or {}).items():
        if _internal(v.get("error", "")):
            found.setdefault(lib, v["error"])
    return found


async def _soak_cycle(plan: dict[str, Any]) -> dict[str, Any]:
    from kazma_core.config_store import get_config_store

    seed = plan["seed"]
    _patch_crawl_seams()
    # Documents writes on (a delete of a library holding documents tells
    # the Documents platform through its write path).
    await asyncio.to_thread(get_config_store().set, "documents.enabled", True, category="documents")
    store, index = await asyncio.to_thread(_open)
    out: dict[str, Any] = {"cycle": plan["cycle"], "identity": None}
    for spec in plan["resume"]:
        _register_site(seed, spec, None)
        result = await _crawl(store, spec, resume=True)
        _emit(_OP, {"op": "resume", "spec": spec["job_id"], "result": result})
    crash = plan.get("crash") or {}
    for i, op in enumerate(plan["ops"]):
        if op["op"] == "crawl":
            _register_site(seed, op, crash.get("page_index") if crash.get("op_index") == i else None)
            result = await _crawl(store, op, resume=False)
        elif op["op"] == "document":
            result = await asyncio.to_thread(_publish_document, seed, op)
        else:
            result = await asyncio.to_thread(_delete, op["library"])
        _emit(_OP, {"index": i, "op": op["op"], "result": result})
    from kazma_core.stores import knowledge_index

    out["fts_before_sweep"] = await asyncio.to_thread(store.fts_coverage)
    start = time.perf_counter()
    passes, error = await asyncio.to_thread(_sweep_until_full, plan["sweep_budget"], plan["sweep_passes"])
    failed = ((knowledge_index._last_repair_report() or {}).get("failed") or {}) if error else {}
    out["sweep"] = {"passes": passes, "error": error, "failed": failed,
                    "seconds": round(time.perf_counter() - start, 3),
                    "embedded": sum(p["embedded"] for p in passes), "removed": sum(p["removed"] for p in passes)}
    live = sorted(plan["live"])
    out["asked"] = [await asyncio.to_thread(_ask, store, index, fact, plan["top_k"]) for fact in plan["ask"]]
    out["absent"] = [await asyncio.to_thread(_absent, store, index, fact, live, plan["top_k"])
                     for fact in plan["absent"]]
    out["vectors"] = await asyncio.to_thread(_vector_facts, store, index, live)
    out["vector_read_failures"] = _read_failures(out)
    out["identity"] = next((v.get("identity") for v in out["vectors"].values() if v.get("identity")), None)
    out["collections"] = await asyncio.to_thread(_collections, index)
    out["journal"] = await asyncio.to_thread(_journal_facts, plan["crawled_tokens"])
    out["document_index_leaks"] = await asyncio.to_thread(_document_index_leaks, plan["deleted_documents"])
    return out


def _child(plan_path: Path) -> int:
    import logging

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    logging.basicConfig(level=str(plan.get("log_level") or "WARNING").upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    refused: list[str] = []
    _guard_network(refused)

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _VECTOR_LOG.append(record.getMessage())

    logging.getLogger("kazma_core.memory.vector_store_global").addHandler(_Capture(level=logging.WARNING))
    _install_embedder(plan["embedder"])
    mode = plan["mode"]
    if mode == "scale-build":
        out = _scale_build(plan)
    elif mode == "scale-restore":
        out = _scale_restore(plan)
    elif mode == "breadth-build":
        out = _breadth_build(plan)
    elif mode == "breadth-search":
        out = _breadth_search(plan)
    else:
        out = asyncio.run(_soak_cycle(plan))
    from kazma_core.memory import vector_store_global as vsg

    reloads = getattr(vsg, "index_reloads", None)  # a build from before 2026-10-15 has none
    out["index_reloads"] = reloads() if callable(reloads) else {}
    out["network_refused"] = refused
    out["peak_rss_mib"] = _peak_rss_mib()
    out["process"] = process_tree_sample()
    _emit(_RESULT, out)
    return 0


# ── the parent: running children ────────────────────────────────────────────


def _run_child(plan: dict[str, Any], env: dict[str, str], work: Path, timeout: float | None,
               log_level: str = "WARNING") -> tuple[int, list[dict[str, Any]], dict[str, Any] | None, str]:
    """One child process. A child that fails keeps its plan and its whole
    stderr beside the plans (``failed-<step>.json`` / ``.log``)."""
    plan["log_level"] = log_level
    tag = f"{plan['mode']}-{plan.get('cycle', plan.get('library'))}"
    plan_file = work / f"plan-{tag}.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(plan_file)],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False)
    if proc.returncode in (0, _KILLED):
        plan_file.unlink(missing_ok=True)  # thousands of cycles in a multi-day run
    else:
        plan_file.replace(work / f"failed-{tag}.json")
        (work / f"failed-{tag}.log").write_text(proc.stderr or "", encoding="utf-8")
    ops = [json.loads(line[len(_OP):]) for line in proc.stdout.splitlines() if line.startswith(_OP)]
    line = next((x for x in proc.stdout.splitlines() if x.startswith(_RESULT)), None)
    result = json.loads(line[len(_RESULT):]) if line else None
    if proc.returncode not in (0, _KILLED):
        return proc.returncode, ops, result, ((proc.stderr or "")[-3000:]
                                              + f"\n(plan and whole stderr kept as {work / ('failed-' + tag)}.json/.log)")
    return proc.returncode, ops, result, proc.stderr or ""


def _orphan_vector_folders(vector_dir: Path) -> dict[str, Any]:
    """Folders in the vector store that no collection owns: what a dropped
    Chroma collection leaves on disk (its vectors). Read from Chroma's own
    segment catalog while no process holds the store."""
    folders = sorted(p for p in vector_dir.iterdir() if p.is_dir()) if vector_dir.is_dir() else []
    catalog = vector_dir / "chroma.sqlite3"
    if not folders:
        return {"folders": 0, "orphans": 0, "orphan_bytes": 0}
    if not catalog.is_file():
        owned: set[str] = set()
    else:
        conn = sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True)
        try:
            owned = {str(r[0]) for r in conn.execute("SELECT id FROM segments")}
        except sqlite3.Error as exc:
            return {"folders": len(folders), "orphans": None, "error": f"segment catalog unreadable: {exc}"}
        finally:
            conn.close()
    orphans = [p for p in folders if p.name not in owned]
    return {"folders": len(folders), "orphans": len(orphans),
            "orphan_bytes": sum(_files_bytes(p) for p in orphans)}


def _unified_items(data_dir: Path) -> list[str]:
    """Databases under the data dir that still hold ``unified_items``."""
    found = []
    for db in sorted(data_dir.rglob("*.db")):
        try:
            conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'unified_items'").fetchone():
                found.append(str(db.relative_to(data_dir)))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return found


def _base_report(mode: str, args: argparse.Namespace, data_dir: Path) -> dict[str, Any]:
    return {
        "harness": "scripts/knowledge_soak.py", "tracker_row": "KB-Q01", "mode": mode,
        "status": RUNNING, "started_at": utc_stamp(), "updated_at": utc_stamp(),
        "hardware": _hardware(data_dir, args.hardware_name),
        "embedder": {
            "mode": args.embedder,
            "note": ("SoakHashEmbedder: a deterministic local embedder of hashed words (1,024 dimensions, "
                     "768 for every second model switch; a 32-dimension topic part and a lexical part): the "
                     "numbers measure the store and the vector index, not an embedding model")
            if args.embedder == "hash" else "the configured embedder, already on this machine",
        },
        "data_dir": str(data_dir), "violations": [], "findings": [],
    }


def _add_finding(report: dict[str, Any], where: str, text: str) -> None:
    """One finding per place, the latest: a multi-day soak repeats it every cycle."""
    report["findings"] = [f for f in report["findings"] if not f.startswith(where + ":")]
    report["findings"].append(f"{where}: {text}")


def _reload_finding(report: dict[str, Any], where: str, reloads: dict[str, int] | None) -> None:
    """An index Chroma lost and the product rebuilt from its log: the
    recovery worked, and it is counted (the pool should not evict at all)."""
    if reloads:
        _add_finding(report, f"{where} index reloads",
                     f"Chroma lost {sum(reloads.values())} index(es) of {sorted(reloads)} (evicted before it was "
                     "written to disk); the product rebuilt each from Chroma's log and asked again")


def _orphan_outcome(report: dict[str, Any], where: str, orphans: dict[str, Any], tolerate: bool) -> list[str]:
    """A vector folder no collection owns: a violation, or a counted finding."""
    if orphans.get("orphans") is None:
        _add_finding(report, where, str(orphans.get("error")))
        return []
    if not orphans["orphans"]:
        return []
    text = (f"{orphans['orphans']} vector folder(s) no collection owns ({orphans['orphan_bytes']} bytes): "
            "a dropped Chroma collection left its vectors on disk")
    if tolerate:
        _add_finding(report, where, text)
        return []
    return [f"{where}: {text}"]


# ── the parent: scale ───────────────────────────────────────────────────────


def _recall_finding(report: dict[str, Any], lib: str, when: str, search: dict[str, Any]) -> None:
    if search.get("meaning_transient_failures"):
        _add_finding(report, f"{lib} meaning search failures {when}",
                     f"{search['meaning_transient_failures']} meaning quer(ies) failed once and answered when "
                     f"asked again (last: {search['meaning_transient_failure_last']})")
    found, asked = search["found"]["meaning"]
    misses = search["meaning_index_misses"]
    if found < asked:
        dropped = [m for m in misses if not m.get("hybrid")]
        unread = asked - found - len(misses)
        _add_finding(report, f"{lib} meaning recall {when}",
                     f"meaning search returned {found} of {asked} planted answers: {len(misses)} missed with their "
                     f"vector present (the approximate HNSW index passed over "
                     f"it; {sum(m['kind'] == 'index' for m in misses)} were nearer than everything it returned)"
                     + (f", {unread} in a process that could not read the collection" if unread else "")
                     + (f"; the hybrid top {report['parameters']['top_k']} then left out {len(dropped)} of them "
                        f"although the keyword index ranked them {sorted({m.get('keyword_rank') for m in dropped}, key=str)}"
                        if dropped else ""))


def _planted(seed: int, lib: str, lang: str, size: int, questions: int) -> list[dict[str, Any]]:
    """The fixed question set of a step: positions and their facts."""
    rng = _rng(seed, lib, "planted")
    positions = sorted(rng.sample(range(size), min(questions, size)))
    out = []
    for i, n in enumerate(positions):
        fact_lang = lang if lang != "mixed" else ("en", "ar")[i % 2]
        out.append({"chunk_no": n, "fact": _fact(_rng(seed, lib, "fact", n), fact_lang)})
    return out


def run_scale(args: argparse.Namespace) -> int:
    work = Path(tempfile.mkdtemp(prefix="kazma-kb-scale-"))
    root = Path(args.data_dir) if args.data_dir else work / "data"
    root.mkdir(parents=True, exist_ok=True)
    sizes = [int(s) for s in args.chunks.split(",") if s.strip()]
    languages = [s.strip() for s in args.languages.split(",") if s.strip()]
    report = _base_report("scale", args, root)
    report["parameters"] = {"chunks": sizes, "languages": languages, "questions": args.questions,
                            "search_repeats": args.search_repeats, "page_chunks": args.page_chunks,
                            "batch_pages": args.batch_pages, "top_k": args.top_k, "seed": args.seed,
                            "sweep_budget_s": args.sweep_budget, "cadence_s": _CADENCE_S,
                            "tolerate_orphan_vector_folders": args.tolerate_orphan_vector_folders,
                            "tolerate_vector_read_failures": args.tolerate_vector_read_failures}
    report["steps"] = []
    started = time.monotonic()
    embedder = {"mode": "hash", "version": 1} if args.embedder == "hash" else {"mode": "configured"}
    for size in sizes:
        for lang in languages:
            step_dir = root / f"scale-{size}-{lang}"
            if step_dir.exists():
                shutil.rmtree(step_dir)
            step_dir.mkdir(parents=True)
            lib = f"scale-{size}-{lang}"
            backup = root / "backups" / f"knowledge-{size}-{lang}.db"
            report["current_step"] = lib
            _write_report(args.report, report)
            env = _env(step_dir)
            plan = {"mode": "scale-build", "library": lib, "language": lang, "size": size, "seed": args.seed,
                    "planted": _planted(args.seed, lib, lang, size, args.questions),
                    "page_chunks": args.page_chunks, "batch_pages": args.batch_pages, "top_k": args.top_k,
                    "search_repeats": args.search_repeats, "sweep_budget": args.sweep_budget,
                    "max_sweep_passes": args.max_sweep_passes, "backup": str(backup), "embedder": embedder}
            step: dict[str, Any] = {"size": size, "language": lang, "library": lib}
            code, _ops, build, err = _run_child(plan, env, work, None, args.child_log_level)
            if code != 0 or build is None:
                step["violations"] = [f"the build child failed ({code}): {err}"]
                report["steps"].append(step)
                report["violations"] += [f"{lib}: {v}" for v in step["violations"]]
                continue
            step.update({k: build[k] for k in ("ingest", "sweep_after_ingest", "sweep_idle", "search", "storage",
                                               "backup", "identity", "planted_lost_to_cap")})
            step["ceilings"] = {"after_ingest": build["ceilings"]}
            step["build_child"] = {"peak_rss_mib": build["peak_rss_mib"], "process": build["process"]}
            violations = list(build["violations"]) + [f"network: {h}" for h in build["network_refused"]]
            _recall_finding(report, lib, "after ingest", build["search"])
            if build["ingest"]["capped"]:
                storage = build["storage"]
                _add_finding(report, f"{lib} ceiling",
                             f"knowledge.db reached its {storage['quota_bytes']}-byte quota at "
                             f"{build['ingest']['chunks']} chunks (about "
                             f"{storage['used_bytes'] // max(1, build['ingest']['chunks'])} bytes a chunk); the step "
                             f"measured that store, and {build['planted_lost_to_cap']} planted answers past the "
                             "quota were not asked")
            if build.get("vector_read_failure"):
                _add_finding(report, f"{lib} vector store read failure after ingest",
                             f"the build process could not read the collection ({build['vector_read_failure']}); "
                             "the restore reads it in a fresh process")
            # The machine is lost: vectors (never backed up) and the database
            # go; the backup is restored. A restore whose process could not
            # read the collection is run once more, in a fresh process.
            for attempt in (1, 2):
                shutil.rmtree(step_dir / "vector_memory", ignore_errors=True)
                for name in ("knowledge.db", "knowledge.db-wal", "knowledge.db-shm"):
                    (step_dir / name).unlink(missing_ok=True)
                plan = {"mode": "scale-restore", "library": lib, "backup": str(backup),
                        "chunks": build["ingest"]["chunks"], "planted": build["planted"], "top_k": args.top_k,
                        "sweep_budget": args.sweep_budget, "max_sweep_passes": args.max_sweep_passes,
                        "embedder": embedder}
                code, _ops, restored, err = _run_child(plan, env, work, None, args.child_log_level)
                if code != 0 or restored is None or not restored.get("vector_read_failure"):
                    break
                step["restore_attempts"] = attempt + 1
                if attempt == 2:
                    text = (f"two restore processes in a row could not read the collection "
                            f"({restored['vector_read_failure']}); the re-embed measurement is incomplete")
                    if args.tolerate_vector_read_failures:
                        _add_finding(report, f"{lib} vector store read failure in the restore", text)
                    else:
                        violations.append(text)
                else:
                    _add_finding(report, f"{lib} vector store read failure in the restore",
                                 f"the restore process could not read the collection "
                                 f"({restored['vector_read_failure']}); it was run again in a fresh process")
            if code != 0 or restored is None:
                violations.append(f"the restore child failed ({code}): {err}")
            else:
                step["restore"] = restored["restore"]
                step["delete"] = restored["delete"]
                step["restore_child"] = {"peak_rss_mib": restored["peak_rss_mib"], "process": restored["process"]}
                violations += restored["violations"] + [f"network: {h}" for h in restored["network_refused"]]
                _recall_finding(report, lib, "after the restore", restored["restore"]["search_after"])
                record = restored["restore"]["coverage_record_before_sweep"]
                if record.get("state") != "unknown" or record.get("vector_chunks"):
                    violations.append(
                        f"coverage record after the restore: before the first repair pass the store reported "
                        f"meaning search {record.get('state')} with {record.get('vector_chunks')} of "
                        f"{record.get('sqlite_chunks')} chunks covered while no vector existed")
                residue, planted = restored["delete"]["after"]["keyword_segment_residue"]
                if residue:
                    violations.append(f"keyword index after the delete: its segments still hold {residue} "
                                      f"of the deleted library's {planted} planted words")
                for when, child in (("after ingest", build), ("in the restore", restored)):
                    _reload_finding(report, f"{lib} {when}", child.get("index_reloads"))
                orphans = _orphan_vector_folders(step_dir / "vector_memory")
                step["delete"]["vector_folders"] = orphans
                violations += _orphan_outcome(report, f"{lib} after the delete", orphans, args.tolerate_orphan_vector_folders)
                tokens = [f["token"] for f in build.get("planted") or [] if f.get("token")]
                catalog_text = _vector_catalog_text(step_dir / "vector_memory", tokens)
                step["delete"]["vector_catalog_text"] = catalog_text
                if catalog_text.get("text_rows") or catalog_text.get("deleted_words"):
                    violations.append(f"vector catalog after the delete: {catalog_text['text_rows']} row(s) of text, "
                                      f"{catalog_text['deleted_words']} of {len(tokens)} planted words left")
                step["ceilings"]["after_delete"] = {"knowledge_db": _db_ceilings(step_dir / "knowledge.db"),
                                                    "vector_dir_bytes": _files_bytes(step_dir / "vector_memory")}
            leftovers = _unified_items(step_dir)
            if leftovers:
                violations.append(f"unified_items exists in {leftovers}")
            step["violations"] = violations
            report["steps"].append(step)
            report["violations"] += [f"{lib}: {v}" for v in violations]
            report["seconds"] = round(time.monotonic() - started, 1)
            report["updated_at"] = utc_stamp()
            _write_report(args.report, report)
            if not args.keep_data:
                shutil.rmtree(step_dir, ignore_errors=True)
                backup.unlink(missing_ok=True)
    report.pop("current_step", None)
    report["seconds"] = round(time.monotonic() - started, 1)
    finished(report, not report["violations"])
    _write_report(args.report, report)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["healthy"] else 1


# ── the parent: search breadth ──────────────────────────────────────────────


def _breadths(text: str) -> list[int | str]:
    out: list[int | str] = []
    for part in (p.strip() for p in text.split(",")):
        if part:
            out.append("auto" if part == "auto" else int(part))
    return out


def run_breadth(args: argparse.Namespace) -> int:
    """Meaning recall and latency of one library's collection at each search
    breadth, per size and language. A build is kept (``breadth-build.json``
    beside its store) and reused by a later run with ``--reuse-builds``."""
    work = Path(tempfile.mkdtemp(prefix="kazma-kb-breadth-"))
    root = Path(args.data_dir) if args.data_dir else work / "data"
    root.mkdir(parents=True, exist_ok=True)
    sizes = [int(s) for s in args.chunks.split(",") if s.strip()]
    languages = [s.strip() for s in args.languages.split(",") if s.strip()]
    breadths = _breadths(args.breadths)
    report = _base_report("breadth", args, root)
    report["parameters"] = {"chunks": sizes, "languages": languages, "breadths": breadths,
                            "questions": args.questions, "search_repeats": args.search_repeats,
                            "top_k": args.top_k, "candidates": args.top_k * 4, "seed": args.seed,
                            "batch": args.batch, "reuse_builds": args.reuse_builds}
    report["steps"] = []
    started = time.monotonic()
    embedder = {"mode": "hash", "version": 1} if args.embedder == "hash" else {"mode": "configured"}
    for size in sizes:
        for lang in languages:
            lib = f"scale-{size}-{lang}"  # the scale step's library: its corpus and questions
            step_dir = root / f"breadth-{size}-{lang}"
            manifest = step_dir / "breadth-build.json"
            report["current_step"] = lib
            _write_report(args.report, report)
            env = _env(step_dir)
            step: dict[str, Any] = {"size": size, "language": lang, "library": lib, "breadths": {}}
            build = None
            if args.reuse_builds and manifest.is_file():
                build = json.loads(manifest.read_text(encoding="utf-8"))
                step["reused_build"] = True
            else:
                if step_dir.exists():
                    shutil.rmtree(step_dir)
                step_dir.mkdir(parents=True)
                plan = {"mode": "breadth-build", "library": lib, "language": lang, "size": size,
                        "seed": args.seed, "planted": _planted(args.seed, lib, lang, size, args.questions),
                        "batch": args.batch, "top_k": args.top_k, "embedder": embedder}
                code, _ops, build, err = _run_child(plan, env, work, None, args.child_log_level)
                if code != 0 or build is None:
                    step["violations"] = [f"the build child failed ({code}): {err}"]
                    report["steps"].append(step)
                    report["violations"] += [f"{lib}: {v}" for v in step["violations"]]
                    continue
                manifest.write_text(json.dumps(build, ensure_ascii=False), encoding="utf-8")
            step.update({k: build.get(k) for k in ("build", "exact", "identity")})
            violations = list(build.get("violations") or [])
            for breadth in breadths:
                plan = {"mode": "breadth-search", "library": lib, "planted": build["planted"],
                        "breadth": breadth, "top_k": args.top_k, "repeats": args.search_repeats,
                        "embedder": embedder}
                code, _ops, res, err = _run_child(plan, env, work, None, args.child_log_level)
                if code != 0 or res is None:
                    violations.append(f"breadth {breadth}: the search child failed ({code}): {err}")
                    continue
                violations += [f"breadth {breadth}: {v}" for v in res.pop("violations", [])]
                violations += [f"breadth {breadth}: network: {h}" for h in res.get("network_refused") or []]
                step["breadths"][str(breadth)] = {k: res.get(k) for k in
                                                  ("breadth_held", "load_ms", "found", "top1", "ms", "misses",
                                                   "peak_rss_mib")}
            full = [int(b) for b, r in step["breadths"].items()
                    if b != "auto" and r["found"][0] == r["found"][1]]
            step["least_breadth_finding_all"] = min(full) if full else None
            step["violations"] = violations
            report["steps"].append(step)
            report["violations"] += [f"{lib}: {v}" for v in violations]
            report["seconds"] = round(time.monotonic() - started, 1)
            report["updated_at"] = utc_stamp()
            _write_report(args.report, report)
            if not args.keep_data:
                shutil.rmtree(step_dir, ignore_errors=True)
    report.pop("current_step", None)
    report["summary"] = _breadth_summary(report["steps"])
    report["seconds"] = round(time.monotonic() - started, 1)
    finished(report, not report["violations"])
    _write_report(args.report, report)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False, default=str))
    return 0 if report["healthy"] else 1


def _breadth_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Per size and breadth: planted answers found over every language, and
    the worst p50/p95 latency of a language."""
    out: dict[str, Any] = {}
    for step in steps:
        cell = out.setdefault(str(step["size"]), {})
        for breadth, res in (step.get("breadths") or {}).items():
            agg = cell.setdefault(breadth, {"found": [0, 0], "p50_ms": 0.0, "p95_ms": 0.0, "held": set()})
            agg["found"][0] += res["found"][0]
            agg["found"][1] += res["found"][1]
            agg["p50_ms"] = max(agg["p50_ms"], res["ms"]["p50"] or 0.0)
            agg["p95_ms"] = max(agg["p95_ms"], res["ms"]["p95"] or 0.0)
            agg["held"].add(res.get("breadth_held"))
    for cell in out.values():
        for agg in cell.values():
            agg["held"] = sorted(h for h in agg["held"] if h is not None)
    return out


# ── the parent: soak planning and the expected state ────────────────────────


def _new_state(args: argparse.Namespace) -> dict[str, Any]:
    embedder = ({"mode": "hash", "version": 1} if args.embedder == "hash"
                else {"mode": "configured", "version": 0, "alternate": True})
    return {"libs": {}, "deleted": {}, "replaced": [], "pending": [], "embedder": embedder,
            "counts": defaultdict(int), "next_lib": 0}


def _next_embedder(current: dict[str, Any]) -> dict[str, Any]:
    """The next model: a new hash version, or with ``--embedder configured``
    the configured model and a hash version in turn."""
    alternate = bool(current.get("alternate"))
    if current["mode"] == "configured":
        return {"mode": "hash", "version": current["version"] + 1, "alternate": alternate}
    if alternate:
        return {"mode": "configured", "version": current["version"], "alternate": True}
    return {"mode": "hash", "version": current["version"] + 1}


def _safe(lib: str) -> str:
    """The folder name a library's fetched pages are kept under (the product's rule)."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in lib)[:80] or "library"


def _page_fact(seed: int, lib: str, spec: dict[str, Any], page_no: int, version: int) -> dict[str, Any]:
    _text, fact = page_doc(seed, lib, spec["lang"], page_no, version)
    return {**fact, "library": lib}


def _doc_fact(seed: int, lib: str, lang: str, key: str, version: int) -> dict[str, Any]:
    _pages, fact = doc_doc(seed, lib, key, version, lang)
    return {**fact, "library": lib}


def _crawl_spec(lib: str, spec: dict[str, Any], kind: str, cycle: int, rng: random.Random,
                args: argparse.Namespace) -> dict[str, Any]:
    """The site as this crawl will find it, and the pages that will fail."""
    site = dict(spec["site"])
    if kind == "refresh":
        for key in list(site):
            roll = rng.random()
            if roll < args.remove_rate:
                del site[key]  # gone from the site: the library keeps its last version
            elif roll < args.remove_rate + args.change_rate:
                site[key] += 1
        for _ in range(rng.randint(0, 2)):
            site[str(spec["next_page"])] = 1
            spec["next_page"] += 1
        if not site:
            site[str(spec["next_page"])] = 1
            spec["next_page"] += 1
    keys = sorted(site, key=int)
    fails = {k for k in keys if rng.random() < args.fail_rate}
    if len(fails) >= len(keys):
        fails.discard(keys[0])
    spec["site"] = site
    return {"op": "crawl", "kind": kind, "library": lib, "lang": spec["lang"], "seed_url": spec["seed_url"],
            "job_id": f"{lib}:{cycle}:{kind}",
            "pages": [{"page_no": int(k), "version": site[k], "fail": k in fails} for k in keys]}


def _apply_crawl(state: dict[str, Any], spec: dict[str, Any]) -> None:
    lib = state["libs"].get(spec["library"])
    if lib is None:
        return
    for page in spec["pages"]:
        if page["fail"]:
            continue
        key = str(page["page_no"])
        old = lib["stored"].get(key)
        if old is not None and old != page["version"]:
            state["replaced"].append(["page", spec["library"], key, old])
        lib["stored"][key] = page["version"]
    del state["replaced"][:-200]


def _plan_cycle(cycle: int, rng: random.Random, state: dict[str, Any], args: argparse.Namespace,
                *, final: bool = False, probe: set[str] | frozenset[str] = frozenset()
                ) -> tuple[dict[str, Any], dict[str, Any]]:
    """The cycle's operations and the state they will leave (projected).
    Libraries in *probe* (their vectors could not be read in the last
    process) are asked about whatever the sample draws."""
    proj = copy.deepcopy(state)
    counts = proj["counts"]
    ops: list[dict[str, Any]] = []
    crawls: list[dict[str, Any]] = []
    live = sorted(proj["libs"])
    if not final:
        force = cycle <= _STAGE_BUDGET
        if (args.switch_every and cycle % args.switch_every == 0) or (
                cycle == _STAGE_BUDGET and not counts["model_switches"]):
            proj["embedder"] = _next_embedder(proj["embedder"])
            counts["model_switches"] += 1
        # A document version into a library that existed before this cycle.
        if live and (rng.random() < args.doc_rate or (force and cycle >= 2 and not counts["documents"])):
            lib = rng.choice(live)
            spec = proj["libs"][lib]
            docs = spec["docs"]
            if docs and rng.random() < 0.6:
                key = rng.choice(sorted(docs))
            else:
                key = f"d{spec['next_doc']}"
                spec["next_doc"] += 1
            old = docs.get(key)
            version = (old["version"] + 1) if old else 1
            ops.append({"op": "document", "library": lib, "doc_key": key, "version": version, "lang": spec["lang"],
                        "document_id": old["document_id"] if old else None})
            if old:
                proj["replaced"].append(["doc", lib, key, old["version"]])
            docs[key] = {"version": version, "document_id": old["document_id"] if old else None}
            counts["documents"] += 1
        # A delete.
        if len(live) >= 2 and (rng.random() < args.delete_rate or (force and cycle >= 3 and not counts["deletes"])):
            lib = rng.choice(live)
            spec = proj["libs"].pop(lib)
            ops.append({"op": "delete", "library": lib,
                        "documents": [d["document_id"] for d in spec["docs"].values() if d.get("document_id")]})
            proj["deleted"][lib] = {"lang": spec["lang"], "seed_url": spec["seed_url"], "stored": spec["stored"],
                                    "docs": {k: d["version"] for k, d in spec["docs"].items()},
                                    "documents": [d["document_id"] for d in spec["docs"].values()
                                                  if d.get("document_id")]}
            while len(proj["deleted"]) > 20:
                proj["deleted"].pop(next(iter(proj["deleted"])))
            live.remove(lib)
            counts["deletes"] += 1
        # A refresh of a library that stays.
        if live and (rng.random() < args.refresh_rate or (force and cycle >= 2 and not counts["refreshes"])):
            lib = rng.choice(live)
            crawls.append(_crawl_spec(lib, proj["libs"][lib], "refresh", cycle, rng, args))
            counts["refreshes"] += 1
        # New libraries (two in the first cycle).
        wanted = 2 if cycle == 1 else int(rng.random() < args.create_rate)
        for _ in range(wanted):
            if len(proj["libs"]) >= args.libraries:
                break
            lang = ("en", "ar", "mixed")[proj["next_lib"] % 3]
            lib = f"kbsoak-{cycle}-{proj['next_lib']}-{lang}"
            proj["next_lib"] += 1
            spec = {"lang": lang, "seed_url": seed_url_for(lib), "next_page": args.pages + 1, "next_doc": 1,
                    "site": {str(n): 1 for n in range(1, args.pages + 1)}, "stored": {}, "docs": {}}
            proj["libs"][lib] = spec
            crawls.append(_crawl_spec(lib, spec, "site", cycle, rng, args))
            counts["crawls"] += 1
    ops += crawls
    for spec in crawls:
        _apply_crawl(proj, spec)
    crash = None
    if crawls and args.crash_rate > 0 and not final and (
            rng.random() < args.crash_rate or (cycle >= 4 and cycle <= _STAGE_BUDGET and not counts["crashes"])):
        last = crawls[-1]
        crash = {"op_index": len(ops) - 1, "page_index": rng.randrange(len(last["pages"]))}
        counts["crashes"] += 1
    # What the searches ask after the cycle, and what they must not find.
    facts = []
    for lib, spec in proj["libs"].items():
        facts += [_page_fact(args.seed, lib, spec, int(k), v) for k, v in spec["stored"].items()]
        facts += [_doc_fact(args.seed, lib, spec["lang"], k, d["version"]) for k, d in spec["docs"].items()]
    ask = rng.sample(facts, min(args.questions, len(facts)))
    for lib in sorted(probe):
        if lib in proj["libs"] and not any(f["library"] == lib for f in ask):
            ask += [f for f in facts if f["library"] == lib][:1]
    gone = []
    for kind, lib, key, version in proj["replaced"][-60:]:
        spec = proj["libs"].get(lib) or proj["deleted"].get(lib)
        if spec is None:
            continue
        fact = (_page_fact(args.seed, lib, spec, int(key), version) if kind == "page"
                else _doc_fact(args.seed, lib, spec["lang"], key, version))
        gone.append({**fact, "deleted": lib in proj["deleted"]})
    for lib, spec in list(proj["deleted"].items())[-5:]:
        gone += [{**_page_fact(args.seed, lib, spec, int(k), v), "deleted": True} for k, v in spec["stored"].items()]
        gone += [{**_doc_fact(args.seed, lib, spec["lang"], k, v), "deleted": True} for k, v in spec["docs"].items()]
    absent = rng.sample(gone, min(args.questions, len(gone)))
    tokens = [page_doc(args.seed, s["library"], s["lang"], p["page_no"], p["version"])[1]["token"]
              for s in crawls + proj["pending"] for p in s["pages"]]
    plan = {"mode": "soak-cycle", "cycle": cycle, "seed": args.seed, "embedder": proj["embedder"],
            "resume": list(proj["pending"]), "ops": ops, "crash": crash, "live": sorted(proj["libs"]),
            "ask": ask, "absent": absent, "top_k": args.top_k, "sweep_budget": args.sweep_budget,
            "sweep_passes": args.sweep_passes, "crawled_tokens": tokens,
            "deleted_documents": [{"library": lib, "document_id": d}
                                  for lib, spec in proj["deleted"].items() for d in spec.get("documents", [])]}
    return plan, proj


def _commit(plan: dict[str, Any], proj: dict[str, Any], ops: list[dict[str, Any]], crashed: bool) -> list[str]:
    """Fill what only the child knew (document ids) into the projected
    state; a crawl the child crashed in waits to be resumed. Returns the
    operations that did not end as planned."""
    problems: list[str] = []
    done = {line["index"]: line for line in ops if "index" in line}
    resumed = {line["spec"]: line for line in ops if line.get("op") == "resume"}
    proj["pending"] = [s for s in proj["pending"] if s["job_id"] not in resumed]
    for spec in plan["resume"]:
        line = resumed.get(spec["job_id"])
        if line is None:
            problems.append(f"resume of {spec['job_id']} did not run")
        else:
            problems += _crawl_outcome(spec, line["result"], resumed=True)
    for i, op in enumerate(plan["ops"]):
        line = done.get(i)
        if line is None:
            if crashed and plan.get("crash") and plan["crash"]["op_index"] == i:
                proj["pending"].append(op)
                continue
            problems.append(f"operation {i} ({op['op']}) did not run")
            continue
        result = line["result"]
        if op["op"] == "document":
            doc = proj["libs"].get(op["library"], {}).get("docs", {}).get(op["doc_key"])
            if not result.get("ok"):
                problems.append(f"document {op['library']}/{op['doc_key']} v{op['version']}: {result.get('code')}")
            if doc is not None:
                doc.update(document_id=result.get("document_id"), version_id=result.get("version_id"),
                           chunks=result.get("chunks"))
            else:  # its library was deleted in the same cycle: what the delete must take
                proj["deleted"].get(op["library"], {}).setdefault("documents", []).append(result.get("document_id"))
        elif op["op"] == "delete":
            if not result.get("deleted") or not result.get("provenance_removed"):
                problems.append(f"delete {op['library']}: {result}")
            proj["counts"]["delete_seconds_max"] = max(proj["counts"]["delete_seconds_max"], result.get("seconds", 0))
        else:
            problems += _crawl_outcome(op, result, resumed=False)
    return problems


def _crawl_outcome(spec: dict[str, Any], result: dict[str, Any], *, resumed: bool) -> list[str]:
    """A crawl ends completed, or partial when a planned page failed; each
    page as planned."""
    failing = {page_url(spec["seed_url"], p["page_no"]) for p in spec["pages"] if p["fail"]}
    want = "partial" if failing else "completed"
    problems = []
    if result.get("status") != want:
        problems.append(f"crawl {spec['job_id']}{' (resumed)' if resumed else ''} ended {result.get('status')}, "
                        f"expected {want}: {result.get('message')}")
    for p in spec["pages"]:
        url = page_url(spec["seed_url"], p["page_no"])
        expected = "failed" if url in failing else "completed"
        got = result.get("pages", {}).get(url)
        if got != expected:
            problems.append(f"crawl {spec['job_id']}: page {url} is {got}, expected {expected}")
    return problems


# ── the parent: the checks, from the stores ─────────────────────────────────

_EXPECTED_CACHE: dict[tuple[Any, ...], tuple[str, str, frozenset[tuple[str, str]]]] = {}


def expected_page(seed: int, lib: str, lang: str, seed_url: str, page_no: int,
                  version: int) -> tuple[str, str, frozenset[tuple[str, str]]]:
    """(url, text, {(chunk id, content hash)}) a stored page must hold, by
    the product's own chunker (a crawl stores what it chunks)."""
    key = (seed, lib, lang, seed_url, page_no, version)
    if key not in _EXPECTED_CACHE:
        from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict
        from kazma_core.stores.knowledge_ingest import _derive_title

        text, _fact = page_doc(seed, lib, lang, page_no, version)
        url = page_url(seed_url, page_no)
        chunks = chunk_markdown_doc(text, source_url=url, library_id=lib, document_title=_derive_title(text, url))
        _EXPECTED_CACHE[key] = (url, text, frozenset((d["id"], d["content_hash"])
                                                    for d in map(chunk_to_dict, chunks)))
        while len(_EXPECTED_CACHE) > 20_000:
            _EXPECTED_CACHE.pop(next(iter(_EXPECTED_CACHE)))
    return _EXPECTED_CACHE[key]


def _provenance(folder: Path) -> dict[str, str]:
    """url -> text of a library's fetched pages."""
    out = {}
    for path in folder.iterdir() if folder.is_dir() else ():
        content = path.read_text(encoding="utf-8")
        first = content.split("\n", 1)[0]
        if first.startswith("# Source: "):
            out[first[len("# Source: "):]] = content
    return out


def _journal_rows(data_dir: Path) -> list[dict[str, Any]]:
    """The crawl journal's rows, read straight from the settings database."""
    db = data_dir / "settings.db"
    if not db.is_file():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT key, value FROM settings WHERE category = 'acquisition_crawls'").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    out = []
    for key, value in rows:
        if not key.startswith("acquisition.crawl.") or key.endswith(".rev"):
            continue
        row = json.loads(value)
        if isinstance(row, dict) and not row.get("evicted"):
            out.append(row)
    return out


def check_stores(data_dir: Path, state: dict[str, Any], seed: int, *, tolerate_orphans: bool = False,
                 report: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    """Every invariant the stores themselves can show. Returns the
    violations and a growth sample."""
    problems: list[str] = []
    db = data_dir / "knowledge.db"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT library_id, source_url, id, content_hash, document_id, version_id, active, "
                            "tombstoned FROM knowledge_chunks").fetchall()
        libraries = {r[0] for r in conn.execute("SELECT id FROM knowledge_libraries")}
        coverage_rows = {r[0] for r in conn.execute("SELECT library_id FROM knowledge_vector_status")}
        keyword_missing = conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks c WHERE c.active = 1 AND c.tombstoned = 0 AND NOT EXISTS "
            "(SELECT 1 FROM knowledge_chunks_fts f WHERE f.rowid = c.rowid AND f.chunk_id = c.id)").fetchone()[0]
        keyword_stale = conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks_fts f WHERE NOT EXISTS (SELECT 1 FROM knowledge_chunks c "
            "WHERE c.rowid = f.rowid AND c.id = f.chunk_id AND c.active = 1 AND c.tombstoned = 0)").fetchone()[0]
        keyword_rows = conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0]
        gone_tokens = [_page_fact(seed, lib, spec, int(k), v)["token"]
                       for lib, spec in state["deleted"].items() for k, v in spec["stored"].items()]
        gone_tokens += [_doc_fact(seed, lib, spec["lang"], k, v)["token"]
                        for lib, spec in state["deleted"].items() for k, v in spec["docs"].items()]
        residue = _segment_residue(conn, gone_tokens) if gone_tokens else 0
    finally:
        conn.close()
    if residue:
        problems.append(f"keyword index: its segments still hold {residue} of {len(gone_tokens)} planted "
                        "words of deleted libraries")
    live = set(state["libs"])
    if libraries != live:
        problems.append(f"libraries: the store holds {sorted(libraries)}, expected {sorted(live)}")
    pages: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    documents: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_library: dict[str, int] = defaultdict(int)
    inactive = 0
    for lib, url, cid, digest, doc, version, active, tombstoned in rows:
        by_library[lib] += 1
        if not active or tombstoned:
            inactive += 1
        elif doc:
            documents[(lib, doc)].append(version)
        else:
            pages[(lib, url)].add((cid, digest))
    expected_urls: dict[str, dict[str, str]] = {}
    for lib, spec in state["libs"].items():
        expected_urls[lib] = {}
        for key, version in spec["stored"].items():
            url, text, want = expected_page(seed, lib, spec["lang"], spec["seed_url"], int(key), version)
            expected_urls[lib][url] = text
            got = pages.pop((lib, url), set())
            if got != want:
                lost, extra = len(want - got), len(got - want)
                problems.append(f"chunks of {url} (v{version}): {lost} published and not active (lost), "
                                f"{extra} active and not published (doubled or stale)")
        for key, doc in spec["docs"].items():
            if not doc.get("document_id"):
                continue
            versions = documents.pop((lib, doc["document_id"]), [])
            if len(versions) != doc.get("chunks") or set(versions) != {doc.get("version_id")}:
                problems.append(f"document {lib}/{key} v{doc['version']}: {len(versions)} active chunk(s) of "
                                f"versions {sorted(set(map(str, versions)))}, expected {doc.get('chunks')} of "
                                f"{doc.get('version_id')}")
    for (lib, url), got in sorted(pages.items()):
        problems.append(f"{len(got)} active chunk(s) of {url} in {lib} that no crawl stored")
    for (lib, doc), versions in sorted(documents.items()):
        problems.append(f"{len(versions)} active chunk(s) of document {doc} in {lib} that were not published")
    for lib in state["deleted"]:
        if by_library.get(lib):
            problems.append(f"deleted library {lib} keeps {by_library[lib]} chunk row(s)")
    if keyword_missing or keyword_stale:
        problems.append(f"keyword index: {keyword_missing} active chunk(s) without a row, "
                        f"{keyword_stale} row(s) without an active chunk")
    for lib in sorted(coverage_rows - live):
        problems.append(f"a coverage record for {lib}, which is not a live library")
    # Fetched pages: exactly the stored pages of live libraries, as stored.
    base = data_dir / "knowledge_provenance"
    safe = {_safe(lib): lib for lib in live}
    provenance_files = 0
    for folder in sorted(base.iterdir()) if base.is_dir() else ():
        lib = safe.get(folder.name)
        files = _provenance(folder)
        provenance_files += len(files)
        if lib is None:
            problems.append(f"fetched pages of {folder.name}, which is not a live library, left on disk "
                            f"({len(files)} file(s))")
            continue
        if len(files) > _PROVENANCE_MAX_FILES:
            problems.append(f"{lib}: {len(files)} fetched pages kept (bound {_PROVENANCE_MAX_FILES})")
        want = expected_urls.get(lib, {})
        if set(files) != set(want):
            problems.append(f"{lib}: fetched pages kept for {len(files)} page(s), {len(want)} stored "
                            f"({len(set(want) - set(files))} missing, {len(set(files) - set(want))} extra)")
        stale = [url for url, content in files.items() if url in want and not content.endswith(want[url])]
        if stale:
            problems.append(f"{lib}: {len(stale)} fetched page(s) hold another version than the stored one")
    for lib, spec in state["libs"].items():
        if spec["stored"] and not (base / _safe(lib)).is_dir():
            problems.append(f"{lib}: no fetched pages kept for its {len(spec['stored'])} stored page(s)")
    leftovers = _unified_items(data_dir)
    if leftovers:
        problems.append(f"unified_items exists in {leftovers}")
    journal = _journal_rows(data_dir)
    journal_bytes = sum(len(json.dumps(r, ensure_ascii=True)) for r in journal)
    if len(journal) > _JOURNAL_MAX_JOBS or journal_bytes > _JOURNAL_MAX_BYTES:
        problems.append(f"crawl journal: {len(journal)} jobs, {journal_bytes} bytes "
                        f"(bound {_JOURNAL_MAX_JOBS} jobs, {_JOURNAL_MAX_BYTES} bytes)")
    running = [r["job_id"] for r in journal if r.get("status") == "running"]
    if running:
        problems.append(f"crawl journal: {running} still running after the cycle")
    vector_dir = data_dir / "vector_memory"
    orphans = _orphan_vector_folders(vector_dir)
    if report is not None:
        problems += _orphan_outcome(report, "vector store", orphans, tolerate_orphans)
    elif orphans.get("orphans") and not tolerate_orphans:
        problems.append(f"vector store: {orphans['orphans']} vector folder(s) no collection owns")
    catalog_text = _vector_catalog_text(vector_dir, gone_tokens)
    if catalog_text.get("error"):
        problems.append(f"vector store: its catalog could not be read for text: {catalog_text['error']}")
    if catalog_text.get("text_rows"):
        problems.append(f"vector store: {catalog_text['text_rows']} catalog row(s) hold text beside a vector "
                        "(a document or metadata Kazma never reads)")
    if catalog_text.get("deleted_words"):
        problems.append(f"vector store: chroma.sqlite3 still holds {catalog_text['deleted_words']} of "
                        f"{len(gone_tokens)} planted words of deleted libraries")
    sample = {
        "libraries": len(libraries), "active_chunks": len(rows) - inactive, "inactive_chunks": inactive,
        "keyword_rows": keyword_rows, "deleted_words_in_keyword_segments": residue,
        "journal_jobs": len(journal), "journal_bytes": journal_bytes,
        "journal_jobs_of_deleted_libraries": sum(1 for r in journal if r.get("library_id") in state["deleted"]),
        "provenance_files": provenance_files, "knowledge_db_bytes": _files_bytes(db),
        "settings_db_bytes": _files_bytes(data_dir / "settings.db"),
        "vector_dir_bytes": _files_bytes(vector_dir) if vector_dir.is_dir() else 0,
        "chroma_sqlite_bytes": _files_bytes(vector_dir / "chroma.sqlite3"),
        "vector_folders": orphans.get("folders"), "orphan_vector_folders": orphans.get("orphans"),
        "orphan_vector_bytes": orphans.get("orphan_bytes"),
        "vector_catalog_text_rows": catalog_text.get("text_rows"),
        "deleted_words_in_vector_catalog": catalog_text.get("deleted_words"),
    }
    return problems, sample


def _vector_catalog_text(vector_dir: Path, tokens: list[str]) -> dict[str, Any]:
    """What text Chroma's catalog holds: rows beside a vector that are not
    the metadata Kazma reads (``text_rows``), and how many of *tokens* --
    planted words of deleted libraries -- any byte of ``chroma.sqlite3`` or
    its journal still holds (``deleted_words``: free pages included)."""
    from kazma_core.memory import chroma_local
    from kazma_core.memory.vector_store_global import KEPT_METADATA

    out: dict[str, Any] = {"text_rows": 0, "deleted_words": 0}
    files = [p for p in (vector_dir / "chroma.sqlite3", vector_dir / "chroma.sqlite3-journal",
                         vector_dir / "chroma.sqlite3-wal") if p.is_file()]
    if not files:
        return out
    try:
        out["text_rows"] = sum(chroma_local.catalog_text_rows(vector_dir, KEPT_METADATA).values())
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    raw = b"".join(p.read_bytes() for p in files)
    out["deleted_words"] = sum(1 for t in tokens if t.encode() in raw)
    return out


def check_facts(plan: dict[str, Any], facts: dict[str, Any], *,
                failed_before: frozenset[str] | set[str] = frozenset(),
                tolerate_unreadable: bool = False) -> tuple[list[str], dict[str, list[str]]]:
    """What only a process holding the vector store can read, as the child
    reported it, and the searches.

    Returns ``(violations, confined)``. ``confined`` holds, per library, the
    problems a vector-store read failure explains, when the child saw Chroma
    fail to read that library's collection (``vector_read_failures``) in
    this process only: a fresh process reads it again, so the soak counts it
    as a finding and has the next start ask that library. The same library
    failing in the previous process too (``failed_before``) survived a
    restart, and that is a violation -- unless ``tolerate_unreadable``
    (``--tolerate-vector-read-failures``), which counts it as a finding too.
    """
    problems: list[str] = []
    confined: dict[str, list[str]] = defaultdict(list)
    unreadable = set(facts.get("vector_read_failures") or {})

    def vector(lib: str, text: str) -> None:
        if lib in unreadable and (lib not in failed_before or tolerate_unreadable):
            confined[lib].append(text)
        elif lib in unreadable:
            problems.append(f"{text} (it failed in the previous process too: it survived a restart)")
        else:
            problems.append(text)

    if facts.get("network_refused"):
        problems.append(f"network attempts refused: {sorted(set(facts['network_refused']))}")
    fts = facts.get("fts_before_sweep") or {}
    if not fts.get("ok", False):
        problems.append(f"keyword index before the repair sweep: {fts}")
    sweep = facts.get("sweep") or {}
    if sweep.get("error"):
        failed = set(sweep.get("failed") or {})
        if failed and failed <= unreadable:
            for lib in sorted(failed):
                vector(lib, f"vector coverage: the repair sweep failed for {lib}: {sweep['failed'][lib]}")
        else:
            problems.append(f"vector coverage: {sweep['error']}")
    for lib, v in (facts.get("vectors") or {}).items():
        if not v.get("available"):
            vector(lib, f"{lib}: the vector store is unavailable {v.get('error') or ''}".strip())
            continue
        if v["missing"] or v["orphans"] or v["foreign"]:
            vector(lib, f"{lib}: {v['missing']} chunk(s) without a vector of {v['identity']}, "
                        f"{v['orphans']} vector(s) without a chunk, {v['foreign']} of another embedder")
        if v["health_state"] != "ok" or v["health_vectors"] != v["health_chunks"]:
            vector(lib, f"{lib}: the coverage record says {v['health_state']} "
                        f"{v['health_vectors']}/{v['health_chunks']} after the sweep")
    names = set(facts.get("collections") or [])
    want = {f"kazma_kb_{lib}" for lib in plan["live"]}
    for name in sorted(n for n in names - want if n.startswith("kazma_kb_")):
        problems.append(f"collection {name} belongs to no live library")
    for ask in facts.get("asked") or []:
        for text in _ask_violations(ask, "search"):
            if (ask.get("meaning_miss") or {}).get("kind") == "query_failed" and "meaning search:" in text:
                vector(ask["library"], text)
            else:
                problems.append(text)
    for gone in facts.get("absent") or []:
        if gone.get("problem"):
            problems.append(f"{gone['library']}: {gone['problem']}")
        if gone.get("found_by"):
            problems.append(f"replaced or deleted answer {gone['token']} ({gone['library']}) still found by "
                            f"{', '.join(gone['found_by'])}")
    journal = facts.get("journal") or {}
    if journal.get("rows", 0) > _JOURNAL_MAX_JOBS or journal.get("bytes", 0) > _JOURNAL_MAX_BYTES:
        problems.append(f"crawl journal (its reader): {journal.get('rows')} jobs, {journal.get('bytes')} bytes")
    if journal.get("running"):
        problems.append(f"crawl journal (its reader): {journal['running']} running after the cycle")
    if journal.get("text_leaks"):
        problems.append(f"crawl journal holds page text: {journal['text_leaks'][:5]}")
    if facts.get("document_index_leaks"):
        problems.append(f"the Documents platform still indexes documents in deleted libraries: "
                        f"{facts['document_index_leaks'][:5]}")
    return problems, dict(confined)


# ── the parent: soak ────────────────────────────────────────────────────────


def _thin(samples: list[dict[str, Any]], keep: int = 400) -> list[dict[str, Any]]:
    if len(samples) <= keep:
        return samples
    step = math.ceil(len(samples) / keep)
    return samples[:50] + samples[50::step] + samples[-1:]


def _soak_summary(report: dict[str, Any], lat: dict[str, list[float]], started: float) -> dict[str, Any]:
    out = {k: v for k, v in report.items() if not k.startswith("_")}
    out.update({
        "updated_at": utc_stamp(),
        "seconds": round(time.monotonic() - started, 1),
        "search_ms": {layer: latency_summary(values) for layer, values in lat.items()},
        "growth": _thin(report["_growth"]),
        "growth_last": report["_growth"][-1] if report["_growth"] else None,
    })
    return out


def run_soak(args: argparse.Namespace) -> int:
    """The soak, with this process's environment put back afterwards: the run
    points it at its private stores, and a caller in the same process (the
    harness's own tests) must not keep reading them."""
    saved = dict(os.environ)
    try:
        return _run_soak(args)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _run_soak(args: argparse.Namespace) -> int:
    work = Path(tempfile.mkdtemp(prefix="kazma-kb-soak-"))
    data_dir = Path(args.data_dir) if args.data_dir else work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update(_env(data_dir))  # the parent reads the chunker only, never a store
    rng = random.Random(args.seed)
    state = _new_state(args)
    report = _base_report("soak", args, data_dir)
    report.update({
        "parameters": {k: v for k, v in vars(args).items() if k not in ("report", "data_dir", "func", "command")},
        "cycles": 0, "restarts": 0, "crashes": 0,
        "operations": {"crawls": 0, "refreshes": 0, "resumes": 0, "documents": 0, "deletes": 0,
                       "model_switches": 0, "pages_fetched": 0, "pages_failed_as_planned": 0},
        "switches": [], "sweep": {"passes": 0, "embedded": 0, "removed": 0, "seconds_max": 0.0},
        "recall": {"hybrid": [0, 0], "keyword": [0, 0], "meaning": [0, 0]}, "meaning_transient_failures": 0,
        "hybrid_rank_drops": 0, "meaning_passed_over": 0, "meaning_query_failed": 0,
        "identities": [], "child": {"peak_rss_mib": None, "handles_max": None},
        "_growth": [],
    })
    lat: dict[str, list[float]] = {"hybrid": [], "keyword": [], "meaning": []}
    started = time.monotonic()
    cycle = 0
    final = False
    finals = 0
    unreadable: set[str] = set()  # libraries the last process could not read the vectors of
    while True:
        if not final and (time.monotonic() - started >= args.duration
                          or (args.max_cycles and cycle >= args.max_cycles)):
            final = True  # one more start: resume what is pending, sweep, check everything
        cycle += 1
        finals += final
        plan, proj = _plan_cycle(cycle, rng, state, args, final=final, probe=unreadable)
        code, ops, facts, err = _run_child(plan, _env(data_dir), work, args.child_timeout,
                                          args.child_log_level)
        report["cycles"] = cycle
        report["restarts"] += 1
        crashed = code == _KILLED and plan.get("crash") is not None
        if code not in (0, _KILLED) or (code == _KILLED and not crashed) or (code == 0 and facts is None):
            report["violations"].append(f"cycle {cycle}: the child failed ({code}): {err}")
            break
        problems = [f"cycle {cycle}: {p}" for p in _commit(plan, proj, ops, crashed)]
        _count_operations(report, plan, ops, crashed)
        state = proj
        if crashed:
            report["crashes"] += 1
            report["violations"] += problems
            _write_report(args.report, _soak_summary(report, lat, started))
            if problems:
                break
            continue
        fact_problems, confined = check_facts(plan, facts, failed_before=unreadable,
                                              tolerate_unreadable=args.tolerate_vector_read_failures)
        problems += [f"cycle {cycle}: {p}" for p in fact_problems]
        store_problems, sample = check_stores(data_dir, state, args.seed,
                                              tolerate_orphans=args.tolerate_orphan_vector_folders, report=report)
        problems += [f"cycle {cycle}: {p}" for p in store_problems]
        _record_cycle(report, lat, plan, facts, sample, cycle)
        report["violations"] += problems
        if confined:
            _note_unreadable(report, cycle, facts, confined, work, err, survived=unreadable & set(confined))
        unreadable = set(confined)
        if problems:  # the product's own log of the cycle, for whoever reads the report
            log = work / f"cycle-{cycle}.log"
            log.write_text(err, encoding="utf-8")
            report["violation_log"] = str(log)
        _write_report(args.report, _soak_summary(report, lat, started))
        # The last start must read every library: one that could not be read
        # is asked again by one more start (twice at most).
        if problems or (final and (not confined or finals >= 3)):
            if final and confined and not problems and not args.tolerate_vector_read_failures:
                report["violations"].append(f"cycle {cycle}: libraries still unreadable after {finals} final "
                                            f"starts: {sorted(confined)}")
            break
        time.sleep(max(0.0, args.cycle_seconds))
    report["held"] = {
        "live_libraries": len(state["libs"]), "deleted_libraries": len(state["deleted"]),
        "stored_pages": sum(len(s["stored"]) for s in state["libs"].values()),
        "documents": sum(len(s["docs"]) for s in state["libs"].values()),
        "pending_resumes": len(state["pending"]),
    }
    summary = _soak_summary(report, lat, started)
    finished(summary, not summary["violations"])
    _write_report(args.report, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if summary["healthy"] else 1


def _note_unreadable(report: dict[str, Any], cycle: int, facts: dict[str, Any], confined: dict[str, list[str]],
                     work: Path, err: str, *, survived: set[str]) -> None:
    """A process that could not read a library's vectors: a counted finding,
    with the product's log of that cycle kept, and the next start asks the
    library again (a violation if it fails there too, unless tolerated)."""
    log = work / f"cycle-{cycle}.log"
    log.write_text(err, encoding="utf-8")
    events = report.setdefault("vector_read_failures", [])
    for lib, texts in sorted(confined.items()):
        events.append({"cycle": cycle, "library": lib, "error": (facts.get("vector_read_failures") or {}).get(lib),
                       "survived_a_restart": lib in survived, "problems": texts[:5], "log": str(log)})
    del events[:-50]
    report["vector_read_failure_count"] = report.get("vector_read_failure_count", 0) + len(confined)
    survivors = sum(e["survived_a_restart"] for e in events)
    _add_finding(report, "vector store read failures",
                 f"{report['vector_read_failure_count']} time(s) a process could not read a library's vectors "
                 f"(Chroma: {events[-1]['error']}); meaning search and the repair sweep failed for that library "
                 f"in that process, and the next start was asked to read it again"
                 + (f"; {survivors} of the last {len(events)} failed in the next process too (tolerated)"
                    if survivors else "") + f" (last log: {events[-1]['log']})")


def _count_operations(report: dict[str, Any], plan: dict[str, Any], ops: list[dict[str, Any]],
                      crashed: bool) -> None:
    counts = report["operations"]
    for line in ops:
        result = line.get("result") or {}
        if line.get("op") == "resume":
            counts["resumes"] += 1
        elif line.get("op") == "document":
            counts["documents"] += 1
        elif line.get("op") == "delete":
            counts["deletes"] += 1
        if line.get("op") in ("resume", "crawl"):
            if line.get("op") == "crawl":
                kind = plan["ops"][line["index"]]["kind"]
                counts["crawls" if kind == "site" else "refreshes"] += 1
            statuses = list((result.get("pages") or {}).values())
            counts["pages_fetched"] += statuses.count("completed")
            counts["pages_failed_as_planned"] += statuses.count("failed")


def _record_cycle(report: dict[str, Any], lat: dict[str, list[float]], plan: dict[str, Any],
                  facts: dict[str, Any], sample: dict[str, Any], cycle: int) -> None:
    recall = report["recall"]
    for ask in facts.get("asked") or []:
        for layer in lat:
            if ask.get(f"{layer}_ms") is not None:
                lat[layer].append(ask[f"{layer}_ms"])
        for layer in ("hybrid", "keyword", "meaning"):
            recall[layer][0] += bool(ask.get(layer))
            recall[layer][1] += 1
        report["meaning_passed_over"] += _passed_over(ask)
        report["meaning_query_failed"] += (ask.get("meaning_miss") or {}).get("kind") == "query_failed"
        if _rank_dropped(ask):
            report["hybrid_rank_drops"] += 1
            _add_finding(report, "hybrid ranking",
                         f"{report['hybrid_rank_drops']} planted answer(s) the keyword index returned (last at rank "
                         f"{ask.get('keyword_rank')}) were left out of the hybrid top {report['parameters']['top_k']} "
                         "when the approximate vector index passed over their vectors")
        if ask.get("meaning_transient_failure"):
            report["meaning_transient_failures"] += 1
            _add_finding(report, "meaning search failures",
                         f"{report['meaning_transient_failures']} meaning quer(ies) failed once and answered "
                         f"when asked again (cycle {cycle}, last: {ask['meaning_transient_failure']})")
    if recall["meaning"][0] < recall["meaning"][1]:
        _add_finding(report, "meaning recall",
                     f"meaning search returned {recall['meaning'][0]} of {recall['meaning'][1]} planted answers: "
                     f"{report['meaning_passed_over']} missed with their vector present (the approximate HNSW "
                     f"index passed over it), {report['meaning_query_failed']} "
                     "in a process that could not read the library's collection (see the read failures)")
    reloads = sum((facts.get("index_reloads") or {}).values())
    if reloads:
        report["index_reloads"] = report.get("index_reloads", 0) + reloads
        _add_finding(report, "vector index reloads",
                     f"Chroma lost {report['index_reloads']} index(es) (evicted before written to disk), the "
                     f"last in cycle {cycle}; the product rebuilt each from Chroma's log and asked again")
    sweep = facts.get("sweep") or {}
    agg = report["sweep"]
    agg["passes"] += len(sweep.get("passes") or [])
    agg["embedded"] += sweep.get("embedded", 0)
    agg["removed"] += sweep.get("removed", 0)
    agg["seconds_max"] = max(agg["seconds_max"], sweep.get("seconds", 0.0))
    identity = facts.get("identity")
    previous = report["identities"][-1] if report["identities"] else None
    if identity and identity != previous:
        report["identities"].append(identity)
        if previous is not None:
            report["operations"]["model_switches"] += 1
            report["switches"].append({
                "cycle": cycle, "from": previous, "to": identity,
                "size_changed": previous.split("|")[2:3] != identity.split("|")[2:3],
                "reembedded": sweep.get("embedded", 0), "passes": len(sweep.get("passes") or []),
                "seconds": sweep.get("seconds")})
            del report["switches"][:-50]
    child = report["child"]
    rss = facts.get("peak_rss_mib")
    if rss is not None:
        child["peak_rss_mib"] = max(child["peak_rss_mib"] or 0, rss)
    handles = (facts.get("process") or {}).get("handles")
    if handles is not None:
        child["handles_max"] = max(child["handles_max"] or 0, handles)
    report["_growth"].append({"cycle": cycle, **sample})


# ── command line ────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--report", default=None, help="the JSON report, rewritten as the run goes")
    common.add_argument("--seed", type=int, default=20261015)
    common.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    common.add_argument("--embedder", choices=("hash", "configured"), default="hash")
    common.add_argument("--hardware-name", default=None, help="what to call this machine in the report")
    common.add_argument("--sweep-budget", type=float, default=300.0, help="seconds per repair pass (product default)")
    common.add_argument("--top-k", type=int, default=5)
    common.add_argument("--child-log-level", default="WARNING", help="the product's log level in the children")
    common.add_argument("--tolerate-orphan-vector-folders", action="store_true",
                        help="count a vector folder no collection owns as a finding, not a violation "
                             "(for a build from before 2026-10-15)")
    common.add_argument("--tolerate-vector-read-failures", action="store_true",
                        help="count a collection Chroma cannot read in two processes in a row as a finding, "
                             "not a violation (for a build from before 2026-10-15)")
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    scale = sub.add_parser("scale", parents=[common], help="measure ingest, search, sweep, restore and delete")
    scale.add_argument("--chunks", default="10000", help="comma-separated sizes, e.g. 10000,100000,1000000")
    scale.add_argument("--languages", default="en,ar,mixed")
    scale.add_argument("--questions", type=int, default=40, help="planted questions per step")
    scale.add_argument("--search-repeats", type=int, default=5)
    scale.add_argument("--page-chunks", type=int, default=20, help="chunks per page")
    scale.add_argument("--batch-pages", type=int, default=50, help="pages per index call")
    scale.add_argument("--max-sweep-passes", type=int, default=10000)
    scale.add_argument("--keep-data", action="store_true", help="keep each step's store after measuring it")
    breadth = sub.add_parser("breadth", parents=[common],
                             help="meaning recall and latency of one library's collection at each search breadth")
    breadth.add_argument("--chunks", default="10000", help="comma-separated sizes, e.g. 10000,100000")
    breadth.add_argument("--languages", default="en,ar,mixed")
    breadth.add_argument("--breadths", default="256,512,1024,2048,auto",
                         help="comma-separated search breadths; auto = the breadth the product sets")
    breadth.add_argument("--questions", type=int, default=40, help="planted questions per step")
    breadth.add_argument("--search-repeats", type=int, default=5)
    breadth.add_argument("--batch", type=int, default=2000, help="vectors per index call")
    breadth.add_argument("--keep-data", action="store_true", help="keep each step's store after measuring it")
    breadth.add_argument("--reuse-builds", action="store_true",
                         help="measure a kept store again instead of building it (with --data-dir)")
    soak = sub.add_parser("soak", parents=[common], help="a mixed workload, a restart every cycle")
    soak.add_argument("--duration", type=float, default=600.0, help="seconds to run")
    soak.add_argument("--cycle-seconds", type=float, default=30.0, help="pause between cycles")
    soak.add_argument("--max-cycles", type=int, default=0, help="stop after this many (0: by time)")
    soak.add_argument("--libraries", type=int, default=4, help="live libraries at most")
    soak.add_argument("--pages", type=int, default=8, help="pages of a new site")
    soak.add_argument("--questions", type=int, default=12, help="questions asked (and refused) per cycle")
    soak.add_argument("--doc-rate", type=float, default=0.5)
    soak.add_argument("--delete-rate", type=float, default=0.15)
    soak.add_argument("--refresh-rate", type=float, default=0.7)
    soak.add_argument("--create-rate", type=float, default=0.3)
    soak.add_argument("--change-rate", type=float, default=0.3, help="share of a site's pages changed per refresh")
    soak.add_argument("--remove-rate", type=float, default=0.1, help="share removed from the site per refresh")
    soak.add_argument("--fail-rate", type=float, default=0.1, help="share of pages that fail in a crawl")
    soak.add_argument("--switch-every", type=int, default=40, help="cycles between embedding-model switches")
    soak.add_argument("--crash-rate", type=float, default=0.05, help="share of cycles that crash inside a crawl")
    soak.add_argument("--sweep-passes", type=int, default=5, help="repair passes a cycle may take")
    soak.add_argument("--child-timeout", type=float, default=1800.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--child"]:
        return _child(Path(argv[1]))
    args = _parser().parse_args(argv)
    runs = {"scale": run_scale, "breadth": run_breadth, "soak": run_soak}
    return runs[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
