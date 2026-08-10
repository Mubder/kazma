"""Typed, safe failures raised by document validation and parsing."""

from __future__ import annotations

__all__ = [
    "DocumentEncryptedError",
    "DocumentFormatError",
    "DocumentLimitError",
    "DocumentOcrError",
    "DocumentOcrUnavailableError",
    "DocumentParseError",
    "DocumentSandboxError",
    "DocumentSecurityError",
    "DocumentUnavailableError",
]


class DocumentParseError(RuntimeError):
    """Base parser error whose message is safe to return to a caller."""

    code = "document_parse_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message
        if code is not None:
            self.code = code


class DocumentFormatError(DocumentParseError):
    code = "unsupported_document_format"


class DocumentUnavailableError(DocumentParseError):
    code = "document_parser_unavailable"


class DocumentOcrError(DocumentParseError):
    code = "ocr_failed"


class DocumentOcrUnavailableError(DocumentOcrError):
    code = "ocr_unavailable"


class DocumentSecurityError(DocumentParseError):
    code = "unsafe_document"


class DocumentEncryptedError(DocumentSecurityError):
    code = "encrypted_document"


class DocumentLimitError(DocumentSecurityError):
    code = "document_limit_exceeded"


class DocumentSandboxError(DocumentParseError):
    code = "document_parser_failed"
