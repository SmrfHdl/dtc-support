"""Entry point: bulk random data plus the verified fixtures."""

from dataclasses import dataclass
from datetime import UTC, datetime

from dtc_datagen.bulk import bulk
from dtc_datagen.fixtures import Fixture, build_fixtures
from dtc_datagen.rows import Dataset
from dtc_policy import PolicyConfig

# Defaults of the CLI; the committed fixtures file is generated with these.
DEFAULT_SEED = 42
DEFAULT_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@dataclass
class Generated:
    dataset: Dataset
    fixtures: list[Fixture]


def generate(
    *, seed: int, customers: int, orders: int, now: datetime, cfg: PolicyConfig
) -> Generated:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ds = Dataset()
    bulk(ds, seed=seed, n_customers=customers, n_orders=orders, now=now, policy_version=cfg.version)
    fixtures = build_fixtures(ds, seed=seed, now=now, cfg=cfg)
    return Generated(ds, fixtures)
