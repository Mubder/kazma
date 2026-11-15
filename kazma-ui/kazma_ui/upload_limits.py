"""The routes that take an uploaded file, and the largest file each takes.

FA-134 (2026-11): a route that takes ``UploadFile = File(...)`` is called
only after Starlette has parsed the whole multipart body and spooled the file
to disk, so the routes' own size checks refused a 2 GB upload already written
to the temporary folder. Every request's body is now bounded before it is
read by ``kazma_ui.body_limits.BodySizeLimitMiddleware`` (2026-11-15), which
gives each route here its file limit plus :data:`MULTIPART_ALLOWANCE` for the
form's boundaries and other fields; the route still checks the file itself.
``tests/test_upload_limits.py`` holds :data:`UPLOAD_LIMITS` to every route
that takes a file.
"""

from __future__ import annotations

from kazma_ui.body_limits import BodySizeLimitMiddleware
from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES

__all__ = ["MAX_STT_BYTES", "MULTIPART_ALLOWANCE", "UPLOAD_LIMITS", "UploadSizeLimitMiddleware"]

#: The FA-134 layer's name: the one body layer now bounds uploads too.
UploadSizeLimitMiddleware = BodySizeLimitMiddleware

#: Room for the multipart framing and the form's small fields.
MULTIPART_ALLOWANCE = 64 * 1024

#: The largest audio file ``POST /api/voice/stt`` takes.
MAX_STT_BYTES = 25 * 1024 * 1024

#: Every route that takes an uploaded file, with the largest file it takes.
UPLOAD_LIMITS: dict[str, int] = {
    "/api/chat/upload": MAX_UPLOAD_BYTES,
    "/api/voice/stt": MAX_STT_BYTES,
}
