"""Shared integration fixtures: a migrated, seeded Postgres and an in-process mock client."""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config

from commerce_mock.app import create_app
from commerce_mock.settings import DATABASE_URL_ENV, DEFAULT_NOW, Settings
from dtc_datagen.generate import Generated, generate
from dtc_datagen.load import asyncpg_dsn, load
from dtc_policy import PolicyConfig

REPO = Path(__file__).parent
ALEMBIC_INI = REPO / "apps" / "commerce_mock" / "alembic.ini"
POLICY_YAML = REPO / "packages" / "dtc_policy" / "policies" / "policy.yaml"
NOW = datetime.fromisoformat(DEFAULT_NOW)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{DATABASE_URL_ENV} is not set (start deploy/compose first)")
    return url


async def _admin(database_url: str, sql: str) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await conn.execute(sql)  # CREATE/DROP DATABASE cannot run in a transaction
    finally:
        await conn.close()


@pytest.fixture
def scratch_database_url(database_url: str) -> Iterator[str]:
    """An empty throwaway database, for tests that drop tables (keeps `seeded` intact)."""
    name = f"dtc_scratch_{os.getpid()}"
    asyncio.run(_admin(database_url, f"DROP DATABASE IF EXISTS {name}"))
    asyncio.run(_admin(database_url, f"CREATE DATABASE {name}"))
    try:
        yield database_url.rsplit("/", 1)[0] + f"/{name}"
    finally:
        asyncio.run(_admin(database_url, f"DROP DATABASE {name} WITH (FORCE)"))


@pytest.fixture(scope="session")
def cfg() -> PolicyConfig:
    return PolicyConfig.from_yaml(POLICY_YAML)


@pytest.fixture(scope="session")
def seeded(database_url: str, cfg: PolicyConfig) -> Generated:
    """Migrate and load the default dataset once per test session."""
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_cfg, "head")
    gen = generate(seed=42, customers=2000, orders=5000, now=NOW, cfg=cfg)
    asyncio.run(load(gen.dataset, database_url))
    return gen


@pytest.fixture(scope="session")
def policy_yaml() -> Path:
    return POLICY_YAML


@pytest.fixture(scope="session")
def now() -> datetime:
    return NOW


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(seeded: Generated, database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """An in-process client; real concurrency (asyncio.gather) works through it."""
    app = create_app(Settings(database_url=database_url, policy_path=POLICY_YAML, now=NOW))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://mock") as c,
    ):
        yield c
