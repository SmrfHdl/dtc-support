"""Runtime settings read from the environment."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from commerce_mock.faults import Faults

DATABASE_URL_ENV = "COMMERCE_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql+asyncpg://dtc:dtc@localhost:5432/dtc"
# Same as the datagen default, so a freshly seeded DB and the mock agree on "now".
DEFAULT_NOW = "2026-09-28T12:00:00Z"


def database_url() -> str:
    return os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL)


@dataclass(frozen=True)
class Settings:
    database_url: str
    policy_path: Path
    now: datetime  # the mock's clock (MOCK_NOW); the system clock is never used
    faults: Faults = field(default_factory=Faults)  # off unless MOCK_* is set

    @classmethod
    def from_env(cls) -> "Settings":
        policy = os.environ.get("POLICY_PATH")
        if not policy:
            raise RuntimeError("POLICY_PATH is not set")
        now = datetime.fromisoformat(os.environ.get("MOCK_NOW", DEFAULT_NOW))
        if now.tzinfo is None:
            raise RuntimeError("MOCK_NOW must include a timezone")
        return cls(
            database_url=database_url(),
            policy_path=Path(policy),
            now=now,
            faults=Faults.from_env(os.environ),
        )
