"""Load trips.yaml and expand each trip into concrete (depart, return) searches."""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CABINS = ("economy", "premium_economy", "business", "first")


@dataclass(frozen=True)
class Search:
    depart: dt.date
    ret: dt.date | None
    leg_dates: tuple[dt.date, ...] = ()   # multi-city: one date per flight

    @property
    def key(self) -> str:
        if self.leg_dates:
            return "_".join(d.isoformat() for d in self.leg_dates)
        return f"{self.depart.isoformat()}_{self.ret.isoformat() if self.ret else 'oneway'}"


@dataclass
class Leg:
    origin: str
    destination: str
    dates: list[dt.date]


@dataclass
class Trip:
    name: str
    origin: str
    destination: str
    depart_dates: list[dt.date]
    return_dates: list[dt.date] = field(default_factory=list)
    trip_length_days: list[int] = field(default_factory=list)
    adults: int = 1
    children: int = 0
    cabin: str = "economy"
    max_stops: int | None = None
    target_price: float | None = None
    legs: list[Leg] = field(default_factory=list)  # set for multi-city trips

    @property
    def one_way(self) -> bool:
        return not self.legs and not self.return_dates and not self.trip_length_days

    @property
    def route(self) -> str:
        if self.legs:
            return " → ".join([self.legs[0].origin] + [leg.destination for leg in self.legs])
        return f"{self.origin} → {self.destination}"

    def searches(self, today: dt.date) -> list[Search]:
        """All future date combinations for this trip, in date order."""
        if self.legs:
            combos = itertools.product(*(leg.dates for leg in self.legs))
            return [Search(c[0], None, tuple(c)) for c in combos
                    if c[0] >= today and all(a <= b for a, b in zip(c, c[1:]))]
        out: list[Search] = []
        for d in self.depart_dates:
            if d < today:
                continue
            if self.one_way:
                out.append(Search(d, None))
                continue
            rets = list(self.return_dates) + [d + dt.timedelta(days=n) for n in self.trip_length_days]
            out.extend(Search(d, r) for r in sorted(set(rets)) if r > d)
        return out


@dataclass
class Config:
    currency: str
    max_searches_per_run: int
    trips: list[Trip]


def _parse_dates(value) -> list[dt.date]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    dates: set[dt.date] = set()
    for item in items:
        if isinstance(item, dt.date):
            dates.add(item)
            continue
        s = str(item).strip()
        if ".." in s:
            start_s, end_s = (p.strip() for p in s.split("..", 1))
            start, end = dt.date.fromisoformat(start_s), dt.date.fromisoformat(end_s)
            if end < start:
                raise ValueError(f"Date range ends before it starts: {s}")
            dates.update(start + dt.timedelta(days=i) for i in range((end - start).days + 1))
        else:
            dates.add(dt.date.fromisoformat(s))
    return sorted(dates)


def _airports(value) -> str:
    """'jfk, ewr' -> 'JFK,EWR'. Google Flights accepts several comma-separated airports."""
    codes = [c.strip().upper() for c in str(value).split(",") if c.strip()]
    bad = [c for c in codes if not (len(c) == 3 and c.isalpha())]
    if not codes or bad:
        raise ValueError(f"Airport codes must be 3 letters, e.g. JFK or JFK,EWR (got '{value}')")
    return ",".join(codes)


def _parse_legs(raw: dict) -> list[Leg]:
    legs = raw.get("legs")
    if not isinstance(legs, list) or len(legs) < 2:
        raise ValueError(f"Trip '{raw.get('name')}': a multi-city trip needs at least 2 flights")
    if len(legs) > 6:
        raise ValueError(f"Trip '{raw.get('name')}': Google Flights allows at most 6 flights")
    out = []
    for i, leg in enumerate(legs, 1):
        if not isinstance(leg, dict) or not all(leg.get(k) for k in ("from", "to", "dates")):
            raise ValueError(f"Trip '{raw.get('name')}': flight {i} needs from, to and dates")
        out.append(Leg(_airports(leg["from"]), _airports(leg["to"]), _parse_dates(leg["dates"])))
    return out  # flight order (each date on or after the previous) is enforced in Trip.searches


def _parse_trip(raw: dict) -> Trip:
    if not raw.get("name"):
        raise ValueError(f"Trip is missing 'name': {raw}")
    legs = _parse_legs(raw) if raw.get("legs") is not None else []
    required = () if legs else ("origin", "destination", "depart_dates")
    for key in required:
        if not raw.get(key):
            raise ValueError(f"Trip is missing '{key}': {raw}")
    cabin = str(raw.get("cabin", "economy")).lower()
    if int(raw.get("adults", 1)) < 1:
        raise ValueError(f"Trip '{raw['name']}': at least 1 adult is required")
    if cabin not in CABINS:
        raise ValueError(f"Trip '{raw['name']}': cabin must be one of {CABINS}")
    lengths = raw.get("trip_length_days") or []
    if not isinstance(lengths, list):
        lengths = [lengths]
    return Trip(
        name=str(raw["name"]),
        origin=legs[0].origin if legs else _airports(raw["origin"]),
        destination=legs[-1].destination if legs else _airports(raw["destination"]),
        depart_dates=legs[0].dates if legs else _parse_dates(raw["depart_dates"]),
        return_dates=_parse_dates(raw.get("return_dates")),
        trip_length_days=[int(n) for n in lengths],
        adults=int(raw.get("adults", 1)),
        children=int(raw.get("children", 0)),
        cabin=cabin,
        max_stops=None if raw.get("max_stops") is None else int(raw["max_stops"]),
        target_price=None if raw.get("target_price") is None else float(raw["target_price"]),
        legs=legs,
    )


def parse_config(data: dict | None, source: str = "trips.yaml") -> Config:
    data = data or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source} should be a mapping with a 'trips' list")
    trips = [_parse_trip(t) for t in data.get("trips") or []]
    if not trips:
        raise ValueError(f"No trips defined in {source}")
    names = [t.name for t in trips]
    if len(set(names)) != len(names):
        raise ValueError("Each trip needs a different name")
    return Config(
        currency=str(data.get("currency", "USD")).upper(),
        max_searches_per_run=int(data.get("max_searches_per_run", 3)),
        trips=trips,
    )


def load_config(path: str | Path) -> Config:
    return parse_config(yaml.safe_load(Path(path).read_text()), str(path))


def plan_searches(config: Config, today: dt.date) -> list[tuple[Trip, Search]]:
    """Pick today's searches within budget, rotating through every combination over the days."""
    all_pairs = [(trip, s) for trip in config.trips for s in trip.searches(today)]
    budget = config.max_searches_per_run
    if budget <= 0 or len(all_pairs) <= budget:
        return all_pairs
    start = (today.toordinal() * budget) % len(all_pairs)
    rotated = list(itertools.islice(itertools.cycle(all_pairs), start, start + budget))
    # keep a stable order for the message
    order = {id(p): i for i, p in enumerate(all_pairs)}
    return sorted(rotated, key=lambda p: order[id(p)])

