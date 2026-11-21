from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import Search, Trip


@dataclass
class Offer:
    price: float
    currency: str
    airlines: list[str]
    flight_numbers: list[str]
    stops: int
    duration_minutes: int | None
    depart_time: str | None
    source: str
    link: str | None = None
    price_level: str | None = None  # e.g. "low" / "typical" / "high" from Google Flights


class Provider(ABC):
    name: str = "provider"

    @abstractmethod
    def search(self, trip: Trip, search: Search, currency: str) -> list[Offer]:
        """Return offers for one date combination. Raise on API errors."""
