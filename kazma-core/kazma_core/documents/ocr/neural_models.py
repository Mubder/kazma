"""The neural OCR engine's model files: where they come from, where they live,
and whether the ones on disk are the ones installed.

2026-10-15 (Q02, Arabic OCR). ``documents.ocr.engine: neural`` runs four
models with ONNX Runtime inside the document worker (``ocr/neural.py``):

``det``          PaddleOCR PP-OCRv5 mobile text detection (Apache-2.0), the
                 ONNX conversion RapidAI publishes with its SHA-256.
``rec_arabic``   EasyOCR's Arabic recognizer ``arabic_g1`` (Apache-2.0):
                 Arabic and Latin letters, Arabic-Indic and European digits.
``rec_latin``    EasyOCR's ``english_g2`` (Apache-2.0): lines with no Arabic.
``rec_letters``  PaddleOCR PP-OCRv5 Arabic mobile recognition (Apache-2.0):
                 the second reader of Arabic letters (``ocr/neural.py``).

The EasyOCR weights are PyTorch files; ``scripts/ocr_models.py install``
downloads every source pinned below, checks its SHA-256, converts the two
EasyOCR models to ONNX (checked against PyTorch on fixed inputs) and writes
the four ``.onnx`` files with a ``manifest.json`` naming each file's SHA-256.
The worker never downloads anything: it reads the model directory and
refuses files whose hashes do not match the manifest.

Where: ``KAZMA_OCR_MODEL_DIR`` when set (the worker image sets it to its
read-only copy), else ``<data dir>/models/ocr`` -- a re-installable cache,
never part of a bundle or a backup (``store_registry``: ``models``).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "IMAGE_MODEL_DIR",
    "MANIFEST_NAME",
    "MODEL_FILES",
    "MODEL_SET_VERSION",
    "SOURCES",
    "ModelSource",
    "ModelStatus",
    "default_model_directory",
    "model_directory",
    "verify_model_directory",
]

#: Bumped whenever a source, a conversion or a file name changes: a directory
#: installed for another version is not used.
MODEL_SET_VERSION = "2026-10-15.1"
MANIFEST_NAME = "manifest.json"
#: Where the document worker image keeps its copy (deploy/document-worker/Dockerfile).
IMAGE_MODEL_DIR = "/opt/kazma/ocr-models"
#: The files the engine loads, by role.
MODEL_FILES: dict[str, str] = {
    "det": "ppocrv5_mobile_det.onnx",
    "rec_arabic": "easyocr_arabic_g1_rec.onnx",
    "rec_latin": "easyocr_english_g2_rec.onnx",
    "rec_letters": "ppocrv5_arabic_mobile_rec.onnx",
}


@dataclass(frozen=True, slots=True)
class ModelSource:
    """A file ``scripts/ocr_models.py install`` downloads, and how it is used."""

    role: str
    url: str
    sha256: str
    size: int
    license: str
    origin: str
    #: The member of a zip archive that holds the weights (EasyOCR ships zips).
    member: str | None = None
    #: The member's own SHA-256 (EasyOCR pins its weights by MD5 only).
    member_sha256: str | None = None


SOURCES: dict[str, ModelSource] = {
    "det": ModelSource(
        "det",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/det/"
        "ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
        4_819_576, "Apache-2.0",
        "PaddleOCR PP-OCRv5_mobile_det, converted to ONNX by RapidAI (RapidOCR v3.10.0)",
    ),
    "rec_letters": ModelSource(
        "rec_letters",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/"
        "arabic_PP-OCRv5_rec_mobile.onnx",
        "c1192e632d0baa9146ae5b756a0e635e3dc63c1733737ebfd1629e87144e9295",
        8_023_828, "Apache-2.0",
        "PaddleOCR arabic_PP-OCRv5_mobile_rec, converted to ONNX by RapidAI (RapidOCR v3.10.0)",
    ),
    "rec_arabic": ModelSource(
        "rec_arabic",
        "https://github.com/JaidedAI/EasyOCR/releases/download/pre-v1.1.6/arabic.zip",
        "90b08b8e456d435bcdd96141221db5f116ef03ab3efc2d3239cfb24520ad7756",
        199_916_962, "Apache-2.0",
        "EasyOCR 1.7.2 recognition model arabic_g1 (generation 1)",
        member="arabic.pth",
        member_sha256="2a9afd42c374deb98aed0b53c9b77d75e1d00d4e0501f3b0276c54190c89b1a8",
    ),
    "rec_latin": ModelSource(
        "rec_latin",
        "https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/english_g2.zip",
        "1b5eaebf1c062de6205560c97ffcfa8dc0e6f413c340e8adc5cfc57e159f61ff",
        14_040_947, "Apache-2.0",
        "EasyOCR 1.7.2 recognition model english_g2 (generation 2)",
        member="english_g2.pth",
        member_sha256="e2272681d9d67a04e2dff396b6e95077bc19001f8f6d3593c307b9852e1c29e8",
    ),
}


def default_model_directory() -> Path:
    """``<data dir>/models/ocr`` (not created here)."""
    from kazma_core.paths import data_dir

    return data_dir() / "models" / "ocr"


def model_directory() -> Path:
    """The model directory this process uses: ``KAZMA_OCR_MODEL_DIR``, else
    the data-dir cache."""
    configured = (os.environ.get("KAZMA_OCR_MODEL_DIR") or "").strip()
    return Path(configured).expanduser() if configured else default_model_directory()


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """Whether a directory holds a complete, intact model set."""

    ready: bool
    directory: str
    version: str | None
    reason: str | None
    files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "directory": self.directory,
            "version": self.version,
            "reason": self.reason,
            "files": list(self.files),
        }


_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_LOCK = threading.Lock()


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _HASH_LOCK:
        cached = _HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _HASH_LOCK:
        _HASH_CACHE[key] = value
    return value


def verify_model_directory(directory: Path | str | None = None) -> ModelStatus:
    """Every model file present and matching the manifest's SHA-256.

    Never raises: a missing, partial, foreign or altered set is ``ready:
    False`` with the reason, and the engine is not used."""
    root = Path(directory) if directory is not None else model_directory()
    shown = str(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return ModelStatus(False, shown, None,
                           "Neural OCR models are not installed (run: python scripts/ocr_models.py install)")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        version = str(manifest["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return ModelStatus(False, shown, None, "The neural OCR model manifest cannot be read")
    if version != MODEL_SET_VERSION:
        return ModelStatus(False, shown, version,
                           f"Neural OCR models are version {version}; this build needs {MODEL_SET_VERSION} "
                           "(run: python scripts/ocr_models.py install)")
    names = []
    for role, name in MODEL_FILES.items():
        entry = files.get(role) if isinstance(files, dict) else None
        path = root / name
        if not isinstance(entry, dict) or entry.get("file") != name:
            return ModelStatus(False, shown, version, f"The neural OCR manifest does not name the {role} model")
        try:
            if not path.is_file() or path.stat().st_size != int(entry["size"]):
                return ModelStatus(False, shown, version, f"The neural OCR {role} model is missing or incomplete")
            if _sha256(path) != str(entry["sha256"]):
                return ModelStatus(False, shown, version,
                                   f"The neural OCR {role} model does not match its manifest")
        except (OSError, ValueError, KeyError, TypeError):
            return ModelStatus(False, shown, version, f"The neural OCR {role} model cannot be read")
        names.append(name)
    return ModelStatus(True, shown, version, None, tuple(names))
