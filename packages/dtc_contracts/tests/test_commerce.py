"""Validation of the commerce API contract (dtc_contracts.commerce)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from dtc_contracts.commerce import CreateReturnIn, OrderOut

ITEM_1 = UUID(int=1)
ITEM_2 = UUID(int=2)
VARIANT = UUID(int=10)
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def create_return(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order_id": UUID(int=100),
        "type": "return",
        "status": "approved",
        "items": [{"order_item_id": ITEM_1, "qty": 1, "condition_claim": "like_new"}],
        "refund_cents": 2000,
        "reason_codes": [],
        "policy_version": "v1",
    }
    return body | overrides


def test_valid_return_and_exchange() -> None:
    CreateReturnIn.model_validate(create_return())
    exchange_items = [
        {
            "order_item_id": ITEM_1,
            "qty": 1,
            "condition_claim": "like_new",
            "exchange_variant_id": VARIANT,
        }
    ]
    CreateReturnIn.model_validate(
        create_return(type="exchange", refund_cents=None, items=exchange_items)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"refund_cents": None}, id="return-without-refund"),
        pytest.param({"type": "exchange"}, id="exchange-with-refund"),
        pytest.param({"type": "exchange", "refund_cents": None}, id="exchange-without-target"),
        pytest.param(
            {
                "items": [
                    {
                        "order_item_id": ITEM_1,
                        "qty": 1,
                        "condition_claim": None,
                        "exchange_variant_id": VARIANT,
                    }
                ]
            },
            id="return-with-target",
        ),
        pytest.param({"status": "pending_manager"}, id="manager-without-reasons"),
        pytest.param({"reason_codes": ["OVER_AUTO_LIMIT"]}, id="approved-with-reasons"),
        pytest.param({"reason_codes": ["NOT_A_REASON"]}, id="unknown-reason"),
        pytest.param({"status": "refunded"}, id="status-not-creatable"),
        pytest.param({"items": []}, id="no-items"),
        pytest.param(
            {"items": [{"order_item_id": ITEM_1, "qty": 1, "condition_claim": None}] * 2},
            id="duplicate-item",
        ),
        pytest.param({"refund_cents": -1}, id="negative-refund"),
        pytest.param({"extra": 1}, id="unknown-field"),
    ],
)
def test_invalid_create_return(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CreateReturnIn.model_validate(create_return(**overrides))


def test_manager_return_with_reasons() -> None:
    CreateReturnIn.model_validate(
        create_return(status="pending_manager", reason_codes=["OVER_AUTO_LIMIT"])
    )


def order(items_shipment: UUID | None, shipments: list[UUID]) -> dict[str, Any]:
    variant = {
        "id": VARIANT,
        "product_id": UUID(int=20),
        "sku": "APP-0001",
        "product_name": "Tee",
        "category_code": "apparel",
        "size": "M",
        "color": "Black",
        "list_price_cents": 2000,
        "stock": 3,
    }
    return {
        "id": UUID(int=100),
        "order_number": "DTC-100001",
        "customer_id": UUID(int=200),
        "status": "shipped",
        "placed_at": NOW,
        "total_cents": 2000,
        "items": [
            {
                "id": ITEM_2,
                "variant": variant,
                "qty": 1,
                "unit_price_cents": 2000,
                "shipment_id": items_shipment,
                "committed_returned_qty": 0,
            }
        ],
        "shipments": [
            {
                "id": s,
                "carrier": "UPS",
                "tracking_number": f"1Z{n}",
                "status": "in_transit",
                "shipped_at": NOW,
                "delivered_at": None,
                "eta": None,
            }
            for n, s in enumerate(shipments)
        ],
        "committed_refunds_cents": 0,
    }


def test_order_items_must_reference_listed_shipments() -> None:
    OrderOut.model_validate(order(UUID(int=300), [UUID(int=300)]))
    OrderOut.model_validate(order(None, []))
    with pytest.raises(ValidationError, match="unknown shipments"):
        OrderOut.model_validate(order(UUID(int=300), []))
