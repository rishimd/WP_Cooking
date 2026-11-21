"""Price history stored as JSON in the repo, so the daily job can compare against yesterday."""

from __future__ import annotations

import json
from pathlib import Path


class History:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _trip(self, trip_name: str) -> dict:
        return self.data.setdefault(trip_name, {"lowest_ever": None, "daily": []})

    def last_price(self, trip_name: str) -> float | None:
        daily = self.data.get(trip_name, {}).get("daily") or []
        return daily[-1]["price"] if daily else None

    def lowest_ever(self, trip_name: str) -> dict | None:
        return self.data.get(trip_name, {}).get("lowest_ever")

    def record(self, trip_name: str, day: str, price: float, details: dict) -> None:
        t = self._trip(trip_name)
        entry = {"date": day, "price": price, **details}
        # one entry per day: re-running on the same day replaces it
        t["daily"] = [e for e in t["daily"] if e["date"] != day] + [entry]
        t["daily"] = t["daily"][-120:]
        if t["lowest_ever"] is None or price < t["lowest_ever"]["price"]:
            t["lowest_ever"] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
