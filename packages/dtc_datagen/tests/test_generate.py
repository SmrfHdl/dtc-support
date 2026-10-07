"""Datagen output: reproducible, internally consistent, and within the spec's ranges."""

from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import fields
from datetime import datetime
from uuid import UUID

import pytest

from dtc_datagen.bulk import SHIPMENT_MIX
from dtc_datagen.generate import DEFAULT_NOW as NOW
from dtc_datagen.generate import Generated, generate
from dtc_datagen.rows import TABLES, Dataset, ShipmentEventRow
from dtc_datagen.snapshot import Index, RequestSpec
from dtc_policy import PolicyConfig, evaluate
from dtc_policy.window import days_since_delivery


def _cells(ds: Dataset) -> Iterator[tuple[str, str, object]]:
    """(table, column, value) for every cell of every row."""
    for table, row_type in TABLES:
        columns = [f.name for f in fields(row_type)]
        for row in getattr(ds, table):
            for column in columns:
                yield table, column, getattr(row, column)


def test_same_seed_same_data(gen: Generated, cfg: PolicyConfig) -> None:
    again = generate(seed=42, customers=2000, orders=5000, now=NOW, cfg=cfg)
    assert again.dataset == gen.dataset
    assert again.fixtures == gen.fixtures


def test_other_seed_other_data(gen: Generated, cfg: PolicyConfig) -> None:
    other = generate(seed=7, customers=2000, orders=5000, now=NOW, cfg=cfg)
    assert other.dataset.orders != gen.dataset.orders


def test_naive_now_rejected(cfg: PolicyConfig) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        generate(seed=1, customers=1, orders=1, now=NOW.replace(tzinfo=None), cfg=cfg)


def test_ids_and_natural_keys_unique(gen: Generated) -> None:
    ds = gen.dataset
    ids = [value for _, column, value in _cells(ds) if column == "id"]
    assert len(ids) == len(set(ids))
    groups: list[Sequence[object]] = [
        [c.email for c in ds.customers],
        [p.sku for p in ds.products],
        [o.order_number for o in ds.orders],
        [s.tracking_number for s in ds.shipments],
        [r.idempotency_key for r in ds.returns] + [r.idempotency_key for r in ds.refunds],
        [(ri.return_id, ri.order_item_id) for ri in ds.return_items],
    ]
    for values in groups:
        assert len(values) == len(set(values))


def test_every_timestamp_is_aware_and_not_after_now(gen: Generated) -> None:
    for table, column, value in _cells(gen.dataset):
        if isinstance(value, datetime) and column != "eta":
            assert value.tzinfo is not None
            assert value <= NOW, (table, column, value)


def test_delivered_within_60_days(gen: Generated, cfg: PolicyConfig) -> None:
    for s in gen.dataset.shipments:
        assert (s.status == "delivered") == (s.delivered_at is not None)
        if s.delivered_at is not None:
            assert 0 <= days_since_delivery(NOW, s.delivered_at, cfg.timezone) <= 60


def test_events_in_order_and_end_in_shipment_status(gen: Generated) -> None:
    events: defaultdict[UUID, list[ShipmentEventRow]] = defaultdict(list)
    for e in gen.dataset.shipment_events:
        events[e.shipment_id].append(e)
    for s in gen.dataset.shipments:
        evs = events[s.id]
        assert [e.ts for e in evs] == sorted(e.ts for e in evs)
        assert evs[0].status == "label_created"
        assert evs[-1].status == s.status


def test_money_and_quantities(gen: Generated) -> None:
    ds = gen.dataset
    assert all(v.list_price_cents >= 0 and v.stock >= 0 for v in ds.variants)
    assert all(i.qty > 0 and i.unit_price_cents >= 0 for i in ds.order_items)
    totals = Counter[UUID]()
    for i in ds.order_items:
        totals[i.order_id] += i.unit_price_cents * i.qty
    assert all(o.total_cents == totals[o.id] for o in ds.orders)


def test_returns_are_consistent(gen: Generated) -> None:
    ds = gen.dataset
    items = {i.id: i for i in ds.order_items}
    shipments = {s.id: s for s in ds.shipments}
    returns = {r.id: r for r in ds.returns}
    committed = Counter[UUID]()
    for ri in ds.return_items:
        item, ret = items[ri.order_item_id], returns[ri.return_id]
        assert item.order_id == ret.order_id
        assert item.shipment_id is not None
        delivered_at = shipments[item.shipment_id].delivered_at
        assert delivered_at is not None
        assert ret.created_at >= delivered_at
        if ret.status != "rejected":
            committed[item.id] += ri.qty
    assert all(committed[i] <= items[i].qty for i in committed)
    for r in ds.returns:
        assert (r.type == "exchange") == (r.refund_cents is None)
    refunded = {r.id for r in ds.returns if r.status == "refunded" and r.type == "return"}
    assert {f.return_id for f in ds.refunds} == refunded


def test_shipment_mix(gen: Generated) -> None:
    bulk = [s.status for s in gen.dataset.shipments if s.tracking_number.startswith("1Z")]
    counts = Counter(bulk)
    for status, share in SHIPMENT_MIX:
        assert abs(counts[status] / len(bulk) - share) < 0.025, status


def test_random_catalog_categories_exist_in_policy(gen: Generated, cfg: PolicyConfig) -> None:
    random_products = [p for p in gen.dataset.products if not p.sku.startswith("FX-")]
    assert {p.category_code for p in random_products} <= set(cfg.categories)


def test_only_case_37_uses_unknown_categories(gen: Generated, cfg: PolicyConfig) -> None:
    unknown = {
        p.sku.split("-P")[0] for p in gen.dataset.products if p.category_code not in cfg.categories
    }
    assert unknown
    assert all(sku.startswith("FX-37-") for sku in unknown)


def test_every_bulk_order_maps_to_a_valid_policy_input(gen: Generated, cfg: PolicyConfig) -> None:
    index = Index(gen.dataset)
    for order in gen.dataset.orders:
        first = index.items_by_order[order.id][0]
        inp = index.policy_input(order.id, "return", [RequestSpec(first, 1, "like_new")], NOW)
        evaluate(inp, cfg)  # raises on an invalid snapshot
