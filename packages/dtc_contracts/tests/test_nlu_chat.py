"""Validation of the NLU output and chat API contracts."""

from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from dtc_contracts import ChatIn, NluResult


def nlu(**overrides: Any) -> dict[str, Any]:
    return {"intent": "return", "confidence": 0.9} | overrides


def test_minimal_nlu_result() -> None:
    r = NluResult.model_validate(nlu())
    assert (r.items, r.item_ids, r.order_number) == ([], [], None)


def test_list_defaults_are_not_shared() -> None:
    a, b = NluResult.model_validate(nlu()), NluResult.model_validate(nlu())
    assert a.items is not b.items


def test_full_nlu_result() -> None:
    r = NluResult.model_validate(
        nlu(
            intent="exchange",
            order_number="DTC-100001",
            email="ava@example.com",
            items=[{"text": "the blue hoodie", "size": "M", "qty": 1}],
            item_ids=[str(UUID(int=1))],
            condition_claim="like_new",
            target={"text": "size L", "size": "L"},
            target_variant_id=str(UUID(int=2)),
        )
    )
    assert r.item_ids == [UUID(int=1)]
    assert r.target is not None
    assert r.target.size == "L"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"intent": "refund_me"}, id="unknown-intent"),
        pytest.param({"confidence": 1.2}, id="confidence-above-1"),
        pytest.param({"confidence": -0.1}, id="confidence-below-0"),
        pytest.param({"order_number": ""}, id="empty-string"),
        pytest.param({"condition_claim": "broken"}, id="unknown-condition"),
        pytest.param({"item_ids": ["not-a-uuid"]}, id="bad-uuid"),
        pytest.param({"items": [{"text": "x", "qty": 0}]}, id="zero-qty"),
        pytest.param({"sentiment": "angry"}, id="extra-field"),
    ],
)
def test_invalid_nlu_result(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NluResult.model_validate(nlu(**overrides))


def test_nlu_schema_forbids_extra_fields() -> None:
    """LLM structured output is generated from this schema; extra keys must be rejected."""
    schema = NluResult.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"intent", "confidence"}


def test_chat_in_strips_message() -> None:
    assert ChatIn(session_id="s_1-A", message="  hi there \n").message == "hi there"


@pytest.mark.parametrize(
    ("session_id", "message"),
    [
        pytest.param("", "hi", id="empty-session"),
        pytest.param("a:b", "hi", id="colon-in-session"),
        pytest.param("a b", "hi", id="space-in-session"),
        pytest.param("x" * 129, "hi", id="long-session"),
        pytest.param("s1", "   ", id="blank-message"),
        pytest.param("s1", "x" * 2001, id="long-message"),
    ],
)
def test_invalid_chat_in(session_id: str, message: str) -> None:
    with pytest.raises(ValidationError):
        ChatIn(session_id=session_id, message=message)
