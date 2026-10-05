"""Policy configuration loaded from YAML (see docs/phases/phase-0.md, Policy config)."""

import os
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dtc_contracts import ConditionClaim, ReasonCode

PositiveInt = Annotated[int, Field(gt=0)]

POLICY_PATH_ENV = "POLICY_PATH"


class ConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReturnConfig(ConfigModel):
    window_days: PositiveInt
    window_anchor: Literal["delivered_at"]
    allowed_conditions: frozenset[ConditionClaim]

    @field_validator("allowed_conditions")
    @classmethod
    def check_no_defective(cls, v: frozenset[ConditionClaim]) -> frozenset[ConditionClaim]:
        if "defective" in v:
            raise ValueError("must not contain 'defective' (defects use the defect flow)")
        return v


class RefundConfig(ConfigModel):
    auto_limit_cents: PositiveInt
    aggregate_scope: Literal["order"]
    release_on: Literal["inspection_passed"]


class ExchangeConfig(ConfigModel):
    require_same_price: bool


class DefectConfig(ConfigModel):
    bypass_reasons: frozenset[
        Literal[
            ReasonCode.WINDOW_EXPIRED,
            ReasonCode.CATEGORY_NOT_RETURNABLE,
            ReasonCode.CATEGORY_NOT_EXCHANGEABLE,
        ]
    ]


class CategoryConfig(ConfigModel):
    returnable: bool
    exchange: Literal["same_product", "same_category", "none"]


class PolicyConfig(ConfigModel):
    version: Annotated[str, Field(min_length=1)]
    timezone: ZoneInfo
    return_: ReturnConfig = Field(alias="return")
    refund: RefundConfig
    exchange: ExchangeConfig
    defect: DefectConfig
    categories: Annotated[dict[str, CategoryConfig], Field(min_length=1)]

    @classmethod
    def from_yaml(cls, path: str | Path) -> Self:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"policy file {path} must contain a YAML mapping")
        return cls.model_validate(data)

    @classmethod
    def from_env(cls) -> Self:
        path = os.environ.get(POLICY_PATH_ENV)
        if not path:
            raise RuntimeError(f"{POLICY_PATH_ENV} is not set")
        return cls.from_yaml(path)
