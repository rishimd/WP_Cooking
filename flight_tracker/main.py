"""Run the daily check: search, keep the cheapest per trip, update history, send the alert.

    python -m flight_tracker.main                 # real run (needs SERPAPI_KEY, TELEGRAM_* env vars)
    python -m flight_tracker.main --dry-run       # print the message instead of sending it
    python -m flight_tracker.main --mock --dry-run  # try it with fake prices, no keys needed
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import sys

from .config import Config, Search, Trip, load_config, plan_searches
from .history import History
from .notify import send_telegram
from .providers import Offer, Provider, available_providers


def fmt_money(amount: float, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "C$", "AUD": "A$"}.get(currency)
    return f"{symbol}{amount:,.0f}" if symbol else f"{amount:,.0f} {currency}"


def fmt_duration(minutes: int | None) -> str:
    return "" if not minutes else f"{minutes // 60}h {minutes % 60:02d}m"


def fmt_date(d: dt.date) -> str:
    return d.strftime("%a %b %-d")


def stops_label(n: int) -> str:
    return "nonstop" if n == 0 else f"{n} stop" + ("s" if n > 1 else "")


def trip_block(trip: Trip, search: Search, offer: Offer | None, history: History,
               currency: str, errors: list[str]) -> str:
    title = f"<b>{html.escape(trip.name)}</b>  ({trip.route})"
    if offer is None:
        reason = "; ".join(errors) if errors else "no flights found for today's dates"
        return f"{title}\n⚠️ {html.escape(reason)}"

    if trip.legs:
        when = "\n".join(f"   {fmt_date(d)}: {leg.origin} → {leg.destination}"
                         for leg, d in zip(trip.legs, search.leg_dates))
    else:
        when = fmt_date(search.depart) + (f" → {fmt_date(search.ret)}" if search.ret else " (one-way)")
    airline = ", ".join(offer.airlines) or "Unknown airline"
    flights = " / ".join(offer.flight_numbers[:4])
    details = " · ".join(x for x in [stops_label(offer.stops), fmt_duration(offer.duration_minutes)] if x)
    price_line = f"💰 <b>{fmt_money(offer.price, currency)}</b>"
    if trip.legs:
        lines = [title, price_line + " total for all flights",
                 f"📅 Cheapest dates:\n{when}",
                 f"First flight: {html.escape(airline)}" + (f" ({html.escape(flights)})" if flights else "") + f" · {details}"]
    else:
        lines = [title, price_line + f" · {html.escape(airline)}" + (f" ({html.escape(flights)})" if flights else ""),
                 f"📅 {when} · {details}"]

    prev = history.last_price(trip.name)
    lowest = history.lowest_ever(trip.name)
    trend = []
    if prev is not None:
        diff = offer.price - prev
        if abs(diff) < 1:
            trend.append("no change since last check")
        else:
            arrow = "🔻" if diff < 0 else "🔺"
            trend.append(f"{arrow} {fmt_money(abs(diff), currency)} vs last check")
    if lowest is None or offer.price < lowest["price"]:
        trend.append("🏆 lowest price seen so far")
    else:
        trend.append(f"lowest seen {fmt_money(lowest['price'], currency)} on {lowest['date']}")
    if offer.price_level:
        trend.append(f"Google says prices are {offer.price_level}")
    lines.append(" · ".join(trend))

    if trip.target_price is not None and offer.price <= trip.target_price:
        lines.append(f"🎯 <b>Below your target of {fmt_money(trip.target_price, currency)}. Good time to book.</b>")
    if offer.link and offer.exact_link:
        lines.append(f'👉 <a href="{html.escape(offer.link)}">Book this exact flight on Google Flights</a>')
    elif offer.link:
        lines.append(f'🔗 <a href="{html.escape(offer.link)}">Open this search on Google Flights</a> '
                     f'(route, dates and travelers filled in)')
    return "\n".join(lines)


def wants_booking_link(config: Config, trip: Trip, offer: Offer, history: History) -> bool:
    """Exact-flight links cost extra searches, so by default only fetch them on a good day."""
    if config.booking_link == "always":
        return True
    if config.booking_link == "off":
        return False
    lowest = history.lowest_ever(trip.name)
    new_low = lowest is None or offer.price < lowest["price"]
    under_target = trip.target_price is not None and offer.price <= trip.target_price
    return new_low or under_target


def build_report(config: Config, providers: list[Provider], history: History,
                 today: dt.date, record: bool = True) -> str:
    """Search today's planned dates, update history (if record), and return the HTML message."""
    planned = plan_searches(config, today)

    # cheapest offer per trip across every date combination and provider checked today
    best: dict[str, tuple[Search, Offer, Provider]] = {}
    errors: dict[str, list[str]] = {}
    for trip, search in planned:
        for provider in providers:
            try:
                offers = provider.search(trip, search, config.currency)
            except Exception as exc:  # keep going: one failed search shouldn't kill the alert
                errors.setdefault(trip.name, []).append(f"{provider.name}: {exc}")
                print(f"[warn] {trip.name} {search.key} {provider.name}: {exc}", file=sys.stderr)
                continue
            for offer in offers:
                if trip.max_stops is not None and offer.stops > trip.max_stops:
                    continue
                if trip.name not in best or offer.price < best[trip.name][1].price:
                    best[trip.name] = (search, offer, provider)

    blocks = []
    for trip in config.trips:
        if not trip.searches(today):
            blocks.append(f"<b>{html.escape(trip.name)}</b>\nAll travel dates are in the past. Remove or update this trip.")
            continue
        if trip.name in best:
            search, offer, provider = best[trip.name]
            if record and wants_booking_link(config, trip, offer, history):
                try:
                    link = provider.booking_link(trip, search, offer, config.currency)
                    if link:
                        offer.link, offer.exact_link = link, True
                except Exception as exc:  # the search link is still there; just note it
                    print(f"[warn] {trip.name}: couldn't get the booking link: {exc}", file=sys.stderr)
        else:
            todays = [s for t, s in planned if t is trip]
            if not todays:
                continue  # not in today's rotation and nothing to report
            search, offer = todays[0], None
        blocks.append(trip_block(trip, search, offer, history, config.currency, errors.get(trip.name, [])))
        if offer is not None and record:
            history.record(trip.name, today.isoformat(), offer.price, {
                "depart": search.depart.isoformat(),
                "return": search.ret.isoformat() if search.ret else None,
                "flights": [{"from": leg.origin, "to": leg.destination, "date": d.isoformat()}
                            for leg, d in zip(trip.legs, search.leg_dates)] or None,
                "airlines": offer.airlines,
                "stops": offer.stops,
            })

    total = sum(len(t.searches(today)) for t in config.trips)
    footer = f"<i>Checked {len(planned)} of {total} date combinations today.</i>"
    return f"✈️ <b>Daily flight check: {today.strftime('%b %-d, %Y')}</b>\n\n" + "\n\n".join(blocks) + f"\n\n{footer}"


def to_plain(message: str) -> str:
    """Telegram HTML -> plain text for logs."""
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2: \1", message)
    return html.unescape(re.sub(r"</?[bi]>", "", text))


def to_markdown(message: str) -> str:
    """Telegram HTML -> Markdown for the GitHub Actions run page."""
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](\1)", message)
    text = re.sub(r"</?b>", "**", text)
    text = re.sub(r"</?i>", "_", text)
    # keep line breaks and the indented flight list
    lines = [line.replace("   ", "&nbsp;&nbsp;&nbsp;") for line in html.unescape(text).split("\n")]
    return "  \n".join(lines) + "\n"


def write_job_summary(message: str) -> None:
    """When running on GitHub Actions, show the results on the run's summary page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(to_markdown(message))


def run(config_path: str, history_path: str, dry_run: bool, use_mock: bool, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    config = load_config(config_path)
    providers = available_providers(use_mock)
    if not providers:
        print("No flight source configured. Set SERPAPI_KEY (or use --mock).", file=sys.stderr)
        return 2

    history = History(history_path)
    message = build_report(config, providers, history, today, record=not use_mock)

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if dry_run:
        print(message)
    elif token and chat_id:
        send_telegram(token, chat_id, message)
        print("Alert sent to Telegram.")
    else:
        # no Telegram yet: show the results in the log and on the GitHub Actions run page
        print("Telegram is not set up, so the alert was not sent. Results:\n")
        print(to_plain(message))
    write_job_summary(message)
    if not use_mock:
        history.save()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Daily cheapest-flight tracker")
    p.add_argument("--config", default="trips.yaml")
    p.add_argument("--history", default="data/history.json")
    p.add_argument("--dry-run", action="store_true", help="print the alert instead of sending it")
    p.add_argument("--mock", action="store_true", help="use fake prices (no API key needed)")
    args = p.parse_args()
    sys.exit(run(args.config, args.history, args.dry_run, args.mock))


if __name__ == "__main__":
    main()
