"""POST endpoints: idempotency, concurrency re-checks, and the return -> refund flow."""

import asyncio
from collections.abc import Callable, Iterator
from itertools import count
from typing import Any
from uuid import UUID

import httpx
import pytest

from dtc_contracts.commerce import (
    CreateRefundIn,
    CreateReturnIn,
    ErrorOut,
    OrderOut,
    RefundOut,
    ReturnItemIn,
    ReturnOut,
)
from dtc_contracts.enums import ReasonCode
from dtc_datagen.generate import Generated
from dtc_datagen.load import load
from dtc_datagen.rows import OrderItemRow
from dtc_policy import PolicyConfig

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
_keys = count(1)


@pytest.fixture(autouse=True, scope="module")
def reseed_after(seeded: Generated, database_url: str) -> Iterator[None]:
    """These tests write; restore the seed so later modules see the original data."""
    yield
    asyncio.run(load(seeded.dataset, database_url))


def key() -> str:
    return f"test-{next(_keys)}"


def untouched_orders(gen: Generated, *, n_items: int) -> Iterator[list[OrderItemRow]]:
    """Bulk orders with exactly n_items items, all delivered, and no returns yet."""
    delivered = {s.id for s in gen.dataset.shipments if s.status == "delivered"}
    with_returns = {r.order_id for r in gen.dataset.returns}
    by_order: dict[UUID, list[OrderItemRow]] = {}
    for i in gen.dataset.order_items:
        by_order.setdefault(i.order_id, []).append(i)
    numbers = {o.id: o.order_number for o in gen.dataset.orders}
    for order_id, items in by_order.items():
        if (
            numbers[order_id].startswith("DTC-")
            and order_id not in with_returns
            and len(items) == n_items
            and all(i.shipment_id in delivered for i in items)
        ):
            yield items


_used: set[UUID] = set()
Pick = Callable[[list[OrderItemRow]], bool]


def fresh(gen: Generated, n_items: int = 1, pick: Pick | None = None) -> list[OrderItemRow]:
    """A new untouched order per call, so tests do not see each other's writes."""
    for items in untouched_orders(gen, n_items=n_items):
        if items[0].order_id not in _used and (pick is None or pick(items)):
            _used.add(items[0].order_id)
            return items
    raise LookupError("no untouched order left")


def return_body(rows: list[OrderItemRow], cfg: PolicyConfig, **overrides: Any) -> CreateReturnIn:
    body: dict[str, Any] = {
        "order_id": rows[0].order_id,
        "type": "return",
        "status": "approved",
        "items": [
            ReturnItemIn(order_item_id=i.id, qty=1, condition_claim="like_new") for i in rows
        ],
        "refund_cents": sum(i.unit_price_cents for i in rows),
        "reason_codes": [],
        "policy_version": cfg.version,
    }
    return CreateReturnIn.model_validate(body | overrides)


async def post(client: httpx.AsyncClient, path: str, body: Any, k: str | None) -> httpx.Response:
    headers = {"Content-Type": "application/json"} | ({} if k is None else {"Idempotency-Key": k})
    return await client.post(path, content=body.model_dump_json(), headers=headers)


def error(r: httpx.Response) -> tuple[int, str]:
    return r.status_code, ErrorOut.model_validate(r.json()).code


async def order_of(client: httpx.AsyncClient, gen: Generated, order_id: UUID) -> OrderOut:
    number = next(o.order_number for o in gen.dataset.orders if o.id == order_id)
    return OrderOut.model_validate((await client.get(f"/orders/{number}")).json())


def cheap(limit: int) -> Pick:
    def pick(items: list[OrderItemRow]) -> bool:
        return all(i.qty == 1 and i.unit_price_cents < limit // 2 for i in items)

    return pick


# --- Idempotency ---------------------------------------------------------------------


async def test_create_then_replay(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    body, k = return_body(items, cfg), key()
    first = await post(client, "/returns", body, k)
    again = await post(client, "/returns", body, k)
    assert (first.status_code, again.status_code) == (201, 200)
    assert ReturnOut.model_validate(first.json()) == ReturnOut.model_validate(again.json())

    order = await order_of(client, seeded, items[0].order_id)
    assert order.items[0].committed_returned_qty == 1
    assert order.committed_refunds_cents == body.refund_cents


async def test_same_key_other_body(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    a = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    b = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    k = key()
    assert (await post(client, "/returns", return_body(a, cfg), k)).status_code == 201
    r = await post(client, "/returns", return_body(b, cfg), k)
    assert error(r) == (422, "idempotency_mismatch")


async def test_missing_idempotency_key(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    assert error(await post(client, "/returns", return_body(items, cfg), None)) == (
        422,
        "invalid_request",
    )


# --- Concurrency (the spec's required tests) ------------------------------------------


async def test_concurrent_returns_for_the_last_unit(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    body = return_body(items, cfg)
    a, b = await asyncio.gather(
        post(client, "/returns", body, key()), post(client, "/returns", body, key())
    )
    codes = sorted([a.status_code, b.status_code])
    assert codes == [201, 409]
    loser = a if a.status_code == 409 else b
    assert error(loser) == (409, "qty_conflict")


async def test_concurrent_returns_crossing_the_limit(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    limit = cfg.refund.auto_limit_cents

    def pair_crosses(items: list[OrderItemRow]) -> bool:
        a, b = (i.unit_price_cents for i in items)
        return a < limit and b < limit and a + b >= limit

    first, second = fresh(seeded, n_items=2, pick=pair_crosses)
    a, b = await asyncio.gather(
        post(client, "/returns", return_body([first], cfg), key()),
        post(client, "/returns", return_body([second], cfg), key()),
    )
    assert sorted([a.status_code, b.status_code]) == [201, 409]
    loser = a if a.status_code == 409 else b
    assert error(loser) == (409, "over_limit")


async def test_concurrent_retries_of_one_request(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    body, k = return_body(items, cfg), key()
    a, b = await asyncio.gather(
        post(client, "/returns", body, k), post(client, "/returns", body, k)
    )
    assert sorted([a.status_code, b.status_code]) == [200, 201]
    assert a.json()["id"] == b.json()["id"]


# --- POST /returns checks -------------------------------------------------------------


async def test_return_rejections(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    other = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    cases: list[tuple[CreateReturnIn, tuple[int, str]]] = [
        (return_body(items, cfg, policy_version="old"), (409, "policy_changed")),
        (return_body(items, cfg, refund_cents=1), (422, "invalid_request")),
        (return_body(items, cfg, order_id=other[0].order_id), (422, "invalid_request")),
        (return_body(items, cfg, order_id=UUID(int=0)), (404, "not_found")),
        (
            return_body(
                items,
                cfg,
                type="exchange",
                refund_cents=None,
                items=[
                    ReturnItemIn(
                        order_item_id=items[0].id,
                        qty=1,
                        condition_claim="like_new",
                        exchange_variant_id=UUID(int=0),
                    )
                ],
            ),
            (422, "invalid_request"),
        ),
    ]
    for body, expected in cases:
        assert error(await post(client, "/returns", body, key())) == expected
    order = await order_of(client, seeded, items[0].order_id)
    assert order.committed_refunds_cents == 0  # nothing was written


async def test_qty_conflict_after_earlier_return(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    assert (await post(client, "/returns", return_body(items, cfg), key())).status_code == 201
    r = await post(client, "/returns", return_body(items, cfg), key())
    assert error(r) == (409, "qty_conflict")


async def test_pending_manager_skips_the_limit(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    limit = cfg.refund.auto_limit_cents

    def pricey(its: list[OrderItemRow]) -> bool:
        return its[0].qty == 1 and its[0].unit_price_cents >= limit

    items = fresh(seeded, pick=pricey)
    approved = return_body(items, cfg)
    assert error(await post(client, "/returns", approved, key())) == (409, "over_limit")
    manager = return_body(
        items, cfg, status="pending_manager", reason_codes=[ReasonCode.OVER_AUTO_LIMIT]
    )
    assert (await post(client, "/returns", manager, key())).status_code == 201


# --- Inspect and refund ---------------------------------------------------------------


async def create(client: httpx.AsyncClient, body: CreateReturnIn) -> ReturnOut:
    r = await post(client, "/returns", body, key())
    assert r.status_code == 201, r.text
    return ReturnOut.model_validate(r.json())


async def inspect(client: httpx.AsyncClient, return_id: UUID, passed: bool) -> httpx.Response:
    return await client.post(f"/returns/{return_id}/inspect", json={"passed": passed})


def refund(return_id: UUID, amount: int, approved_by: str | None = None) -> CreateRefundIn:
    return CreateRefundIn(return_id=return_id, amount_cents=amount, approved_by=approved_by)


async def test_return_inspect_refund_flow(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    ret = await create(client, return_body(items, cfg))
    assert ret.refund_cents is not None
    amount = ret.refund_cents

    early = await post(client, "/refunds", refund(ret.id, amount), key())
    assert error(early) == (409, "not_releasable")

    assert (await inspect(client, ret.id, True)).json()["status"] == "inspection_passed"
    assert error(await post(client, "/refunds", refund(ret.id, amount + 1), key())) == (
        422,
        "invalid_request",
    )

    k = key()
    first = await post(client, "/refunds", refund(ret.id, amount), k)
    replay = await post(client, "/refunds", refund(ret.id, amount), k)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert RefundOut.model_validate(first.json()).status == "completed"

    again = await post(client, "/refunds", refund(ret.id, amount), key())
    assert error(again) == (409, "already_refunded")
    assert error(await inspect(client, ret.id, True)) == (409, "invalid_transition")


async def test_manager_refund_needs_approver(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    body = return_body(items, cfg, status="pending_manager", reason_codes=[ReasonCode.DEFECT_CLAIM])
    ret = await create(client, body)
    assert ret.refund_cents is not None
    assert (await inspect(client, ret.id, True)).status_code == 200
    no_approver = await post(client, "/refunds", refund(ret.id, ret.refund_cents), key())
    assert error(no_approver) == (422, "invalid_request")
    approved = refund(ret.id, ret.refund_cents, approved_by="manager@example.com")
    assert (await post(client, "/refunds", approved, key())).status_code == 201


async def test_rejected_return_frees_qty_and_refund(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    items = fresh(seeded, pick=cheap(cfg.refund.auto_limit_cents))
    ret = await create(client, return_body(items, cfg))
    assert (await inspect(client, ret.id, False)).json()["status"] == "rejected"
    order = await order_of(client, seeded, items[0].order_id)
    assert (order.items[0].committed_returned_qty, order.committed_refunds_cents) == (0, 0)
    assert error(await post(client, "/refunds", refund(ret.id, ret.refund_cents or 0), key())) == (
        409,
        "not_releasable",
    )


async def test_unknown_return(client: httpx.AsyncClient) -> None:
    assert error(await inspect(client, UUID(int=0), True)) == (404, "not_found")
    assert error(await post(client, "/refunds", refund(UUID(int=0), 1), key())) == (
        404,
        "not_found",
    )
