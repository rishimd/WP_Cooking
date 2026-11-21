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

    @property
    def key(self) -> str:
        return f"{self.depart.isoformat()}_{self.ret.isoformat() if self.ret else 'oneway'}"


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

    @property
    def one_way(self) -> bool:
        return not self.return_dates and not self.trip_length_days

    def searches(self, today: dt.date) -> list[Search]:
        """All future date combinations for this trip, in date order."""
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


def _parse_trip(raw: dict) -> Trip:
    for key in ("name", "origin", "destination", "depart_dates"):
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
        origin=_airports(raw["origin"]),
        destination=_airports(raw["destination"]),
        depart_dates=_parse_dates(raw["depart_dates"]),
        return_dates=_parse_dates(raw.get("return_dates")),
        trip_length_days=[int(n) for n in lengths],
        adults=int(raw.get("adults", 1)),
        children=int(raw.get("children", 0)),
        cabin=cabin,
        max_stops=None if raw.get("max_stops") is None else int(raw["max_stops"]),
        target_price=None if raw.get("target_price") is None else float(raw["target_price"]),
    )


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    trips = [_parse_trip(t) for t in data.get("trips") or []]
    if not trips:
        raise ValueError(f"No trips defined in {path}")
    return Config(
        currency=str(data.get("currency", "USD")).upper(),
        max_searches_per_run=int(data.get("max_searches_per_run", 3)),
        trips=trips,
    )


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


HEADER = """\
# Your travel plan. Edit it in the web page (python -m flight_tracker.ui) or by hand.
# depart_dates / return_dates: a date, a range "YYYY-MM-DD..YYYY-MM-DD", or a list.
# Leave out return_dates and trip_length_days for one-way. See README.md.
"""


def save_config(path: str | Path, currency: str, max_searches_per_run: int, trips: list[dict]) -> Config:
    """Validate raw trip dicts and write them to trips.yaml. Raises ValueError if invalid."""
    data = {"currency": currency.upper(), "max_searches_per_run": int(max_searches_per_run), "trips": trips}
    parsed = Config(data["currency"], data["max_searches_per_run"], [_parse_trip(t) for t in trips])
    names = [t.name for t in parsed.trips]
    if len(set(names)) != len(names):
        raise ValueError("Each trip needs a different name")
    Path(path).write_text(HEADER + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return parsed
