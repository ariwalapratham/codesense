"""JSON logging and per-request context (request id, access log)."""

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

_REQUEST_ID_MAX_LEN = 128
_SKIP_ACCESS_LOG_PATHS = frozenset({"/health"})


def new_request_id() -> str:
    return str(uuid.uuid4())


def normalize_request_id(raw: Optional[str]) -> str:
    """Accept a client-supplied id, or mint one. Reject values that would break logs."""
    if not raw:
        return new_request_id()
    cleaned = raw.strip()[:_REQUEST_ID_MAX_LEN]
    if cleaned and all(ch.isalnum() or ch in "-_" for ch in cleaned):
        return cleaned
    return new_request_id()


def get_request_id(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    current = request_id_ctx.get()
    return current or new_request_id()


class JsonFormatter(logging.Formatter):
    """Single-line JSON log records. Never include request bodies or secrets."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        for key in ("method", "path", "status", "duration_ms", "code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    """Configure process-wide JSON logs. Safe to call once at import."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Set X-Request-ID on every response and emit a JSON access log."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self._logger = logging.getLogger("core.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header_value = None
        for key, value in scope.get("headers") or []:
            if key == b"x-request-id":
                header_value = value.decode("latin-1")
                break

        request_id = normalize_request_id(header_value)
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        ctx_token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(raw=list(message.get("headers", [])))
                headers["X-Request-ID"] = request_id
                message = {**message, "headers": headers.raw}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in _SKIP_ACCESS_LOG_PATHS:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                self._logger.info(
                    "request completed",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status_code,
                        "duration_ms": duration_ms,
                    },
                )
            request_id_ctx.reset(ctx_token)
