"""Seeded randomness: the only source of ids, choices, and times in datagen."""

import random
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID


class Rng:
    def __init__(self, seed: int, stream: str) -> None:
        # A str seed is hashed with SHA-512, so it is stable across runs and processes.
        self._r = random.Random(f"{seed}:{stream}")

    def uuid(self) -> UUID:
        return UUID(int=self._r.getrandbits(128), version=4)

    def randint(self, lo: int, hi: int) -> int:
        """Uniform integer in [lo, hi]."""
        return self._r.randint(lo, hi)

    def chance(self, p: float) -> bool:
        return self._r.random() < p

    def choice[T](self, items: Sequence[T]) -> T:
        return self._r.choice(items)

    def weighted[T](self, items: Sequence[T], weights: Sequence[float]) -> T:
        return self._r.choices(items, weights=weights)[0]

    def sample[T](self, items: Sequence[T], k: int) -> list[T]:
        return self._r.sample(items, k)

    def between(self, start: datetime, end: datetime) -> datetime:
        """Uniform time in [start, end], to the second."""
        seconds = int((end - start).total_seconds())
        return start + timedelta(seconds=self._r.randint(0, max(seconds, 0)))
