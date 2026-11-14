"""One durable-settings error response for every API write path."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from kazma_core.config_availability import ConfigStoreUnavailableError
from kazma_core.errors import validation_error

from kazma_ui.i18n import current_language


def install_config_error_handler(app: FastAPI) -> None:
    """Keep failed saves explicit, retryable and free of database secrets."""
    @app.exception_handler(ConfigStoreUnavailableError)
    async def unavailable(request: Request, exc: ConfigStoreUnavailableError) -> JSONResponse:
        # Kazma's sentence (the database's answer worded by refusal_reason).
        detail = validation_error(exc, max_chars=600)
        if current_language() == "ar":
            detail = "تعذر تأكيد حفظ الإعدادات في التخزين الدائم. تحقق من اتصال قاعدة البيانات ثم أعد التحميل والمراجعة قبل المحاولة مجدداً."
        return JSONResponse(
            status_code=503,
            content={"status": "error", "code": "config_store_unavailable", "detail": detail, "backend": exc.backend},
            headers={"Retry-After": "5"},
        )
