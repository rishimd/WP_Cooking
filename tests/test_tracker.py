import datetime as dt
import json

from flight_tracker.config import Config, Trip, load_config, plan_searches, _parse_dates
from flight_tracker.history import History
from flight_tracker.main import run
from flight_tracker.providers.serpapi import SerpApiProvider, parse_response

TODAY = dt.date(2026, 11, 21)


def make_trip(**kw):
    base = dict(name="T", origin="JFK", destination="DEL",
                depart_dates=_parse_dates("2027-01-10..2027-01-12"))
    base.update(kw)
    return Trip(**base)


def test_date_range_and_list():
    assert _parse_dates(["2027-01-01..2027-01-03", "2027-01-10"]) == [
        dt.date(2027, 1, 1), dt.date(2027, 1, 2), dt.date(2027, 1, 3), dt.date(2027, 1, 10)]


def test_trip_length_round_trip_and_past_dates_skipped():
    trip = make_trip(depart_dates=[dt.date(2026, 11, 1), dt.date(2027, 1, 10)], trip_length_days=[7, 14])
    keys = [s.key for s in trip.searches(TODAY)]
    assert keys == ["2027-01-10_2027-01-17", "2027-01-10_2027-01-24"]


def test_one_way():
    trip = make_trip()
    assert trip.one_way and all(s.ret is None for s in trip.searches(TODAY))


def test_rotation_covers_everything_within_budget():
    cfg = Config("USD", 2, [make_trip(trip_length_days=[7, 10])])  # 6 combinations
    seen = set()
    for i in range(3):
        picked = plan_searches(cfg, TODAY + dt.timedelta(days=i))
        assert len(picked) == 2
        seen.update(s.key for _, s in picked)
    assert len(seen) == 6


def test_serpapi_params():
    p = SerpApiProvider("k").build_params(
        make_trip(trip_length_days=[7], max_stops=0, cabin="business"),
        make_trip(trip_length_days=[7]).searches(TODAY)[0], "USD")
    assert p["type"] == 1 and p["return_date"] == "2027-01-17"
    assert p["stops"] == 1 and p["travel_class"] == 3


SAMPLE = {
    "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x"},
    "price_insights": {"price_level": "low"},
    "best_flights": [{
        "price": 812, "total_duration": 980,
        "layovers": [{"id": "LHR"}],
        "flights": [
            {"airline": "Air India", "flight_number": "AI 102", "departure_airport": {"time": "2027-01-10 20:00"}},
            {"airline": "Air India", "flight_number": "AI 161"},
        ],
    }],
    "other_flights": [{"price": 640, "layovers": [{}, {}],
                       "flights": [{"airline": "Cheap Air", "flight_number": "CA 1"}]}],
}


def test_parse_response():
    offers = parse_response(SAMPLE, "USD")
    assert [o.price for o in offers] == [812, 640]
    assert offers[0].airlines == ["Air India"] and offers[0].stops == 1
    assert offers[0].link.startswith("https://") and offers[0].price_level == "low"


def test_end_to_end_with_fake_api(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "trips.yaml"
    cfg.write_text("""
currency: USD
max_searches_per_run: 3
trips:
  - name: Home
    origin: JFK
    destination: DEL
    depart_dates: ["2027-01-10"]
    trip_length_days: [14]
    max_stops: 1
    target_price: 900
""")
    monkeypatch.setenv("SERPAPI_KEY", "x")
    monkeypatch.setattr(SerpApiProvider, "search", lambda self, t, s, c: parse_response(SAMPLE, c))
    hist = tmp_path / "history.json"
    hist.write_text(json.dumps({"Home": {"lowest_ever": {"date": "2026-11-20", "price": 850},
                                         "daily": [{"date": "2026-11-20", "price": 850}]}}))

    assert run(str(cfg), str(hist), dry_run=True, use_mock=False, today=TODAY) == 0
    out = capsys.readouterr().out
    # the $640 option has 2 stops, so it is filtered out by max_stops: 1
    assert "$812" in out and "$640" not in out
    assert "🔻 $38 vs last check" in out and "lowest price seen so far" in out
    assert "Below your target" in out
    saved = History(hist)
    assert saved.last_price("Home") == 812 and saved.lowest_ever("Home")["price"] == 812


def test_example_config_loads():
    assert load_config("trips.yaml").trips


def test_ui_form_round_trip(tmp_path):
    from flight_tracker.config import save_config
    from flight_tracker.ui import from_form, to_form
    form = {"name": "Family", "origin": "jfk, ewr", "destination": "DEL", "depart_from": "2027-01-10",
            "depart_to": "2027-01-12", "trip_type": "round", "return_mode": "length", "length_days": "10, 14",
            "adults": "2", "children": "1", "cabin": "economy", "max_stops": "", "target_price": ""}
    raw = from_form(form)
    assert raw["depart_dates"] == "2027-01-10..2027-01-12" and raw["trip_length_days"] == [10, 14]
    assert "max_stops" not in raw and raw["children"] == 1
    cfg = save_config(tmp_path / "t.yaml", "inr", 3, [raw])
    assert cfg.trips[0].origin == "JFK,EWR" and len(cfg.trips[0].searches(TODAY)) == 6
    back = to_form(raw)
    assert back["depart_to"] == "2027-01-12" and back["return_mode"] == "length" and back["length_days"] == "10, 14"


def test_ui_rejects_bad_input(tmp_path):
    import pytest
    from flight_tracker.config import save_config
    from flight_tracker.ui import from_form
    with pytest.raises(ValueError):
        from_form({"name": "X", "origin": "JFK", "destination": "DEL", "depart_from": "2027-01-10",
                   "trip_type": "round", "return_mode": "dates"})  # no return date
    with pytest.raises(ValueError):
        save_config(tmp_path / "t.yaml", "USD", 3, [{"name": "X", "origin": "New York",
                                                     "destination": "DEL", "depart_dates": "2027-01-10"}])
