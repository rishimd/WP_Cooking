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

### 2. Create a Telegram bot (optional)
You can skip Telegram at first. Without it, each daily run still finds the cheapest flights and saves the price history. To see the results, open **Actions → Daily flight check**, click the latest run, and read its summary. The trip page's **Latest prices** box shows them too. Add the two Telegram secrets whenever you want the alerts on your phone.

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the **bot token** it gives you.
2. Open your new bot and send it any message (for example "hi").
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789`. That number is your **chat ID**.

### 3. Add the secrets to GitHub
Go to your repo's **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `SERPAPI_KEY` | your SerpApi key |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather (optional) |
| `TELEGRAM_CHAT_ID` | your chat ID (optional) |

### 4. Enter your travel plan: the trip page

There's one page for entering trips, with cities, dates, adults and children, cabin, stops and an alert price. Each trip is **round trip**, **one-way** or **multi-city**. For multi-city, add each flight in order (2 to 6 flights, for example New York → Delhi → Mumbai → New York), each with a date and an optional flexible range. The alert shows the cheapest **total price for the whole itinerary** and the best date for each flight. You can use the page two ways.

**A. From your phone or any browser (hosted on GitHub Pages)**
1. In the repo, open **Settings → Pages**. Under **Build and deployment**, choose **Deploy from a branch**, then select your default branch and the **/docs** folder, and save. After a minute, GitHub shows the page address, for example `https://<you>.github.io/<repo>/`.
2. Make a token that only works on this repo: [GitHub → New fine-grained token](https://github.com/settings/personal-access-tokens/new). Under **Repository access**, choose **Only select repositories** and pick this repo. Set **Contents: Read and write** and **Actions: Read and write**.
3. Open the page, paste the token, and click **Connect**. The token stays in that browser and is sent only to GitHub. Do this once per device.

**Save trips** commits `trips.yaml` to the repo, so the next daily alert uses your changes. **Send me prices now** runs the daily check right away, and the Telegram message arrives in 1–2 minutes. The page also shows the latest prices found.

> **Privacy:** GitHub Pages is free for **public** repos, but then anyone can read `trips.yaml` and `data/history.json`. To keep your trips private, keep this repo private and host the page somewhere else. It's one static file (`docs/index.html`) that holds no data, so you can put it in a separate public repo's Pages, or on any static host. Then enter this repo's name on the page. GitHub Pro can also serve Pages from a private repo.

**B. On your computer, with no token needed**
```bash
pip install -r requirements.txt
python -m flight_tracker.ui        # opens http://127.0.0.1:8765
```
This saves to your local `trips.yaml`, which you then commit and push. Here **Check prices now** shows a preview of the alert on the page. It uses real prices if `SERPAPI_KEY` is set in your terminal and sample prices otherwise.

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
Each date combination costs one search: departure × return dates for a round trip, or one date per flight for multi-city. Flexible ranges on several flights multiply quickly, so keep them short. `max_searches_per_run: 3` keeps you inside SerpApi's 100 searches a month. If your plan has more combinations than that, the tracker checks a different set each day and cycles through all of them, and the message footer shows how many it covered. On a paid plan, raise `max_searches_per_run` to check every combination daily.

## Layout
```
trips.yaml                  your travel plan
flight_tracker/main.py      runs searches, builds the message, sends it
docs/index.html             the trip page (GitHub Pages, or served locally)
flight_tracker/ui.py        serves the trip page on your computer
flight_tracker/config.py    parses trips and plans the day's searches
flight_tracker/providers/   price sources (serpapi.py; add more here)
flight_tracker/history.py   price history in data/history.json, committed by the workflow
flight_tracker/notify.py    Telegram delivery
```

To add another price source, subclass `Provider` in `flight_tracker/providers/base.py` and register it in `providers/__init__.py`. The tracker compares offers from every configured source.
