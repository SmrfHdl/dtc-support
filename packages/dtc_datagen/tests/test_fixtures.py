"""Fixtures cover the required cases, agree with the policy, and match the committed file."""

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from dtc_contracts import DecisionCode
from dtc_datagen.fixtures import (
    CASES,
    FIXTURES_PER_CASE,
    FixtureMismatchError,
    build_fixtures,
    to_jsonl,
    verify,
)
from dtc_datagen.generate import DEFAULT_NOW as NOW
from dtc_datagen.generate import Generated
from dtc_datagen.rows import Dataset
from dtc_policy import PolicyConfig

COMMITTED = Path(__file__).parents[3] / "evals" / "datasets" / "policy_fixtures.jsonl"
SKIPPED = {"04", "31", "39"}  # see docs/phases/phase-0.md, Datagen


def test_every_required_case_has_fixtures(gen: Generated) -> None:
    counts = Counter(f.case for f in gen.fixtures)
    expected = {f"{n:02d}" for n in range(1, 40)} - SKIPPED | {"38a"}
    assert set(counts) == expected
    assert set(counts.values()) == {FIXTURES_PER_CASE}
    assert len(CASES) == len(expected)


def test_fixtures_do_not_depend_on_bulk_size(gen: Generated, cfg: PolicyConfig) -> None:
    alone = build_fixtures(Dataset(), seed=42, now=NOW, cfg=cfg)
    assert alone == gen.fixtures


def test_verify_catches_a_wrong_expectation(gen: Generated, cfg: PolicyConfig) -> None:
    wrong = replace(gen.fixtures[0], code=DecisionCode.DENY)
    with pytest.raises(FixtureMismatchError, match=wrong.order_number):
        verify(gen.dataset, [wrong], now=NOW, cfg=cfg)


def test_committed_jsonl_is_up_to_date(cfg: PolicyConfig) -> None:
    ds = Dataset()
    fixtures = build_fixtures(ds, seed=42, now=NOW, cfg=cfg)
    fresh = to_jsonl(ds, fixtures, now=NOW, cfg=cfg)
    assert COMMITTED.read_text() == fresh, (
        "evals/datasets/policy_fixtures.jsonl is stale: run `uv run datagen --no-load` "
        "with the default --seed/--now and commit the result"
    )


def test_defect_hint_set_in_cases_24_and_25() -> None:
    for record in map(json.loads, COMMITTED.read_text().splitlines()):
        hint = any(i["defect_claim_possible"] for i in record["decision"]["items"])
        if record["case"] in {"24", "25"}:
            assert hint, record["order_number"]
        if record["case"] in {"26", "27"}:
            assert not hint, record["order_number"]
