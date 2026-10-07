"""Load a Dataset into the `commerce` schema with COPY."""

from dataclasses import astuple, fields

import asyncpg

from dtc_datagen.rows import TABLES, Dataset

SCHEMA = "commerce"


def asyncpg_dsn(url: str) -> str:
    """asyncpg wants a plain postgresql:// URL, not SQLAlchemy's postgresql+asyncpg://."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def load(ds: Dataset, database_url: str) -> dict[str, int]:
    """Replace all generated tables in one transaction; returns rows per table."""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        async with conn.transaction():
            # No CASCADE: a new table referencing these must be added here on purpose.
            await conn.execute("TRUNCATE " + ", ".join(f"{SCHEMA}.{name}" for name, _ in TABLES))
            counts: dict[str, int] = {}
            for name, row_type in TABLES:
                rows = getattr(ds, name)
                await conn.copy_records_to_table(
                    name,
                    schema_name=SCHEMA,
                    columns=[f.name for f in fields(row_type)],
                    records=[astuple(r) for r in rows],
                )
                counts[name] = len(rows)
            return counts
    finally:
        await conn.close()
