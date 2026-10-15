"""ChromaDB global vector store (shared, non-chat-memory).

Backs the knowledge-base index, semantic cache, and semantic router — NOT the
chat memory (which is V2 since the V1→V2 cutover). Relocated here from
``swarm/memory/vector.py`` so the V1 ``swarm/memory`` package could be deleted
without breaking the subsystems that depend on this class.

Encoder pattern: ``get_encoder()`` delegates to the pluggable
``embedder.get_embedder()`` factory so the system can use either local
sentence-transformers or a remote OpenAI-compatible endpoint (NVIDIA NIM).
All vector backends MUST use ``get_encoder()`` / ``get_embedder()`` so the
model is never loaded twice.
"""

from __future__ import annotations

import atexit
import functools
import hashlib
import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from kazma_core.memory import chroma_local
from kazma_core.memory.embedder import get_embedder

__all__ = [
    "KEPT_METADATA",
    "VectorStore",
    "compact_vectors",
    "directory_client",
    "embedder_identity",
    "get_encoder",
    "index_reloads",
    "release_dropped_vectors",
]

logger = logging.getLogger(__name__)

_DEFAULT_COLLECTION = "kazma_global"

#: One Chroma client per persist directory, for the whole process: every
#: collection on it (one per Knowledge Library) shares it. Opening a
#: second PersistentClient on the same directory double-loads its SQLite
#: handle. This lived in ``memory/chroma_client.py`` until 2026-07-31, when
#: that module was deleted with V1 while this import of it stayed -- inside a
#: ``try`` that reported "chromadb not installed", so the Knowledge Library
#: had no meaning search from then until 2026-09-26.
_CLIENTS: dict[str, Any] = {}
_CLIENTS_LOCK = threading.Lock()
#: After a failed start, how long before a store tries again (and at most how
#: often it says so): a store is asked on every search.
_RETRY_AFTER_S = 300.0

#: Collections compare by angle. Chroma's default is squared L2, which every
#: collection got until 2026-09-26 (no ``hnsw:space`` was set) while
#: :meth:`VectorStore.query` reported ``1 - distance`` as a cosine. Vectors
#: are made unit length before they are stored or searched, so an older L2
#: collection ranks exactly as a cosine one, and :func:`_similarity` turns
#: either distance into the cosine it stands for.
_SPACE = "cosine"

#: The HNSW index a new collection is built with (KB-Q01, 2026-10-15).
#: Chroma's defaults (16 neighbours, construction and search breadth 100)
#: returned 31-39 of 40 planted answers at 10,000 chunks while an exact
#: search ranked every one of them first (``scripts/knowledge_soak.py``'s
#: corpus, EN/AR/mixed). 48 neighbours, a construction breadth of 400 and a
#: search breadth of 256 returned 40 of 40 in 11 of 12 measurements (39 in
#: the other; Chroma builds in parallel, so graphs differ a little), for
#: about 1 ms more a query and a tenth more build time; a wider search on
#: the default graph needed a breadth of 2,000 to reach 40. An existing
#: collection gets the search breadth by Chroma's ``modify`` (it applies
#: when Chroma next loads the index) and the graph by
#: :meth:`VectorStore.rebuild_graph`, which copies its vectors -- nothing
#: is embedded again.
_HNSW: dict[str, Any] = {"space": _SPACE, "max_neighbors": 48, "ef_construction": 400, "ef_search": 256}
#: Vectors copied per call when a collection's graph is rebuilt.
_COPY_PAGE = 500

#: A record id no Knowledge chunk has (theirs are ``library:url digest:
#: position:content digest``, the digests hexadecimal). Deleting it
#: pushes one record to a collection's write log, and Chroma rebuilds an
#: index it lost from that log before it applies the record -- the one way
#: to make it replay the log without a restart (see :mod:`chroma_local`).
_RELOAD_PROBE_ID = "kazma:reload-index"
#: How many times this process rebuilt a lost index, by collection
#: (``/metrics``, the repair report): expected 0.
_INDEX_RELOADS: dict[str, int] = {}

#: The only metadata a vector is stored with: what Kazma filters or reads
#: by (``model`` -- the embedder that made it -- the library, the document
#: of a document-scoped search, and the generic store's tenant and source).
#: Kazma stores no document beside a vector either (KB-Q01, 2026-10-15): the
#: Knowledge store holds the chunk, and an older build's 2,000-character
#: preview, title, heading and address -- read by nothing -- stayed readable
#: in ``chroma.sqlite3`` after the vector was deleted. Every write keeps
#: these keys only, and a collection holding anything else is rebuilt.
KEPT_METADATA = frozenset({"model", "library_id", "document_id", "tenant_id", "source"})
#: How long a collection found to hold vectors of another embedder is
#: trusted to still hold some, before it is asked again.
_MIXED_RECHECK_S = 60.0


def _kept(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """*metadata* cut to :data:`KEPT_METADATA` (None when nothing is left:
    Chroma refuses an empty mapping)."""
    kept = {k: v for k, v in (metadata or {}).items() if k in KEPT_METADATA and v is not None}
    return kept or None


_T = TypeVar("_T")


def _client_key(persist_dir: str | None) -> str:
    return str(Path(persist_dir).expanduser().resolve()) if persist_dir else ":memory:"


class _Gate:
    """Every Chroma call on a client holds the gate shared; closing the
    client (to let go of a dropped collection's files) takes it exclusive,
    which waits for the calls in flight and holds new ones. Shared is
    re-entrant within a thread, and a waiting close holds new callers."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._shared = 0
        self._closing = False
        self._waiting = 0
        self._local = threading.local()

    @contextmanager
    def shared(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            with self._cond:
                self._cond.wait_for(lambda: not self._closing and not self._waiting)
                self._shared += 1
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth = depth
            if depth == 0:
                with self._cond:
                    self._shared -= 1
                    self._cond.notify_all()

    def acquire_exclusive(self, timeout: float) -> bool:
        if getattr(self._local, "depth", 0):
            return False  # a caller inside a Chroma call never closes the client
        with self._cond:
            self._waiting += 1
            try:
                ok = self._cond.wait_for(lambda: not self._closing and not self._shared, timeout)
                if ok:
                    self._closing = True
                return ok
            finally:
                self._waiting -= 1
                self._cond.notify_all()

    def release_exclusive(self) -> None:
        with self._cond:
            self._closing = False
            self._cond.notify_all()


_GATES: dict[str, _Gate] = {}
#: Bumped each time a directory's client is closed: a store holding a
#: collection of an older client re-opens it.
_GENERATIONS: dict[str, int] = {}
_EXIT_HOOK: list[bool] = []


def _gate(key: str) -> _Gate:
    with _CLIENTS_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = _GATES[key] = _Gate()
        return gate


def _gated(method: Callable[..., _T]) -> Callable[..., _T]:
    """Run a :class:`VectorStore` method inside its client's shared gate."""

    @functools.wraps(method)
    def wrapper(self: VectorStore, *args: Any, **kwargs: Any) -> _T:
        with _gate(self._key).shared():
            return method(self, *args, **kwargs)

    return wrapper


#: Ids fetched per call when listing a collection.
_ID_PAGE = 5000
#: The most text one vector is made from; keyword search still reads the
#: whole chunk. Measured on the live machine (bge-m3, 24 CPU threads,
#: 2026-09-27): chunks of median length embed at ~18 a second, but the 32
#: longest took 113 s at a 4,000-character cap, 58 s at 1,500 and 37 s at
#: 1,000 -- and the model would read 8,192 tokens of the 60,000-character
#: one. 1,500 characters (a few hundred words) carries a chunk's subject;
#: 942 of the live 6,598 chunks are longer.
_EMBED_MAX_CHARS = 1500


def _unit(vec: Any) -> list[float] | None:
    """*vec* scaled to length 1; None for an empty, zero or non-finite vector."""
    if vec is None:
        return None
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(x * x for x in values))
    if not values or not math.isfinite(norm) or norm == 0.0:
        return None
    return [x / norm for x in values]


def _similarity(distance: float, space: str) -> float:
    """The cosine similarity a Chroma distance between unit vectors stands for."""
    if space == "l2":  # squared euclidean: |a - b|^2 = 2 - 2 cos
        return 1.0 - distance / 2.0
    return 1.0 - distance  # cosine: 1 - cos; ip: 1 - a.b


def _collection_space(collection: Any) -> str:
    """The distance a collection was created with (Chroma's default is l2)."""
    config = getattr(collection, "configuration_json", None)
    hnsw = config.get("hnsw") if isinstance(config, dict) else None
    space = hnsw.get("space") if isinstance(hnsw, dict) else None
    if not space:
        metadata = getattr(collection, "metadata", None)
        space = metadata.get("hnsw:space") if isinstance(metadata, dict) else None
    return str(space or "l2")


def _client_key(persist_dir: str | None) -> str:
    return str(Path(persist_dir).expanduser().resolve()) if persist_dir else ":memory:"


def _shared_client(persist_dir: str | None) -> Any:
    """The process's Chroma client for *persist_dir* (in-memory when None).

    A client registered for the folder (:func:`directory_client`) is used
    as it is. Telemetry off: Chroma's default sends usage events to PostHog.
    Before a directory's client starts -- before any collection opens, so
    nothing in this process holds a folder -- the open-file limit loaded
    indexes use is raised, the folders no collection owns are removed, the
    store's id is recorded, and Chroma's system is started with Kazma's
    index pool, one no shard of which can overflow (:mod:`chroma_local`).
    """
    key = _client_key(persist_dir)
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is not None:
            return client
    import chromadb
    from chromadb.config import Settings

    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            settings = Settings(anonymized_telemetry=False)
            if persist_dir:
                Path(key).mkdir(parents=True, exist_ok=True)
                chroma_local.raise_stream_limits()
                chroma_local.remove_orphan_segment_folders(key)
                chroma_local.vector_store_id(key, create=True)
                if getattr(chromadb, "__path__", None) is not None:  # the package (not a stand-in)
                    try:
                        chroma_local.start_system(key, settings)
                    except (ImportError, AttributeError) as exc:  # Chroma moved what Kazma sizes
                        logger.warning("[VectorStore] Chroma's index pool keeps its default size, which a "
                                       "shard of many libraries can overflow: %s", exc)
                client = chromadb.PersistentClient(path=key, settings=settings)
                if not _EXIT_HOOK:
                    _EXIT_HOOK.append(True)
                    atexit.register(_release_at_exit)
            else:
                client = chromadb.Client(settings)
            _CLIENTS[key] = client
        return client


@contextmanager
def directory_client(persist_dir: str, client: Any) -> Iterator[Any]:
    """Serve every store on *persist_dir* from *client* while the block runs.

    For a private folder only -- the Knowledge retrieval benchmark's
    (``stores/knowledge_benchmark.py``), whose exact in-process client
    stands in for Chroma where chromadb is not installed. A folder some
    client already serves is refused: the block never takes over a live
    store's vectors.
    """
    key = _client_key(persist_dir)
    with _CLIENTS_LOCK:
        if key in _CLIENTS:
            raise RuntimeError(f"a vector client already serves {key}")
        _CLIENTS[key] = client
    try:
        yield client
    finally:
        with _CLIENTS_LOCK:
            if _CLIENTS.get(key) is client:
                del _CLIENTS[key]


def index_reloads() -> dict[str, int]:
    """Indexes this process rebuilt from Chroma's log, by collection."""
    with _CLIENTS_LOCK:
        return dict(_INDEX_RELOADS)


def release_dropped_vectors(persist_dir: str | None, *, timeout: float = 60.0) -> dict[str, Any]:
    """Remove the vector folders no collection owns in *persist_dir*.

    A folder whose index this process loaded is held open (Windows) until
    the process lets go of it: then the client is closed -- after the calls
    in flight, holding new ones -- and the folders removed; every store
    re-opens its collection on its next call. A client busy past *timeout*,
    or one that fails to close (logged), keeps its folders for the next
    pass. Returns :func:`chroma_local.remove_orphan_segment_folders`'s
    report with ``client_closed``.
    """
    report: dict[str, Any] = {"removed": 0, "removed_bytes": 0, "held": [], "unreadable": False,
                              "client_closed": False}
    if not persist_dir:
        return report
    key = _client_key(persist_dir)
    report.update(chroma_local.remove_orphan_segment_folders(key))
    if not report["held"]:
        return report
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
    close = getattr(client, "close", None)
    if client is None or not callable(close):
        return report
    gate = _gate(key)
    if not gate.acquire_exclusive(timeout):
        logger.info("[VectorStore] %d dropped vector folder(s) wait for a quiet moment to go", len(report["held"]))
        return report
    try:
        with _CLIENTS_LOCK:
            if _CLIENTS.get(key) is client:
                del _CLIENTS[key]
            _GENERATIONS[key] = _GENERATIONS.get(key, 0) + 1
        try:
            close()
            report["client_closed"] = True
        except (*_chroma_errors(), OSError, RuntimeError):  # the folders wait for the next pass
            logger.warning("[VectorStore] could not close the vector store to let go of dropped "
                           "collections' files", exc_info=True)
        removed_before = report["removed"]
        again = chroma_local.remove_orphan_segment_folders(key)
        report.update(again, removed=removed_before + again["removed"],
                      removed_bytes=report["removed_bytes"] + again["removed_bytes"], client_closed=True)
    finally:
        gate.release_exclusive()
    if report["held"]:
        logger.warning("[VectorStore] %d dropped vector folder(s) are still held by another process: %s",
                       len(report["held"]), ", ".join(report["held"][:5]))
    return report


def compact_vectors(persist_dir: str | None, *, timeout: float = 60.0) -> dict[str, Any]:
    """Rewrite the vector catalog of *persist_dir* when a deletion left
    deleted text in it (:func:`chroma_local.compact_catalog`).

    Runs only while :func:`chroma_local.compaction_due`, between Chroma
    calls: the client's gate is taken exclusive -- the calls in flight
    finish, new ones wait for the rewrite -- and the client stays open (the
    loaded indexes are kept). A client busy past *timeout* leaves it for the
    next pass. Returns the compaction's report with ``due``; never raises.
    """
    report: dict[str, Any] = {"due": False, "compacted": False}
    if not persist_dir:
        return report
    key = _client_key(persist_dir)
    if not chroma_local.compaction_due(key):
        return report
    report["due"] = True
    gate = _gate(key)
    if not gate.acquire_exclusive(timeout):
        logger.info("[VectorStore] the vector catalog's compaction waits for a quiet moment")
        return report
    try:
        report.update(chroma_local.compact_catalog(key))
    finally:
        gate.release_exclusive()
    return report


def _release_at_exit() -> None:
    """At exit, let go of every directory's dropped vectors (best effort: a
    client still busy after a few seconds keeps them for the next start)."""
    for key in [k for k in list(_CLIENTS) if k != ":memory:"]:
        try:
            release_dropped_vectors(key, timeout=5.0)
        except (*_chroma_errors(), OSError, RuntimeError):  # never fails the exit
            logger.debug("[VectorStore] exit cleanup of %s failed", key, exc_info=True)


def embedder_identity(model: Any) -> str:
    """Which embedder made a vector, read off the embedder INSTANCE.

    Class, model name, size and a digest of the endpoint (``local`` for an
    in-process model). The Knowledge Library tagged its vectors with the
    CONFIGURED model name (Knowledge audit KB-E03): a model changed in
    Settings while the process still ran the old one, or a remote model that
    fell back to the local one, tagged vectors with a model that had not
    made them, and the repair never re-made them. Empty when there is no
    embedder.
    """
    if model is None:
        return ""
    name = next(
        (v for v in (getattr(model, "_model_name", None), getattr(model, "model_name", None),
                     getattr(model, "_model", None)) if isinstance(v, str) and v),
        "",
    )
    dim = getattr(model, "dim", None)
    base = getattr(model, "_base_url", "") or ""
    where = hashlib.sha256(str(base).encode("utf-8")).hexdigest()[:12] if base else "local"
    return f"{type(model).__name__}|{name or '-'}|{dim if isinstance(dim, int) else '-'}|{where}"


def _collection_missing(exc: BaseException) -> bool:
    """A collection another handle dropped (Chroma's ``NotFoundError``)."""
    return (type(exc).__name__ in ("NotFoundError", "InvalidCollectionException")
            or "does not exist" in str(exc).lower())


def _chroma_errors() -> tuple[type[BaseException], ...]:
    """Chroma's own error class (``chromadb.errors.ChromaError``; its errors
    are not OSError or ValueError), or none without the real chromadb."""
    try:
        from chromadb.errors import ChromaError
    except ImportError:
        return ()
    return (ChromaError,)


@contextmanager
def _unless_missing() -> Iterator[None]:
    """Ignore a collection that is not there; raise anything else."""
    try:
        yield
    except (*_chroma_errors(), ValueError) as exc:
        if not _collection_missing(exc):
            raise


def get_encoder(model_name: str | None = None) -> Any | None:
    """Return the shared encoder (delegates to the pluggable embedder).

    The ``model_name`` arg is accepted for backward compatibility but
    ignored — the model is chosen by config (``memory.embedding`` in
    ``kazma.yaml`` or ``KAZMA_EMBED_*`` env vars). Returns an object with
    an ``encode(text) -> list[float]`` method (an Embedder).
    """
    return get_embedder()


# ── VectorStore ────────────────────────────────────────────────────────────


class VectorStore:
    """ChromaDB-backed global vector store (Layer 1).

    Manages cross-worker semantic search via ChromaDB collections.
    Embeddings are produced by the shared ``get_encoder()`` singleton.

    Args:
        collection_name: ChromaDB collection name.
        persist_dir:     Optional directory for persistent storage.
    """

    def __init__(
        self,
        collection_name: str = _DEFAULT_COLLECTION,
        persist_dir: str | None = None,
    ) -> None:
        self._collection_name = collection_name
        self._persist_dir = persist_dir
        self._key = _client_key(persist_dir)
        self._generation = 0  # of the client the open collection belongs to
        self._collection: Any = None
        self._client: Any = None
        self._ready: bool = False
        self._model: Any | None = None
        self._failed_at: float | None = None
        self._space: str = _SPACE
        self._dim: int | None = None  # the size this collection's vectors are
        self._mismatch_noted: int | None = None
        self._open_lock = threading.RLock()
        #: (client generation, embedder identity) when the collection was
        #: found to hold only that embedder's vectors (:meth:`only_model`),
        #: and when it was last found holding another's.
        self._one_model: tuple[int, str] | None = None
        self._mixed_seen: tuple[int, str, float] | None = None
        #: The client generation the collection was last found free of text
        #: (:meth:`holds_text`); a store of this build never writes any.
        self._text_free: int | None = None

    # ── Initialisation ─────────────────────────────────────────────────

    def _ensure_client(self) -> bool:
        """Lazy-init the ChromaDB client and collection.  Returns True on success.

        A failure is retried after :data:`_RETRY_AFTER_S` and reported once
        per attempt with its real cause: "chromadb not installed" only when
        ``chromadb`` itself is missing. A collection of a client that was
        closed since (:func:`release_dropped_vectors`) is opened again.
        """
        if self._ready and self._generation == _GENERATIONS.get(self._key, 0):
            return True
        with self._open_lock:
            return self._open()

    def _open(self) -> bool:
        generation = _GENERATIONS.get(self._key, 0)
        if self._ready and self._generation == generation:
            return True
        if self._ready:
            self._ready = False
            self._collection = None
            self._dim = None
        if self._failed_at is not None and time.monotonic() - self._failed_at < _RETRY_AFTER_S:
            return False
        try:
            self._client = _shared_client(self._persist_dir)
            self._model = get_encoder()
            # Every write and search passes its own vectors, so the collection
            # needs no embedding function (Chroma would otherwise attach its
            # default model, and it is deprecating custom wrappers without a
            # config). An existing collection keeps its metadata, space and
            # graph; its search breadth is raised here (:data:`_HNSW`).
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"description": "Kazma global semantic memory"},
                configuration={"hnsw": dict(_HNSW)},
                embedding_function=None,
            )
            self._space = _collection_space(self._collection)
            self._widen_search()
            self._generation = generation
            self._ready = True
            self._failed_at = None
            logger.info("[VectorStore] ChromaDB collection ready: %s", self._collection_name)
            return True
        except ImportError as exc:
            self._failed_at = time.monotonic()
            if (exc.name or "").split(".")[0] == "chromadb":
                logger.warning("[VectorStore] chromadb not installed — meaning search off for %s",
                               self._collection_name)
            else:
                logger.warning("[VectorStore] %s: meaning search off (%s)",
                               self._collection_name, exc)
            return False
        except Exception as exc:
            self._failed_at = time.monotonic()
            logger.warning("[VectorStore] ChromaDB init failed for %s: %s",
                           self._collection_name, exc)
            return False

    @property
    def available(self) -> bool:
        """Whether the vector store is ready for queries."""
        with _gate(self._key).shared():
            return self._ensure_client() and self._model is not None

    @property
    def identity(self) -> str:
        """:func:`embedder_identity` of the embedder this store writes and
        queries with ("" while it is unavailable)."""
        return embedder_identity(self._model) if self.available else ""

    @property
    def store_id(self) -> str:
        """The id of the vector store this collection lives in
        (:func:`chroma_local.vector_store_id`): recorded with a coverage
        measurement, so a measurement is never shown for another store."""
        return chroma_local.vector_store_id(self._key, create=True) if self._persist_dir else ""

    def _hnsw(self) -> dict[str, Any]:
        """The collection's HNSW settings, as Chroma reports them."""
        config = getattr(self._collection, "configuration_json", None)
        hnsw = config.get("hnsw") if isinstance(config, dict) else None
        return hnsw if isinstance(hnsw, dict) else {}

    def _widen_search(self) -> None:
        """Raise an older collection's search breadth to :data:`_HNSW`'s
        (Chroma applies it when it next loads the index). Best effort."""
        breadth = self._hnsw().get("ef_search")
        if not isinstance(breadth, int) or breadth >= _HNSW["ef_search"]:
            return
        try:
            self._collection.modify(configuration={"hnsw": {"ef_search": _HNSW["ef_search"]}})
            logger.info("[VectorStore] %s searches %d candidates now (was %d)", self._collection_name,
                        _HNSW["ef_search"], breadth)
        except (*_chroma_errors(), ValueError, TypeError) as exc:  # searches keep the old breadth
            logger.info("[VectorStore] could not widen the search of %s: %s", self._collection_name, exc)

    def _hnsw_read(self, call: Callable[[], _T]) -> _T:
        """A read that needs the collection's HNSW index, rebuilt once from
        Chroma's log when Chroma lost it ("Nothing found on disk": an index
        it evicted before writing it to disk, :mod:`chroma_local`)."""
        try:
            return call()
        except _chroma_errors() as exc:
            if "nothing found on disk" not in str(exc).lower():
                raise
            with _CLIENTS_LOCK:
                _INDEX_RELOADS[self._collection_name] = _INDEX_RELOADS.get(self._collection_name, 0) + 1
            logger.warning("[VectorStore] Chroma lost the index of %s (evicted before it was written to "
                           "disk); rebuilding it from Chroma's log", self._collection_name)
            self._collection.delete(ids=[_RELOAD_PROBE_ID])
            return call()

    def _reopen_if_missing(self, exc: BaseException) -> bool:
        """Re-open the collection by name when another handle dropped it
        (Knowledge audit KB-E12): a second index instance kept the dead
        handle and failed with ``NotFoundError`` until the next restart.
        True when the caller may retry once."""
        if not _collection_missing(exc):
            return False
        logger.info("[VectorStore] %s was dropped elsewhere; re-opening it", self._collection_name)
        self._ready = False
        self._collection = None
        self._dim = None
        self._failed_at = None
        return self._ensure_client()

    def _held_dim(self) -> int | None:
        """The size of the vectors the collection holds (None: empty)."""
        held = self._hnsw_read(
            functools.partial(self._collection.get, limit=1, include=["embeddings"])).get("embeddings")
        return len(held[0]) if held is not None and len(held) else None

    def _drop_collection(self) -> None:
        """Delete the collection, then the vector folder Chroma leaves behind
        (:mod:`chroma_local`): at once when nothing holds it, else when
        :func:`release_dropped_vectors` closes the client. When it held text
        the catalog is marked for compaction first."""
        self._note_deleting()
        self._client.delete_collection(self._collection_name)
        self._collection = None
        self._ready = False
        self._dim = None
        self._one_model = self._mixed_seen = self._text_free = None
        if self._persist_dir:
            chroma_local.remove_orphan_segment_folders(self._key)

    def _note_deleting(self) -> None:
        """Before rows of this collection are deleted: if it may hold text
        (an older build's, :meth:`holds_text`), mark the catalog for
        compaction -- the deleted rows' bytes stay in the file until it is
        rewritten. A collection known free of text marks nothing."""
        if (not self._persist_dir or self._text_free == self._generation
                or chroma_local.compaction_due(self._key)):
            return
        if self._text_check() is not False:
            chroma_local.mark_compaction_due(self._key)

    def _text_check(self) -> bool | None:
        found = chroma_local.holds_text(self._key, self._collection_name, KEPT_METADATA)
        if found is False:
            self._text_free = self._generation
        return found

    @_gated
    def holds_text(self) -> bool:
        """Whether the collection holds a document or metadata outside
        :data:`KEPT_METADATA` (an older build's): the repair pass rebuilds
        it then. False for an in-memory store or one that is unavailable,
        and while the catalog cannot be read (logged; asked again next
        pass -- a deletion still marks it for compaction)."""
        if not self._persist_dir or not self._ensure_client():
            return False
        if self._text_free == self._generation:
            return False
        return self._text_check() is True

    @_gated
    def only_model(self, model: str) -> bool:
        """Whether every vector of the collection carries *model*
        (``embedder_identity``): a search then needs no filter on it.

        Chroma's metadata filter costs more than the search it narrows (at
        10,000 vectors a query took 40 ms with the filter and 4 ms without,
        KB-Q01), and a collection holds one library, so the filter is needed
        only while vectors of another embedder are still there -- after a
        model switch, until the repair pass has re-made them. Found by one
        filtered read (Chroma's ``$ne`` also matches a vector with no tag);
        kept while the client lives, since every write carries the store's
        own identity. A collection found mixed is asked again after
        :data:`_MIXED_RECHECK_S`.
        """
        if not model or not self._ensure_client():
            return False
        if self._one_model == (self._generation, model):
            return True
        seen = self._mixed_seen
        if seen and seen[:2] == (self._generation, model) and time.monotonic() - seen[2] < _MIXED_RECHECK_S:
            return False
        try:
            other = self._collection.get(where={"model": {"$ne": model}}, limit=1, include=[])
        except (*_chroma_errors(), ValueError) as exc:  # the filtered search stays the answer
            logger.info("[VectorStore] could not tell which embedders %s holds vectors of: %s",
                        self._collection_name, exc)
            return False
        if other.get("ids"):
            self._mixed_seen = (self._generation, model, time.monotonic())
            return False
        self._one_model = (self._generation, model)
        self._mixed_seen = None
        return True

    def _note_written(self, metadatas: list[dict[str, Any] | None]) -> None:
        """A write whose vectors carry another identity than the one
        :meth:`only_model` found ends what it found."""
        if self._one_model is not None and any((m or {}).get("model") != self._one_model[1] for m in metadatas):
            self._one_model = None

    # ── CRUD ───────────────────────────────────────────────────────────

    def _encode(self, text: str) -> list[float] | None:
        """Embed a single text string.  Returns None on failure."""
        if self._model is None:
            return None
        try:
            # _model is an Embedder (via get_encoder → get_embedder).
            return self._model.encode(text[:_EMBED_MAX_CHARS])
        except Exception as exc:
            logger.warning("[VectorStore] Encode failed: %s", exc)
            return None

    def index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Embed *text* and store its vector under *doc_id*.

        Returns True on success. Only the vector and the metadata in
        :data:`KEPT_METADATA` are stored, never the text. The text is
        embedded before the client's gate is taken: a model call never
        holds up a close.
        """
        if not self.available:
            return False
        # encode() returns [] on failure (not None) — treat empty as miss.
        embedding = _unit(self._encode(text))
        if not embedding:
            logger.warning("[VectorStore] Index skipped — empty embedding for %s", doc_id)
            return False
        # ChromaDB rejects empty metadata dicts — always pass at least one key.
        meta = _kept(metadata) or {"source": "memory"}
        with _gate(self._key).shared():
            if not self._ensure_client():
                return False
            for attempt in (0, 1):
                try:
                    self._fit(len(embedding))
                    self._collection.upsert(ids=[doc_id], embeddings=[embedding], metadatas=[meta])
                    self._note_written([meta])
                    return True
                except Exception as exc:
                    if attempt == 0 and self._reopen_if_missing(exc):
                        continue
                    logger.warning("[VectorStore] Index failed: %s", exc)
                    return False
        return False

    def encode_query(self, text: str) -> list[float] | None:
        """*text*'s unit vector from this store's embedder (None when the
        store is unavailable or the model gives nothing), so one question is
        embedded once however many collections it searches."""
        if not self.available:
            return None
        return _unit(self._encode(text))

    def similarities(self, embedding: list[float], ids: list[str], *, model: str) -> dict[str, float]:
        """Cosine of *embedding* (a unit vector) with each stored vector of
        *ids* that *model* made (``embedder_identity``); an id with no such
        vector, or one of another size, is left out. A failing collection is
        logged and answers nothing, as :meth:`query` does: the caller judges
        those ids by their words."""
        ids = [str(i) for i in ids if i]
        if not ids or not embedding or not self._ensure_client():
            return {}
        got: dict[str, Any] = {}
        for attempt in (0, 1):
            try:
                got = self._collection.get(ids=ids, include=["embeddings", "metadatas"])
                break
            except Exception as exc:
                if attempt == 0 and self._reopen_if_missing(exc):
                    continue
                logger.warning("[VectorStore] Similarity lookup failed: %s", exc)
                return {}
        out: dict[str, float] = {}
        found = got.get("ids") or []
        vectors = got.get("embeddings")
        metas = got.get("metadatas") or [None] * len(found)
        if vectors is None:
            return {}
        for doc_id, vec, meta in zip(found, vectors, metas):
            if (meta or {}).get("model") != model or vec is None or len(vec) != len(embedding):
                continue
            # Stored vectors are unit length (every write goes through _unit).
            out[str(doc_id)] = float(sum(float(a) * float(b) for a, b in zip(embedding, vec)))
        return out

    def query(
        self,
        text: str,
        limit: int = 10,
        where: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        *,
        embedding: list[float] | None = None,
    ) -> list[tuple[str, float]]:
        """Semantic search via cosine similarity.

        Returns list of (doc_id, similarity_score) tuples. The question is
        embedded before the client's gate is taken (as in :meth:`index`).

        Args:
            tenant_id: If provided, results are filtered to this tenant.
            embedding: *text*'s unit vector when the caller already has it
                (:meth:`encode_query`); else it is embedded here.
        """
        if not self.available:
            return []
        embedding = embedding or _unit(self._encode(text))
        if not embedding:
            logger.warning("[VectorStore] Query skipped — empty embedding")
            return []
        # Build combined where filter
        filters: dict[str, Any] = {}
        if where:
            filters.update(where)
        if tenant_id:
            filters["tenant_id"] = tenant_id
        return self._query_vector(embedding, limit, filters)

    @_gated
    def _query_vector(self, embedding: list[float], limit: int,
                      filters: dict[str, Any]) -> list[tuple[str, float]]:
        if not self._ensure_client():
            return []
        for attempt in (0, 1):
            try:
                count = self._collection.count() if hasattr(self._collection, "count") else limit
                if count <= 0:
                    return []
                # A collection of another size cannot be searched with this
                # model's vectors: refuse quietly until the repair sweep has
                # re-made them (KB-E03), instead of a failed query -- and a
                # WARNING -- on every search.
                if self._dim is None:
                    self._dim = self._held_dim()
                if self._dim is not None and self._dim != len(embedding):
                    if self._mismatch_noted != self._dim:
                        self._mismatch_noted = self._dim
                        logger.info(
                            "[VectorStore] %s holds %d-dimension vectors and the model makes %d: "
                            "meaning search waits for the repair sweep", self._collection_name,
                            self._dim, len(embedding))
                    return []
                results = self._hnsw_read(functools.partial(
                    self._collection.query,
                    query_embeddings=[embedding],
                    n_results=min(limit, count),
                    where=filters or None,
                ))
                if not results or not results.get("ids") or not results["ids"][0]:
                    return []
                scored: list[tuple[str, float]] = []
                ids_list = results["ids"][0]
                distances = results.get("distances", [[0.0] * len(ids_list)])[0]
                for i, doc_id in enumerate(ids_list):
                    dist = distances[i] if i < len(distances) else 0.0
                    scored.append((doc_id, _similarity(float(dist), self._space)))
                scored.sort(key=lambda x: x[1], reverse=True)
                return scored
            except Exception as exc:
                if attempt == 0 and self._reopen_if_missing(exc):
                    continue
                logger.warning("[VectorStore] Query failed: %s", exc)
                return []
        return []

    @_gated
    def delete(self, doc_id: str) -> bool:
        """Remove a document from the collection."""
        if not self._ensure_client():
            return False
        for attempt in (0, 1):
            try:
                self._note_deleting()
                self._collection.delete(ids=[doc_id])
                return True
            except Exception as exc:
                if attempt == 0 and self._reopen_if_missing(exc):
                    continue
                logger.warning("[VectorStore] Delete failed: %s", exc)
                return False
        return False

    def ids(self) -> set[str]:
        """Every document id the collection holds (empty when unavailable).

        A failing collection raises: the caller decides what an incomplete
        answer means (the backfill must not take "none" for "all missing").
        """
        return set(self.models())

    @_gated
    def models(self) -> dict[str, str | None]:
        """Every document id with the model its vector records (None: none).

        Read in pages, filtered here: Chroma's ``$ne`` also matches a vector
        without the key, and every vector written before 2026-09-27 has none.
        """
        if not self._ensure_client():
            return {}
        try:
            return self._models_once()
        except Exception as exc:
            if self._reopen_if_missing(exc):
                return self._models_once()
            raise

    def _models_once(self) -> dict[str, str | None]:
        found: dict[str, str | None] = {}
        offset = 0
        while True:
            page = self._collection.get(include=["metadatas"], limit=_ID_PAGE, offset=offset)
            ids = page.get("ids") or []
            for doc_id, meta in zip(ids, page.get("metadatas") or [None] * len(ids)):
                found[str(doc_id)] = (meta or {}).get("model")
            if len(ids) < _ID_PAGE:
                return found
            offset += len(ids)

    @_gated
    def delete_many(self, doc_ids: list[str]) -> int:
        """Remove *doc_ids* from the collection; returns how many were asked for.

        A failing collection raises to the caller.
        """
        doc_ids = [str(i) for i in doc_ids if i]
        if not doc_ids or not self._ensure_client():
            return 0
        self._note_deleting()
        for start in range(0, len(doc_ids), 500):
            try:
                self._collection.delete(ids=doc_ids[start:start + 500])
            except Exception as exc:
                if not self._reopen_if_missing(exc):
                    raise
                self._collection.delete(ids=doc_ids[start:start + 500])
        return len(doc_ids)

    def _fit(self, dim: int) -> None:
        """Make the collection hold vectors of size *dim* before writing one.

        A collection keeps the size of its first vector, and Chroma refuses
        any other: after a switch to a model of another size every write
        would fail. The vectors it holds were made by the old model, so they
        are dropped with the collection and the maintenance pass embeds the
        library again. Only a size the collection actually holds decides it.
        """
        if self._dim == dim:
            return
        stored = self._held_dim()
        if stored is not None and stored != dim:
            logger.warning(
                "[VectorStore] %s holds %d-dimension vectors and the model now makes %d: "
                "rebuilding it", self._collection_name, stored, dim,
            )
            self._drop_collection()
            if not self._ensure_client():
                raise RuntimeError(f"could not recreate {self._collection_name}")
        self._dim = dim
        self._mismatch_noted = None

    @_gated
    def drop(self) -> bool:
        """Delete the whole collection (its Knowledge Library is being deleted).

        False when the store is unavailable; a failing client raises.
        """
        if not self._ensure_client():
            return False
        self._drop_collection()
        return True

    @_gated
    def graph_outdated(self) -> bool:
        """Whether the collection's HNSW graph is sparser than :data:`_HNSW`
        (a collection made before 2026-10-15, with Chroma's defaults)."""
        if not self._ensure_client():
            return False
        hnsw = self._hnsw()
        return any(isinstance(hnsw.get(key), int) and hnsw[key] < _HNSW[key]
                   for key in ("max_neighbors", "ef_construction"))

    @_gated
    def rebuild_collection(self) -> int:
        """Rebuild the collection from the vectors it holds, with
        :data:`_HNSW`'s graph and nothing but each vector and its
        :data:`KEPT_METADATA` -- nothing is embedded again; returns the
        vectors copied. The repair pass calls it for a collection made with
        a sparser graph (:meth:`graph_outdated`) or holding text
        (:meth:`holds_text`).

        The vectors are copied into ``<name>.rebuild``, made with the
        settings; only when it holds as many as the old one is the old one
        dropped (the catalog marked for compaction) and the copy renamed to
        its name. A copy left by an earlier attempt is dropped first.
        Vectors written while it copies may be missing from the copy: the
        repair sweep embeds what is missing. A failure leaves the old
        collection in place and raises.
        """
        with self._open_lock:  # no caller re-creates the name while it is swapped
            if not self._ensure_client():
                return 0
            temp = f"{self._collection_name}.rebuild"
            with _unless_missing():
                self._client.delete_collection(temp)
            old = self._collection
            copy = self._client.create_collection(
                name=temp, metadata={"description": "Kazma global semantic memory"},
                configuration={"hnsw": dict(_HNSW)}, embedding_function=None)
            copied = offset = 0
            complete = False
            try:
                while True:
                    page = self._hnsw_read(functools.partial(
                        old.get, include=["embeddings", "metadatas"], limit=_COPY_PAGE, offset=offset))
                    ids = list(page.get("ids") or [])
                    if ids:
                        copy.upsert(ids=ids, embeddings=page["embeddings"],
                                    metadatas=[_kept(m) or {"source": "memory"}
                                               for m in (page.get("metadatas") or [None] * len(ids))])
                        copied += len(ids)
                    if len(ids) < _COPY_PAGE:
                        break
                    offset += len(ids)
                held = old.count()
                if copy.count() != held:
                    raise RuntimeError(f"the copy of {self._collection_name} holds {copy.count()} vectors, "
                                       f"the collection {held}: it changed while it was copied")
                complete = True
            finally:
                if not complete:  # the old collection stays; the next pass tries again
                    with _unless_missing():
                        self._client.delete_collection(temp)
            self._drop_collection()
            copy.modify(name=self._collection_name)
            if not self._ensure_client():
                raise RuntimeError(f"could not re-open {self._collection_name} after its rebuild")
            self._text_free = self._generation  # the copy was written with vectors and kept keys only
        logger.info("[VectorStore] rebuilt %s with %d neighbours a vector and no text (%d vectors copied)",
                    self._collection_name, _HNSW["max_neighbors"], copied)
        return copied

    def index_many(self, docs: list[tuple[str, str, dict[str, Any]]]) -> int:
        """Embed and store ``(doc_id, text, metadata)`` in one batch; returns how many.

        One model call for the batch: the Knowledge Library backfill embeds
        thousands of chunks, and ``index`` encodes one at a time. As there,
        only the vectors and their :data:`KEPT_METADATA` are stored. A text
        the embedder returns nothing for is left out; a failing model or
        collection raises to the caller.
        """
        docs = [(i, t, m) for i, t, m in docs if i and t]
        if not docs or not self.available:
            return 0
        texts = [t[:_EMBED_MAX_CHARS] for _i, t, _m in docs]
        encode_batch = getattr(self._model, "encode_batch", None)
        vectors = encode_batch(texts) if callable(encode_batch) else [self._model.encode(t) for t in texts]
        keep = [(d, u) for d, u in ((d, _unit(v)) for d, v in zip(docs, vectors)) if u]
        if not keep:
            return 0
        # The batch was embedded outside the gate: a model call never holds
        # up a close.
        with _gate(self._key).shared():
            if not self._ensure_client():
                return 0
            metadatas = [_kept(d[2]) or {"source": "memory"} for d, _v in keep]
            for attempt in (0, 1):
                try:
                    self._fit(len(keep[0][1]))
                    self._collection.upsert(
                        ids=[d[0] for d, _v in keep],
                        embeddings=[v for _d, v in keep],
                        metadatas=metadatas,
                    )
                    self._note_written(metadatas)
                    return len(keep)
                except Exception as exc:
                    if attempt == 0 and self._reopen_if_missing(exc):
                        continue
                    raise
        return 0

    @_gated
    def count(self) -> int:
        """Return the number of documents in the collection."""
        if not self._ensure_client():
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0
