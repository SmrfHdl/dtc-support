"""Input and output contracts of dtc_policy.evaluate()."""

from datetime import timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from dtc_contracts.enums import DecisionCode, ItemStatus, ReasonCode

ConditionClaim = Literal["new_with_tags", "like_new", "worn", "defective"]
ShipmentStatus = Literal[
    "label_created", "in_transit", "out_for_delivery", "delivered", "exception", "lost"
]


PositiveInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class VariantSnapshot(ContractModel):
    # variant: a specific version of a product (e.g. "T-shirt, blue, M")
    variant_id: UUID
    product_id: UUID
    category_code: str
    list_price_cents: NonNegInt  # the item's current normal price on the store, before any discount
    stock: NonNegInt  # number of units available in the warehouse


class OrderItemSnapshot(ContractModel):
    order_item_id: UUID
    variant: VariantSnapshot  # the variant that was bought
    qty: PositiveInt  # number of units
    unit_price_cents: NonNegInt  # price actually paid per unit, after item-level discount
    committed_returned_qty: NonNegInt  # qty in all non-rejected returns (pending ones included)
    shipment_status: ShipmentStatus  # status of the shipment carrying this item
    delivered_at: AwareDatetime | None

    @model_validator(mode="after")
    def _check_shipment_status(self) -> Self:
        if self.shipment_status == "delivered" and self.delivered_at is None:
            raise ValueError("delivered_at is required when shipment_status is delivered")
        return self

    @model_validator(mode="after")
    def _check_qty(self) -> Self:
        if self.committed_returned_qty > self.qty:
            raise ValueError("committed_returned_qty must be less than or equal to qty")
        return self


class OrderSnapshot(ContractModel):
    order_id: UUID
    items: list[OrderItemSnapshot]  # all items in the order bought by the customer
    committed_refunds_cents: NonNegInt  # refund amount of all non-rejected returns on this order


class RequestedItem(ContractModel):
    # item the customer wishes to return/exchange
    order_item_id: UUID
    qty: PositiveInt
    condition_claim: ConditionClaim | None
    target_variant: VariantSnapshot | None = None  # exchange only


class PolicyInput(ContractModel):
    action: Literal["return", "exchange"]
    now: AwareDatetime
    order: OrderSnapshot
    items: Annotated[list[RequestedItem], Field(min_length=1)]
    risk_score: float | None = None  # reserved; ignored when None

    @model_validator(mode="after")
    def _check_items(self) -> Self:
        requested_ids = [item.order_item_id for item in self.items]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("duplicate order_item_id in items")

        order_ids = {item.order_item_id for item in self.order.items}
        unknown = set(requested_ids) - order_ids
        if unknown:
            raise ValueError(f"order_item_id not in order: {sorted(map(str, unknown))}")
        return self

    @model_validator(mode="after")
    def _check_time(self) -> Self:
        limit = self.now + timedelta(minutes=5)
        for item in self.order.items:
            if item.delivered_at is not None and item.delivered_at > limit:
                raise ValueError(
                    f"delivered_at is in the future for order_item_id {item.order_item_id}"
                )
        return self


class ItemDecision(ContractModel):
    order_item_id: UUID
    status: ItemStatus
    reasons: list[ReasonCode]
    defect_claim_possible: bool = False


class MissingField(ContractModel):
    order_item_id: UUID
    field: Literal["condition", "target_variant"]


class PolicyDecision(ContractModel):
    code: DecisionCode
    reasons: list[ReasonCode]
    items: list[ItemDecision]
    refund_cents: NonNegInt | None
    missing: list[MissingField]
    policy_version: str
