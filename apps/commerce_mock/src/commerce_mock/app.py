"""FastAPI app factory for the commerce mock.

Run: uvicorn commerce_mock.app:create_app --factory   (settings from env, see settings.py)
"""

import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import logfire
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from commerce_mock import errors, observe
from commerce_mock.routes import reads, writes
from commerce_mock.settings import Settings
from dtc_policy import PolicyConfig


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_async_engine(s.database_url, pool_size=20, max_overflow=10)
        app.state.settings = s
        app.state.policy = PolicyConfig.from_yaml(s.policy_path)
        app.state.sessions = async_sessionmaker(engine, expire_on_commit=False)
        app.state.fault_rng = random.Random()
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="commerce-mock", lifespan=lifespan)
    errors.install(app)
    observe.install_request_log(app)
    faults = [Depends(observe.inject_fault)]
    app.include_router(reads.router, dependencies=faults)
    app.include_router(writes.router, dependencies=faults)

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    # Traces go to Logfire only when LOGFIRE_TOKEN is set; spans are still created locally.
    logfire.configure(
        service_name="commerce-mock", send_to_logfire="if-token-present", console=False
    )
    logfire.instrument_fastapi(app, excluded_urls=",".join(observe.UNOBSERVED_PATHS))
    logfire.instrument_asyncpg()
    return app
