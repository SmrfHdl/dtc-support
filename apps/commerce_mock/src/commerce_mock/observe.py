"""Fault injection (a router dependency) and the request_log middleware."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_mock.db.models import RequestLog
from commerce_mock.errors import ApiError
from commerce_mock.faults import Faults

Handler = Callable[[Request], Awaitable[Response]]


async def inject_fault(request: Request) -> None:
    """Runs before every API route, once routing knows which endpoint was hit."""
    faults: Faults = request.app.state.settings.faults
    rng: random.Random = request.app.state.fault_rng
    fault = faults.for_route(request.scope["endpoint"].__name__)
    if not fault.active:
        return
    await asyncio.sleep(fault.delay_s(rng))
    if fault.fails(rng):
        raise ApiError(503, "injected_fault", "injected by MOCK_ERROR_RATE")


def trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None


def install_request_log(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_log(request: Request, call_next: Handler) -> Response:  # pyright: ignore[reportUnusedFunction]
        start = time.perf_counter()
        response = await call_next(request)
        sessions: async_sessionmaker[AsyncSession] = app.state.sessions
        async with sessions() as session, session.begin():
            session.add(
                RequestLog(
                    id=uuid4(),
                    ts=app.state.settings.now,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    idempotency_key=request.headers.get("Idempotency-Key"),
                    latency_ms=round((time.perf_counter() - start) * 1000),
                    trace_id=trace_id(),
                )
            )
        return response
