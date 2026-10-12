"""GET endpoints (docs/phases/phase-0.md, Commerce mock API)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import Select, func, select

from commerce_mock.db.models import (
    Customer,
    Order,
    OrderItem,
    Product,
    Return,
    ReturnItem,
    Shipment,
    ShipmentEvent,
    Variant,
)
from commerce_mock.deps import Session
from commerce_mock.errors import not_found
from dtc_contracts.commerce import (
    CustomerOrdersOut,
    CustomerOut,
    OrderItemOut,
    OrderOut,
    OrderSummaryOut,
    ShipmentEventOut,
    ShipmentEventsOut,
    ShipmentOut,
    VariantOut,
)

router = APIRouter()


def variant_out(v: Variant, p: Product) -> VariantOut:
    return VariantOut(
        id=v.id,
        product_id=p.id,
        sku=p.sku,
        product_name=p.name,
        category_code=p.category_code,
        size=v.size,
        color=v.color,
        list_price_cents=v.list_price_cents,
        stock=v.stock,
    )


def shipment_out(s: Shipment) -> ShipmentOut:
    return ShipmentOut(
        id=s.id,
        carrier=s.carrier,
        tracking_number=s.tracking_number,
        status=s.status,
        shipped_at=s.shipped_at,
        delivered_at=s.delivered_at,
        eta=s.eta,
    )


def live_returns(order_id: UUID) -> Select[UUID]:
    """Ids of the order's returns that still count (everything but rejected)."""
    return select(Return.id).where(Return.order_id == order_id, Return.status != "rejected")


@router.get("/customers/lookup")
async def lookup_customer(email: str, session: Session) -> CustomerOut:
    customer = await session.scalar(select(Customer).where(Customer.email == email.strip().lower()))
    if customer is None:
        raise not_found("customer")
    return CustomerOut(id=customer.id, email=customer.email, name=customer.name)


@router.get("/customers/{customer_id}/orders")
async def customer_orders(
    customer_id: UUID,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> CustomerOrdersOut:
    if await session.get(Customer, customer_id) is None:
        raise not_found("customer")
    orders = await session.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.placed_at.desc(), Order.id)
        .limit(limit)
    )
    return CustomerOrdersOut(
        customer_id=customer_id,
        orders=[
            OrderSummaryOut(
                id=o.id,
                order_number=o.order_number,
                status=o.status,
                placed_at=o.placed_at,
                total_cents=o.total_cents,
            )
            for o in orders
        ],
    )


@router.get("/orders/{order_number}")
async def get_order(order_number: str, session: Session) -> OrderOut:
    order = await session.scalar(select(Order).where(Order.order_number == order_number))
    if order is None:
        raise not_found("order")

    committed = (
        select(ReturnItem.order_item_id, func.sum(ReturnItem.qty).label("qty"))
        .where(ReturnItem.return_id.in_(live_returns(order.id)))
        .group_by(ReturnItem.order_item_id)
        .subquery()
    )
    item_rows = await session.execute(
        select(OrderItem, Variant, Product, func.coalesce(committed.c.qty, 0))
        .join(Variant, Variant.id == OrderItem.variant_id)
        .join(Product, Product.id == Variant.product_id)
        .outerjoin(committed, committed.c.order_item_id == OrderItem.id)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    )
    shipments = await session.scalars(
        select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.id)
    )
    committed_refunds = await session.scalar(
        select(func.coalesce(func.sum(Return.refund_cents), 0)).where(
            Return.id.in_(live_returns(order.id))
        )
    )
    return OrderOut(
        id=order.id,
        order_number=order.order_number,
        customer_id=order.customer_id,
        status=order.status,
        placed_at=order.placed_at,
        total_cents=order.total_cents,
        items=[
            OrderItemOut(
                id=item.id,
                variant=variant_out(variant, product),
                qty=item.qty,
                unit_price_cents=item.unit_price_cents,
                shipment_id=item.shipment_id,
                committed_returned_qty=int(qty),
            )
            for item, variant, product, qty in item_rows
        ],
        shipments=[shipment_out(s) for s in shipments],
        committed_refunds_cents=int(committed_refunds or 0),
    )


@router.get("/shipments/{tracking_number}/events")
async def shipment_events(tracking_number: str, session: Session) -> ShipmentEventsOut:
    shipment = await session.scalar(
        select(Shipment).where(Shipment.tracking_number == tracking_number)
    )
    if shipment is None:
        raise not_found("shipment")
    events = await session.scalars(
        select(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == shipment.id)
        .order_by(ShipmentEvent.ts, ShipmentEvent.id)
    )
    return ShipmentEventsOut(
        tracking_number=shipment.tracking_number,
        carrier=shipment.carrier,
        status=shipment.status,
        eta=shipment.eta,
        events=[
            ShipmentEventOut(
                ts=e.ts,
                status=e.status,
                location=e.location,
                description=e.description,
            )
            for e in events
        ],
    )


@router.get("/variants/{variant_id}")
async def get_variant(variant_id: UUID, session: Session) -> VariantOut:
    row = (
        await session.execute(
            select(Variant, Product)
            .join(Product, Product.id == Variant.product_id)
            .where(Variant.id == variant_id)
        )
    ).one_or_none()
    if row is None:
        raise not_found("variant")
    return variant_out(*row)
