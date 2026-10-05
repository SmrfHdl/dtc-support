"""Required policy test cases (docs/phases/phase-0.md, Required test cases)."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from dtc_contracts import (
    ConditionClaim,
    DecisionCode,
    ItemStatus,
    MissingField,
    OrderItemSnapshot,
    OrderSnapshot,
    PolicyDecision,
    PolicyInput,
    ReasonCode,
    RequestedItem,
    ShipmentStatus,
    VariantSnapshot,
)
from dtc_policy import PolicyConfig, evaluate
from dtc_policy.config import DefectConfig, ExchangeConfig

R = ReasonCode
D = DecisionCode
S = ItemStatus

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 28, 19, 0, tzinfo=UTC)  # 12:00 in LA (PDT)
ITEM_1 = UUID(int=1)
ITEM_2 = UUID(int=2)


def variant(
    vid: int, *, product: int, category: str, list_price: int = 2000, stock: int = 5
) -> VariantSnapshot:
    return VariantSnapshot(
        variant_id=UUID(int=1000 + vid),
        product_id=UUID(int=2000 + product),
        category_code=category,
        list_price_cents=list_price,
        stock=stock,
    )


SHIRT_M = variant(1, product=1, category="apparel")
SHIRT_L = variant(2, product=1, category="apparel")
PANTS = variant(3, product=2, category="apparel")
BAG_A = variant(4, product=3, category="accessories", list_price=3000)
BAG_B = variant(5, product=4, category="accessories", list_price=3000)
BAG_C = variant(6, product=5, category="accessories", list_price=3500)
SOCKS_WHITE = variant(7, product=6, category="underwear")
SOCKS_BLACK = variant(8, product=6, category="underwear")
CLEARANCE = variant(9, product=7, category="final_sale")
MYSTERY = variant(10, product=8, category="mystery")
MYSTERY_2 = variant(11, product=8, category="mystery")


def out_of_stock(v: VariantSnapshot) -> VariantSnapshot:
    return v.model_copy(update={"stock": 0})


def bought(
    v: VariantSnapshot = SHIRT_M,
    item_id: UUID = ITEM_1,
    *,
    qty: int = 1,
    unit_price: int | None = None,
    committed_qty: int = 0,
    status: ShipmentStatus = "delivered",
    days_ago: int = 10,
    delivered_at: datetime | None = None,
) -> OrderItemSnapshot:
    if delivered_at is None and status == "delivered":
        delivered_at = NOW - timedelta(days=days_ago)
    return OrderItemSnapshot(
        order_item_id=item_id,
        variant=v,
        qty=qty,
        unit_price_cents=v.list_price_cents if unit_price is None else unit_price,
        committed_returned_qty=committed_qty,
        shipment_status=status,
        delivered_at=delivered_at,
    )


def ask(
    item_id: UUID = ITEM_1,
    *,
    qty: int = 1,
    condition: ConditionClaim | None = "like_new",
    target: VariantSnapshot | None = None,
) -> RequestedItem:
    return RequestedItem(
        order_item_id=item_id, qty=qty, condition_claim=condition, target_variant=target
    )


def make(
    order_items: list[OrderItemSnapshot],
    requested: list[RequestedItem],
    *,
    action: Literal["return", "exchange"] = "return",
    committed_refunds: int = 0,
    now: datetime = NOW,
) -> PolicyInput:
    return PolicyInput(
        action=action,
        now=now,
        order=OrderSnapshot(
            order_id=UUID(int=999),
            items=order_items,
            committed_refunds_cents=committed_refunds,
        ),
        items=requested,
    )


def one(
    v: VariantSnapshot = SHIRT_M,
    *,
    action: Literal["return", "exchange"] = "return",
    condition: ConditionClaim | None = "like_new",
    target: VariantSnapshot | None = None,
    qty: int = 1,
    item_qty: int | None = None,
    committed_qty: int = 0,
    status: ShipmentStatus = "delivered",
    days_ago: int = 10,
    unit_price: int | None = None,
    committed_refunds: int = 0,
) -> PolicyInput:
    """A single-item request; defaults give an approvable return."""
    return make(
        [
            bought(
                v,
                qty=qty if item_qty is None else item_qty,
                unit_price=unit_price,
                committed_qty=committed_qty,
                status=status,
                days_ago=days_ago,
            )
        ],
        [ask(qty=qty, condition=condition, target=target)],
        action=action,
        committed_refunds=committed_refunds,
    )


def exchange(
    target: VariantSnapshot | None,
    v: VariantSnapshot = SHIRT_M,
    *,
    condition: ConditionClaim | None = "like_new",
    unit_price: int | None = None,
) -> PolicyInput:
    return one(v, action="exchange", target=target, condition=condition, unit_price=unit_price)


def check(
    d: PolicyDecision,
    code: DecisionCode,
    *,
    reasons: list[ReasonCode] | None = None,
    refund: int | None = None,
    items: list[tuple[ItemStatus, list[ReasonCode]]] | None = None,
) -> None:
    assert d.code == code
    assert d.reasons == (reasons or [])
    assert d.refund_cents == refund
    if items is not None:
        assert [(i.status, i.reasons) for i in d.items] == items


# --- Window (cases 1-4) ---------------------------------------------------------------


def test_case01_29_days_approve(cfg: PolicyConfig) -> None:
    check(evaluate(one(days_ago=29), cfg), D.APPROVE, refund=2000, items=[(S.ELIGIBLE, [])])


def test_case02_30_days_window_expired(cfg: PolicyConfig) -> None:
    d = evaluate(one(days_ago=30), cfg)
    check(d, D.DENY, items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED])])
    assert d.items[0].defect_claim_possible is False


def test_case03_days_counted_in_store_tz(cfg: PolicyConfig) -> None:
    # 23:30 LA on Aug 29 is Aug 30 in UTC: 30 days in LA (expired), only 29 by UTC dates.
    delivered_at = datetime(2026, 8, 29, 23, 30, tzinfo=LA).astimezone(UTC)
    inp = make([bought(delivered_at=delivered_at)], [ask()])
    check(evaluate(inp, cfg), D.DENY, items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED])])


def test_case04_dst_no_off_by_one(cfg: PolicyConfig) -> None:
    # Fall back on 2026-11-01: elapsed time is 29d 1h45m, but 30 calendar days in LA.
    delivered_at = datetime(2026, 10, 15, 23, 30, tzinfo=LA)
    now = datetime(2026, 11, 14, 0, 15, tzinfo=LA)
    inp = make([bought(delivered_at=delivered_at)], [ask()], now=now)
    check(evaluate(inp, cfg), D.DENY, items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED])])


# --- Delivery, refund limit, category, quantity, condition (cases 5-13) ----------------


def test_case05_all_in_transit_wismo(cfg: PolicyConfig) -> None:
    inp = make(
        [bought(status="in_transit"), bought(PANTS, ITEM_2, status="in_transit")],
        [ask(), ask(ITEM_2)],
    )
    check(
        evaluate(inp, cfg),
        D.DENY,
        reasons=[R.NOT_DELIVERED],
        items=[(S.INELIGIBLE, [R.NOT_DELIVERED])] * 2,
    )


@pytest.mark.parametrize("status", ["label_created", "out_for_delivery", "exception", "lost"])
def test_every_non_delivered_status_is_not_delivered(
    cfg: PolicyConfig, status: ShipmentStatus
) -> None:
    check(evaluate(one(status=status), cfg), D.DENY, reasons=[R.NOT_DELIVERED])


def test_case06_refund_4999_approve(cfg: PolicyConfig) -> None:
    check(evaluate(one(unit_price=4999), cfg), D.APPROVE, refund=4999)


def test_case07_refund_5000_needs_manager(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(unit_price=5000), cfg),
        D.NEED_MANAGER,
        reasons=[R.OVER_AUTO_LIMIT],
        refund=5000,
        items=[(S.ELIGIBLE, [])],
    )


def test_case08_pending_refunds_count_toward_limit(cfg: PolicyConfig) -> None:
    inp = one(unit_price=2500, committed_refunds=3000)
    check(evaluate(inp, cfg), D.NEED_MANAGER, reasons=[R.OVER_AUTO_LIMIT], refund=2500)


def test_case09_final_sale_not_returnable(cfg: PolicyConfig) -> None:
    d = evaluate(one(CLEARANCE), cfg)
    check(d, D.DENY, items=[(S.INELIGIBLE, [R.CATEGORY_NOT_RETURNABLE])])
    assert d.items[0].defect_claim_possible is False


def test_case10_partial_approve(cfg: PolicyConfig) -> None:
    inp = make([bought(), bought(CLEARANCE, ITEM_2)], [ask(), ask(ITEM_2)])
    check(
        evaluate(inp, cfg),
        D.APPROVE,
        refund=2000,
        items=[(S.ELIGIBLE, []), (S.INELIGIBLE, [R.CATEGORY_NOT_RETURNABLE])],
    )


def test_case11_qty_exceeds_remaining(cfg: PolicyConfig) -> None:
    inp = one(qty=2, item_qty=2, committed_qty=1)
    check(evaluate(inp, cfg), D.DENY, items=[(S.INELIGIBLE, [R.QTY_EXCEEDED])])


def test_remaining_qty_is_returnable(cfg: PolicyConfig) -> None:
    inp = one(qty=1, item_qty=2, committed_qty=1)
    check(evaluate(inp, cfg), D.APPROVE, refund=2000)


def test_refund_multiplies_unit_price_by_requested_qty(cfg: PolicyConfig) -> None:
    inp = one(qty=2, item_qty=3, unit_price=1500)
    check(evaluate(inp, cfg), D.APPROVE, refund=3000)


def test_not_delivered_and_qty_exceeded_both_recorded(cfg: PolicyConfig) -> None:
    inp = one(qty=2, item_qty=1, status="in_transit")
    check(
        evaluate(inp, cfg),
        D.DENY,
        reasons=[R.NOT_DELIVERED],
        items=[(S.INELIGIBLE, [R.NOT_DELIVERED, R.QTY_EXCEEDED])],
    )


def test_case12_worn_not_accepted(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(condition="worn"), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.CONDITION_NOT_ACCEPTED])],
    )


def test_new_with_tags_accepted(cfg: PolicyConfig) -> None:
    check(evaluate(one(condition="new_with_tags"), cfg), D.APPROVE, refund=2000)


def test_case13_missing_condition(cfg: PolicyConfig) -> None:
    d = evaluate(one(condition=None), cfg)
    check(d, D.NEED_INFO, items=[(S.NEEDS_INFO, [])])
    assert d.missing == [MissingField(order_item_id=ITEM_1, field="condition")]


def test_case14_defective_needs_manager(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        refund=2000,
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM])],
    )


# --- Exchange (cases 15-22) -------------------------------------------------------------


def test_case15_same_product_other_size(cfg: PolicyConfig) -> None:
    check(evaluate(exchange(SHIRT_L), cfg), D.APPROVE, refund=None, items=[(S.ELIGIBLE, [])])


def test_case16_target_out_of_stock(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(out_of_stock(SHIRT_L)), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.OUT_OF_STOCK])],
    )


def test_target_stock_equal_to_qty_is_enough(cfg: PolicyConfig) -> None:
    exact = SHIRT_L.model_copy(update={"stock": 2})
    inp = one(action="exchange", target=exact, qty=2, item_qty=2)
    check(evaluate(inp, cfg), D.APPROVE)


def test_target_stock_below_requested_qty(cfg: PolicyConfig) -> None:
    low = SHIRT_L.model_copy(update={"stock": 1})
    inp = one(action="exchange", target=low, qty=2, item_qty=2)
    check(evaluate(inp, cfg), D.DENY, items=[(S.INELIGIBLE, [R.OUT_OF_STOCK])])


@pytest.mark.parametrize("target", [BAG_A, PANTS], ids=["other-category", "other-product"])
def test_case17_same_product_scope(cfg: PolicyConfig, target: VariantSnapshot) -> None:
    check(
        evaluate(exchange(target), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.EXCHANGE_SCOPE])],
    )


def test_case18_same_category_price_mismatch(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(BAG_C, BAG_A), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.PRICE_MISMATCH])],
    )


def test_same_category_same_price_approve(cfg: PolicyConfig) -> None:
    check(evaluate(exchange(BAG_B, BAG_A), cfg), D.APPROVE)


def test_same_category_other_category_out_of_scope(cfg: PolicyConfig) -> None:
    other = variant(20, product=9, category="apparel", list_price=3000)
    check(
        evaluate(exchange(other, BAG_A), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.EXCHANGE_SCOPE])],
    )


def test_price_check_disabled_by_config(cfg: PolicyConfig) -> None:
    lenient = cfg.model_copy(update={"exchange": ExchangeConfig(require_same_price=False)})
    check(evaluate(exchange(BAG_C, BAG_A), lenient), D.APPROVE)


def test_case19_same_product_no_price_check(cfg: PolicyConfig) -> None:
    full_price_l = SHIRT_L.model_copy(update={"list_price_cents": 2500})
    check(evaluate(exchange(full_price_l, unit_price=1200), cfg), D.APPROVE)


def test_case20_same_variant_not_defective(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(SHIRT_M), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.EXCHANGE_SCOPE])],
    )


def test_case21_missing_target(cfg: PolicyConfig) -> None:
    d = evaluate(exchange(None), cfg)
    check(d, D.NEED_INFO, items=[(S.NEEDS_INFO, [])])
    assert d.missing == [MissingField(order_item_id=ITEM_1, field="target_variant")]


def test_missing_condition_and_target(cfg: PolicyConfig) -> None:
    d = evaluate(exchange(None, condition=None), cfg)
    assert d.code == D.NEED_INFO
    assert [m.field for m in d.missing] == ["condition", "target_variant"]


def test_case22_underwear_not_exchangeable(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(SOCKS_BLACK, SOCKS_WHITE), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.CATEGORY_NOT_EXCHANGEABLE])],
    )


def test_exchange_not_subject_to_refund_limit(cfg: PolicyConfig) -> None:
    pricey_m = SHIRT_M.model_copy(update={"list_price_cents": 9000})
    pricey_l = SHIRT_L.model_copy(update={"list_price_cents": 9000})
    inp = one(pricey_m, action="exchange", target=pricey_l, committed_refunds=4000)
    check(evaluate(inp, cfg), D.APPROVE, refund=None)


def test_returnable_false_does_not_block_exchange(cfg: PolicyConfig) -> None:
    no_returns = cfg.categories | {
        "apparel": cfg.categories["apparel"].model_copy(update={"returnable": False})
    }
    strict = cfg.model_copy(update={"categories": no_returns})
    check(evaluate(exchange(SHIRT_L), strict), D.APPROVE)


# --- Partial delivery (case 23) ----------------------------------------------------------


def test_case23_partial_delivery(cfg: PolicyConfig) -> None:
    inp = make([bought(), bought(PANTS, ITEM_2, status="in_transit")], [ask(), ask(ITEM_2)])
    check(
        evaluate(inp, cfg),
        D.APPROVE,
        refund=2000,
        items=[(S.ELIGIBLE, []), (S.INELIGIBLE, [R.NOT_DELIVERED])],
    )


def test_deny_mixed_reasons_has_no_decision_reason(cfg: PolicyConfig) -> None:
    inp = make(
        [bought(status="in_transit"), bought(CLEARANCE, ITEM_2)],
        [ask(), ask(ITEM_2)],
    )
    check(evaluate(inp, cfg), D.DENY, reasons=[])


# --- Defect hint (cases 24-27) -------------------------------------------------------------


def test_case24_no_condition_past_window_hint(cfg: PolicyConfig) -> None:
    d = evaluate(one(condition=None, days_ago=45), cfg)
    check(d, D.DENY, items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED])])
    assert d.missing == []
    assert d.items[0].defect_claim_possible is True


def test_case25_no_condition_final_sale_hint(cfg: PolicyConfig) -> None:
    d = evaluate(one(CLEARANCE, condition=None), cfg)
    check(d, D.DENY, items=[(S.INELIGIBLE, [R.CATEGORY_NOT_RETURNABLE])])
    assert d.items[0].defect_claim_possible is True


def test_case26_strict_failure_no_hint(cfg: PolicyConfig) -> None:
    inp = one(condition=None, days_ago=45, qty=2, item_qty=1)
    d = evaluate(inp, cfg)
    check(d, D.DENY, items=[(S.INELIGIBLE, [R.QTY_EXCEEDED])])
    assert d.items[0].defect_claim_possible is False


def test_case27_worn_past_window_no_hint(cfg: PolicyConfig) -> None:
    d = evaluate(one(condition="worn", days_ago=45), cfg)
    check(d, D.DENY, items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED])])
    assert d.items[0].defect_claim_possible is False


def test_no_hint_when_blocking_reason_not_bypassable(cfg: PolicyConfig) -> None:
    narrow = cfg.model_copy(update={"defect": DefectConfig(bypass_reasons=frozenset())})
    d = evaluate(one(condition=None, days_ago=45), narrow)
    assert d.items[0].defect_claim_possible is False


# --- Defective (cases 28-36) ---------------------------------------------------------------


def test_case28_defective_past_window(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(condition="defective", days_ago=45), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        refund=2000,
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.WINDOW_EXPIRED])],
    )


def test_case29_defective_final_sale(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(CLEARANCE, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        refund=2000,
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.CATEGORY_NOT_RETURNABLE])],
    )


def test_case30_defective_not_delivered(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(condition="defective", status="in_transit"), cfg),
        D.DENY,
        reasons=[R.NOT_DELIVERED],
        items=[(S.INELIGIBLE, [R.NOT_DELIVERED])],
    )


def test_case31_window_not_bypassable(cfg: PolicyConfig) -> None:
    narrow = cfg.model_copy(
        update={
            "defect": DefectConfig(
                bypass_reasons=frozenset({R.CATEGORY_NOT_RETURNABLE, R.CATEGORY_NOT_EXCHANGEABLE})
            )
        }
    )
    check(
        evaluate(one(condition="defective", days_ago=45), narrow),
        D.DENY,
        items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED])],
    )


def test_case32_defective_over_limit(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(condition="defective", unit_price=6000), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM, R.OVER_AUTO_LIMIT],
        refund=6000,
    )


def test_case33_defective_underwear_replacement(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(SOCKS_WHITE, SOCKS_WHITE, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        refund=None,
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM])],
    )


def test_defective_same_product_replacement_in_scope(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(SHIRT_M, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM])],
    )


def test_defective_underwear_other_variant(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(SOCKS_BLACK, SOCKS_WHITE, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.CATEGORY_NOT_EXCHANGEABLE])],
    )


def test_case34_defective_exchange_other_category(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(BAG_A, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.EXCHANGE_SCOPE])],
    )


def test_case35_defective_exchange_out_of_stock(cfg: PolicyConfig) -> None:
    check(
        evaluate(exchange(out_of_stock(SHIRT_L), condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.OUT_OF_STOCK])],
    )


def test_defective_replacement_out_of_stock(cfg: PolicyConfig) -> None:
    gone = out_of_stock(SHIRT_M)
    check(
        evaluate(exchange(gone, gone, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM],
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.OUT_OF_STOCK])],
    )


def test_case36_defective_exchange_missing_target(cfg: PolicyConfig) -> None:
    d = evaluate(exchange(None, condition="defective"), cfg)
    check(d, D.NEED_INFO)
    assert d.missing == [MissingField(order_item_id=ITEM_1, field="target_variant")]


# --- Unknown category, mixed requests (cases 37-38a) ------------------------------------


def test_case37_unknown_category(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(MYSTERY), cfg),
        D.NEED_MANAGER,
        reasons=[R.UNKNOWN_CATEGORY],
        refund=2000,
        items=[(S.NEEDS_REVIEW, [R.UNKNOWN_CATEGORY])],
    )


def test_unknown_category_exchange_skips_scope_and_price(cfg: PolicyConfig) -> None:
    other = MYSTERY_2.model_copy(update={"list_price_cents": 9999})
    check(
        evaluate(exchange(other, MYSTERY), cfg),
        D.NEED_MANAGER,
        reasons=[R.UNKNOWN_CATEGORY],
        refund=None,
        items=[(S.NEEDS_REVIEW, [R.UNKNOWN_CATEGORY])],
    )


def test_unknown_category_still_checks_window(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(MYSTERY, days_ago=45), cfg),
        D.DENY,
        items=[(S.INELIGIBLE, [R.WINDOW_EXPIRED, R.UNKNOWN_CATEGORY])],
    )


def test_unknown_category_defective(cfg: PolicyConfig) -> None:
    check(
        evaluate(one(MYSTERY, condition="defective"), cfg),
        D.NEED_MANAGER,
        reasons=[R.DEFECT_CLAIM, R.UNKNOWN_CATEGORY],
        refund=2000,
        items=[(S.NEEDS_REVIEW, [R.DEFECT_CLAIM, R.UNKNOWN_CATEGORY])],
    )


def test_case38_need_info_beats_need_manager(cfg: PolicyConfig) -> None:
    inp = make(
        [bought(), bought(PANTS, ITEM_2)],
        [ask(condition=None), ask(ITEM_2, condition="defective")],
    )
    d = evaluate(inp, cfg)
    check(d, D.NEED_INFO, items=[(S.NEEDS_INFO, []), (S.NEEDS_REVIEW, [R.DEFECT_CLAIM])])
    assert d.missing == [MissingField(order_item_id=ITEM_1, field="condition")]


def test_case38a_nothing_to_refund_never_hits_limit(cfg: PolicyConfig) -> None:
    check(evaluate(one(CLEARANCE, committed_refunds=6000), cfg), D.DENY)


def test_policy_version_is_recorded(cfg: PolicyConfig) -> None:
    assert evaluate(one(), cfg).policy_version == cfg.version
