"""Build a PolicyInput from generated rows (docs/phases/phase-0.md, Mapping to PolicyInput).

The commerce client does the same from the database; a P0 step-5 test compares the two.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from dtc_contracts import (
    ConditionClaim,
    OrderItemSnapshot,
    OrderSnapshot,
    PolicyInput,
    RequestedItem,
    ShipmentStatus,
    VariantSnapshot,
)
from dtc_datagen.rows import Dataset, ShipmentRow, VariantRow

Action = Literal["return", "exchange"]


@dataclass(frozen=True)
class RequestSpec:
    """One requested item, by id (what the orchestrator gets from the customer)."""

    order_item_id: UUID
    qty: int
    condition_claim: ConditionClaim | None
    target_variant_id: UUID | None = None


class Index:
    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self.products = {p.id: p for p in ds.products}
        self.variants = {v.id: v for v in ds.variants}
        self.shipments = {s.id: s for s in ds.shipments}
        self.orders = {o.id: o for o in ds.orders}
        self.items_by_order: dict[UUID, list[UUID]] = defaultdict(list)
        self.items = {i.id: i for i in ds.order_items}
        for item in ds.order_items:
            self.items_by_order[item.order_id].append(item.id)

        live = {r.id: r for r in ds.returns if r.status != "rejected"}
        self.committed_qty: dict[UUID, int] = defaultdict(int)
        for ri in ds.return_items:
            if ri.return_id in live:
                self.committed_qty[ri.order_item_id] += ri.qty
        self.committed_refunds: dict[UUID, int] = defaultdict(int)
        for r in live.values():
            self.committed_refunds[r.order_id] += r.refund_cents or 0

    def variant_snapshot(self, variant_id: UUID) -> VariantSnapshot:
        v: VariantRow = self.variants[variant_id]
        return VariantSnapshot(
            variant_id=v.id,
            product_id=v.product_id,
            category_code=self.products[v.product_id].category_code,
            list_price_cents=v.list_price_cents,
            stock=v.stock,
        )

    def policy_input(
        self, order_id: UUID, action: Action, requests: list[RequestSpec], now: datetime
    ) -> PolicyInput:
        items: list[OrderItemSnapshot] = []
        for item_id in self.items_by_order[order_id]:
            item = self.items[item_id]
            shipment: ShipmentRow | None = (
                None if item.shipment_id is None else self.shipments[item.shipment_id]
            )
            status: ShipmentStatus = "label_created" if shipment is None else shipment.status
            items.append(
                OrderItemSnapshot(
                    order_item_id=item.id,
                    variant=self.variant_snapshot(item.variant_id),
                    qty=item.qty,
                    unit_price_cents=item.unit_price_cents,
                    committed_returned_qty=self.committed_qty[item.id],
                    shipment_status=status,
                    delivered_at=None if shipment is None else shipment.delivered_at,
                )
            )
        return PolicyInput(
            action=action,
            now=now,
            order=OrderSnapshot(
                order_id=order_id,
                items=items,
                committed_refunds_cents=self.committed_refunds[order_id],
            ),
            items=[
                RequestedItem(
                    order_item_id=r.order_item_id,
                    qty=r.qty,
                    condition_claim=r.condition_claim,
                    target_variant=None
                    if r.target_variant_id is None
                    else self.variant_snapshot(r.target_variant_id),
                )
                for r in requests
            ],
        )
