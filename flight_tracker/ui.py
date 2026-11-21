"""A small local web page for editing your trips and checking prices.

    python -m flight_tracker.ui            # opens http://127.0.0.1:8765
    python -m flight_tracker.ui --port 9000 --no-browser

Saving writes trips.yaml. The daily GitHub Actions job reads that file, so commit and push it
after you change your trips (or run the check yourself).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from .config import _parse_dates, load_config, save_config
from .history import History
from .main import build_report
from .providers import available_providers

PAGE = Path(__file__).with_name("ui.html")


def _compact(dates: list[dt.date]):
    """Dates back to the shortest YAML form: one date, a range, or a list."""
    if not dates:
        return None
    if len(dates) == 1:
        return dates[0].isoformat()
    if (dates[-1] - dates[0]).days == len(dates) - 1:
        return f"{dates[0].isoformat()}..{dates[-1].isoformat()}"
    return [d.isoformat() for d in dates]


def to_form(raw: dict) -> dict:
    """trips.yaml entry -> the flat fields the page shows."""
    depart = _parse_dates(raw.get("depart_dates"))
    ret = _parse_dates(raw.get("return_dates"))
    lengths = raw.get("trip_length_days") or []
    lengths = lengths if isinstance(lengths, list) else [lengths]
    if ret:
        trip_type, return_mode = "round", "dates"
    elif lengths:
        trip_type, return_mode = "round", "length"
    else:
        trip_type, return_mode = "oneway", "dates"
    return {
        "name": raw.get("name", ""),
        "origin": raw.get("origin", ""),
        "destination": raw.get("destination", ""),
        "depart_from": depart[0].isoformat() if depart else "",
        "depart_to": depart[-1].isoformat() if len(depart) > 1 else "",
        "trip_type": trip_type,
        "return_mode": return_mode,
        "return_from": ret[0].isoformat() if ret else "",
        "return_to": ret[-1].isoformat() if len(ret) > 1 else "",
        "length_days": ", ".join(str(n) for n in lengths),
        "adults": raw.get("adults", 1),
        "children": raw.get("children", 0),
        "cabin": raw.get("cabin", "economy"),
        "max_stops": "" if raw.get("max_stops") is None else str(raw["max_stops"]),
        "target_price": "" if raw.get("target_price") is None else raw["target_price"],
    }


def _range(start: str, end: str):
    if not start:
        return None
    if end and end != start:
        return f"{start}..{end}"
    return start


def from_form(f: dict) -> dict:
    """Page fields -> trips.yaml entry. Validation happens in save_config."""
    origin = str(f.get("origin", "")).strip()
    dest = str(f.get("destination", "")).strip()
    raw: dict = {
        "name": str(f.get("name") or f"{origin} to {dest}").strip(),
        "origin": origin,
        "destination": dest,
        "depart_dates": _range(f.get("depart_from", ""), f.get("depart_to", "")),
    }
    if f.get("trip_type") == "round":
        if f.get("return_mode") == "length":
            days = [int(x) for x in str(f.get("length_days", "")).replace(" ", "").split(",") if x]
            if not days:
                raise ValueError(f"{raw['name']}: enter how many days you'll be away")
            raw["trip_length_days"] = days
        else:
            ret = _range(f.get("return_from", ""), f.get("return_to", ""))
            if not ret:
                raise ValueError(f"{raw['name']}: pick a return date, or choose one-way")
            raw["return_dates"] = ret
    raw["adults"] = int(f.get("adults") or 1)
    if int(f.get("children") or 0):
        raw["children"] = int(f["children"])
    raw["cabin"] = f.get("cabin") or "economy"
    if str(f.get("max_stops", "")) != "":
        raw["max_stops"] = int(f["max_stops"])
    if str(f.get("target_price", "")).strip() != "":
        raw["target_price"] = float(f["target_price"])
    if not raw["depart_dates"]:
        raise ValueError(f"{raw['name']}: pick a departure date")
    return raw


class Handler(BaseHTTPRequestHandler):
    config_path = Path("trips.yaml")
    history_path = Path("data/history.json")

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/trips":
            data = yaml.safe_load(self.config_path.read_text()) if self.config_path.exists() else {}
            data = data or {}
            self._json({
                "currency": data.get("currency", "USD"),
                "max_searches_per_run": data.get("max_searches_per_run", 3),
                "trips": [to_form(t) for t in data.get("trips") or []],
                "has_api_key": bool(os.environ.get("SERPAPI_KEY")),
            })
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        # only accept requests from this page, not from other sites open in the browser
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self._json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
        try:
            body = self._body()
            if self.path == "/api/trips":
                trips = [from_form(t) for t in body.get("trips", [])]
                if not trips:
                    raise ValueError("Add at least one trip")
                cfg = save_config(self.config_path, body.get("currency", "USD"),
                                  body.get("max_searches_per_run", 3), trips)
                total = sum(len(t.searches(dt.date.today())) for t in cfg.trips)
                return self._json({"ok": True, "combinations": total})
            if self.path == "/api/check":
                use_mock = bool(body.get("mock")) or not os.environ.get("SERPAPI_KEY")
                cfg = load_config(self.config_path)
                # a preview: don't write to history, so the daily "vs last check" stays meaningful
                msg = build_report(cfg, available_providers(use_mock), History(self.history_path),
                                   dt.date.today(), record=False)
                return self._json({"ok": True, "mock": use_mock, "message": msg})
            self._send(404, b"Not found", "text/plain")
        except (ValueError, TypeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": f"Something went wrong: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    p = argparse.ArgumentParser(description="Edit your trips in the browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--config", default="trips.yaml")
    p.add_argument("--history", default="data/history.json")
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    Handler.config_path, Handler.history_path = Path(args.config), Path(args.history)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Flight tracker is open at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
