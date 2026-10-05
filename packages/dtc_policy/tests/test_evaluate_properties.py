"""Property tests for evaluate() (docs/phases/phase-0.md, Property tests)."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from dtc_contracts import (
    ConditionClaim,
    DecisionCode,
    ItemStatus,
    OrderItemSnapshot,
    OrderSnapshot,
    PolicyInput,
    ReasonCode,
    RequestedItem,
    ShipmentStatus,
    VariantSnapshot,
)
from dtc_policy import PolicyConfig, evaluate
from dtc_policy.config import BypassReason, DefectConfig
from dtc_policy.window import days_since_delivery

NOW = datetime(2026, 9, 28, 19, 0, tzinfo=UTC)
CATEGORIES = ["apparel", "footwear", "accessories", "underwear", "final_sale", "mystery"]
STATUSES: list[ShipmentStatus] = ["delivered", "delivered", "delivered", "in_transit", "lost"]
CONDITIONS: list[ConditionClaim | None] = [None, "new_with_tags", "like_new", "worn", "defective"]
ACTIONS: list[Literal["return", "exchange"]] = ["return", "exchange"]
STRICT = {ReasonCode.NOT_DELIVERED, ReasonCode.QTY_EXCEEDED}
SOFT: list[BypassReason] = [
    ReasonCode.WINDOW_EXPIRED,
    ReasonCode.CATEGORY_NOT_RETURNABLE,
    ReasonCode.CATEGORY_NOT_EXCHANGEABLE,
]

variants = st.builds(
    VariantSnapshot,
    variant_id=st.integers(1, 6).map(lambda n: UUID(int=1000 + n)),
    product_id=st.integers(1, 3).map(lambda n: UUID(int=2000 + n)),
    category_code=st.sampled_from(CATEGORIES),
    list_price_cents=st.sampled_from([0, 1000, 2000, 4999, 5000]),
    stock=st.integers(0, 3),
)


@st.composite
def order_items(draw: st.DrawFn, item_id: UUID) -> OrderItemSnapshot:
    qty = draw(st.integers(1, 3))
    status = draw(st.sampled_from(STATUSES))
    delivered_at = None
    if status == "delivered":
        delivered_at = NOW - timedelta(minutes=draw(st.integers(0, 60 * 24 * 60)))
    return OrderItemSnapshot(
        order_item_id=item_id,
        variant=draw(variants),
        qty=qty,
        unit_price_cents=draw(st.integers(0, 6000)),
        committed_returned_qty=draw(st.integers(0, qty)),
        shipment_status=status,
        delivered_at=delivered_at,
    )


@st.composite
def policy_inputs(draw: st.DrawFn) -> PolicyInput:
    n = draw(st.integers(1, 3))
    bought = [draw(order_items(UUID(int=i + 1))) for i in range(n)]
    chosen = draw(
        st.lists(
            st.sampled_from(bought), min_size=1, max_size=n, unique_by=lambda o: o.order_item_id
        )
    )
    requested = [
        RequestedItem(
            order_item_id=o.order_item_id,
            qty=draw(st.integers(1, 3)),
            condition_claim=draw(st.sampled_from(CONDITIONS)),
            target_variant=draw(st.one_of(st.none(), st.just(o.variant), variants)),
        )
        for o in chosen
    ]
    return PolicyInput(
        action=draw(st.sampled_from(ACTIONS)),
        now=NOW,
        order=OrderSnapshot(
            order_id=UUID(int=999),
            items=bought,
            committed_refunds_cents=draw(st.integers(0, 6000)),
        ),
        items=requested,
    )


@st.composite
def configs(draw: st.DrawFn, base: PolicyConfig) -> PolicyConfig:
    bypass = draw(st.frozensets(st.sampled_from(SOFT)))
    return base.model_copy(update={"defect": DefectConfig(bypass_reasons=bypass)})


def _order_item(inp: PolicyInput, item_id: UUID) -> OrderItemSnapshot:
    return next(o for o in inp.order.items if o.order_item_id == item_id)


@given(data=st.data())
def test_nothing_eligible_past_window(cfg: PolicyConfig, data: st.DataObject) -> None:
    inp = data.draw(policy_inputs())
    for d in evaluate(inp, cfg).items:
        oi = _order_item(inp, d.order_item_id)
        if oi.delivered_at is not None:
            days = days_since_delivery(inp.now, oi.delivered_at, cfg.timezone)
            if days >= cfg.return_.window_days:
                assert d.status != ItemStatus.ELIGIBLE


@given(data=st.data())
def test_refund_equals_paid_for_refundable_items(cfg: PolicyConfig, data: st.DataObject) -> None:
    inp = data.draw(policy_inputs())
    d = evaluate(inp, cfg)
    if inp.action == "exchange" or d.code in (DecisionCode.DENY, DecisionCode.NEED_INFO):
        assert d.refund_cents is None
        return
    expected = sum(
        _order_item(inp, req.order_item_id).unit_price_cents * req.qty
        for req, item in zip(inp.items, d.items, strict=True)
        if item.status in (ItemStatus.ELIGIBLE, ItemStatus.NEEDS_REVIEW)
    )
    assert d.refund_cents == expected


@st.composite
def plain_returns(draw: st.DrawFn) -> PolicyInput:
    """Returns of apparel in the window, so many of them are APPROVE."""
    items = [
        OrderItemSnapshot(
            order_item_id=UUID(int=i + 1),
            variant=VariantSnapshot(
                variant_id=UUID(int=1000 + i),
                product_id=UUID(int=2000),
                category_code="apparel",
                list_price_cents=2000,
                stock=1,
            ),
            qty=1,
            unit_price_cents=draw(st.integers(0, 3000)),
            committed_returned_qty=0,
            shipment_status="delivered",
            delivered_at=NOW - timedelta(days=draw(st.integers(0, 40))),
        )
        for i in range(draw(st.integers(1, 3)))
    ]
    return PolicyInput(
        action="return",
        now=NOW,
        order=OrderSnapshot(
            order_id=UUID(int=999),
            items=items,
            committed_refunds_cents=draw(st.integers(1, 6000)),
        ),
        items=[
            RequestedItem(order_item_id=o.order_item_id, qty=1, condition_claim="like_new")
            for o in items
        ],
    )


@given(data=st.data())
def test_lower_committed_refunds_keeps_approve(cfg: PolicyConfig, data: st.DataObject) -> None:
    inp = data.draw(st.one_of(plain_returns(), policy_inputs()))
    x = inp.order.committed_refunds_cents
    if x == 0 or evaluate(inp, cfg).code != DecisionCode.APPROVE:
        return
    lower = inp.model_copy(
        update={"order": inp.order.model_copy(update={"committed_refunds_cents": x - 1})}
    )
    assert evaluate(lower, cfg).code == DecisionCode.APPROVE


@given(data=st.data())
def test_defective_ineligible_only_for_strict_or_unbypassable(
    cfg: PolicyConfig, data: st.DataObject
) -> None:
    inp = data.draw(policy_inputs())
    c = data.draw(configs(cfg))
    for req, d in zip(inp.items, evaluate(inp, c).items, strict=True):
        if req.condition_claim == "defective" and d.status == ItemStatus.INELIGIBLE:
            reasons = set(d.reasons)
            assert reasons & STRICT or (reasons & set(SOFT)) - c.defect.bypass_reasons


@given(data=st.data())
def test_defect_hint_implies_bypassable_soft_denial(cfg: PolicyConfig, data: st.DataObject) -> None:
    inp = data.draw(policy_inputs())
    c = data.draw(configs(cfg))
    for req, d in zip(inp.items, evaluate(inp, c).items, strict=True):
        if d.defect_claim_possible:
            assert d.status == ItemStatus.INELIGIBLE
            assert req.condition_claim is None
            assert not set(d.reasons) & STRICT


@given(data=st.data())
def test_unknown_category_alone_never_denies(cfg: PolicyConfig, data: st.DataObject) -> None:
    inp = data.draw(policy_inputs())
    for d in evaluate(inp, cfg).items:
        if ReasonCode.UNKNOWN_CATEGORY in d.reasons and d.status == ItemStatus.INELIGIBLE:
            assert set(d.reasons) - {ReasonCode.UNKNOWN_CATEGORY}


@given(data=st.data())
def test_deterministic(cfg: PolicyConfig, data: st.DataObject) -> None:
    inp = data.draw(policy_inputs())
    assert evaluate(inp, cfg) == evaluate(inp, cfg)
