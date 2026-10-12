"""Shared base model and constrained types for all contracts."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

PositiveInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
