"""CommerceClient over HTTP (the commerce mock now, a Shopify adapter later)."""

from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from dtc_commerce.errors import (
    CommerceConflictError,
    CommerceError,
    CommerceInvalidError,
    CommerceUnavailableError,
)
from dtc_contracts.commerce import (
    CreateRefundIn,
    CreateReturnIn,
    CustomerOrdersOut,
    CustomerOut,
    ErrorOut,
    InspectReturnIn,
    OrderOut,
    OrderSummaryOut,
    ProductVariantsOut,
    RefundOut,
    ReturnOut,
    ShipmentEventsOut,
    VariantOut,
)


class HttpCommerceClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http  # owns base_url, timeouts, and the transport

    async def lookup_customer(self, email: str) -> CustomerOut | None:
        return await self._get(CustomerOut, "/customers/lookup", params={"email": email})

    async def customer_orders(self, customer_id: UUID, limit: int = 10) -> list[OrderSummaryOut]:
        body = await self._get(
            CustomerOrdersOut, f"/customers/{customer_id}/orders", params={"limit": limit}
        )
        return [] if body is None else body.orders

    async def get_order(self, order_number: str) -> OrderOut | None:
        return await self._get(OrderOut, f"/orders/{order_number}")

    async def shipment_events(self, tracking_number: str) -> ShipmentEventsOut | None:
        return await self._get(ShipmentEventsOut, f"/shipments/{tracking_number}/events")

    async def get_variant(self, variant_id: UUID) -> VariantOut | None:
        return await self._get(VariantOut, f"/variants/{variant_id}")

    async def product_variants(self, product_id: UUID) -> ProductVariantsOut | None:
        return await self._get(ProductVariantsOut, f"/products/{product_id}/variants")

    async def create_return(self, body: CreateReturnIn, idempotency_key: str) -> ReturnOut:
        r = await self._send("POST", "/returns", body, idempotency_key)
        return ReturnOut.model_validate_json(r.content)

    async def inspect_return(self, return_id: UUID, *, passed: bool) -> ReturnOut:
        r = await self._send(
            "POST", f"/returns/{return_id}/inspect", InspectReturnIn(passed=passed)
        )
        return ReturnOut.model_validate_json(r.content)

    async def create_refund(self, body: CreateRefundIn, idempotency_key: str) -> RefundOut:
        r = await self._send("POST", "/refunds", body, idempotency_key)
        return RefundOut.model_validate_json(r.content)

    async def _get[M: BaseModel](
        self, model: type[M], path: str, params: dict[str, Any] | None = None
    ) -> M | None:
        r = await self._request("GET", path, params=params)
        if r.status_code == 404:
            return None
        _raise_for_status(r)
        return model.model_validate_json(r.content)

    async def _send(
        self, method: str, path: str, body: BaseModel, idempotency_key: str | None = None
    ) -> httpx.Response:
        headers = {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}
        r = await self._request(
            method,
            path,
            content=body.model_dump_json(),
            headers={"Content-Type": "application/json", **headers},
        )
        _raise_for_status(r)
        return r

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.TransportError as e:
            raise CommerceUnavailableError(0, None, f"{type(e).__name__}: {e}") from e


def _raise_for_status(r: httpx.Response) -> None:
    if r.is_success:
        return
    try:
        err = ErrorOut.model_validate_json(r.content)
        code, detail = err.code, err.detail
    except ValidationError:
        code, detail = None, r.text[:500]
    if r.status_code == 409:
        raise CommerceConflictError(r.status_code, code, detail)
    if r.status_code >= 500:
        raise CommerceUnavailableError(r.status_code, code, detail)
    if 400 <= r.status_code < 500:
        raise CommerceInvalidError(r.status_code, code, detail)
    raise CommerceError(r.status_code, code, detail)
