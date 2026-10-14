"""NLU output contract (docs/phases/phase-1.md, Contracts).

Everything here is an unverified claim taken from customer text (Hard rule 5). Code
verifies identity against the commerce system and keeps only ids it offered the model.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from dtc_contracts.base import ContractModel, PositiveInt
from dtc_contracts.policy import ConditionClaim

Intent = Literal[
    "return",
    "exchange",
    "wismo",
    "provide_info",  # answers a question (email, order number, condition, size...)
    "affirm",  # "yes" to a confirmation
    "negate",  # "no" to a confirmation
    "request_human",
    "other",
]
NonEmpty = Annotated[str, Field(min_length=1, max_length=200)]


class ItemMention(ContractModel):
    """What the customer said about one item, in their words."""

    text: NonEmpty  # "the blue hoodie", "both shirts"
    size: NonEmpty | None = None
    color: NonEmpty | None = None
    qty: PositiveInt | None = None


class NluResult(ContractModel):
    intent: Intent
    confidence: Annotated[float, Field(ge=0, le=1)]
    order_number: NonEmpty | None = None
    email: NonEmpty | None = None
    items: list[ItemMention] = Field(default_factory=list[ItemMention])
    # Picked from the order's item list in the prompt, if one was given.
    item_ids: list[UUID] = Field(default_factory=list[UUID])
    condition_claim: ConditionClaim | None = None
    target: ItemMention | None = None  # exchange: "size L", "the black one"
    target_variant_id: UUID | None = None  # picked from the variant list in the prompt
