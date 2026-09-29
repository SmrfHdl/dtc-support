"""Input validation of PolicyInput (phase-0 spec, case 39)."""

import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from dtc_contracts import PolicyInput

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
ORDER_ITEM_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ID = UUID("00000000-0000-0000-0000-000000000099")
LA = ZoneInfo("America/Los_Angeles")  # UTC-7 on 2026-09-28, so NOW is 05:00 in LA

VARIANT: dict[str, Any] = {
    "variant_id": UUID("00000000-0000-0000-0000-000000000010"),
    "product_id": UUID("00000000-0000-0000-0000-000000000020"),
    "category_code": "apparel",
    "list_price_cents": 2000,
    "stock": 3,
}
REQUESTED: dict[str, Any] = {
    "order_item_id": ORDER_ITEM_ID,
    "qty": 1,
    "condition_claim": "like_new",
}


def valid_payload() -> dict[str, Any]:
    return copy.deepcopy(
        {
            "action": "return",
            "now": NOW,
            "order": {
                "order_id": UUID("00000000-0000-0000-0000-000000000100"),
                "items": [
                    {
                        "order_item_id": ORDER_ITEM_ID,
                        "variant": VARIANT,
                        "qty": 2,
                        "unit_price_cents": 2000,
                        "committed_returned_qty": 0,
                        "shipment_status": "delivered",
                        "delivered_at": NOW - timedelta(days=10),
                    }
                ],
                "committed_refunds_cents": 0,
            },
            "items": [REQUESTED],
        }
    )


def set_in(payload: dict[str, Any], path: tuple[str | int, ...], value: Any) -> dict[str, Any]:
    """Replace the value at `path`, e.g. ("order", "items", 0, "qty")."""
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return payload


ORDER_ITEM = ("order", "items", 0)


def test_valid_payload_is_accepted() -> None:
    PolicyInput.model_validate(valid_payload())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param((*ORDER_ITEM, "delivered_at"), NOW + timedelta(minutes=4), id="future-4min"),
        pytest.param((*ORDER_ITEM, "delivered_at"), NOW + timedelta(minutes=5), id="future-5min"),
        pytest.param(
            (*ORDER_ITEM, "delivered_at"), datetime(2026, 9, 28, 5, 4, tzinfo=LA), id="la-tz-4min"
        ),
        pytest.param((*ORDER_ITEM, "shipment_status"), "in_transit", id="not-delivered"),
        pytest.param((*ORDER_ITEM, "committed_returned_qty"), 2, id="committed-equals-qty"),
        pytest.param(("items", 0, "condition_claim"), None, id="condition-none"),
    ],
)
def test_edge_values_are_accepted(path: tuple[str | int, ...], value: Any) -> None:
    PolicyInput.model_validate(set_in(valid_payload(), path, value))


def test_not_delivered_item_may_have_no_delivered_at() -> None:
    payload = valid_payload()
    set_in(payload, (*ORDER_ITEM, "shipment_status"), "in_transit")
    set_in(payload, (*ORDER_ITEM, "delivered_at"), None)
    PolicyInput.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        # field-level rules
        pytest.param(("now",), datetime(2026, 9, 28, 12, 0), "timezone", id="naive-now"),  # noqa: DTZ001
        pytest.param(
            (*ORDER_ITEM, "delivered_at"),
            datetime(2026, 9, 20),  # noqa: DTZ001
            "timezone",
            id="naive-delivered-at",
        ),
        pytest.param(("items",), [], "at least 1", id="empty-items"),
        pytest.param(("items", 0, "qty"), 0, "greater than 0", id="requested-qty-zero"),
        pytest.param(("items", 0, "qty"), -1, "greater than 0", id="requested-qty-negative"),
        pytest.param((*ORDER_ITEM, "qty"), 0, "greater than 0", id="order-qty-zero"),
        pytest.param((*ORDER_ITEM, "unit_price_cents"), -1, "greater than or equal", id="neg-paid"),
        pytest.param(
            (*ORDER_ITEM, "variant", "list_price_cents"), -1, "greater than or equal", id="neg-list"
        ),
        pytest.param(
            (*ORDER_ITEM, "variant", "stock"), -1, "greater than or equal", id="neg-stock"
        ),
        pytest.param(
            ("order", "committed_refunds_cents"), -1, "greater than or equal", id="neg-committed"
        ),
        pytest.param(("action",), "refund", "Input should be", id="bad-action"),
        pytest.param(("items", 0, "condition_claim"), "broken", "Input should be", id="bad-cond"),
        pytest.param((*ORDER_ITEM, "shipment_status"), "shipped", "Input should be", id="bad-ship"),
        pytest.param(("items", 0, "conditon_claim"), "worn", "Extra inputs", id="typo-field"),
        # OrderItemSnapshot rules
        pytest.param((*ORDER_ITEM, "delivered_at"), None, "delivered_at is required", id="no-date"),
        pytest.param(
            (*ORDER_ITEM, "committed_returned_qty"), 3, "less than or equal to qty", id="over-qty"
        ),
        # PolicyInput cross-field rules
        pytest.param(("items",), [REQUESTED, REQUESTED], "duplicate", id="duplicate-id"),
        pytest.param(("items", 0, "order_item_id"), OTHER_ID, "not in order", id="foreign-id"),
        pytest.param(
            (*ORDER_ITEM, "delivered_at"),
            NOW + timedelta(minutes=5, seconds=1),
            "in the future",
            id="future-5min-1s",
        ),
        pytest.param(
            (*ORDER_ITEM, "delivered_at"), NOW + timedelta(days=1), "in the future", id="future-1d"
        ),
        # 05:06 LA is 12:06 UTC: in the future, although the wall-clock hour is earlier than 12:00
        pytest.param(
            (*ORDER_ITEM, "delivered_at"),
            datetime(2026, 9, 28, 5, 6, tzinfo=LA),
            "in the future",
            id="la-tz-6min",
        ),
    ],
)
def test_invalid_payload_is_rejected(path: tuple[str | int, ...], value: Any, match: str) -> None:
    payload = set_in(valid_payload(), path, value)
    with pytest.raises(ValidationError, match=match):
        PolicyInput.model_validate(payload)


def test_models_are_frozen() -> None:
    inp = PolicyInput.model_validate(valid_payload())
    with pytest.raises(ValidationError, match="frozen"):
        inp.action = "exchange"  # type: ignore[misc]
