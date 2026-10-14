"""HTTP contract of the commerce API (docs/phases/phase-0.md, Commerce mock API).

The mock serves these models and `dtc_commerce` parses them. Response shapes follow the
commerce system (orders, shipments), not the policy: mapping to PolicyInput is the
client's job.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from dtc_contracts.base import ContractModel, NonNegInt, PositiveInt
from dtc_contracts.enums import ReasonCode
from dtc_contracts.policy import ConditionClaim, ShipmentStatus

OrderStatus = Literal["placed", "partially_shipped", "shipped", "delivered", "cancelled"]
ReturnType = Literal["return", "exchange"]
ReturnStatus = Literal[
    "approved", "pending_manager", "received", "inspection_passed", "refunded", "rejected"
]
RefundStatus = Literal["pending", "completed", "failed"]
ErrorCode = Literal[
    "not_found",
    "invalid_request",
    "idempotency_mismatch",
    "policy_changed",
    "qty_conflict",
    "over_limit",
    "not_releasable",
    "already_refunded",
    "invalid_transition",
    "injected_fault",
]


class ErrorOut(ContractModel):
    code: ErrorCode
    detail: str


# --- Reads ---------------------------------------------------------------------------


class CustomerOut(ContractModel):
    id: UUID
    email: str
    name: str


class OrderSummaryOut(ContractModel):
    id: UUID
    order_number: str
    status: OrderStatus
    placed_at: AwareDatetime
    total_cents: NonNegInt


class CustomerOrdersOut(ContractModel):
    customer_id: UUID
    orders: list[OrderSummaryOut]  # newest first


class VariantOut(ContractModel):
    id: UUID
    product_id: UUID
    sku: str
    product_name: str
    category_code: str
    size: str
    color: str
    list_price_cents: NonNegInt
    stock: NonNegInt


class ProductVariantsOut(ContractModel):
    """Every variant of one product (exchange targets: other sizes and colors)."""

    product_id: UUID
    sku: str
    product_name: str
    category_code: str
    variants: list[VariantOut]  # ordered by size, then color


class ShipmentOut(ContractModel):
    id: UUID
    carrier: str
    tracking_number: str
    status: ShipmentStatus
    shipped_at: AwareDatetime | None
    delivered_at: AwareDatetime | None
    eta: AwareDatetime | None


class OrderItemOut(ContractModel):
    id: UUID
    variant: VariantOut
    qty: PositiveInt
    unit_price_cents: NonNegInt
    shipment_id: UUID | None  # None: not in a shipment yet
    committed_returned_qty: NonNegInt  # qty in non-rejected returns, computed by the server


class OrderOut(ContractModel):
    id: UUID
    order_number: str
    customer_id: UUID
    status: OrderStatus
    placed_at: AwareDatetime
    total_cents: NonNegInt
    items: list[OrderItemOut]
    shipments: list[ShipmentOut]
    committed_refunds_cents: NonNegInt  # refund_cents of non-rejected returns, computed

    @model_validator(mode="after")
    def _check_shipment_refs(self) -> Self:
        known = {s.id for s in self.shipments}
        dangling = {i.shipment_id for i in self.items if i.shipment_id is not None} - known
        if dangling:
            raise ValueError(f"items reference unknown shipments: {sorted(map(str, dangling))}")
        return self


class ShipmentEventOut(ContractModel):
    ts: AwareDatetime
    status: ShipmentStatus
    location: str | None
    description: str


class ShipmentEventsOut(ContractModel):
    tracking_number: str
    carrier: str
    status: ShipmentStatus
    eta: AwareDatetime | None
    events: list[ShipmentEventOut]  # oldest first


# --- Writes --------------------------------------------------------------------------


class ReturnItemIn(ContractModel):
    order_item_id: UUID
    qty: PositiveInt
    condition_claim: ConditionClaim | None
    exchange_variant_id: UUID | None = None


class CreateReturnIn(ContractModel):
    """Written after the policy decided APPROVE (approved) or NEED_MANAGER (pending_manager)."""

    order_id: UUID
    type: ReturnType
    status: Literal["approved", "pending_manager"]
    items: Annotated[list[ReturnItemIn], Field(min_length=1)]
    refund_cents: NonNegInt | None  # the policy's refund_cents; None for exchanges
    reason_codes: list[ReasonCode]  # decision-level reasons
    policy_version: str

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if (self.type == "exchange") != (self.refund_cents is None):
            raise ValueError("refund_cents is required for returns and must be None for exchanges")
        if self.type == "exchange" and any(i.exchange_variant_id is None for i in self.items):
            raise ValueError("every exchange item needs exchange_variant_id")
        if self.type == "return" and any(i.exchange_variant_id is not None for i in self.items):
            raise ValueError("return items cannot have exchange_variant_id")
        if (self.status == "pending_manager") != bool(self.reason_codes):
            raise ValueError("pending_manager needs reason_codes; approved must have none")
        ids = [i.order_item_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate order_item_id in items")
        return self


class ReturnOut(ContractModel):
    id: UUID
    order_id: UUID
    type: ReturnType
    status: ReturnStatus
    refund_cents: NonNegInt | None
    reason_codes: list[ReasonCode]
    policy_version: str
    created_at: AwareDatetime
    items: list[ReturnItemIn]


class InspectReturnIn(ContractModel):
    passed: bool


class CreateRefundIn(ContractModel):
    return_id: UUID
    amount_cents: NonNegInt
    approved_by: str | None = None  # required when the return needed a manager


class RefundOut(ContractModel):
    id: UUID
    return_id: UUID
    amount_cents: NonNegInt
    status: RefundStatus
    approved_by: str | None
    created_at: AwareDatetime
