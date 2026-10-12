"""The commerce client builds, from the mock API, the same PolicyInput datagen builds from
its rows, for every fixture; and evaluate() then gives the fixture's expected decision."""

from datetime import datetime

import httpx
import pytest

from dtc_commerce import HttpCommerceClient, ItemRequest, build_policy_input
from dtc_contracts import PolicyInput
from dtc_datagen.generate import Generated
from dtc_datagen.snapshot import Index, RequestSpec
from dtc_policy import PolicyConfig, evaluate

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def normalized(inp: PolicyInput) -> PolicyInput:
    """Order items by id: the API sorts them, datagen keeps insertion order."""
    order = inp.order.model_copy(
        update={"items": sorted(inp.order.items, key=lambda i: i.order_item_id)}
    )
    return inp.model_copy(update={"order": order})


async def test_every_fixture_round_trips(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig, now: datetime
) -> None:
    commerce = HttpCommerceClient(client)
    index = Index(seeded.dataset)
    assert len(seeded.fixtures) == 740
    for f in seeded.fixtures:
        order = await commerce.get_order(f.order_number)
        assert order is not None, f.order_number
        requests = [
            ItemRequest(r.order_item_id, r.qty, r.condition_claim, r.target_variant_id)
            for r in f.requests
        ]
        via_api = await build_policy_input(commerce, order, f.action, requests, now)
        via_rows = index.policy_input(f.order_id, f.action, list(f.requests), now)
        assert normalized(via_api) == normalized(via_rows), f.order_number

        d = evaluate(via_api, cfg)
        assert (d.code, tuple(d.reasons), d.refund_cents) == (f.code, f.reasons, f.refund_cents)


async def test_bulk_orders_round_trip(
    client: httpx.AsyncClient, seeded: Generated, now: datetime
) -> None:
    """A sample of random orders (partial deliveries, earlier returns, no-shipment items)."""
    commerce = HttpCommerceClient(client)
    index = Index(seeded.dataset)
    for o in seeded.dataset.orders[:300]:
        order = await commerce.get_order(o.order_number)
        assert order is not None
        first = index.items_by_order[o.id][0]
        via_api = await build_policy_input(
            commerce, order, "return", [ItemRequest(first, 1, "like_new")], now
        )
        via_rows = index.policy_input(o.id, "return", [RequestSpec(first, 1, "like_new")], now)
        assert normalized(via_api) == normalized(via_rows), o.order_number
