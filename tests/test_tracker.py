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



def test_duplicate_names_rejected():
    import pytest
    from flight_tracker.config import parse_config
    trip = {"name": "A", "origin": "JFK", "destination": "DEL", "depart_dates": "2027-01-10"}
    with pytest.raises(ValueError, match="different name"):
        parse_config({"trips": [trip, dict(trip)]})
    with pytest.raises(ValueError, match="3 letters"):
        parse_config({"trips": [dict(trip, origin="New York")]})
    assert parse_config({"trips": [dict(trip, origin="jfk, ewr")]}).trips[0].origin == "JFK,EWR"


def test_local_server_validates_before_saving(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from flight_tracker.ui import Handler

    cfg = tmp_path / "trips.yaml"
    cfg.write_text("trips: [{name: A, origin: JFK, destination: DEL, depart_dates: '2027-01-10'}]\n")
    Handler.config_path, Handler.history_path = cfg, tmp_path / "h.json"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def put(body):
        req = urllib.request.Request(base + "/api/file", data=json.dumps(body).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        assert put({"path": "trips.yaml", "text": "trips: [{name: B}]"}) == 400       # invalid: kept old file
        assert "name: A" in cfg.read_text()
        assert put({"path": "data/history.json", "text": "{}"}) == 403               # only trips.yaml writable
        good = "trips: [{name: B, origin: SFO, destination: BOM, depart_dates: '2027-02-01'}]\n"
        assert put({"path": "trips.yaml", "text": good}) == 200 and cfg.read_text() == good
        r = json.load(urllib.request.urlopen(base + "/api/file?path=trips.yaml"))
        assert r["text"] == good
    finally:
        srv.shutdown()


MULTI = {"name": "Tour", "legs": [
    {"from": "JFK", "to": "DEL", "dates": "2027-01-10..2027-01-11"},
    {"from": "DEL", "to": "BOM", "dates": ["2027-01-11", "2027-01-18"]},
    {"from": "BOM", "to": "JFK", "dates": "2027-01-28"}], "adults": 2}


def test_multi_city_searches_keep_flights_in_order():
    from flight_tracker.config import parse_config
    trip = parse_config({"trips": [MULTI]}).trips[0]
    assert trip.route == "JFK → DEL → BOM → JFK" and not trip.one_way
    keys = [s.key for s in trip.searches(TODAY)]
    # 2 x 2 x 1 = 4, all in order (Jan 11 -> Jan 11 is allowed: same-day connection)
    assert keys == ["2027-01-10_2027-01-11_2027-01-28", "2027-01-10_2027-01-18_2027-01-28",
                    "2027-01-11_2027-01-11_2027-01-28", "2027-01-11_2027-01-18_2027-01-28"]
    bad = dict(MULTI, legs=[MULTI["legs"][0], {"from": "DEL", "to": "BOM", "dates": "2027-01-01"}])
    assert parse_config({"trips": [bad]}).trips[0].searches(TODAY) == []


def test_multi_city_validation():
    import pytest
    from flight_tracker.config import parse_config
    with pytest.raises(ValueError, match="at least 2"):
        parse_config({"trips": [dict(MULTI, legs=MULTI["legs"][:1])]})
    with pytest.raises(ValueError, match="flight 2"):
        parse_config({"trips": [dict(MULTI, legs=[MULTI["legs"][0], {"from": "DEL"}])]})


def test_multi_city_serpapi_params():
    from flight_tracker.config import parse_config
    trip = parse_config({"trips": [MULTI]}).trips[0]
    p = SerpApiProvider("k").build_params(trip, trip.searches(TODAY)[0], "USD")
    assert p["type"] == 3 and "outbound_date" not in p and "departure_id" not in p
    assert json.loads(p["multi_city_json"]) == [
        {"departure_id": "JFK", "arrival_id": "DEL", "date": "2027-01-10"},
        {"departure_id": "DEL", "arrival_id": "BOM", "date": "2027-01-11"},
        {"departure_id": "BOM", "arrival_id": "JFK", "date": "2027-01-28"}]


def test_multi_city_message(tmp_path, monkeypatch, capsys):
    import yaml
    cfg = tmp_path / "trips.yaml"
    cfg.write_text(yaml.safe_dump({"max_searches_per_run": 3, "trips": [MULTI]}))
    monkeypatch.setenv("SERPAPI_KEY", "x")
    monkeypatch.setattr(SerpApiProvider, "search", lambda self, t, s, c: parse_response(SAMPLE, c))
    hist = tmp_path / "h.json"
    assert run(str(cfg), str(hist), dry_run=True, use_mock=False, today=TODAY) == 0
    out = capsys.readouterr().out
    assert "JFK → DEL → BOM → JFK" in out and "total for all flights" in out
    assert "Sun Jan 10: JFK → DEL" in out and "Thu Jan 28: BOM → JFK" in out
    assert "Checked 3 of 4" in out
    assert History(hist).data["Tour"]["daily"][0]["flights"][2] == {"from": "BOM", "to": "JFK", "date": "2027-01-28"}


def test_runs_without_telegram(tmp_path, monkeypatch, capsys):
    """No Telegram secrets: still succeeds, prints results, writes the Actions summary and history."""
    cfg = tmp_path / "trips.yaml"
    cfg.write_text("trips: [{name: Home, origin: JFK, destination: DEL, depart_dates: '2027-01-10', trip_length_days: [14]}]\n")
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "x")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(SerpApiProvider, "search", lambda self, t, s, c: parse_response(SAMPLE, c))
    hist = tmp_path / "h.json"
    assert run(str(cfg), str(hist), dry_run=False, use_mock=False, today=TODAY) == 0
    out = capsys.readouterr().out
    assert "not set up" in out and "$640" in out and "<b>" not in out
    md = summary.read_text()
    assert "**$640**" in md and "[Open this search on Google Flights](https://" in md
    assert History(hist).last_price("Home") == 640


class FakeSerpApi:
    """Stands in for requests.Session: answers by which token the request carries."""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params, timeout):
        key = params.get("booking_token") or params.get("departure_token") or "search"
        self.calls.append(key)
        body = json.dumps(self.pages[key]).encode()
        return type("R", (), {"status_code": 200, "content": body, "json": lambda self: json.loads(body),
                              "text": body.decode()})()


def flights(price, stops=0, **tokens):
    return {"price": price, "layovers": [{}] * stops, "flights": [{"airline": "X", "flight_number": "X 1"}], **tokens}


def test_round_trip_follows_tokens_to_exact_booking_page():
    trip = make_trip(trip_length_days=[14], max_stops=1)
    search = trip.searches(TODAY)[0]
    fake = FakeSerpApi({
        "search": {"search_metadata": {"google_flights_url": "https://g/search"},
                   "other_flights": [flights(700, departure_token="dep1")]},
        # return flights: the cheapest has 2 stops (over max_stops), so the $520 one is chosen
        "dep1": {"other_flights": [flights(500, stops=2, booking_token="nope"), flights(520, booking_token="book1")]},
        "book1": {"search_metadata": {"google_flights_url": "https://g/booking/exact"}},
    })
    api = SerpApiProvider("k", session=fake)
    offer = api.search(trip, search, "USD")[0]
    assert offer.link == "https://g/search" and offer.tokens == {"departure_token": "dep1"}
    assert api.booking_link(trip, search, offer, "USD") == "https://g/booking/exact"
    assert fake.calls == ["search", "dep1", "book1"]


def test_multi_city_needs_one_hop_per_later_flight():
    from flight_tracker.config import parse_config
    trip = parse_config({"trips": [MULTI]}).trips[0]       # 3 flights -> 2 hops + booking
    search = trip.searches(TODAY)[0]
    fake = FakeSerpApi({
        "search": {"other_flights": [flights(900, departure_token="a")]},
        "a": {"other_flights": [flights(900, departure_token="b")]},
        "b": {"other_flights": [flights(900, booking_token="c")]},
        "c": {"search_metadata": {"google_flights_url": "https://g/booking/multi"}},
    })
    api = SerpApiProvider("k", session=fake)
    offer = api.search(trip, search, "USD")[0]
    assert api.booking_link(trip, search, offer, "USD") == "https://g/booking/multi"
    assert fake.calls == ["search", "a", "b", "c"]


def test_fallback_search_link_when_serpapi_gives_none():
    from urllib.parse import parse_qs, urlparse
    trip = make_trip(origin="JFK,EWR", trip_length_days=[14])
    fake = FakeSerpApi({"search": {"other_flights": [flights(700)]}})
    offer = SerpApiProvider("k", session=fake).search(trip, trip.searches(TODAY)[0], "USD")[0]
    q = parse_qs(urlparse(offer.link).query)["q"][0]
    assert q == "Flights from JFK to DEL on 2027-01-10 through 2027-01-24"


def test_booking_link_only_on_good_days(tmp_path, monkeypatch, capsys):
    """Default 'deals': exact link on a new low or under target; the search link otherwise."""
    cfg = tmp_path / "trips.yaml"
    cfg.write_text("trips: [{name: Home, origin: JFK, destination: DEL, depart_dates: '2027-01-10', "
                   "trip_length_days: [14], target_price: 600}]\n")
    monkeypatch.setenv("SERPAPI_KEY", "x")
    hist = tmp_path / "h.json"
    price = {"v": 700}
    monkeypatch.setattr(SerpApiProvider, "search", lambda self, t, s, c: [
        type(parse_response(SAMPLE, c)[0])(price=price["v"], currency=c, airlines=["X"], flight_numbers=[],
                                           stops=0, duration_minutes=None, depart_time=None, source="g",
                                           link="https://g/search")])
    asked = []
    monkeypatch.setattr(SerpApiProvider, "booking_link", lambda self, t, s, o, c: asked.append(1) or "https://g/exact")

    def day(n, p):
        price["v"] = p
        run(str(cfg), str(hist), dry_run=True, use_mock=False, today=TODAY + dt.timedelta(days=n))
        return capsys.readouterr().out
    assert "Book this exact flight" in day(0, 700)          # first price seen = new low
    out = day(1, 750)                                        # pricier, above target: no extra searches
    assert "Open this search on Google Flights" in out and len(asked) == 1
    assert "Book this exact flight" in day(2, 590)           # under target
    assert len(asked) == 2
