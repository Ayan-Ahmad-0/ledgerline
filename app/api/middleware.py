import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.core.request_id import get_request_id, set_request_id
from app.core.logging import logger
import uuid


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """Logs request lifecycle with elapsed milliseconds and request ID."""

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        set_request_id(req_id)

        start_time = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)

        logger.info(
            f"{request.method} {request.url.path} returned {response.status_code} in {elapsed_ms}ms",
            extra={"path": request.url.path, "status": response.status_code, "latency_ms": elapsed_ms}
        )
        return response
