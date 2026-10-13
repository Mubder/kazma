"""Typed, safe failures raised by document validation and parsing."""

from __future__ import annotations

__all__ = [
    "DocumentEncryptedError",
    "DocumentFormatError",
    "DocumentLimitError",
    "DocumentOcrError",
    "DocumentOcrUnavailableError",
    "DocumentParseError",
    "DocumentPolicyError",
    "DocumentRenderError",
    "DocumentSandboxError",
    "DocumentSecurityError",
    "DocumentUnavailableError",
    "infrastructure_errors",
    "storage_full",
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
        # Resource failures are retryable only under the queue's finite retry
        # budget. Content/security/output-budget failures stay permanent.
        self.retryable = self.code in {
            "parser_timeout", "parser_oom", "sandbox_terminated",
            "parser_worker_failure", "parser_result_missing", "document_policy_unavailable",
            # The isolated worker broker is down or full: infrastructure, not
            # content. An invalid isolation setting or result stream is not.
            "worker_isolation_unavailable", "worker_isolation_busy",
            # A required malware scanner that cannot run is an outage: the
            # document is held (fail closed) and retried, never rejected as
            # if its content were unsafe (2026-10-13, F07).
            "malware_scanner_unavailable",
        }


def infrastructure_errors() -> tuple[type[BaseException], ...]:
    """Store and file-system failures that say nothing about the document.

    A locked or unreachable metadata database (SQLite or Postgres, including
    a pool that timed out) and an operating-system error are outages: the
    queue retries them within its finite budget. Only these -- a
    ``ValueError`` or ``TypeError`` may be the document or a bug and keeps
    its own handling. Postgres errors used to fall through to the generic
    handler and REJECT a document permanently over a database blip.
    """
    import sqlite3

    errs: list[type[BaseException]] = [OSError, sqlite3.Error]
    try:
        import psycopg

        errs += [psycopg.OperationalError, psycopg.InterfaceError]
    except ImportError:
        pass
    return tuple(errs)


def storage_full(exc: BaseException) -> bool:
    """True for "no space left" / "quota exceeded" from the file system."""
    import errno

    return isinstance(exc, OSError) and exc.errno in {
        errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC),
    }


class DocumentPolicyError(DocumentParseError):
    """Live policy could not be read; execution must not use permissive defaults."""

    code = "document_policy_unavailable"


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


class DocumentRenderError(DocumentParseError):
    """A render could not be produced honestly.

    Raised in preference to emitting a document that *looks* successful but is
    wrong — the canonical case being an Arabic PDF drawn with a font that has
    no Arabic glyphs, which produces a blank page and a warning string nobody
    reads.
    """

    code = "document_render_failed"
