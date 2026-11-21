from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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
    tokens: dict = field(default_factory=dict)  # provider data needed to follow this offer to booking
    exact_link: bool = False  # True when link opens this exact itinerary, not just the search


class Provider(ABC):
    name: str = "provider"

    @abstractmethod
    def search(self, trip: Trip, search: Search, currency: str) -> list[Offer]:
        """Return offers for one date combination. Raise on API errors."""

    def booking_link(self, trip: Trip, search: Search, offer: Offer, currency: str) -> str | None:
        """A link to book this exact itinerary, if the provider can make one. May cost extra searches."""
        return None
