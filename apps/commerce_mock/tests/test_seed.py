"""The default dataset loads into the migrated schema (all constraints hold) in < 1 min."""

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from dtc_datagen.generate import generate
from dtc_datagen.load import asyncpg_dsn, load
from dtc_datagen.rows import TABLES
from dtc_policy import PolicyConfig

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
POLICY_YAML = Path(__file__).parents[3] / "packages" / "dtc_policy" / "policies" / "policy.yaml"


async def _counts(database_url: str) -> dict[str, int]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        return {
            name: await conn.fetchval(f"SELECT count(*) FROM commerce.{name}") for name, _ in TABLES
        }
    finally:
        await conn.close()


def test_seed_loads_and_is_repeatable(database_url: str) -> None:
    cfg_alembic = Config(str(ALEMBIC_INI))
    cfg_alembic.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg_alembic, "head")

    start = time.perf_counter()
    gen = generate(
        seed=42,
        customers=2000,
        orders=5000,
        now=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        cfg=PolicyConfig.from_yaml(POLICY_YAML),
    )
    loaded = asyncio.run(load(gen.dataset, database_url))
    assert time.perf_counter() - start < 60

    # Loading again replaces the data instead of failing on duplicate keys.
    assert asyncio.run(load(gen.dataset, database_url)) == loaded
    assert asyncio.run(_counts(database_url)) == loaded
