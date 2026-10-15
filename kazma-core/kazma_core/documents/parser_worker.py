"""JSON file protocol entry point for isolated document parser execution."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

from .config import DocumentConfig
from .errors import DocumentFormatError, DocumentParseError
from .ocr import apply_ocr
from .parsers.common import ParseContext, sha256_path
from .registry import ParserRegistry
from .sniff import sniff_document

_PROTOCOL_VERSION = 1


def _write_response(path: Path, response: dict[str, Any]) -> None:
    payload = json.dumps(
        response,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = path.with_suffix(f"{path.suffix}.writing")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _load_request(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("protocol_version") != _PROTOCOL_VERSION:
        raise ValueError("invalid parser worker request")
    return value


def _config(value: object) -> DocumentConfig:
    if not isinstance(value, dict):
        raise ValueError("invalid document parser limits")
    allowed = {item.name for item in fields(DocumentConfig)}
    if set(value) != allowed:
        raise ValueError("document parser limits schema mismatch")
    normalized = dict(value)
    normalized["storage_root"] = Path(str(normalized["storage_root"])).resolve()
    return DocumentConfig(**normalized)


def execute(request_path: Path, result_path: Path) -> int:
    started = time.monotonic()
    try:
        request = _load_request(request_path)
        source = Path(str(request["source_path"])).resolve(strict=True)
        expected_sha = str(request["source_sha256"])
        config = _config(request["config"])
        actual_sha = sha256_path(source)
        if actual_sha != expected_sha:
            raise DocumentParseError(
                "Document changed before isolated parsing began",
                code="document_changed",
            )
        sniffed = sniff_document(source, config)
        if request.get("operation", "parse") == "inspect":
            _write_response(result_path, {
                "protocol_version": _PROTOCOL_VERSION, "ok": True,
                "code": "ok", "message": "Document inspected",
                "source_sha256": actual_sha,
                "sniff": {"mime_type": sniffed.mime_type, "extension": sniffed.extension,
                          "container": sniffed.container},
            })
            return 0
        if request.get("operation", "parse") != "parse":
            raise ValueError("invalid parser worker operation")
        if (
            ("mime_type" in request and sniffed.mime_type != request["mime_type"])
            or ("extension" in request and sniffed.extension != request["extension"])
        ):
            raise DocumentParseError(
                "Document type changed before isolated parsing began",
                code="document_changed",
            )
        registry = ParserRegistry()
        plugin, _ = registry.resolve(
            mime_type=sniffed.mime_type,
            extension=sniffed.extension,
        )
        parser = plugin.factory()
        context = ParseContext(
            config=config,
            source_sha256=actual_sha,
            mime_type=sniffed.mime_type,
            extension=sniffed.extension,
            parser_id=plugin.parser_id,
            parser_version=plugin.parser_version,
        )
        ir = parser.parse(source, context)
        ocr = request.get("ocr", {})
        if not isinstance(ocr, dict):
            raise ValueError("invalid OCR worker request")
        force_ocr = bool(ocr.get("force", False))
        raw_pages = ocr.get("pages")
        if raw_pages is not None and not isinstance(raw_pages, list):
            raise ValueError("invalid OCR page selector")
        if isinstance(raw_pages, list) and any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in raw_pages
        ):
            raise DocumentFormatError("OCR pages must be 1-based integers")
        ocr_pages = (
            tuple(int(item) for item in raw_pages)
            if isinstance(raw_pages, list)
            else None
        )
        language = ocr.get("language")
        if language is not None and not isinstance(language, str):
            raise ValueError("invalid OCR language")
        models = ocr.get("models")
        if models is not None and not isinstance(models, str):
            raise ValueError("invalid OCR model directory")
        if config.ocr_enabled or force_ocr:
            ir = apply_ocr(
                source,
                ir,
                config,
                force=force_ocr,
                language=language,
                pages=ocr_pages,
                work_dir=result_path.parent,
                model_dir=Path(models) if models else None,
                # The parent ends this worker at worker_timeout_seconds:
                # OCR leaves a fifth of it for the rest of the parse.
                deadline=started + 0.8 * config.worker_timeout_seconds,
            )
        # Local third-party parser code shares the same process-tree resource
        # and wall-clock boundary. The credentialed remote broker stays in the
        # parent, where the immutable durable audit scope exists.
        from .extract_salvage import maybe_salvage_extract

        ir = maybe_salvage_extract(source, ir, config=config, allow_remote=False)
        if sha256_path(source) != actual_sha:
            raise DocumentParseError(
                "Document changed during isolated parsing",
                code="document_changed",
            )
        ir_json = ir.to_json()
        _write_response(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": True,
                "code": "ok",
                "message": "Document parsed",
                "source_sha256": actual_sha,
                "ir_sha256": hashlib.sha256(ir_json.encode("utf-8")).hexdigest(),
                "ir": json.loads(ir_json),
            },
        )
        return 0
    except DocumentParseError as exc:
        _write_response(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": False,
                "code": exc.code,
                "message": exc.safe_message,
            },
        )
        return 0
    except Exception as exc:
        _write_response(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": False,
                "code": "parser_worker_failure",
                "message": f"Document parser failed safely ({type(exc).__name__})",
            },
        )
        return 1


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    return execute(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
