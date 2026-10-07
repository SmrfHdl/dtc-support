"""dtc_datagen rows must match the ORM tables (datagen cannot import the app)."""

import types
from dataclasses import fields
from typing import Union, get_args, get_origin, get_type_hints

import pytest

from commerce_mock.db.migration import target_metadata
from dtc_datagen.rows import TABLES


def _nullable(hint: object) -> bool:
    return get_origin(hint) in (Union, types.UnionType) and type(None) in get_args(hint)


@pytest.mark.parametrize(("table", "row_type"), TABLES, ids=[name for name, _ in TABLES])
def test_row_fields_match_columns(table: str, row_type: type) -> None:
    columns = target_metadata.tables[f"commerce.{table}"].columns
    hints = get_type_hints(row_type)
    assert [f.name for f in fields(row_type)] == [c.name for c in columns]
    for c in columns:
        assert _nullable(hints[c.name]) == c.nullable, c.name


def test_every_generated_table_is_loaded() -> None:
    generated = {f"commerce.{name}" for name, _ in TABLES}
    assert set(target_metadata.tables) - generated == {"commerce.request_log"}
