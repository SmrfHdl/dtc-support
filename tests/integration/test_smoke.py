"""The smoke script's checks pass against the in-process mock (keeps the script from rotting)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from dtc_commerce import HttpCommerceClient
from dtc_datagen.generate import Generated
from dtc_policy import PolicyConfig

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
SCRIPT = Path(__file__).parents[2] / "scripts" / "smoke_test.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_smoke_checks_pass(
    client: httpx.AsyncClient, seeded: Generated, cfg: PolicyConfig
) -> None:
    smoke = load_script()
    checks = await smoke.run(HttpCommerceClient(client), cfg)
    assert [c.label.split(":")[0] for c in checks] == [
        "APPROVE", "DENY", "NEED_MANAGER", "NEED_INFO", "DENY",
    ]  # fmt: skip
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
