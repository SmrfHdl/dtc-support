"""HttpCommerceClient error mapping, without a server (httpx.MockTransport)."""

from collections.abc import Callable
from uuid import UUID

import httpx
import pytest

from dtc_commerce import (
    CommerceConflictError,
    CommerceInvalidError,
    CommerceUnavailableError,
    HttpCommerceClient,
)
from dtc_contracts.commerce import CreateRefundIn

pytestmark = pytest.mark.anyio
REFUND = CreateRefundIn(return_id=UUID(int=1), amount_cents=100)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpCommerceClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    return HttpCommerceClient(http)


async def test_404_read_is_none() -> None:
    c = client(lambda _: httpx.Response(404, json={"code": "not_found", "detail": "order"}))
    assert await c.get_order("DTC-1") is None
    assert await c.customer_orders(UUID(int=1)) == []
    assert await c.product_variants(UUID(int=1)) is None


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (409, CommerceConflictError),
        (422, CommerceInvalidError),
        (404, CommerceInvalidError),  # a 404 on a write is a bad request, not "absent"
        (503, CommerceUnavailableError),
    ],
)
async def test_write_errors(status: int, error: type[Exception]) -> None:
    c = client(lambda _: httpx.Response(status, json={"code": "over_limit", "detail": "x"}))
    with pytest.raises(error):
        await c.create_refund(REFUND, "key-1")


async def test_error_body_is_parsed() -> None:
    c = client(lambda _: httpx.Response(409, json={"code": "qty_conflict", "detail": "item 1"}))
    with pytest.raises(CommerceConflictError) as exc:
        await c.create_refund(REFUND, "key-1")
    assert (exc.value.code, exc.value.detail) == ("qty_conflict", "item 1")


async def test_non_json_error_body() -> None:
    c = client(lambda _: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(CommerceUnavailableError) as exc:
        await c.get_order("DTC-1")
    assert exc.value.code is None


async def test_transport_failure_is_unavailable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(CommerceUnavailableError):
        await client(fail).get_order("DTC-1")


async def test_idempotency_key_header_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(409, json={"code": "already_refunded", "detail": "x"})

    with pytest.raises(CommerceConflictError):
        await client(handler).create_refund(REFUND, "key-42")
    assert seen[0].headers["Idempotency-Key"] == "key-42"
