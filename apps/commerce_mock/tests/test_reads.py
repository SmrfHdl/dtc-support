"""GET endpoints against the seeded default dataset."""

from collections import Counter
from uuid import UUID

import httpx
import pytest

from dtc_contracts.commerce import (
    CustomerOrdersOut,
    CustomerOut,
    ErrorOut,
    OrderOut,
    ShipmentEventsOut,
    VariantOut,
)
from dtc_datagen.generate import Generated
from dtc_datagen.snapshot import Index

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def order_id(gen: Generated, order_number: str) -> UUID:
    return next(o.id for o in gen.dataset.orders if o.order_number == order_number)


async def assert_not_found(response: httpx.Response) -> None:
    assert response.status_code == 404
    assert ErrorOut.model_validate(response.json()).code == "not_found"


async def test_lookup_customer(client: httpx.AsyncClient, seeded: Generated) -> None:
    c = seeded.dataset.customers[0]
    r = await client.get("/customers/lookup", params={"email": f"  {c.email.upper()} "})
    assert CustomerOut.model_validate(r.json()) == CustomerOut(id=c.id, email=c.email, name=c.name)
    await assert_not_found(await client.get("/customers/lookup", params={"email": "x@y.z"}))


async def test_customer_orders_newest_first(client: httpx.AsyncClient, seeded: Generated) -> None:
    counts = Counter(o.customer_id for o in seeded.dataset.orders)
    customer_id, n = counts.most_common(1)[0]
    assert n >= 3
    r = await client.get(f"/customers/{customer_id}/orders", params={"limit": 2})
    body = CustomerOrdersOut.model_validate(r.json())
    expected = sorted(
        (o for o in seeded.dataset.orders if o.customer_id == customer_id),
        key=lambda o: o.placed_at,
        reverse=True,
    )[:2]
    assert [o.id for o in body.orders] == [o.id for o in expected]


async def test_customer_orders_errors(client: httpx.AsyncClient) -> None:
    await assert_not_found(await client.get(f"/customers/{UUID(int=0)}/orders"))
    r = await client.get(f"/customers/{UUID(int=0)}/orders", params={"limit": 0})
    assert r.status_code == 422
    assert ErrorOut.model_validate(r.json()).code == "invalid_request"


@pytest.mark.parametrize("order_number", ["FX-08-01", "FX-11-01", "FX-23-01", "FX-38a-01"])
async def test_order_matches_generated_rows(
    client: httpx.AsyncClient, seeded: Generated, order_number: str
) -> None:
    body = OrderOut.model_validate((await client.get(f"/orders/{order_number}")).json())
    index = Index(seeded.dataset)
    oid = order_id(seeded, order_number)
    assert body.id == oid
    assert body.committed_refunds_cents == index.committed_refunds[oid]
    for item in body.items:
        assert item.committed_returned_qty == index.committed_qty[item.id]
        assert item.shipment_id == index.items[item.id].shipment_id
        assert item.variant.category_code == index.variant_snapshot(item.variant.id).category_code
    assert {i.id for i in body.items} == set(index.items_by_order[oid])


async def test_rejected_returns_do_not_count(client: httpx.AsyncClient, seeded: Generated) -> None:
    rejected = next(r for r in seeded.dataset.returns if r.status == "rejected")
    others = [r for r in seeded.dataset.returns if r.order_id == rejected.order_id]
    number = next(o.order_number for o in seeded.dataset.orders if o.id == rejected.order_id)
    body = OrderOut.model_validate((await client.get(f"/orders/{number}")).json())
    expected = sum(r.refund_cents or 0 for r in others if r.status != "rejected")
    assert body.committed_refunds_cents == expected


async def test_unknown_order(client: httpx.AsyncClient) -> None:
    await assert_not_found(await client.get("/orders/DTC-999999"))


async def test_shipment_events_oldest_first(client: httpx.AsyncClient, seeded: Generated) -> None:
    s = next(s for s in seeded.dataset.shipments if s.status == "delivered")
    body = ShipmentEventsOut.model_validate(
        (await client.get(f"/shipments/{s.tracking_number}/events")).json()
    )
    expected = sorted(
        (e for e in seeded.dataset.shipment_events if e.shipment_id == s.id), key=lambda e: e.ts
    )
    assert body.status == "delivered"
    assert [(e.ts, e.status) for e in body.events] == [(e.ts, e.status) for e in expected]
    await assert_not_found(await client.get("/shipments/NOPE/events"))


async def test_variant(client: httpx.AsyncClient, seeded: Generated) -> None:
    unknown_category = next(p for p in seeded.dataset.products if p.sku.startswith("FX-37-"))
    v = next(v for v in seeded.dataset.variants if v.product_id == unknown_category.id)
    body = VariantOut.model_validate((await client.get(f"/variants/{v.id}")).json())
    assert (body.sku, body.category_code, body.stock) == (
        unknown_category.sku,
        unknown_category.category_code,
        v.stock,
    )
    await assert_not_found(await client.get(f"/variants/{UUID(int=0)}"))
