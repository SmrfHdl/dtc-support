"""Writes orders with consistent shipments, events, and returns into a Dataset.

Used by both the random bulk data and the fixtures, so both follow the same rules:
every timestamp is <= now, events are in order, and the last event matches the
shipment status.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from dtc_contracts import ConditionClaim, ShipmentStatus
from dtc_datagen.rng import Rng
from dtc_datagen.rows import (
    CustomerRow,
    Dataset,
    OrderItemRow,
    OrderRow,
    RefundRow,
    ReturnItemRow,
    ReturnRow,
    ShipmentEventRow,
    ShipmentRow,
    VariantRow,
)

CARRIERS = ("UPS", "USPS", "FedEx")
CITIES = (
    "Los Angeles, CA",
    "Reno, NV",
    "Phoenix, AZ",
    "Denver, CO",
    "Dallas, TX",
    "Chicago, IL",
    "Columbus, OH",
    "Atlanta, GA",
    "Newark, NJ",
    "Seattle, WA",
)
WAREHOUSE = "Ontario, CA"
DESCRIPTIONS: dict[str, str] = {
    "label_created": "Shipping label created",
    "in_transit": "In transit",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "exception": "Delivery exception: address issue",
    "lost": "Package reported lost by carrier",
}
NON_REJECTED_RETURN_STATUSES = (
    "approved",
    "pending_manager",
    "received",
    "inspection_passed",
    "refunded",
)

H = timedelta(hours=1)
D = timedelta(days=1)


@dataclass(frozen=True)
class ShipmentPlan:
    """Final state of one shipment. Lines with an equal plan share the shipment."""

    status: ShipmentStatus
    delivered_at: datetime | None = None  # required when status is delivered


@dataclass(frozen=True)
class Line:
    variant: VariantRow
    qty: int
    unit_price_cents: int
    shipment: ShipmentPlan | None  # None: not in any shipment yet


class Builder:
    def __init__(self, ds: Dataset, rng: Rng, now: datetime, tracking_prefix: str) -> None:
        self.ds = ds
        self.rng = rng
        self.now = now
        self._tracking_prefix = tracking_prefix
        self._tracking_seq = 0

    def customer(self, email: str, name: str) -> CustomerRow:
        row = CustomerRow(
            id=self.rng.uuid(),
            email=email,
            name=name,
            created_at=self.rng.between(self.now - 1000 * D, self.now - 90 * D),
        )
        self.ds.customers.append(row)
        return row

    def order(
        self, customer: CustomerRow, order_number: str, lines: list[Line]
    ) -> list[OrderItemRow]:
        """Add an order; returns its items in the same order as `lines`."""
        order_id = self.rng.uuid()
        plans = list(dict.fromkeys(line.shipment for line in lines if line.shipment is not None))
        shipments = [self._shipment(order_id, plan) for plan in plans]
        shipment_by_plan = {plan: s for plan, (s, _) in zip(plans, shipments, strict=True)}

        label_times = [label_ts for _, label_ts in shipments]
        if label_times:
            placed_at = min(label_times) - self.rng.randint(0, 12 * 60) * timedelta(minutes=1)
        else:
            placed_at = self.rng.between(self.now - 2 * D, self.now)

        items = [
            OrderItemRow(
                id=self.rng.uuid(),
                order_id=order_id,
                variant_id=line.variant.id,
                shipment_id=None if line.shipment is None else shipment_by_plan[line.shipment].id,
                qty=line.qty,
                unit_price_cents=line.unit_price_cents,
            )
            for line in lines
        ]
        self.ds.orders.append(
            OrderRow(
                id=order_id,
                order_number=order_number,
                customer_id=customer.id,
                status=_order_status([line.shipment for line in lines]),
                placed_at=placed_at,
                total_cents=sum(line.unit_price_cents * line.qty for line in lines),
            )
        )
        self.ds.order_items.extend(items)
        return items

    def prior_return(
        self,
        item: OrderItemRow,
        delivered_at: datetime,
        *,
        qty: int,
        status: str,
        policy_version: str,
        condition: ConditionClaim | None = "like_new",
        exchange_variant: VariantRow | None = None,
    ) -> ReturnRow:
        """An earlier return/exchange of `item` (counts toward committed_* unless rejected)."""
        is_exchange = exchange_variant is not None
        created_at = self.rng.between(
            min(delivered_at + H, self.now), min(delivered_at + 25 * D, self.now)
        )
        row = ReturnRow(
            id=self.rng.uuid(),
            order_id=item.order_id,
            type="exchange" if is_exchange else "return",
            status=status,
            refund_cents=None if is_exchange else item.unit_price_cents * qty,
            reason_codes=["DEFECT_CLAIM"] if condition == "defective" else [],
            policy_version=policy_version,
            idempotency_key=str(self.rng.uuid()),
            created_at=created_at,
        )
        self.ds.returns.append(row)
        self.ds.return_items.append(
            ReturnItemRow(
                return_id=row.id,
                order_item_id=item.id,
                qty=qty,
                condition_claim=condition,
                exchange_variant_id=None if exchange_variant is None else exchange_variant.id,
            )
        )
        if status == "refunded" and row.refund_cents is not None:
            self.ds.refunds.append(
                RefundRow(
                    id=self.rng.uuid(),
                    return_id=row.id,
                    amount_cents=row.refund_cents,
                    status="completed",
                    approved_by=None,
                    idempotency_key=str(self.rng.uuid()),
                    created_at=self.rng.between(min(created_at + 2 * D, self.now), self.now),
                )
            )
        return row

    def _shipment(self, order_id: UUID, plan: ShipmentPlan) -> tuple[ShipmentRow, datetime]:
        """Add a shipment and its events; returns it with its label time."""
        rng, now = self.rng, self.now
        events: list[tuple[datetime, ShipmentStatus]] = []
        shipped_at: datetime | None = None
        eta: datetime | None
        match plan.status:
            case "delivered":
                assert plan.delivered_at is not None
                shipped_at = plan.delivered_at - rng.randint(2 * 24, 6 * 24) * H
                ofd = plan.delivered_at - rng.randint(2, 8) * H
                events = [
                    (shipped_at, "in_transit"),
                    (ofd, "out_for_delivery"),
                    (plan.delivered_at, "delivered"),
                ]
                eta = shipped_at + 5 * D
            case "in_transit":
                shipped_at = rng.between(now - 5 * D, now - 6 * H)
                events = [(shipped_at, "in_transit")]
                eta = now + rng.randint(1, 4) * D
            case "out_for_delivery":
                shipped_at = rng.between(now - 5 * D, now - 1 * D)
                events = [
                    (shipped_at, "in_transit"),
                    (now - rng.randint(1, 6) * H, "out_for_delivery"),
                ]
                eta = now + 6 * H
            case "exception":
                shipped_at = rng.between(now - 20 * D, now - 5 * D)
                events = [
                    (shipped_at, "in_transit"),
                    (rng.between(shipped_at + D, now), "exception"),
                ]
                eta = shipped_at + 5 * D
            case "lost":
                shipped_at = rng.between(now - 30 * D, now - 10 * D)
                events = [
                    (shipped_at, "in_transit"),
                    (rng.between(shipped_at + 5 * D, now), "lost"),
                ]
                eta = shipped_at + 5 * D
            case "label_created":
                eta = now + rng.randint(3, 7) * D

        first = shipped_at if shipped_at is not None else now
        label_ts = rng.between(first - 2 * D, first - 2 * H)
        if shipped_at is None:
            label_ts = rng.between(now - 3 * D, now - H)

        self._tracking_seq += 1
        row = ShipmentRow(
            id=rng.uuid(),
            order_id=order_id,
            carrier=rng.choice(CARRIERS),
            tracking_number=f"{self._tracking_prefix}{self._tracking_seq:09d}",
            status=plan.status,
            shipped_at=shipped_at,
            delivered_at=plan.delivered_at if plan.status == "delivered" else None,
            eta=eta,
        )
        self.ds.shipments.append(row)
        label: tuple[datetime, ShipmentStatus] = (label_ts, "label_created")
        for ts, status in [label, *events]:
            self.ds.shipment_events.append(
                ShipmentEventRow(
                    id=rng.uuid(),
                    shipment_id=row.id,
                    ts=ts,
                    status=status,
                    location=WAREHOUSE if status == "label_created" else rng.choice(CITIES),
                    description=DESCRIPTIONS[status],
                )
            )
        return row, label_ts


def _order_status(plans: list[ShipmentPlan | None]) -> str:
    statuses = ["label_created" if p is None else p.status for p in plans]
    if all(s == "label_created" for s in statuses):
        return "placed"
    if any(s == "label_created" for s in statuses):
        return "partially_shipped"
    if all(s == "delivered" for s in statuses):
        return "delivered"
    return "shipped"
