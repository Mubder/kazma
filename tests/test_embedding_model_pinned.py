"""The shipped embedding model is loaded at a pinned commit (2026-11, supply chain).

``SentenceTransformer("BAAI/bge-m3")`` with no revision follows the
repository's ``main`` on the Hugging Face Hub: whoever can push there changes
the weights every install downloads next, and a server whose cache misses a
file asks the Hub for whatever ``main`` is now. Kazma pins the model it ships
(``embedder.DEFAULT_MODEL_REVISION``, the ``refs/main`` every install's cache
already holds, so nothing is downloaded again), and every place that loads,
downloads or checks the model passes the revision. A model the owner
configured that Kazma does not ship loads unpinned unless they give one
(``KAZMA_EMBED_REVISION`` / ``memory.embedding.revision``).

Behaviour: a fake ``sentence_transformers`` and ``huggingface_hub`` record the
revision each call got (no model, no network). Gate: every call that loads or
fetches a Hugging Face model in product code and scripts names ``revision=``;
a planted unpinned call -- the line ``embedder.py`` had -- is its negative
control.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]

PIN = "5617a9f61b028005a4858fdac845db406aefb181"


@pytest.fixture
def hub(monkeypatch):
    """Fake model libraries: the constructor and the cache check record their
    arguments. Returns ``(SentenceTransformer, snapshot_download)``."""
    fake_hub = ModuleType("huggingface_hub")
    fake_hub.snapshot_download = MagicMock(return_value="/hf/cache/models--x")
    fake_st = ModuleType("sentence_transformers")
    ctor = MagicMock(name="SentenceTransformer")
    fake_st.SentenceTransformer = ctor
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    for name in ("KAZMA_EMBED_ALLOW_DOWNLOAD", "KAZMA_EMBED_PROVIDER", "KAZMA_EMBED_MODEL",
                 "KAZMA_VECTOR_MODEL", "KAZMA_EMBED_REVISION", "KAZMA_EMBED_DIM",
                 "KAZMA_EMBED_BASE_URL", "KAZMA_EMBED_FLEET"):
        monkeypatch.delenv(name, raising=False)
    from kazma_core.memory import embedder

    embedder.reset_embedder()
    monkeypatch.setattr(embedder, "_store_embedding_overrides", lambda: {})
    yield ctor, fake_hub.snapshot_download
    embedder.reset_embedder()


def test_the_pin_is_one_full_commit():
    from kazma_core.memory import embedder

    assert embedder.DEFAULT_MODEL_REVISION == PIN
    assert re.fullmatch(r"[0-9a-f]{40}", embedder.DEFAULT_MODEL_REVISION)
    assert embedder._model_revision(embedder.DEFAULT_MODEL) == PIN


def test_the_shipped_model_loads_at_the_pin(hub):
    ctor, snapshot = hub
    from kazma_core.memory.embedder import LocalSentenceTransformerEmbedder

    emb = LocalSentenceTransformerEmbedder()
    assert emb._ensure_model() is ctor.return_value
    assert ctor.call_args.kwargs["revision"] == PIN

    # A fallback embedder checks the cache at the same commit before loading.
    fallback = LocalSentenceTransformerEmbedder(allow_download=False)
    fallback._ensure_model()
    assert snapshot.call_args.kwargs["revision"] == PIN
    assert snapshot.call_args.kwargs["local_files_only"] is True


def test_install_downloads_and_checks_the_pin(hub):
    ctor, snapshot = hub
    from kazma_core.memory import embedder

    embedder.download_local_model()
    assert ctor.call_args.args == (embedder.DEFAULT_MODEL,)
    assert ctor.call_args.kwargs["revision"] == PIN
    assert embedder.local_model_downloaded() is True
    assert snapshot.call_args.kwargs["revision"] == PIN


def test_the_server_s_embedder_loads_the_pin(hub):
    ctor, _snapshot = hub
    from kazma_core.memory import embedder

    emb = embedder.get_embedder()
    assert isinstance(emb, embedder.LocalSentenceTransformerEmbedder)
    emb._ensure_model()
    assert ctor.call_args.kwargs["revision"] == PIN


def test_another_model_is_unpinned_unless_the_owner_gives_a_revision(hub, monkeypatch):
    ctor, _snapshot = hub
    from kazma_core.memory import embedder

    monkeypatch.setenv("KAZMA_EMBED_MODEL", "intfloat/multilingual-e5-large")
    embedder.get_embedder()._ensure_model()
    assert ctor.call_args.args == ("intfloat/multilingual-e5-large",)
    assert ctor.call_args.kwargs["revision"] is None

    embedder.reset_embedder()
    monkeypatch.setenv("KAZMA_EMBED_REVISION", "abc1234")
    embedder.get_embedder()._ensure_model()
    assert ctor.call_args.kwargs["revision"] == "abc1234"


def test_a_revision_given_for_a_remote_model_does_not_reach_the_local_fallback(hub, monkeypatch):
    """A broken remote config falls back to the shipped model: at its pin,
    never at the revision the owner gave the remote model."""
    ctor, _snapshot = hub
    from kazma_core.memory import embedder

    monkeypatch.setenv("KAZMA_EMBED_PROVIDER", "openai-compatible")
    monkeypatch.setenv("KAZMA_EMBED_REVISION", "remote-only")
    emb = embedder.get_embedder()
    assert isinstance(emb, embedder.LocalSentenceTransformerEmbedder)
    assert emb._revision == PIN


# ── the gate: every model load or fetch names its revision ────────────────

#: Calls that load or fetch a Hugging Face model or file.
_MODEL_CALLS = frozenset({
    "SentenceTransformer", "CrossEncoder", "SparseEncoder",
    "snapshot_download", "hf_hub_download", "from_pretrained",
})
_PRODUCT_PREFIXES = (
    "kazma-core/kazma_core/", "kazma-gateway/kazma_gateway/", "kazma-ui/kazma_ui/",
    "kazma-tui/kazma_tui/", "kazma-cli/kazma_cli/", "kazma-skills/kazma_skills/",
    "scripts/", "deploy/",
)
_SHELL_SUFFIXES = (".ps1", ".sh", ".cmd", ".bat", ".yml", ".yaml")
_SHELL_CALL = re.compile(r"\b(?:SentenceTransformer|snapshot_download|hf_hub_download)\s*\(")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def unpinned_calls(source: str, filename: str = "<planted>") -> list[str]:
    """Each model load or fetch in *source* that names no ``revision=``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Call) and _call_name(node) in _MODEL_CALLS:
            if not any(kw.arg == "revision" for kw in node.keywords):
                found.append(f"{filename}:{node.lineno} {_call_name(node)}(...)")
    return found


def unpinned_shell_calls(text: str, filename: str = "<planted>") -> list[str]:
    """A model load written into a script's text (``python -c "..."``)."""
    return [
        f"{filename}:{n} {line.strip()[:80]}"
        for n, line in enumerate(text.splitlines(), 1)
        if _SHELL_CALL.search(line) and "revision" not in line
    ]


def _tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()


def test_every_model_load_names_its_revision():
    offenders: list[str] = []
    seen = 0
    for rel in _tracked():
        if not (rel.startswith(_PRODUCT_PREFIXES) or "/" not in rel):
            continue
        path = REPO / rel
        if not path.is_file():
            continue
        if rel.endswith(".py"):
            text = path.read_text(encoding="utf-8")
            if any(name in text for name in _MODEL_CALLS):
                seen += 1
            offenders += unpinned_calls(text, rel)
        elif rel.endswith(_SHELL_SUFFIXES) or Path(rel).name.startswith("Dockerfile"):
            offenders += unpinned_shell_calls(path.read_text(encoding="utf-8", errors="replace"), rel)
    assert seen, "the gate found no model loads at all; it would be decoration"
    assert not offenders, (
        "A Hugging Face model is loaded or fetched with no revision: it follows the "
        "repository's main branch. Pass revision=embedder._model_revision(name):\n"
        + "\n".join(offenders)
    )


def test_negative_control_the_old_unpinned_load_is_found():
    """The lines embedder.py had before the pin."""
    old = (
        "from sentence_transformers import SentenceTransformer\n"
        "from huggingface_hub import snapshot_download\n"
        "model = SentenceTransformer(self._model_name)\n"
        "snapshot_download(repo_id=model_name, local_files_only=True)\n"
    )
    assert len(unpinned_calls(old)) == 2
    pinned = old.replace("self._model_name)", "self._model_name, revision=r)").replace(
        "local_files_only=True)", "revision=r, local_files_only=True)")
    assert unpinned_calls(pinned) == []
    assert unpinned_shell_calls('python -c "SentenceTransformer(\'BAAI/bge-m3\')"')
    assert not unpinned_shell_calls('python -c "SentenceTransformer(m, revision=r)"')
