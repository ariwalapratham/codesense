"""Application errors and the API error envelope."""

import logging
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import get_request_id

logger = logging.getLogger(__name__)

STATUS_TO_CODE: Dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "repo_too_large",
    422: "validation_error",
    429: "rate_limited",
    502: "upstream_error",
    503: "upstream_error",
}


def code_for_status(status_code: int) -> str:
    """Map an HTTP status code to a documented error code."""
    if status_code in STATUS_TO_CODE:
        return STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "internal_error"
    return "validation_error"


class AppError(Exception):
    """Controlled failure that the API turns into the error envelope."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


def error_body(
    *,
    code: str,
    message: str,
    request_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the public error envelope."""
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details,
        }
    }


def _request_id_from(request: Request) -> str:
    return get_request_id(getattr(request.state, "request_id", None))


def _retry_after_seconds(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, datetime):
        now = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
        return max(0, int((value - now).total_seconds()))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _retry_after_seconds(parsed)
    return None


def _http_exception_payload(exc: StarletteHTTPException) -> tuple[str, Optional[Dict[str, Any]]]:
    detail = exc.detail
    if isinstance(detail, dict):
        message = (
            detail.get("message")
            or detail.get("error")
            or "Request failed"
        )
        extra = {k: v for k, v in detail.items() if k not in {"message", "error"}}
        return str(message), extra or None
    if isinstance(detail, list):
        return "Request failed", {"errors": jsonable_encoder(detail)}
    return str(detail) if detail else "Request failed", None


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Optional[Dict[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    sanitize: bool = False,
) -> JSONResponse:
    response_headers = dict(headers or {})
    if status_code == 429 and "Retry-After" not in response_headers:
        retry_after = _retry_after_seconds((details or {}).get("retry_after"))
        if retry_after is not None:
            response_headers["Retry-After"] = str(retry_after)

    public_message = "Internal server error" if sanitize else message
    public_details = None if sanitize else details
    return JSONResponse(
        status_code=status_code,
        content=error_body(
            code=code,
            message=public_message,
            request_id=request_id,
            details=public_details,
        ),
        headers=response_headers,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = _request_id_from(request)
    log = logger.error if exc.status_code >= 500 else logger.warning
    log(
        "Application error: %s",
        exc.message,
        extra={"code": exc.code, "status": exc.status_code},
    )
    return _error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        details=exc.details,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = _request_id_from(request)
    message, details = _http_exception_payload(exc)
    if exc.status_code >= 500:
        logger.error("HTTP error %s: %s", exc.status_code, message)
    return _error_response(
        status_code=exc.status_code,
        code=code_for_status(exc.status_code),
        message=message,
        request_id=request_id,
        details=details,
        headers=exc.headers,
        sanitize=exc.status_code >= 500,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = _request_id_from(request)
    return _error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        request_id=request_id,
        details={"errors": jsonable_encoder(exc.errors())},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id_from(request)
    logger.error("Unhandled exception", exc_info=exc)
    return _error_response(
        status_code=500,
        code="internal_error",
        message="Internal server error",
        request_id=request_id,
        sanitize=True,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach envelope handlers. Register the catch-all last."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
