from pathlib import Path

import pytest

from dtc_datagen.generate import DEFAULT_NOW, DEFAULT_SEED, Generated, generate
from dtc_policy import PolicyConfig

REPO = Path(__file__).parents[3]
POLICY_YAML = REPO / "packages" / "dtc_policy" / "policies" / "policy.yaml"


@pytest.fixture(scope="session")
def cfg() -> PolicyConfig:
    return PolicyConfig.from_yaml(POLICY_YAML)


@pytest.fixture(scope="session")
def gen(cfg: PolicyConfig) -> Generated:
    """The default-size dataset (generation takes well under a second)."""
    return generate(seed=DEFAULT_SEED, customers=2000, orders=5000, now=DEFAULT_NOW, cfg=cfg)
