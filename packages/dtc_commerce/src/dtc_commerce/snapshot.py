"""Commerce API responses -> PolicyInput (docs/phases/phase-0.md, Mapping to PolicyInput)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from dtc_commerce.client import CommerceClient
from dtc_contracts import (
    ConditionClaim,
    OrderItemSnapshot,
    OrderSnapshot,
    PolicyInput,
    RequestedItem,
    ShipmentStatus,
    VariantSnapshot,
)
from dtc_contracts.commerce import OrderOut, VariantOut

Action = Literal["return", "exchange"]


@dataclass(frozen=True)
class ItemRequest:
    """What the customer asked for one item (unverified claims, by id)."""

    order_item_id: UUID
    qty: int
    condition_claim: ConditionClaim | None
    target_variant_id: UUID | None = None


class UnknownVariantError(LookupError):
    pass


def variant_snapshot(v: VariantOut) -> VariantSnapshot:
    return VariantSnapshot(
        variant_id=v.id,
        product_id=v.product_id,
        category_code=v.category_code,
        list_price_cents=v.list_price_cents,
        stock=v.stock,
    )


def to_policy_input(
    order: OrderOut,
    action: Action,
    requests: list[ItemRequest],
    targets: dict[UUID, VariantOut],
    now: datetime,
) -> PolicyInput:
    shipments = {s.id: s for s in order.shipments}
    items: list[OrderItemSnapshot] = []
    for item in order.items:
        shipment = None if item.shipment_id is None else shipments[item.shipment_id]
        status: ShipmentStatus = "label_created" if shipment is None else shipment.status
        items.append(
            OrderItemSnapshot(
                order_item_id=item.id,
                variant=variant_snapshot(item.variant),
                qty=item.qty,
                unit_price_cents=item.unit_price_cents,
                committed_returned_qty=item.committed_returned_qty,
                shipment_status=status,
                delivered_at=None if shipment is None else shipment.delivered_at,
            )
        )
    return PolicyInput(
        action=action,
        now=now,
        order=OrderSnapshot(
            order_id=order.id, items=items, committed_refunds_cents=order.committed_refunds_cents
        ),
        items=[
            RequestedItem(
                order_item_id=r.order_item_id,
                qty=r.qty,
                condition_claim=r.condition_claim,
                target_variant=None
                if r.target_variant_id is None
                else variant_snapshot(targets[r.target_variant_id]),
            )
            for r in requests
        ],
    )


async def build_policy_input(
    client: CommerceClient,
    order: OrderOut,
    action: Action,
    requests: list[ItemRequest],
    now: datetime,
) -> PolicyInput:
    """Fetch exchange targets, then map. Raises UnknownVariantError for a bad target id."""
    targets: dict[UUID, VariantOut] = {}
    for r in requests:
        if r.target_variant_id is not None and r.target_variant_id not in targets:
            v = await client.get_variant(r.target_variant_id)
            if v is None:
                raise UnknownVariantError(str(r.target_variant_id))
            targets[r.target_variant_id] = v
    return to_policy_input(order, action, requests, targets, now)
