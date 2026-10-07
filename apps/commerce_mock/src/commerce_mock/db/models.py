"""ORM models of the `commerce` schema (see docs/phases/phase-0.md, Schema `commerce`)."""

from datetime import datetime
from typing import get_args
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from commerce_mock.db.base import Base
from dtc_contracts import ConditionClaim, ShipmentStatus

SHIPMENT_STATUSES: tuple[str, ...] = get_args(ShipmentStatus)
CONDITION_CLAIMS: tuple[str, ...] = get_args(ConditionClaim)
ORDER_STATUSES = ("placed", "partially_shipped", "shipped", "delivered", "cancelled")
RETURN_TYPES = ("return", "exchange")
RETURN_STATUSES = (
    "approved",
    "pending_manager",
    "received",
    "inspection_passed",
    "refunded",
    "rejected",
)
REFUND_STATUSES = ("pending", "completed", "failed")


def one_of(column: str, values: tuple[str, ...]) -> str:
    """SQL for a CHECK that `column` holds one of `values` (NULL passes)."""
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_code: Mapped[str] = mapped_column(String(255), nullable=False)


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        CheckConstraint("list_price_cents >= 0", name="list_price_cents_non_negative"),
        CheckConstraint("stock >= 0", name="stock_non_negative"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    size: Mapped[str] = mapped_column(String(255), nullable=False)
    color: Mapped[str] = mapped_column(String(255), nullable=False)
    list_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(one_of("status", ORDER_STATUSES), name="status_valid"),
        CheckConstraint("total_cents >= 0", name="total_cents_non_negative"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(one_of("status", SHIPMENT_STATUSES), name="status_valid"),
        # Same rule as OrderItemSnapshot validation: delivered needs a delivery time.
        CheckConstraint(
            "status <> 'delivered' OR delivered_at IS NOT NULL", name="delivered_has_time"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True, nullable=False)
    carrier: Mapped[str] = mapped_column(String(255), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_cents_non_negative"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True, nullable=False)
    variant_id: Mapped[UUID] = mapped_column(ForeignKey("variants.id"), nullable=False)
    # NULL until the item is in a shipment (treated as label_created).
    shipment_id: Mapped[UUID | None] = mapped_column(ForeignKey("shipments.id"), index=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"
    __table_args__ = (CheckConstraint(one_of("status", SHIPMENT_STATUSES), name="status_valid"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("shipments.id"), index=True, nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Return(Base):
    __tablename__ = "returns"
    __table_args__ = (
        CheckConstraint(one_of("type", RETURN_TYPES), name="type_valid"),
        CheckConstraint(one_of("status", RETURN_STATUSES), name="status_valid"),
        CheckConstraint("refund_cents >= 0", name="refund_cents_non_negative"),
        # Returns carry the policy's refund amount; exchanges never do.
        CheckConstraint("(type = 'exchange') = (refund_cents IS NULL)", name="refund_by_type"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), index=True, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    refund_cents: Mapped[int | None] = mapped_column(Integer)
    reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    policy_version: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReturnItem(Base):
    __tablename__ = "return_items"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(one_of("condition_claim", CONDITION_CLAIMS), name="condition_valid"),
    )
    return_id: Mapped[UUID] = mapped_column(ForeignKey("returns.id"), primary_key=True)
    order_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_items.id"), primary_key=True, index=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    condition_claim: Mapped[str | None] = mapped_column(Text)
    exchange_variant_id: Mapped[UUID | None] = mapped_column(ForeignKey("variants.id"))


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(one_of("status", REFUND_STATUSES), name="status_valid"),
        CheckConstraint("amount_cents >= 0", name="amount_cents_non_negative"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    return_id: Mapped[UUID] = mapped_column(ForeignKey("returns.id"), index=True, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RequestLog(Base):
    __tablename__ = "request_log"
    __table_args__ = (CheckConstraint("latency_ms >= 0", name="latency_ms_non_negative"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
