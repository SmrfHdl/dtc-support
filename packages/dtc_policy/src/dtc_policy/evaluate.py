"""Deterministic return/exchange policy (see docs/phases/phase-0.md, Evaluation order)."""

from uuid import UUID

from dtc_contracts import (
    DecisionCode,
    ItemDecision,
    ItemStatus,
    MissingField,
    OrderItemSnapshot,
    PolicyDecision,
    PolicyInput,
    ReasonCode,
    RequestedItem,
)
from dtc_policy.config import CategoryConfig, PolicyConfig
from dtc_policy.window import days_since_delivery

BLOCKING = frozenset(
    {
        ReasonCode.WINDOW_EXPIRED,
        ReasonCode.CATEGORY_NOT_RETURNABLE,
        ReasonCode.CATEGORY_NOT_EXCHANGEABLE,
    }
)
# Decision-level reasons copied from NEEDS_REVIEW items, in output order.
REVIEW_REASONS = (ReasonCode.DEFECT_CLAIM, ReasonCode.UNKNOWN_CATEGORY)


def evaluate(inp: PolicyInput, cfg: PolicyConfig) -> PolicyDecision:
    order_items = {oi.order_item_id: oi for oi in inp.order.items}
    items: list[ItemDecision] = []
    missing: list[MissingField] = []
    for req in inp.items:
        decision, item_missing = _evaluate_item(inp, cfg, req, order_items[req.order_item_id])
        items.append(decision)
        missing.extend(item_missing)
    return _aggregate(inp, cfg, order_items, items, missing)


def _evaluate_item(
    inp: PolicyInput, cfg: PolicyConfig, req: RequestedItem, oi: OrderItemSnapshot
) -> tuple[ItemDecision, list[MissingField]]:
    def done(
        status: ItemStatus, reasons: list[ReasonCode], *, defect_claim_possible: bool = False
    ) -> ItemDecision:
        return ItemDecision(
            order_item_id=req.order_item_id,
            status=status,
            reasons=reasons,
            defect_claim_possible=defect_claim_possible,
        )

    # 1. Strict checks: both are collected, then stop.
    reasons: list[ReasonCode] = []
    if oi.shipment_status != "delivered":
        reasons.append(ReasonCode.NOT_DELIVERED)
    if req.qty > oi.qty - oi.committed_returned_qty:
        reasons.append(ReasonCode.QTY_EXCEEDED)
    if reasons:
        return done(ItemStatus.INELIGIBLE, reasons), []

    defective = req.condition_claim == "defective"
    target = req.target_variant
    # Defective exchange to the same variant is a replacement: always in scope.
    replacement = (
        inp.action == "exchange"
        and defective
        and target is not None
        and target.variant_id == oi.variant.variant_id
    )

    # 2. Soft checks (reasons only).
    assert oi.delivered_at is not None  # guaranteed by OrderItemSnapshot validation
    days = days_since_delivery(inp.now, oi.delivered_at, cfg.timezone)
    if days >= cfg.return_.window_days:
        reasons.append(ReasonCode.WINDOW_EXPIRED)
    category = cfg.categories.get(oi.variant.category_code)
    if category is None:
        reasons.append(ReasonCode.UNKNOWN_CATEGORY)
    elif inp.action == "return" and not category.returnable:
        reasons.append(ReasonCode.CATEGORY_NOT_RETURNABLE)
    elif inp.action == "exchange" and category.exchange == "none" and not replacement:
        reasons.append(ReasonCode.CATEGORY_NOT_EXCHANGEABLE)

    # 3. Soft failure gate.
    blocking = BLOCKING.intersection(reasons)
    if not defective and blocking:
        possible = req.condition_claim is None and blocking <= cfg.defect.bypass_reasons
        return done(ItemStatus.INELIGIBLE, reasons, defect_claim_possible=possible), []
    if defective and not blocking <= cfg.defect.bypass_reasons:
        return done(ItemStatus.INELIGIBLE, reasons), []

    # 4. Missing info.
    item_missing: list[MissingField] = []
    if req.condition_claim is None:
        item_missing.append(MissingField(order_item_id=req.order_item_id, field="condition"))
    if inp.action == "exchange" and target is None:
        item_missing.append(MissingField(order_item_id=req.order_item_id, field="target_variant"))
    if item_missing:
        return done(ItemStatus.NEEDS_INFO, reasons), item_missing

    exchange_reasons = (
        _exchange_reasons(cfg, req, oi, category, replacement=replacement)
        if inp.action == "exchange"
        else []
    )

    # 5. Defective: always NEEDS_REVIEW, every reason recorded for the reviewer.
    if defective:
        return done(
            ItemStatus.NEEDS_REVIEW, [ReasonCode.DEFECT_CLAIM, *reasons, *exchange_reasons]
        ), []

    # 6. Condition.
    if req.condition_claim not in cfg.return_.allowed_conditions:
        return done(ItemStatus.INELIGIBLE, [*reasons, ReasonCode.CONDITION_NOT_ACCEPTED]), []

    # 7. Exchange checks.
    if exchange_reasons:
        return done(ItemStatus.INELIGIBLE, [*reasons, *exchange_reasons]), []

    # 8. Unknown category goes to a human; otherwise eligible.
    if category is None:
        return done(ItemStatus.NEEDS_REVIEW, reasons), []
    return done(ItemStatus.ELIGIBLE, reasons), []


def _exchange_reasons(
    cfg: PolicyConfig,
    req: RequestedItem,
    oi: OrderItemSnapshot,
    category: CategoryConfig | None,
    *,
    replacement: bool,
) -> list[ReasonCode]:
    target = req.target_variant
    assert target is not None  # missing target is handled in step 4
    bought = oi.variant
    reasons: list[ReasonCode] = []

    # Scope and price are skipped for unknown categories and for replacements.
    rule = None if category is None or replacement else category.exchange
    if rule == "same_product" and (
        target.product_id != bought.product_id or target.variant_id == bought.variant_id
    ):
        reasons.append(ReasonCode.EXCHANGE_SCOPE)
    if rule == "same_category" and target.category_code != bought.category_code:
        reasons.append(ReasonCode.EXCHANGE_SCOPE)

    if target.stock < req.qty:
        reasons.append(ReasonCode.OUT_OF_STOCK)

    if (
        rule == "same_category"
        and cfg.exchange.require_same_price
        and target.list_price_cents != bought.list_price_cents
    ):
        reasons.append(ReasonCode.PRICE_MISMATCH)
    return reasons


def _aggregate(
    inp: PolicyInput,
    cfg: PolicyConfig,
    order_items: dict[UUID, OrderItemSnapshot],
    items: list[ItemDecision],
    missing: list[MissingField],
) -> PolicyDecision:
    def decide(
        code: DecisionCode, reasons: list[ReasonCode], refund_cents: int | None
    ) -> PolicyDecision:
        return PolicyDecision(
            code=code,
            reasons=reasons,
            items=items,
            refund_cents=refund_cents,
            missing=missing,
            policy_version=cfg.version,
        )

    if missing:
        return decide(DecisionCode.NEED_INFO, [], None)

    refundable = {
        d.order_item_id for d in items if d.status in (ItemStatus.ELIGIBLE, ItemStatus.NEEDS_REVIEW)
    }
    is_return = inp.action == "return"
    refund = sum(
        order_items[req.order_item_id].unit_price_cents * req.qty
        for req in inp.items
        if req.order_item_id in refundable
    )
    refund_cents = refund if is_return else None

    # Every NEEDS_REVIEW item carries DEFECT_CLAIM or UNKNOWN_CATEGORY.
    review = {r for d in items if d.status == ItemStatus.NEEDS_REVIEW for r in d.reasons}
    reasons = [r for r in REVIEW_REASONS if r in review]
    if (
        is_return
        and refundable
        and inp.order.committed_refunds_cents + refund >= cfg.refund.auto_limit_cents
    ):
        reasons.append(ReasonCode.OVER_AUTO_LIMIT)
    if reasons:
        return decide(DecisionCode.NEED_MANAGER, reasons, refund_cents)

    if any(d.status == ItemStatus.ELIGIBLE for d in items):
        return decide(DecisionCode.APPROVE, [], refund_cents)

    all_undelivered = all(ReasonCode.NOT_DELIVERED in d.reasons for d in items)
    return decide(DecisionCode.DENY, [ReasonCode.NOT_DELIVERED] if all_undelivered else [], None)
