"""Migrations round-trip on a real Postgres and match the ORM models."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from commerce_mock.db.migration import CONTEXT_OPTS, target_metadata

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"


@pytest.fixture
def alembic_cfg(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _diff(connection: Connection) -> list[object]:
    ctx = MigrationContext.configure(connection, opts=CONTEXT_OPTS)
    return list(compare_metadata(ctx, target_metadata))


async def _model_diff(database_url: str) -> list[object]:
    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    await engine.dispose()
    return diff


def test_upgrade_downgrade_upgrade(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")


def test_models_match_migrations(alembic_cfg: Config, database_url: str) -> None:
    command.upgrade(alembic_cfg, "head")
    assert asyncio.run(_model_diff(database_url)) == []
