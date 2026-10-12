"""POST endpoints: idempotent, and re-checked under row locks (phase-0.md, Commerce mock API).

The policy decided on a snapshot. Between that read and this write another request may
have returned the same unit or used up the refund limit, so every write re-checks the
numbers inside one transaction after `SELECT ... FOR UPDATE` on the rows it depends on.
"""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from commerce_mock.db.models import OrderItem, Refund, Return, ReturnItem, Variant
from commerce_mock.deps import Now, Policy, Session
from commerce_mock.errors import ApiError, not_found
from commerce_mock.routes.reads import live_returns
from dtc_contracts.commerce import (
    CreateRefundIn,
    CreateReturnIn,
    InspectReturnIn,
    RefundOut,
    ReturnItemIn,
    ReturnOut,
)
from dtc_contracts.enums import ReasonCode

router = APIRouter()

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]
INSPECTABLE = ("approved", "received", "pending_manager")


def request_hash(body: BaseModel) -> str:
    """SHA-256 of the parsed body, so formatting and key order do not matter."""
    return hashlib.sha256(body.model_dump_json().encode()).hexdigest()


async def idempotent[T](
    session: AsyncSession,
    response: Response,
    *,
    lock: Callable[[], Awaitable[None]],
    find: Callable[[], Awaitable[tuple[str | None, T] | None]],
    create: Callable[[], Awaitable[T]],
    body_hash: str,
) -> T:
    """Run `create` once per Idempotency-Key; replay the stored result afterwards.

    The key is looked up *after* `lock`, in the same transaction. A retry of a request
    that is still running waits on the lock, then finds the first one's row and replays
    it, instead of failing the business checks the first request already consumed.
    Requests that lock different rows can still race on one key: the unique index stops
    the second insert, and it replays the winner's row.
    """

    async def replay() -> T | None:
        found = await find()
        if found is None:
            return None
        stored_hash, result = found
        if stored_hash != body_hash:
            raise ApiError(422, "idempotency_mismatch", "Idempotency-Key reused with another body")
        response.status_code = 200
        return result

    try:
        async with session.begin():
            await lock()
            if (result := await replay()) is not None:
                return result
            return await create()
    except IntegrityError:
        async with session.begin():
            if (result := await replay()) is not None:
                return result
        raise


async def return_out(session: AsyncSession, r: Return) -> ReturnOut:
    items = await session.scalars(
        select(ReturnItem).where(ReturnItem.return_id == r.id).order_by(ReturnItem.order_item_id)
    )
    return ReturnOut(
        id=r.id,
        order_id=r.order_id,
        type=r.type,
        status=r.status,
        refund_cents=r.refund_cents,
        reason_codes=[ReasonCode(c) for c in r.reason_codes],
        policy_version=r.policy_version,
        created_at=r.created_at,
        items=[
            ReturnItemIn(
                order_item_id=i.order_item_id,
                qty=i.qty,
                condition_claim=i.condition_claim,
                exchange_variant_id=i.exchange_variant_id,
            )
            for i in items
        ],
    )


def refund_out(r: Refund) -> RefundOut:
    return RefundOut(
        id=r.id,
        return_id=r.return_id,
        amount_cents=r.amount_cents,
        status=r.status,
        approved_by=r.approved_by,
        created_at=r.created_at,
    )


async def committed_refunds(session: AsyncSession, order_id: UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Return.refund_cents), 0)).where(
            Return.id.in_(live_returns(order_id))
        )
    )
    return int(total or 0)


@router.post("/returns", status_code=201)
async def create_return(
    body: CreateReturnIn,
    key: IdempotencyKey,
    response: Response,
    session: Session,
    policy: Policy,
    now: Now,
) -> ReturnOut:
    body_hash = request_hash(body)
    items: dict[UUID, OrderItem] = {}

    async def lock() -> None:
        # Every item of the order: concurrent writes on one order now run one at a time.
        locked = await session.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == body.order_id)
            .order_by(OrderItem.id)
            .with_for_update()
        )
        items.update((i.id, i) for i in locked)

    async def find() -> tuple[str | None, ReturnOut] | None:
        r = await session.scalar(select(Return).where(Return.idempotency_key == key))
        return None if r is None else (r.request_hash, await return_out(session, r))

    async def create() -> ReturnOut:
        if body.policy_version != policy.version:
            raise ApiError(
                409, "policy_changed", f"policy is {policy.version}, got {body.policy_version}"
            )
        if not items:
            raise not_found("order")
        unknown = [str(i.order_item_id) for i in body.items if i.order_item_id not in items]
        if unknown:
            raise ApiError(422, "invalid_request", f"items not in this order: {unknown}")

        rows = await session.execute(
            select(ReturnItem.order_item_id, func.sum(ReturnItem.qty))
            .where(ReturnItem.return_id.in_(live_returns(body.order_id)))
            .group_by(ReturnItem.order_item_id)
        )
        committed = {item_id: int(qty) for item_id, qty in rows}
        for req in body.items:
            left = items[req.order_item_id].qty - committed.get(req.order_item_id, 0)
            if req.qty > left:
                raise ApiError(
                    409, "qty_conflict", f"item {req.order_item_id}: {left} left, {req.qty} asked"
                )

        targets = {i.exchange_variant_id for i in body.items if i.exchange_variant_id is not None}
        found = set(await session.scalars(select(Variant.id).where(Variant.id.in_(targets))))
        if missing := targets - found:
            raise ApiError(422, "invalid_request", f"unknown variants: {sorted(map(str, missing))}")

        if body.type == "return":
            expected = sum(items[i.order_item_id].unit_price_cents * i.qty for i in body.items)
            if body.refund_cents != expected:
                raise ApiError(
                    422, "invalid_request", f"refund_cents {body.refund_cents} != {expected}"
                )
            limit = policy.refund.auto_limit_cents
            earlier = await committed_refunds(session, body.order_id)
            if body.status == "approved" and earlier + expected >= limit:
                raise ApiError(
                    409, "over_limit", f"{earlier} committed + {expected} new >= {limit}"
                )

        r = Return(
            id=uuid4(),
            order_id=body.order_id,
            type=body.type,
            status=body.status,
            refund_cents=body.refund_cents,
            reason_codes=[c.value for c in body.reason_codes],
            policy_version=body.policy_version,
            idempotency_key=key,
            request_hash=body_hash,
            created_at=now,
        )
        session.add(r)
        await session.flush()  # the return row must exist before its items (FK)
        session.add_all(
            ReturnItem(
                return_id=r.id,
                order_item_id=i.order_item_id,
                qty=i.qty,
                condition_claim=i.condition_claim,
                exchange_variant_id=i.exchange_variant_id,
            )
            for i in body.items
        )
        await session.flush()
        return await return_out(session, r)

    return await idempotent(
        session, response, lock=lock, find=find, create=create, body_hash=body_hash
    )


@router.post("/returns/{return_id}/inspect")
async def inspect_return(return_id: UUID, body: InspectReturnIn, session: Session) -> ReturnOut:
    async with session.begin():
        r = await session.scalar(select(Return).where(Return.id == return_id).with_for_update())
        if r is None:
            raise not_found("return")
        if r.status not in INSPECTABLE:
            raise ApiError(409, "invalid_transition", f"cannot inspect a {r.status} return")
        r.status = "inspection_passed" if body.passed else "rejected"
        await session.flush()
        return await return_out(session, r)


@router.post("/refunds", status_code=201)
async def create_refund(
    body: CreateRefundIn, key: IdempotencyKey, response: Response, session: Session, now: Now
) -> RefundOut:
    body_hash = request_hash(body)
    locked: list[Return] = []

    async def lock() -> None:
        r = await session.scalar(
            select(Return).where(Return.id == body.return_id).with_for_update()
        )
        locked.extend([] if r is None else [r])

    async def find() -> tuple[str | None, RefundOut] | None:
        f = await session.scalar(select(Refund).where(Refund.idempotency_key == key))
        return None if f is None else (f.request_hash, refund_out(f))

    async def create() -> RefundOut:
        if not locked:
            raise not_found("return")
        r = locked[0]
        if r.status == "refunded":
            raise ApiError(409, "already_refunded", f"return {r.id} is already refunded")
        if r.type != "return" or r.status != "inspection_passed" or r.refund_cents is None:
            raise ApiError(
                409, "not_releasable", f"{r.type} in status {r.status} cannot be refunded"
            )
        if body.amount_cents != r.refund_cents:
            raise ApiError(
                422, "invalid_request", f"amount_cents {body.amount_cents} != {r.refund_cents}"
            )
        if r.reason_codes and not body.approved_by:
            raise ApiError(422, "invalid_request", "approved_by is required: a manager decides")

        f = Refund(
            id=uuid4(),
            return_id=r.id,
            amount_cents=body.amount_cents,
            status="completed",
            approved_by=body.approved_by,
            idempotency_key=key,
            request_hash=body_hash,
            created_at=now,
        )
        session.add(f)
        r.status = "refunded"
        await session.flush()
        return refund_out(f)

    return await idempotent(
        session, response, lock=lock, find=find, create=create, body_hash=body_hash
    )
