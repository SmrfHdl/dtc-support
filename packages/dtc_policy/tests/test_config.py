import copy
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

from dtc_contracts import ReasonCode
from dtc_policy.config import POLICY_PATH_ENV, PolicyConfig

POLICY_YAML = Path(__file__).parents[1] / "policies" / "policy.yaml"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return yaml.safe_load(POLICY_YAML.read_text(encoding="utf-8"))


def _with(raw: dict[str, Any], section: str, key: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(raw)
    data[section][key] = value
    return data


def test_loads_sample_policy() -> None:
    cfg = PolicyConfig.from_yaml(POLICY_YAML)

    assert cfg.version == "2026.09.28-1"
    assert cfg.timezone == ZoneInfo("America/Los_Angeles")
    assert cfg.return_.window_days == 30
    assert cfg.return_.allowed_conditions == {"new_with_tags", "like_new"}
    assert cfg.refund.auto_limit_cents == 5000
    assert cfg.exchange.require_same_price is True
    assert cfg.defect.bypass_reasons == {
        ReasonCode.WINDOW_EXPIRED,
        ReasonCode.CATEGORY_NOT_RETURNABLE,
        ReasonCode.CATEGORY_NOT_EXCHANGEABLE,
    }
    assert set(cfg.categories) == {"apparel", "footwear", "accessories", "underwear", "final_sale"}
    assert cfg.categories["underwear"].returnable is False
    assert cfg.categories["accessories"].exchange == "same_category"


def test_config_is_frozen() -> None:
    cfg = PolicyConfig.from_yaml(POLICY_YAML)
    with pytest.raises(ValidationError):
        cfg.version = "x"  # pyright: ignore[reportAttributeAccessIssue]


def test_typo_key_rejected(raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    data["return"]["window_day"] = data["return"].pop("window_days")
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(data)


def test_return_key_must_use_yaml_name(raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    data["return_"] = data.pop("return")
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(data)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("return", "window_anchor", "shipped_at"),
        ("return", "window_days", 0),
        ("return", "allowed_conditions", ["new_with_tags", "defective"]),
        ("return", "allowed_conditions", ["brand_new"]),
        ("refund", "auto_limit_cents", 0),
        ("refund", "aggregate_scope", "item"),
        ("refund", "release_on", "delivered"),
        ("defect", "bypass_reasons", ["QTY_EXCEEDED"]),
        ("defect", "bypass_reasons", ["NOT_DELIVERED"]),
    ],
)
def test_invalid_values_rejected(raw: dict[str, Any], section: str, key: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(_with(raw, section, key, value))


def test_invalid_timezone_rejected(raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    data["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(data)


def test_empty_categories_rejected(raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    data["categories"] = {}
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(data)


def test_invalid_category_exchange_rejected(raw: dict[str, Any]) -> None:
    data = copy.deepcopy(raw)
    data["categories"]["apparel"]["exchange"] = "any"
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(data)


def test_empty_yaml_file_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        PolicyConfig.from_yaml(path)


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY_PATH_ENV, str(POLICY_YAML))
    assert PolicyConfig.from_env().version == "2026.09.28-1"


def test_from_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(POLICY_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match=POLICY_PATH_ENV):
        PolicyConfig.from_env()
