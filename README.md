# Flight Tracker ✈️

Finds the cheapest flight for each trip in `trips.yaml` once a day and sends you a Telegram message with:

- the cheapest fare, airline, dates, stops and duration
- how the price moved since the last check, and the lowest price seen so far
- a 🎯 flag when the price drops below your `target_price`
- a link that opens the search in Google Flights

Prices come from Google Flights through [SerpApi](https://serpapi.com/google-flights-api). The job runs on GitHub Actions, so nothing needs to stay running on your computer.

## Setup (about 10 minutes)

### 1. Get a SerpApi key
Sign up at <https://serpapi.com>. The free plan includes 100 searches a month. Copy your key from the dashboard.

### 2. Create a Telegram bot
1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the **bot token** it gives you.
2. Open your new bot and send it any message (for example "hi").
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789`. That number is your **chat ID**.

### 3. Add the secrets to GitHub
Go to your repo's **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `SERPAPI_KEY` | your SerpApi key |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID |

### 4. Enter your travel plan
Open the trip editor on your computer:

```bash
pip install -r requirements.txt
python -m flight_tracker.ui        # opens http://127.0.0.1:8765 in your browser
```

For each trip, pick the cities (type a city name or airport code), your dates (with optional flexibility), the number of adults and children, cabin, stops, and an optional alert price. Click **Save trips**, then commit and push `trips.yaml` so the daily job uses it. **Check prices now** shows a preview of the alert. It uses real prices if `SERPAPI_KEY` is set in your terminal and sample prices otherwise. Each check uses up searches.

You can also edit `trips.yaml` by hand. The comments in it explain the format.

### 5. Turn it on
The workflow in `.github/workflows/daily-flight-check.yml` runs every day at 03:17 UTC. Change the `cron` line to use a different time. GitHub only runs scheduled workflows from the **default branch**, so merge this branch into it. To test right away, open **Actions → Daily flight check → Run workflow**.

## Try it locally

```bash
pip install -r requirements.txt
python -m flight_tracker.main --mock --dry-run      # fake prices, no keys needed
SERPAPI_KEY=... python -m flight_tracker.main --dry-run   # real prices, prints instead of sending
python -m pytest -q
```

## Staying within the free search limit
Each depart-date × return-date combination costs one search. `max_searches_per_run: 3` keeps you inside SerpApi's 100 searches a month. If your plan has more combinations than that, the tracker checks a different set each day and cycles through all of them, and the message footer shows how many it covered. On a paid plan, raise `max_searches_per_run` to check every combination daily.

## Layout
```
trips.yaml                  your travel plan
flight_tracker/main.py      runs searches, builds the message, sends it
flight_tracker/ui.py        local trip editor (ui.html is the page)
flight_tracker/config.py    parses trips and plans the day's searches
flight_tracker/providers/   price sources (serpapi.py; add more here)
flight_tracker/history.py   price history in data/history.json, committed by the workflow
flight_tracker/notify.py    Telegram delivery
```

To add another price source, subclass `Provider` in `flight_tracker/providers/base.py` and register it in `providers/__init__.py`. The tracker compares offers from every configured source.
