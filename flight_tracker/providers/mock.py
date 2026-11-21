"""Fake prices for trying the tracker without an API key (--mock)."""

from __future__ import annotations

import datetime as dt
import random

from ..config import Search, Trip
from .base import Offer, Provider


class MockProvider(Provider):
    name = "Mock"

    def search(self, trip: Trip, search: Search, currency: str) -> list[Offer]:
        rng = random.Random(f"{trip.name}{search.key}{dt.date.today()}")
        base = 400 + rng.randint(0, 600)
        return [
            Offer(
                price=float(base + rng.randint(0, 300)),
                currency=currency,
                airlines=[rng.choice(["Air Demo", "Sample Air", "Test Airways"])],
                flight_numbers=[f"XX {rng.randint(100, 999)}"],
                stops=rng.randint(0, 2),
                duration_minutes=rng.randint(300, 1200),
                depart_time=f"{search.depart.isoformat()} {rng.randint(0, 23):02d}:{rng.choice(['00', '30'])}",
                source=self.name,
            )
            for _ in range(3)
        ]
