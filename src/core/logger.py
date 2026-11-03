from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.config import settings

_shared_processors: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

if settings.ENVIRONMENT in ("local", "development"):
    _renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
else:
    _renderer = structlog.processors.JSONRenderer()

structlog.configure(
    processors=[
        *_shared_processors,
        _renderer,
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attaches a correlation/request ID to every request and response."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        request_id: str = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        request.state.request_id = request_id

        response: Response = await call_next(request)  # type: ignore[arg-type]
        response.headers["X-Request-ID"] = request_id

        return response
