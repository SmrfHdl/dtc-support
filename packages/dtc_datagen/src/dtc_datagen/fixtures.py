"""Fixture orders for the required policy cases (docs/phases/phase-0.md, Required test cases).

Each fixture has its own customer, products, and variants, plus the request and the
expected decision. `build_fixtures` runs every fixture through `evaluate()` and fails on
any mismatch, so a fixture can never disagree with the policy it is meant to exercise.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID

from dtc_contracts import ConditionClaim, DecisionCode, ReasonCode, ShipmentStatus
from dtc_datagen.builder import NON_REJECTED_RETURN_STATUSES, Builder, Line, ShipmentPlan
from dtc_datagen.catalog import COLORS, SPEC_BY_CODE, add_product, price
from dtc_datagen.rng import Rng
from dtc_datagen.rows import Dataset, OrderItemRow, VariantRow
from dtc_datagen.snapshot import Action, Index, RequestSpec
from dtc_policy import PolicyConfig, evaluate

FIXTURES_PER_CASE = 20
RETURNABLE = ("apparel", "footwear", "accessories")
SAME_PRODUCT = ("apparel", "footwear")  # categories with `exchange: same_product`
UNKNOWN_CATEGORIES = ("home_goods", "gift_set", "beauty")  # must not be in the policy YAML
NOT_DELIVERED: tuple[ShipmentStatus | None, ...] = (
    None,  # no shipment yet
    "label_created",
    "in_transit",
    "out_for_delivery",
    "exception",
    "lost",
)
MAX_DELIVERED_DAYS = 60

D = DecisionCode
R = ReasonCode


@dataclass(frozen=True)
class Fixture:
    case: str
    order_number: str
    order_id: UUID
    action: Action
    requests: tuple[RequestSpec, ...]
    code: DecisionCode
    reasons: tuple[ReasonCode, ...]
    refund_cents: int | None


class Kit:
    """Helpers for building one fixture order."""

    def __init__(self, b: Builder, cfg: PolicyConfig, case: str, index: int) -> None:
        self.b = b
        self.rng = b.rng
        self.cfg = cfg
        self.W = cfg.return_.window_days
        self.L = cfg.refund.auto_limit_cents
        # Sorted: iterating a frozenset of str depends on PYTHONHASHSEED.
        self.accepted: tuple[ConditionClaim, ...] = tuple(sorted(cfg.return_.allowed_conditions))
        self.case = case
        self.order_number = f"FX-{case}-{index:02d}"
        self.customer = b.customer(
            email=f"fx-{case}-{index:02d}@example.com", name=f"Fixture {case}-{index:02d}"
        )
        self._products = 0
        self.lines: tuple[Line, ...] = ()
        self.items: list[OrderItemRow] = []

    # --- catalog -------------------------------------------------------------------

    def product(
        self, category: str, *, n: int = 2, list_price: int | None = None, stock: int | None = None
    ) -> list[VariantRow]:
        """A product with `n` variants (sizes, or colors for one-size categories)."""
        self._products += 1
        spec = SPEC_BY_CODE.get(category)
        sizes = spec.sizes if spec is not None else ("One Size",)
        if len(sizes) >= n:
            sizes, colors = self.rng.sample(sizes, n), [self.rng.choice(COLORS)]
        else:
            sizes, colors = [sizes[0]], self.rng.sample(COLORS, n)
        return add_product(
            self.b.ds,
            self.rng,
            sku=f"{self.order_number}-P{self._products}",
            name=f"Fixture {category} {self._products}",
            category_code=category,
            sizes=sizes,
            colors=colors,
            list_price_cents=list_price if list_price is not None else self.price(1000, self.L - 1),
            stock=stock if stock is not None else self.rng.randint(1, 30),
        )

    def sibling(self, v: VariantRow, *, list_price: int, stock: int) -> VariantRow:
        """Another variant of v's product with its own list price."""
        row = VariantRow(
            id=self.rng.uuid(),
            product_id=v.product_id,
            size=v.size,
            color=self.rng.choice([c for c in COLORS if c != v.color]),
            list_price_cents=list_price,
            stock=stock,
        )
        self.b.ds.variants.append(row)
        return row

    def price(self, lo: int, hi: int) -> int:
        return price(self.rng, lo, hi)

    def choice[T](self, items: tuple[T, ...]) -> T:
        return self.rng.choice(items)

    # --- shipments -----------------------------------------------------------------

    def delivered(self, days: int, *, late_evening: bool = False) -> ShipmentPlan:
        """Delivered exactly `days` calendar days ago in the store timezone."""
        tz, now = self.cfg.timezone, self.b.now
        day = now.astimezone(tz).date() - timedelta(days=days)
        start, end = (time(23, 0), time(23, 59)) if late_evening else (time(8, 0), time(20, 59))
        lo = datetime.combine(day, start, tzinfo=tz)
        hi = min(datetime.combine(day, end, tzinfo=tz), now)
        if lo > hi:  # today, before the delivery hours
            lo = datetime.combine(day, time(0, 0), tzinfo=tz)
        return ShipmentPlan("delivered", self.rng.between(lo, hi))

    def in_window(self) -> ShipmentPlan:
        return self.delivered(self.rng.randint(0, self.W - 1))

    def expired(self) -> ShipmentPlan:
        return self.delivered(self.rng.randint(self.W, MAX_DELIVERED_DAYS))

    def not_delivered(self) -> ShipmentPlan | None:
        status: ShipmentStatus | None = self.choice(NOT_DELIVERED)
        return None if status is None else ShipmentPlan(status)

    # --- order, earlier returns, request -------------------------------------------

    def order(self, *lines: Line) -> list[OrderItemRow]:
        self.lines = lines
        self.items = self.b.order(self.customer, self.order_number, list(lines))
        return self.items

    def prior_return(self, item: OrderItemRow, *, qty: int = 1, status: str | None = None) -> None:
        line = self.lines[self.items.index(item)]
        assert line.shipment is not None
        assert line.shipment.delivered_at is not None
        self.b.prior_return(
            item,
            line.shipment.delivered_at,
            qty=qty,
            status=status or self.choice(NON_REJECTED_RETURN_STATUSES),
            policy_version=self.cfg.version,
        )

    def done(
        self,
        action: Action,
        requests: list[RequestSpec],
        code: DecisionCode,
        reasons: tuple[ReasonCode, ...] = (),
        refund_cents: int | None = None,
    ) -> Fixture:
        return Fixture(
            case=self.case,
            order_number=self.order_number,
            order_id=self.items[0].order_id,
            action=action,
            requests=tuple(requests),
            code=code,
            reasons=reasons,
            refund_cents=refund_cents,
        )


def line(
    v: VariantRow, plan: ShipmentPlan | None, *, qty: int = 1, paid: int | None = None
) -> Line:
    return Line(v, qty, v.list_price_cents if paid is None else paid, plan)


def ask(
    item: OrderItemRow,
    *,
    qty: int = 1,
    condition: ConditionClaim | None = "like_new",
    target: VariantRow | None = None,
) -> RequestSpec:
    return RequestSpec(item.id, qty, condition, None if target is None else target.id)


def paid(item: OrderItemRow, qty: int = 1) -> int:
    return item.unit_price_cents * qty


# --- Cases ----------------------------------------------------------------------------
# Comments give the case from the spec table. Cases 4, 31, and 39 have no fixtures.


def c01(k: Kit) -> Fixture:  # delivered W-1 days ago -> APPROVE
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.delivered(k.W - 1)))
    req = ask(it, condition=k.choice(k.accepted))
    return k.done("return", [req], D.APPROVE, refund_cents=paid(it))


def c02(k: Kit) -> Fixture:  # delivered W days ago -> DENY
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.delivered(k.W)))
    return k.done("return", [ask(it, condition=k.choice(k.accepted))], D.DENY)


def c03(k: Kit) -> Fixture:  # 23:xx store time (next day in UTC): days counted in store tz
    expired = k.rng.chance(0.5)
    plan = k.delivered(k.W if expired else k.W - 1, late_evening=True)
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], plan))
    if expired:
        return k.done("return", [ask(it)], D.DENY)
    return k.done("return", [ask(it)], D.APPROVE, refund_cents=paid(it))


def c05(k: Kit) -> Fixture:  # nothing delivered -> DENY / NOT_DELIVERED (WISMO)
    n = k.rng.randint(1, 3)
    items = k.order(
        *(line(k.product(k.choice(RETURNABLE))[0], k.not_delivered()) for _ in range(n))
    )
    conditions: tuple[ConditionClaim | None, ...] = (None, "like_new", "worn", "defective")
    reqs = [ask(it, condition=k.choice(conditions)) for it in items]
    return k.done("return", reqs, D.DENY, (R.NOT_DELIVERED,))


def c06(k: Kit) -> Fixture:  # refund L-1 -> APPROVE
    plan = k.in_window()
    if k.rng.chance(0.5):
        items = k.order(line(k.product("apparel", list_price=k.L - 1)[0], plan))
    else:
        a = k.price(1000, k.L - 2000)
        items = k.order(
            line(k.product("apparel", list_price=a)[0], plan),
            line(k.product("accessories", list_price=k.L - 1 - a)[0], plan),
        )
    return k.done("return", [ask(it) for it in items], D.APPROVE, refund_cents=k.L - 1)


def c07(k: Kit) -> Fixture:  # refund >= L -> NEED_MANAGER / OVER_AUTO_LIMIT
    p = k.L if k.rng.chance(0.5) else k.price(k.L, 2 * k.L)
    [it] = k.order(line(k.product("footwear", list_price=p)[0], k.in_window()))
    return k.done("return", [ask(it)], D.NEED_MANAGER, (R.OVER_AUTO_LIMIT,), p)


def c08(k: Kit) -> Fixture:  # earlier pending refund + new request crosses L
    plan = k.in_window()
    earlier = k.price(k.L // 2, k.L - 1000)
    new = k.price(k.L - earlier, k.L - 1)
    a, b = k.order(
        line(k.product("apparel", list_price=earlier)[0], plan),
        line(k.product("apparel", list_price=new)[0], plan),
    )
    k.prior_return(a)
    return k.done("return", [ask(b)], D.NEED_MANAGER, (R.OVER_AUTO_LIMIT,), new)


def c09(k: Kit) -> Fixture:  # final_sale -> DENY / CATEGORY_NOT_RETURNABLE
    [it] = k.order(line(k.product("final_sale")[0], k.in_window()))
    return k.done("return", [ask(it, condition=k.choice(k.accepted))], D.DENY)


def c10(k: Kit) -> Fixture:  # eligible + final_sale -> partial APPROVE
    plan = k.in_window()
    a, b = k.order(
        line(k.product(k.choice(RETURNABLE))[0], plan),
        line(k.product("final_sale")[0], plan),
    )
    return k.done("return", [ask(a), ask(b)], D.APPROVE, refund_cents=paid(a))


def c11(k: Kit) -> Fixture:  # qty > qty - committed_returned_qty -> DENY / QTY_EXCEEDED
    q = k.rng.randint(2, 3)
    returned = k.rng.randint(1, q - 1)
    [it] = k.order(
        line(k.product("apparel", list_price=k.price(1000, 1500))[0], k.in_window(), qty=q)
    )
    k.prior_return(it, qty=returned)
    return k.done("return", [ask(it, qty=k.rng.randint(q - returned + 1, q))], D.DENY)


def c12(k: Kit) -> Fixture:  # worn -> DENY / CONDITION_NOT_ACCEPTED
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.in_window()))
    return k.done("return", [ask(it, condition="worn")], D.DENY)


def c13(k: Kit) -> Fixture:  # condition missing -> NEED_INFO
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.in_window()))
    return k.done("return", [ask(it, condition=None)], D.NEED_INFO)


def c14(k: Kit) -> Fixture:  # defective -> NEED_MANAGER / DEFECT_CLAIM
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.in_window()))
    req = ask(it, condition="defective")
    return k.done("return", [req], D.NEED_MANAGER, (R.DEFECT_CLAIM,), paid(it))


def c15(k: Kit) -> Fixture:  # exchange to another size, in stock -> APPROVE
    v, other = k.product(k.choice(SAME_PRODUCT))
    [it] = k.order(line(v, k.in_window()))
    req = ask(it, condition=k.choice(k.accepted), target=other)
    return k.done("exchange", [req], D.APPROVE)


def c16(k: Kit) -> Fixture:  # exchange target out of stock -> DENY
    v, other = k.product(k.choice(SAME_PRODUCT), stock=0)
    [it] = k.order(line(v, k.in_window()))
    return k.done("exchange", [ask(it, target=other)], D.DENY)


def c17(k: Kit) -> Fixture:  # same_product exchange to another product -> DENY / EXCHANGE_SCOPE
    category = k.choice(SAME_PRODUCT)
    v = k.product(category)[0]
    target = k.product(k.choice(("apparel", "footwear", "accessories", "underwear")))[0]
    [it] = k.order(line(v, k.in_window()))
    return k.done("exchange", [ask(it, target=target)], D.DENY)


def c18(k: Kit) -> Fixture:  # same_category exchange, different list price -> DENY
    p = k.price(1500, 4000)
    v = k.product("accessories", list_price=p)[0]
    target = k.product("accessories", list_price=p + 100 * k.rng.randint(1, 10))[0]
    [it] = k.order(line(v, k.in_window()))
    return k.done("exchange", [ask(it, target=target)], D.DENY)


def c19(k: Kit) -> Fixture:  # same_product, bought on sale, target at full price -> APPROVE
    v = k.product(k.choice(SAME_PRODUCT))[0]
    target = k.sibling(
        v, list_price=v.list_price_cents + 100 * k.rng.randint(5, 20), stock=k.rng.randint(1, 9)
    )
    [it] = k.order(line(v, k.in_window(), paid=v.list_price_cents * k.rng.randint(60, 90) // 100))
    return k.done("exchange", [ask(it, target=target)], D.APPROVE)


def c20(k: Kit) -> Fixture:  # exchange to the same variant, not defective -> DENY
    v = k.product(k.choice(SAME_PRODUCT))[0]
    [it] = k.order(line(v, k.in_window()))
    return k.done("exchange", [ask(it, target=v)], D.DENY)


def c21(k: Kit) -> Fixture:  # exchange without target -> NEED_INFO
    [it] = k.order(line(k.product(k.choice(SAME_PRODUCT))[0], k.in_window()))
    return k.done("exchange", [ask(it)], D.NEED_INFO)


def c22(k: Kit) -> Fixture:  # underwear exchange, not defective -> DENY
    v, other = k.product("underwear")
    [it] = k.order(line(v, k.in_window()))
    req = ask(it, condition=k.choice(k.accepted), target=other)
    return k.done("exchange", [req], D.DENY)


def c23(k: Kit) -> Fixture:  # partial delivery -> APPROVE for the delivered item
    a, b = k.order(
        line(k.product(k.choice(RETURNABLE))[0], k.in_window()),
        line(k.product(k.choice(RETURNABLE))[0], k.not_delivered()),
    )
    return k.done("return", [ask(a), ask(b)], D.APPROVE, refund_cents=paid(a))


def c24(k: Kit) -> Fixture:  # no condition, past window -> DENY (defect hint)
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.expired()))
    return k.done("return", [ask(it, condition=None)], D.DENY)


def c25(k: Kit) -> Fixture:  # no condition, final_sale -> DENY (defect hint)
    [it] = k.order(line(k.product("final_sale")[0], k.in_window()))
    return k.done("return", [ask(it, condition=None)], D.DENY)


def c26(k: Kit) -> Fixture:  # no condition, past window, qty exceeded -> DENY (no hint)
    q = k.rng.randint(1, 2)
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.expired(), qty=q))
    k.prior_return(it, qty=q)
    return k.done("return", [ask(it, condition=None)], D.DENY)


def c27(k: Kit) -> Fixture:  # worn, past window -> DENY (no hint)
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.expired()))
    return k.done("return", [ask(it, condition="worn")], D.DENY)


def c28(k: Kit) -> Fixture:  # defective, past window -> NEED_MANAGER
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.expired()))
    req = ask(it, condition="defective")
    return k.done("return", [req], D.NEED_MANAGER, (R.DEFECT_CLAIM,), paid(it))


def c29(k: Kit) -> Fixture:  # defective final_sale -> NEED_MANAGER
    plan = k.in_window() if k.rng.chance(0.5) else k.expired()
    [it] = k.order(line(k.product("final_sale")[0], plan))
    req = ask(it, condition="defective")
    return k.done("return", [req], D.NEED_MANAGER, (R.DEFECT_CLAIM,), paid(it))


def c30(k: Kit) -> Fixture:  # defective, not delivered -> DENY / NOT_DELIVERED
    [it] = k.order(line(k.product(k.choice(RETURNABLE))[0], k.not_delivered()))
    return k.done("return", [ask(it, condition="defective")], D.DENY, (R.NOT_DELIVERED,))


def c32(k: Kit) -> Fixture:  # defective, refund >= L -> DEFECT_CLAIM + OVER_AUTO_LIMIT
    p = k.price(k.L, 2 * k.L)
    [it] = k.order(line(k.product("footwear", list_price=p)[0], k.in_window()))
    reasons = (R.DEFECT_CLAIM, R.OVER_AUTO_LIMIT)
    return k.done("return", [ask(it, condition="defective")], D.NEED_MANAGER, reasons, p)


def c33(k: Kit) -> Fixture:  # defective underwear replaced with the same variant
    v = k.product("underwear")[0]
    [it] = k.order(line(v, k.in_window()))
    req = ask(it, condition="defective", target=v)
    return k.done("exchange", [req], D.NEED_MANAGER, (R.DEFECT_CLAIM,))


def c34(k: Kit) -> Fixture:  # defective exchange to another category -> NEED_MANAGER
    v = k.product(k.choice(SAME_PRODUCT))[0]
    target = k.product("accessories")[0]
    [it] = k.order(line(v, k.in_window()))
    req = ask(it, condition="defective", target=target)
    return k.done("exchange", [req], D.NEED_MANAGER, (R.DEFECT_CLAIM,))


def c35(k: Kit) -> Fixture:  # defective exchange, target out of stock -> NEED_MANAGER
    v, other = k.product(k.choice(SAME_PRODUCT), stock=0)
    [it] = k.order(line(v, k.in_window()))
    req = ask(it, condition="defective", target=other)
    return k.done("exchange", [req], D.NEED_MANAGER, (R.DEFECT_CLAIM,))


def c36(k: Kit) -> Fixture:  # defective exchange without target -> NEED_INFO
    [it] = k.order(line(k.product(k.choice(SAME_PRODUCT))[0], k.in_window()))
    return k.done("exchange", [ask(it, condition="defective")], D.NEED_INFO)


def c37(k: Kit) -> Fixture:  # category missing from the YAML -> NEED_MANAGER / UNKNOWN_CATEGORY
    [it] = k.order(line(k.product(k.choice(UNKNOWN_CATEGORIES))[0], k.in_window()))
    req = ask(it, condition=k.choice(k.accepted))
    return k.done("return", [req], D.NEED_MANAGER, (R.UNKNOWN_CATEGORY,), paid(it))


def c38(k: Kit) -> Fixture:  # one item missing condition + one defective -> NEED_INFO
    plan = k.in_window()
    a, b = k.order(
        line(k.product(k.choice(RETURNABLE))[0], plan),
        line(k.product(k.choice(RETURNABLE))[0], plan),
    )
    return k.done("return", [ask(a, condition=None), ask(b, condition="defective")], D.NEED_INFO)


def c38a(k: Kit) -> Fixture:  # earlier refunds over L, only a final_sale item -> DENY
    plan = k.in_window()
    a, b = k.order(
        line(k.product("footwear", list_price=k.price(k.L, 2 * k.L))[0], plan),
        line(k.product("final_sale")[0], plan),
    )
    k.prior_return(a)
    return k.done("return", [ask(b, condition=k.choice(k.accepted))], D.DENY)


CASES: tuple[tuple[str, Callable[[Kit], Fixture]], ...] = (
    ("01", c01), ("02", c02), ("03", c03), ("05", c05), ("06", c06), ("07", c07),
    ("08", c08), ("09", c09), ("10", c10), ("11", c11), ("12", c12), ("13", c13),
    ("14", c14), ("15", c15), ("16", c16), ("17", c17), ("18", c18), ("19", c19),
    ("20", c20), ("21", c21), ("22", c22), ("23", c23), ("24", c24), ("25", c25),
    ("26", c26), ("27", c27), ("28", c28), ("29", c29), ("30", c30), ("32", c32),
    ("33", c33), ("34", c34), ("35", c35), ("36", c36), ("37", c37), ("38", c38),
    ("38a", c38a),
)  # fmt: skip


def build_fixtures(ds: Dataset, *, seed: int, now: datetime, cfg: PolicyConfig) -> list[Fixture]:
    """Add fixture rows to `ds` and return the fixtures, verified against `evaluate()`."""
    b = Builder(ds, Rng(seed, "fixtures"), now, tracking_prefix="FX")
    fixtures = [
        fn(Kit(b, cfg, case, i)) for case, fn in CASES for i in range(1, FIXTURES_PER_CASE + 1)
    ]
    verify(ds, fixtures, now=now, cfg=cfg)
    return fixtures


class FixtureMismatchError(Exception):
    pass


def verify(ds: Dataset, fixtures: list[Fixture], *, now: datetime, cfg: PolicyConfig) -> None:
    index = Index(ds)
    for f in fixtures:
        d = evaluate(index.policy_input(f.order_id, f.action, list(f.requests), now), cfg)
        got = (d.code, tuple(d.reasons), d.refund_cents)
        want = (f.code, f.reasons, f.refund_cents)
        if got != want:
            raise FixtureMismatchError(f"{f.order_number}: expected {want}, got {got}")


def to_jsonl(ds: Dataset, fixtures: list[Fixture], *, now: datetime, cfg: PolicyConfig) -> str:
    """One JSON object per fixture: the request, the expectation, and the full decision."""
    index = Index(ds)
    out: list[str] = []
    for f in fixtures:
        inp = index.policy_input(f.order_id, f.action, list(f.requests), now)
        record: dict[str, Any] = {
            "case": f.case,
            "order_number": f.order_number,
            "action": f.action,
            "now": now.isoformat(),
            "items": [
                {
                    "order_item_id": str(r.order_item_id),
                    "qty": r.qty,
                    "condition_claim": r.condition_claim,
                    "target_variant_id": None
                    if r.target_variant_id is None
                    else str(r.target_variant_id),
                }
                for r in f.requests
            ],
            "expected": {
                "code": f.code.value,
                "reasons": [r.value for r in f.reasons],
                "refund_cents": f.refund_cents,
            },
            "decision": evaluate(inp, cfg).model_dump(mode="json"),
        }
        out.append(json.dumps(record, sort_keys=True))
    return "\n".join(out) + "\n"
