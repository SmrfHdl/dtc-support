"""What Alembic compares and migrates: the `commerce` schema only (ADR-0006)."""

from typing import Any

from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from sqlalchemy import MetaData

import commerce_mock.db.models  # noqa: F401  # pyright: ignore[reportUnusedImport]  # registers tables
from commerce_mock.db.base import SCHEMA, Base

target_metadata: MetaData = Base.metadata


def include_name(
    name: str | None, type_: NameFilterType, parent_names: NameFilterParentNames
) -> bool:
    # `support` has its own history; never touch it from here.
    if type_ == "schema":
        return name == SCHEMA
    return True


# Shared by migrations/env.py and the model-drift test, so both see the same thing.
CONTEXT_OPTS: dict[str, Any] = {
    "version_table_schema": SCHEMA,
    "include_schemas": True,
    "include_name": include_name,
    "compare_type": True,
}
