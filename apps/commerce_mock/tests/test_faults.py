"""Fault configuration and sampling (no server, no DB)."""

import random
from statistics import quantiles

import pytest

from commerce_mock.faults import Fault, Faults


def test_off_by_default() -> None:
    faults = Faults.from_env({})
    assert not faults.for_route("get_order").active


def test_defaults_and_route_override() -> None:
    faults = Faults.from_env(
        {
            "MOCK_LATENCY_P50_MS": "10",
            "MOCK_LATENCY_P99_MS": "50",
            "MOCK_ERROR_RATE": "0.01",
            "MOCK_ERROR_RATE__GET_ORDER": "0.5",
        }
    )
    assert faults.for_route("lookup_customer") == Fault(10, 50, 0.01)
    # The override changes only the error rate; latency still comes from the defaults.
    assert faults.for_route("get_order") == Fault(10, 50, 0.5)


@pytest.mark.parametrize(
    "fault",
    [{"error_rate": 1.5}, {"error_rate": -0.1}, {"p50_ms": 50, "p99_ms": 10}, {"p50_ms": -1}],
)
def test_invalid_values(fault: dict[str, float]) -> None:
    with pytest.raises(ValueError, match=r"error rate|p50"):
        Fault(**fault)


def test_latency_matches_p50_and_p99() -> None:
    rng = random.Random(1)
    fault = Fault(p50_ms=20, p99_ms=200)
    samples = [fault.delay_s(rng) * 1000 for _ in range(50_000)]
    cuts = quantiles(samples, n=100)
    assert cuts[49] == pytest.approx(20, rel=0.05)
    assert cuts[98] == pytest.approx(200, rel=0.1)


def test_fixed_latency_without_p99() -> None:
    assert Fault(p50_ms=15).delay_s(random.Random(1)) == pytest.approx(0.015)


def test_error_rate() -> None:
    rng = random.Random(1)
    fault = Fault(error_rate=0.2)
    rate = sum(fault.fails(rng) for _ in range(20_000)) / 20_000
    assert rate == pytest.approx(0.2, abs=0.01)
