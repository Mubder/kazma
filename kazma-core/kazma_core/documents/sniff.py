"""Content-first document type detection and archive security preflight."""

from __future__ import annotations

import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import DocumentConfig
from .errors import (
    DocumentEncryptedError,
    DocumentFormatError,
    DocumentLimitError,
    DocumentSecurityError,
)

__all__ = ["SniffResult", "preflight_ooxml", "sniff_document"]

_PDF = b"%PDF-"
_OLE = bytes.fromhex("D0CF11E0A1B11AE1")
_OOXML_MIMES = {
    "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_EXTENSION_MIMES = {
    ".pdf": {"application/pdf"},
    ".docx": {_OOXML_MIMES["word"]},
    ".xlsx": {_OOXML_MIMES["xl"]},
    ".pptx": {_OOXML_MIMES["ppt"]},
    ".doc": {"application/msword"},
    ".xls": {"application/vnd.ms-excel"},
    ".ppt": {"application/vnd.ms-powerpoint"},
    ".csv": {"text/csv"},
    ".tsv": {"text/tab-separated-values"},
    ".json": {"application/json"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown"},
    ".markdown": {"text/markdown"},
    ".log": {"text/x-log"},
    ".html": {"text/html"},
    ".htm": {"text/html"},
    ".rtf": {"application/rtf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".tif": {"image/tiff"},
    ".tiff": {"image/tiff"},
    ".bmp": {"image/bmp"},
    ".webp": {"image/webp"},
}
_MACRO_EXTENSIONS = frozenset({".docm", ".dotm", ".xlsm", ".xltm", ".pptm", ".potm"})
_UNSAFE_XML_MARKERS = (b"<!doctype", b"<!entity")
_NESTED_ARCHIVE_EXTENSIONS = frozenset(
    {".7z", ".bz2", ".gz", ".rar", ".tar", ".tgz", ".xz", ".zip"}
)
_EMBEDDED_MEMBER_PARTS = frozenset({"activex", "embeddings", "oleobjects"})
# High-risk PDF dictionary names only. Do NOT treat mere /OpenAction or /AA as
# hostile — many legitimate PDFs open to a page. Do NOT match short `/JS`
# alone: compressed content streams produce random-byte false positives.
# Hostile corpus uses /JavaScript which still matches.
_PDF_ACTIVE_CONTENT_RE = re.compile(
    rb"/(?:JavaScript|Launch|EmbeddedFile|RichMedia|GoToE)(?=[\s/\]>)])",
    re.IGNORECASE,
)
# Catalog/name-tree only (short form appears only as a PDF name token).
_PDF_JS_NAME_RE = re.compile(rb"(?:[\s<\[/])/(?:JS)(?=[\s/\]>()])")
_ARCHIVE_CHUNK_BYTES = 64 * 1024


def _encoded_xml_markers() -> tuple[bytes, ...]:
    markers: list[bytes] = []
    for marker in _UNSAFE_XML_MARKERS:
        text = marker.decode("ascii")
        markers.extend(
            (
                marker,
                text.encode("utf-16-le"),
                text.encode("utf-16-be"),
                text.encode("utf-32-le"),
                text.encode("utf-32-be"),
            )
        )
    return tuple(markers)


_ENCODED_UNSAFE_XML_MARKERS = _encoded_xml_markers()


@dataclass(frozen=True, slots=True)
class SniffResult:
    mime_type: str
    extension: str
    container: str | None = None


def preflight_ooxml(path: Path, config: DocumentConfig) -> str:
    """Stream and validate an OOXML ZIP, then return its document family."""

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentFormatError("Invalid OOXML ZIP container") from exc
    with archive:
        members = archive.infolist()
        if len(members) > config.max_archive_members:
            raise DocumentLimitError("OOXML archive contains too many members")
        expanded = 0
        compressed = 0
        images = 0
        roots: set[str] = set()
        for member in members:
            normalized = member.filename.replace("\\", "/")
            parts = PurePosixPath(normalized).parts
            lower_parts = {part.lower() for part in parts}
            if (
                not normalized
                or normalized.startswith("/")
                or re.match(r"^[A-Za-z]:", normalized)
                or ".." in parts
            ):
                raise DocumentSecurityError("OOXML archive contains an unsafe member path")
            if member.flag_bits & 0x1:
                raise DocumentEncryptedError("Encrypted OOXML archives are not supported")
            unix_mode = (member.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise DocumentSecurityError("OOXML archive contains a symbolic link")
            compressed += member.compress_size
            lower = normalized.lower()
            if lower.endswith("vbaproject.bin"):
                raise DocumentSecurityError("Macro-enabled Office documents are disabled")
            if lower_parts & _EMBEDDED_MEMBER_PARTS:
                raise DocumentSecurityError(
                    "OOXML embedded objects and active content are disabled"
                )
            if PurePosixPath(lower).suffix in _NESTED_ARCHIVE_EXTENSIONS:
                raise DocumentSecurityError("Nested archives in OOXML are disabled")
            if "/media/" in f"/{lower}" and PurePosixPath(lower).suffix in {
                ".bmp",
                ".gif",
                ".jpeg",
                ".jpg",
                ".png",
                ".tif",
                ".tiff",
                ".webp",
            }:
                images += 1
                if images > config.max_images:
                    raise DocumentLimitError(
                        "OOXML image count exceeds the configured limit"
                    )

            scan_xml = lower.endswith((".xml", ".rels"))
            marker_overlap = max(map(len, _ENCODED_UNSAFE_XML_MARKERS)) - 1
            tail = b""
            member_expanded = 0
            first_chunk = True
            try:
                with archive.open(member) as stream:
                    while True:
                        chunk = stream.read(_ARCHIVE_CHUNK_BYTES)
                        if not chunk:
                            break
                        if first_chunk:
                            first_chunk = False
                            if chunk.startswith(
                                (
                                    b"PK\x03\x04",
                                    b"7z\xbc\xaf\x27\x1c",
                                    b"Rar!\x1a\x07",
                                    b"\x1f\x8b",
                                )
                            ):
                                raise DocumentSecurityError(
                                    "Nested archives in OOXML are disabled"
                                )
                        member_expanded += len(chunk)
                        expanded += len(chunk)
                        if expanded > config.max_expanded_bytes:
                            raise DocumentLimitError(
                                "OOXML expanded size exceeds the configured limit"
                            )
                        if (
                            member_expanded / max(member.compress_size, 1)
                            > config.max_compression_ratio
                        ):
                            raise DocumentLimitError(
                                "OOXML compression ratio exceeds the configured limit"
                            )
                        if scan_xml:
                            window = (tail + chunk).lower()
                            if any(
                                marker in window
                                for marker in _ENCODED_UNSAFE_XML_MARKERS
                            ):
                                raise DocumentSecurityError(
                                    "OOXML archive contains unsafe XML declarations"
                                )
                            if lower.endswith(".rels") and re.search(
                                rb"targetmode\s*=\s*['\"]\s*external\s*['\"]",
                                window,
                                re.IGNORECASE,
                            ):
                                raise DocumentSecurityError(
                                    "OOXML external relationships are disabled"
                                )
                            tail = window[-max(marker_overlap, 128):]
            except (DocumentLimitError, DocumentSecurityError):
                raise
            except RuntimeError as exc:
                if "password" in str(exc).lower() or "encrypt" in str(exc).lower():
                    raise DocumentEncryptedError(
                        "Encrypted OOXML archives are not supported"
                    ) from exc
                raise DocumentFormatError(
                    "OOXML archive member could not be decompressed"
                ) from exc
            except (EOFError, NotImplementedError, OSError, zipfile.BadZipFile) as exc:
                raise DocumentFormatError(
                    "OOXML archive member failed integrity validation"
                ) from exc
            if member_expanded != member.file_size:
                raise DocumentFormatError(
                    "OOXML archive member size does not match its directory record"
                )
            root = parts[0] if parts else ""
            if root in _OOXML_MIMES:
                roots.add(root)
        ratio = expanded / max(compressed, 1)
        if ratio > config.max_compression_ratio:
            raise DocumentLimitError("OOXML compression ratio exceeds the configured limit")
        if len(roots) != 1:
            raise DocumentFormatError("OOXML container has an ambiguous or missing document family")
        if "[Content_Types].xml" not in archive.namelist():
            raise DocumentFormatError("OOXML container is missing [Content_Types].xml")
        return roots.pop()


def _strict_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentFormatError("Text document is not valid UTF-8") from exc


def _stream_contains(path: Path, markers: tuple[bytes, ...]) -> set[bytes]:
    """Find byte markers without buffering the accepted document."""
    found: set[bytes] = set()
    overlap = max(len(marker) for marker in markers) - 1
    tail = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            window = tail + chunk
            for marker in markers:
                if marker in window:
                    found.add(marker)
            if len(found) == len(markers):
                break
            tail = window[-overlap:] if overlap else b""
    return found


def _pdf_action_is_dangerous(action: object) -> bool:
    """True when a PDF action dictionary is JS/Launch-style (not plain GoTo)."""
    if action is None:
        return False
    try:
        obj = action.get_object() if hasattr(action, "get_object") else action
        subtype = str(obj.get("/S", "") or "")
    except Exception:
        return False
    return subtype in {"/JavaScript", "/JS", "/Launch", "/RichMedia", "/GoToE"}


def _pdf_has_active_content_structural(path: Path) -> bool | None:
    """Use pypdf structure when available. Returns None if pypdf is missing."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path), strict=False)
    except Exception:
        return None
    if getattr(reader, "is_encrypted", False):
        # Encryption is handled separately via /Encrypt byte marker.
        return False
    try:
        root = reader.trailer.get("/Root", {})
        if hasattr(root, "get_object"):
            root = root.get_object()
    except Exception:
        return False
    try:
        if _pdf_action_is_dangerous(root.get("/OpenAction")):
            return True
        if "/JavaScript" in root or "/Launch" in root:
            return True
        names = root.get("/Names")
        if names is not None:
            names = names.get_object() if hasattr(names, "get_object") else names
            if any(key in names for key in ("/JavaScript", "/EmbeddedFiles")):
                return True
        for page in reader.pages:
            if _pdf_action_is_dangerous(page.get("/AA")):
                return True
            for reference in page.get("/Annots", ()) or ():
                try:
                    annotation = reference.get_object()
                except Exception:
                    continue
                if any(
                    _pdf_action_is_dangerous(annotation.get(key))
                    for key in ("/A", "/AA")
                ) or "/JavaScript" in annotation:
                    return True
    except Exception:
        # Parse failed mid-walk — can't judge structurally. Return None (not
        # False) so the caller falls back to the conservative byte scan. A bare
        # `return False` here previously declared such PDFs "clean" and skipped
        # the byte-scan fallback entirely (audit finding).
        return None
    return False


def _pdf_has_active_content(path: Path) -> bool:
    """Detect high-risk PDF active content with low false-positive rate.

    Prefers a structural pypdf pass (catalog / names / annotations). Falls back
    to a conservative byte scan that avoids short ``/JS`` matches inside
    compressed streams (a common false positive on real-world PDFs).
    """

    structural = _pdf_has_active_content_structural(path)
    if structural is not None:
        return structural

    tail = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_ARCHIVE_CHUNK_BYTES), b""):
            window = tail + chunk
            if _PDF_ACTIVE_CONTENT_RE.search(window) or _PDF_JS_NAME_RE.search(window):
                return True
            tail = window[-64:]
    return False


def sniff_document(path: Path, config: DocumentConfig) -> SniffResult:
    """Detect a supported format and enforce extension/content compatibility."""

    extension = path.suffix.lower()
    if extension in _MACRO_EXTENSIONS:
        raise DocumentSecurityError("Macro-enabled Office documents are disabled")
    if extension not in _EXTENSION_MIMES:
        raise DocumentFormatError(
            f"Unsupported document extension {extension or '<none>'}"
        )
    try:
        size = path.stat().st_size
        if size <= 0:
            raise DocumentFormatError("Document is empty")
        if size > config.intake_max_bytes:
            raise DocumentLimitError("Document exceeds the configured intake size limit")
        with path.open("rb") as stream:
            prefix = stream.read(min(size, 65_536))
    except OSError as exc:
        raise DocumentFormatError("Document could not be read") from exc

    if prefix.startswith(_PDF):
        markers = _stream_contains(path, (b"PK\x03\x04", b"/Encrypt"))
        if b"PK\x03\x04" in markers:
            raise DocumentSecurityError("PDF/ZIP polyglot documents are not supported")
        if b"/Encrypt" in markers:
            raise DocumentEncryptedError("Encrypted PDFs are not supported")
        if _pdf_has_active_content(path):
            raise DocumentSecurityError(
                "PDF JavaScript, Launch, or embedded-file content is disabled"
            )
        mime = "application/pdf"
        container = None
    elif prefix.startswith(b"PK\x03\x04"):
        if _PDF in _stream_contains(path, (_PDF,)):
            raise DocumentSecurityError("ZIP/PDF polyglot documents are not supported")
        family = preflight_ooxml(path, config)
        mime = _OOXML_MIMES[family]
        container = "ooxml"
    elif prefix.startswith(_OLE):
        legacy = {
            ".doc": "application/msword",
            ".xls": "application/vnd.ms-excel",
            ".ppt": "application/vnd.ms-powerpoint",
        }
        mime = legacy.get(extension, "application/x-ole-storage")
        container = "ole"
    elif prefix.lstrip().startswith(b"{\\rtf"):
        mime = "application/rtf"
        container = None
    elif prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
        container = None
    elif prefix.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
        container = None
    elif prefix.startswith((b"II*\x00", b"MM\x00*")):
        mime = "image/tiff"
        container = None
    elif prefix.startswith(b"BM"):
        mime = "image/bmp"
        container = None
    elif (
        len(prefix) >= 12
        and prefix.startswith(b"RIFF")
        and prefix[8:12] == b"WEBP"
    ):
        mime = "image/webp"
        container = None
    else:
        text = _strict_text(prefix)
        stripped = text.lstrip()
        lower = stripped[:512].lower()
        if lower.startswith(("<!doctype html", "<html", "<head", "<body")):
            mime = "text/html"
        elif extension == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DocumentFormatError("JSON document is invalid UTF-8 JSON") from exc
            mime = "application/json"
        elif extension == ".csv":
            mime = "text/csv"
        elif extension == ".tsv":
            mime = "text/tab-separated-values"
        elif extension in {".md", ".markdown"}:
            mime = "text/markdown"
        elif extension == ".log":
            mime = "text/x-log"
        elif extension == ".txt":
            mime = "text/plain"
        else:
            raise DocumentFormatError("Document signature does not match a supported format")
        container = None

    if mime not in _EXTENSION_MIMES[extension]:
        raise DocumentFormatError(
            f"Document content ({mime}) does not match extension {extension}"
        )
    return SniffResult(mime_type=mime, extension=extension, container=container)
