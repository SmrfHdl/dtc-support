"""Injected faults reach the client and every request lands in request_log."""

import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from statistics import median

import asyncpg
import httpx
import pytest

from commerce_mock.app import create_app
from commerce_mock.faults import Fault, Faults
from commerce_mock.settings import Settings
from dtc_contracts.commerce import ErrorOut
from dtc_datagen.generate import Generated
from dtc_datagen.load import asyncpg_dsn

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def faulty(
    seeded: Generated, database_url: str, now: datetime, policy_yaml: Path
) -> AsyncIterator[httpx.AsyncClient]:
    faults = Faults(routes={"GET_ORDER": Fault(error_rate=1.0)})
    app = create_app(Settings(database_url, policy_yaml, now, faults))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://m") as c,
    ):
        yield c


async def log_rows(database_url: str, path: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        return await conn.fetch(
            "SELECT status_code, trace_id FROM commerce.request_log WHERE path = $1", path
        )
    finally:
        await conn.close()


async def test_injected_error_only_on_its_route(
    faulty: httpx.AsyncClient, seeded: Generated, database_url: str
) -> None:
    path = f"/orders/{seeded.dataset.orders[0].order_number}"
    before = await log_rows(database_url, path)
    r = await faulty.get(path)
    assert r.status_code == 503
    assert ErrorOut.model_validate(r.json()).code == "injected_fault"
    variant = seeded.dataset.variants[0].id
    assert (await faulty.get(f"/variants/{variant}")).status_code == 200

    after = await log_rows(database_url, path)
    new_503 = [row for row in after if row["status_code"] == 503]
    assert len(new_503) == 1 + sum(row["status_code"] == 503 for row in before)
    assert all(row["trace_id"] is None or len(row["trace_id"]) == 32 for row in after)


async def test_get_order_p50_under_20ms(client: httpx.AsyncClient, seeded: Generated) -> None:
    """Exit criterion: mock p50 < 20ms with fault injection off (in-process, no network)."""
    numbers = [o.order_number for o in seeded.dataset.orders[:200]]
    await client.get(f"/orders/{numbers[0]}")  # warm the pool
    timings: list[float] = []
    for n in numbers:
        start = time.perf_counter()
        r = await client.get(f"/orders/{n}")
        timings.append((time.perf_counter() - start) * 1000)
        assert r.status_code == 200
    assert median(timings) < 20, f"p50 {median(timings):.1f}ms"
