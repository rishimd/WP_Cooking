"""Google Flights prices via SerpApi (https://serpapi.com/google-flights-api)."""

from __future__ import annotations

import json
from urllib.parse import urlencode

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
            "sort_by": 2,  # cheapest first
            "currency": currency,
            "adults": trip.n_adults,          # includes children 12+, who pay adult fares
            "children": trip.n_children,      # 2-11
            "infants_in_seat": trip.n_infants if trip.infants_in_seat else 0,
            "infants_on_lap": 0 if trip.infants_in_seat else trip.n_infants,
            "travel_class": TRAVEL_CLASS[trip.cabin],
            "stops": STOPS.get(trip.max_stops, 0) if trip.max_stops is not None else 0,
            "hl": "en",
            "api_key": self.api_key,
        }
        if search.ret is not None:
            params["return_date"] = search.ret.isoformat()
        if trip.legs:
            # multi-city: Google Flights lists options for the first flight, priced for the whole itinerary
            for key in ("departure_id", "arrival_id", "outbound_date", "return_date"):
                params.pop(key, None)
            params["type"] = 3
            params["multi_city_json"] = json.dumps([
                {"departure_id": leg.origin, "arrival_id": leg.destination, "date": d.isoformat()}
                for leg, d in zip(trip.legs, search.leg_dates)
            ])
        return params

    def _get(self, params: dict) -> dict:
        resp = self.session.get(API_URL, params=params, timeout=60)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "error" in data:
            raise RuntimeError(f"SerpApi error ({resp.status_code}): {data.get('error') or resp.text[:200]}")
        return data

    def booking_link(self, trip: Trip, search: Search, offer: Offer, currency: str) -> str | None:
        """Follow Google Flights' steps to the booking page for this exact itinerary.

        One-way: 1 extra search. Round trip: 2 (pick the return flight, then booking).
        Multi-city: one per later flight, plus 1 for booking.
        """
        params = self.build_params(trip, search, currency)
        tokens = offer.tokens
        hops = len(trip.legs) - 1 if trip.legs else (1 if search.ret else 0)
        for _ in range(hops):
            if not tokens.get("departure_token"):
                return None
            data = self._get({**params, "departure_token": tokens["departure_token"]})
            choices = [o for o in parse_response(data, currency)
                       if trip.max_stops is None or o.stops <= trip.max_stops]
            if not choices:
                return None
            tokens = min(choices, key=lambda o: o.price).tokens
        if not tokens.get("booking_token"):
            return None
        data = self._get({**params, "booking_token": tokens["booking_token"]})
        return (data.get("search_metadata") or {}).get("google_flights_url")

    def search(self, trip: Trip, search: Search, currency: str) -> list[Offer]:
        resp = self.session.get(API_URL, params=self.build_params(trip, search, currency), timeout=60)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "error" in data:
            msg = data.get("error") or resp.text[:200]
            # "no results" is a normal answer, not a failure
            if "hasn't returned any results" in str(msg):
                return []
            raise RuntimeError(f"SerpApi error ({resp.status_code}): {msg}")
        offers = parse_response(data, currency)
        for o in offers:
            o.link = o.link or search_url(trip, search)
        return offers


def search_url(trip: Trip, search: Search) -> str | None:
    """Fallback Google Flights link from a plain-language query, if SerpApi gave none."""
    if trip.legs:
        return None
    first = lambda codes: codes.split(",")[0]
    q = f"Flights from {first(trip.origin)} to {first(trip.destination)} on {search.depart.isoformat()}"
    q += f" through {search.ret.isoformat()}" if search.ret else " one way"
    return "https://www.google.com/travel/flights?" + urlencode({"q": q})


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
                tokens={k: group[k] for k in ("departure_token", "booking_token") if group.get(k)},
            )
        )
    return offers
