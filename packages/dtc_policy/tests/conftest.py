from pathlib import Path

import pytest

from dtc_policy import PolicyConfig

POLICY_YAML = Path(__file__).parents[1] / "policies" / "policy.yaml"


@pytest.fixture(scope="session")
def cfg() -> PolicyConfig:
    return PolicyConfig.from_yaml(POLICY_YAML)
