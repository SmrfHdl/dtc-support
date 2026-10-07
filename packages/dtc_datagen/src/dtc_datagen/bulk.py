"""Random customers and orders with the spec's shipment mix."""

from datetime import datetime, timedelta
from uuid import UUID

from dtc_contracts import ConditionClaim, ShipmentStatus
from dtc_datagen.builder import NON_REJECTED_RETURN_STATUSES, Builder, Line, ShipmentPlan
from dtc_datagen.catalog import random_catalog
from dtc_datagen.rng import Rng
from dtc_datagen.rows import Dataset, OrderItemRow, VariantRow

FIRST_NAMES = (
    "Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan", "Lily", "Lucas", "Chloe", "Mason",
    "Emma", "Leo", "Nora", "Owen", "Ruby", "Eli", "Hazel", "Jack", "Ivy", "Theo",
)  # fmt: skip
LAST_NAMES = (
    "Nguyen", "Smith", "Garcia", "Kim", "Patel", "Johnson", "Lee", "Brown", "Lopez", "Chen",
    "Davis", "Martin", "Tran", "Wilson", "Clark", "Young", "Walker", "Hall", "Allen", "Wright",
)  # fmt: skip

SHIPMENT_MIX: tuple[tuple[ShipmentStatus, float], ...] = (
    ("delivered", 0.70),
    ("in_transit", 0.15),
    ("label_created", 0.08),
    ("exception", 0.05),
    ("lost", 0.02),
)
DELIVERED_MAX_AGE = timedelta(days=60)
RETURN_RATE = 0.10  # share of delivered orders with an earlier return
RETURN_STATUSES = (*NON_REJECTED_RETURN_STATUSES, "rejected")
RETURN_STATUS_WEIGHTS = (20, 10, 15, 10, 35, 10)
CONDITIONS: tuple[ConditionClaim, ...] = ("like_new", "new_with_tags", "defective")
N_PRODUCTS = 60


def bulk(
    ds: Dataset, *, seed: int, n_customers: int, n_orders: int, now: datetime, policy_version: str
) -> None:
    rng = Rng(seed, "bulk")
    b = Builder(ds, rng, now, tracking_prefix="1Z")
    catalog = random_catalog(ds, rng, N_PRODUCTS)
    all_products = [p for products in catalog.values() for p in products]
    all_variants = [v for p in all_products for v in p]
    siblings = {v.id: p for p in all_products for v in p}

    customers = [
        b.customer(
            email=f"{first.lower()}.{last.lower()}.{i + 1}@example.com",
            name=f"{first} {last}",
        )
        for i in range(n_customers)
        for first, last in [(rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))]
    ]

    statuses: list[ShipmentStatus] = [s for s, _ in SHIPMENT_MIX]
    weights = [w for _, w in SHIPMENT_MIX]

    def plan() -> ShipmentPlan:
        status: ShipmentStatus = rng.weighted(statuses, weights)
        if status == "delivered":
            return ShipmentPlan(status, rng.between(now - DELIVERED_MAX_AGE, now))
        return ShipmentPlan(status)

    for i in range(n_orders):
        n_lines = rng.weighted([1, 2, 3, 4], [55, 30, 12, 3])
        variants = rng.sample(all_variants, n_lines)
        plans = [plan()]
        if n_lines >= 2 and rng.chance(0.12):
            plans.append(plan())  # split shipment, possibly a partial delivery
        lines = [
            Line(
                variant=v,
                qty=rng.weighted([1, 2, 3], [85, 12, 3]),
                unit_price_cents=_paid_price(rng, v),
                shipment=plans[0] if j == 0 else rng.choice(plans),
            )
            for j, v in enumerate(variants)
        ]
        items = b.order(rng.choice(customers), f"DTC-{100001 + i}", lines)

        delivered = [
            (item, line.shipment.delivered_at)
            for item, line in zip(items, lines, strict=True)
            if line.shipment is not None and line.shipment.delivered_at is not None
        ]
        if delivered and rng.chance(RETURN_RATE):
            item, delivered_at = rng.choice(delivered)
            _random_return(b, rng, item, delivered_at, siblings, policy_version)


def _paid_price(rng: Rng, v: VariantRow) -> int:
    if rng.chance(0.15):  # bought on sale
        return v.list_price_cents * rng.randint(60, 90) // 100
    return v.list_price_cents


def _random_return(
    b: Builder,
    rng: Rng,
    item: OrderItemRow,
    delivered_at: datetime,
    siblings: dict[UUID, list[VariantRow]],
    policy_version: str,
) -> None:
    others = [v for v in siblings[item.variant_id] if v.id != item.variant_id]
    exchange_to = rng.choice(others) if others and rng.chance(0.15) else None
    b.prior_return(
        item,
        delivered_at,
        qty=rng.randint(1, item.qty),
        status=rng.weighted(RETURN_STATUSES, RETURN_STATUS_WEIGHTS),
        policy_version=policy_version,
        condition=rng.weighted(CONDITIONS, [60, 25, 15]),
        exchange_variant=exchange_to,
    )
