"""Alembic environment for the `commerce` schema (own history, see ADR-0006)."""

import asyncio

from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from commerce_mock.db.base import SCHEMA
from commerce_mock.db.migration import CONTEXT_OPTS, target_metadata
from commerce_mock.settings import database_url


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        literal_binds=connection is None,
        **CONTEXT_OPTS,
    )


def run_migrations_offline() -> None:
    _configure(url=database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    # The version table lives in the schema, so the schema must exist first.
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(context.config.get_main_option("sqlalchemy.url") or database_url())
    async with engine.begin() as conn:
        await conn.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
