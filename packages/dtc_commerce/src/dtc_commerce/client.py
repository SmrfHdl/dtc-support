"""The only commerce interface the orchestrator knows (CLAUDE.md: import boundaries)."""

from typing import Protocol
from uuid import UUID

from dtc_contracts.commerce import (
    CreateRefundIn,
    CreateReturnIn,
    CustomerOut,
    OrderOut,
    OrderSummaryOut,
    RefundOut,
    ReturnOut,
    ShipmentEventsOut,
    VariantOut,
)


class CommerceClient(Protocol):
    """Reads return None when the thing does not exist; writes raise dtc_commerce.errors."""

    async def lookup_customer(self, email: str) -> CustomerOut | None: ...

    async def customer_orders(
        self, customer_id: UUID, limit: int = 10
    ) -> list[OrderSummaryOut]: ...

    async def get_order(self, order_number: str) -> OrderOut | None: ...

    async def shipment_events(self, tracking_number: str) -> ShipmentEventsOut | None: ...

    async def get_variant(self, variant_id: UUID) -> VariantOut | None: ...

    async def create_return(self, body: CreateReturnIn, idempotency_key: str) -> ReturnOut: ...

    async def inspect_return(self, return_id: UUID, *, passed: bool) -> ReturnOut: ...

    async def create_refund(self, body: CreateRefundIn, idempotency_key: str) -> RefundOut: ...
