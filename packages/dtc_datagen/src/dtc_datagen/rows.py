"""One dataclass per `commerce` table. Field names and order are the column names."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from dtc_contracts import ShipmentStatus


@dataclass(frozen=True, slots=True)
class CustomerRow:
    id: UUID
    email: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProductRow:
    id: UUID
    sku: str
    name: str
    category_code: str


@dataclass(frozen=True, slots=True)
class VariantRow:
    id: UUID
    product_id: UUID
    size: str
    color: str
    list_price_cents: int
    stock: int


@dataclass(frozen=True, slots=True)
class OrderRow:
    id: UUID
    order_number: str
    customer_id: UUID
    status: str
    placed_at: datetime
    total_cents: int


@dataclass(frozen=True, slots=True)
class ShipmentRow:
    id: UUID
    order_id: UUID
    carrier: str
    tracking_number: str
    status: ShipmentStatus
    shipped_at: datetime | None
    delivered_at: datetime | None
    eta: datetime | None


@dataclass(frozen=True, slots=True)
class OrderItemRow:
    id: UUID
    order_id: UUID
    variant_id: UUID
    shipment_id: UUID | None
    qty: int
    unit_price_cents: int


@dataclass(frozen=True, slots=True)
class ShipmentEventRow:
    id: UUID
    shipment_id: UUID
    ts: datetime
    status: ShipmentStatus
    location: str | None
    description: str


@dataclass(frozen=True, slots=True)
class ReturnRow:
    id: UUID
    order_id: UUID
    type: str
    status: str
    refund_cents: int | None
    reason_codes: list[str]
    policy_version: str
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReturnItemRow:
    return_id: UUID
    order_item_id: UUID
    qty: int
    condition_claim: str | None
    exchange_variant_id: UUID | None


@dataclass(frozen=True, slots=True)
class RefundRow:
    id: UUID
    return_id: UUID
    amount_cents: int
    status: str
    approved_by: str | None
    idempotency_key: str
    created_at: datetime


@dataclass(slots=True)
class Dataset:
    """All rows, one list per table."""

    customers: list[CustomerRow] = field(default_factory=list[CustomerRow])
    products: list[ProductRow] = field(default_factory=list[ProductRow])
    variants: list[VariantRow] = field(default_factory=list[VariantRow])
    orders: list[OrderRow] = field(default_factory=list[OrderRow])
    shipments: list[ShipmentRow] = field(default_factory=list[ShipmentRow])
    order_items: list[OrderItemRow] = field(default_factory=list[OrderItemRow])
    shipment_events: list[ShipmentEventRow] = field(default_factory=list[ShipmentEventRow])
    returns: list[ReturnRow] = field(default_factory=list[ReturnRow])
    return_items: list[ReturnItemRow] = field(default_factory=list[ReturnItemRow])
    refunds: list[RefundRow] = field(default_factory=list[RefundRow])

    def extend(self, other: "Dataset") -> None:
        for name, _ in TABLES:
            getattr(self, name).extend(getattr(other, name))


# Table name -> row type, in foreign-key order (parents first).
TABLES: tuple[tuple[str, type], ...] = (
    ("customers", CustomerRow),
    ("products", ProductRow),
    ("variants", VariantRow),
    ("orders", OrderRow),
    ("shipments", ShipmentRow),
    ("order_items", OrderItemRow),
    ("shipment_events", ShipmentEventRow),
    ("returns", ReturnRow),
    ("return_items", ReturnItemRow),
    ("refunds", RefundRow),
)
