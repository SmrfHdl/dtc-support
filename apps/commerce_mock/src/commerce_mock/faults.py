"""Fault injection: latency and errors per route, from env (phase-0.md, Commerce mock API).

    MOCK_LATENCY_P50_MS, MOCK_LATENCY_P99_MS, MOCK_ERROR_RATE      defaults for every route
    MOCK_ERROR_RATE__GET_ORDER=0.2                                  override for one route

The suffix is the route function's name in upper case (get_order -> GET_ORDER).
Latency is log-normal with the given median and 99th percentile.
"""

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, field

P99_Z = 2.3263  # standard normal quantile at 0.99


@dataclass(frozen=True)
class Fault:
    p50_ms: float = 0.0
    p99_ms: float = 0.0
    error_rate: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.error_rate <= 1:
            raise ValueError(f"error rate must be in [0, 1], got {self.error_rate}")
        if self.p50_ms < 0 or (self.p99_ms and self.p99_ms < self.p50_ms):
            raise ValueError(f"need 0 <= p50 <= p99, got {self.p50_ms}, {self.p99_ms}")

    @property
    def active(self) -> bool:
        return self.p50_ms > 0 or self.error_rate > 0

    def delay_s(self, rng: random.Random) -> float:
        if self.p50_ms <= 0:
            return 0.0
        sigma = math.log(self.p99_ms / self.p50_ms) / P99_Z if self.p99_ms else 0.0
        return rng.lognormvariate(math.log(self.p50_ms), sigma) / 1000

    def fails(self, rng: random.Random) -> bool:
        return rng.random() < self.error_rate


_VARS = {"p50_ms": "MOCK_LATENCY_P50_MS", "p99_ms": "MOCK_LATENCY_P99_MS",
         "error_rate": "MOCK_ERROR_RATE"}  # fmt: skip


@dataclass(frozen=True)
class Faults:
    default: Fault = Fault()
    routes: Mapping[str, Fault] = field(default_factory=dict[str, Fault])

    def for_route(self, name: str | None) -> Fault:
        return self.routes.get((name or "").upper(), self.default)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Faults":
        default = Fault(**{f: float(env[v]) for f, v in _VARS.items() if v in env})
        overrides: dict[str, dict[str, float]] = {}
        for f, v in _VARS.items():
            for name, value in env.items():
                if name.startswith(v + "__"):
                    overrides.setdefault(name.removeprefix(v + "__"), {})[f] = float(value)
        routes = {
            route: Fault(**({f: getattr(default, f) for f in _VARS} | values))
            for route, values in overrides.items()
        }
        return cls(default, routes)
