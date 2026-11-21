"""Google Flights prices via SerpApi (https://serpapi.com/google-flights-api)."""

from __future__ import annotations

import requests

from ..config import Search, Trip
from .base import Offer, Provider

API_URL = "https://serpapi.com/search.json"
TRAVEL_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
# SerpApi "stops": 0 any, 1 nonstop only, 2 one stop or fewer, 3 two stops or fewer
STOPS = {0: 1, 1: 2, 2: 3}


class SerpApiProvider(Provider):
    name = "Google Flights"

    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.api_key = api_key
        self.session = session or requests.Session()

    def build_params(self, trip: Trip, search: Search, currency: str) -> dict:
        params = {
            "engine": "google_flights",
            "departure_id": trip.origin,
            "arrival_id": trip.destination,
            "outbound_date": search.depart.isoformat(),
            "type": 2 if search.ret is None else 1,
            "currency": currency,
            "adults": trip.adults,
            "travel_class": TRAVEL_CLASS[trip.cabin],
            "stops": STOPS.get(trip.max_stops, 0) if trip.max_stops is not None else 0,
            "hl": "en",
            "api_key": self.api_key,
        }
        if search.ret is not None:
            params["return_date"] = search.ret.isoformat()
        return params

    def search(self, trip: Trip, search: Search, currency: str) -> list[Offer]:
        resp = self.session.get(API_URL, params=self.build_params(trip, search, currency), timeout=60)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "error" in data:
            msg = data.get("error") or resp.text[:200]
            # "no results" is a normal answer, not a failure
            if "hasn't returned any results" in str(msg):
                return []
            raise RuntimeError(f"SerpApi error ({resp.status_code}): {msg}")
        return parse_response(data, currency)


def parse_response(data: dict, currency: str) -> list[Offer]:
    link = (data.get("search_metadata") or {}).get("google_flights_url")
    price_level = (data.get("price_insights") or {}).get("price_level")
    offers: list[Offer] = []
    for group in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        price = group.get("price")
        segments = group.get("flights") or []
        if price is None or not segments:
            continue
        airlines: list[str] = []
        for seg in segments:
            if seg.get("airline") and seg["airline"] not in airlines:
                airlines.append(seg["airline"])
        offers.append(
            Offer(
                price=float(price),
                currency=currency,
                airlines=airlines,
                flight_numbers=[s["flight_number"] for s in segments if s.get("flight_number")],
                stops=len(group.get("layovers") or []),
                duration_minutes=group.get("total_duration"),
                depart_time=(segments[0].get("departure_airport") or {}).get("time"),
                source=SerpApiProvider.name,
                link=link,
                price_level=price_level,
            )
        )
    return offers
