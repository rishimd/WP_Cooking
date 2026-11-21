"""Run the trip page (docs/index.html) on your computer, saving to the local trips.yaml.

    python -m flight_tracker.ui            # opens http://127.0.0.1:8765
    python -m flight_tracker.ui --port 9000 --no-browser

The same page also works hosted on GitHub Pages, where it saves to the repo through
the GitHub API instead. See README.md.
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
from urllib.parse import parse_qs, urlparse

import yaml

from .config import load_config, parse_config
from .history import History
from .main import build_report
from .providers import available_providers

PAGE = Path(__file__).resolve().parent.parent / "docs" / "index.html"


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

    def _files(self) -> dict[str, Path]:
        # the only two files the page may read; only trips.yaml may be written
        return {"trips.yaml": self.config_path, "data/history.json": self.history_path}

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/mode":
            self._json({"mode": "local", "has_api_key": bool(os.environ.get("SERPAPI_KEY"))})
        elif url.path == "/api/file":
            name = (parse_qs(url.query).get("path") or [""])[0]
            path = self._files().get(name)
            if path is None:
                return self._json({"error": "Unknown file"}, HTTPStatus.NOT_FOUND)
            if not path.exists():
                return self._json({"missing": True}, HTTPStatus.NOT_FOUND)
            self._json({"text": path.read_text()})
        else:
            self._send(404, b"Not found", "text/plain")

    def _check_origin(self) -> bool:
        # only accept writes from this page, not from other sites open in the browser
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self._json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_PUT(self):
        if not self._check_origin():
            return
        if urlparse(self.path).path != "/api/file":
            return self._send(404, b"Not found", "text/plain")
        try:
            body = self._body()
            if body.get("path") != "trips.yaml":
                return self._json({"error": "Only trips.yaml can be saved"}, HTTPStatus.FORBIDDEN)
            text = str(body.get("text", ""))
            cfg = parse_config(yaml.safe_load(text))  # refuse anything the daily job couldn't read
            self.config_path.write_text(text)
            total = sum(len(t.searches(dt.date.today())) for t in cfg.trips)
            self._json({"ok": True, "combinations": total})
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        if not self._check_origin():
            return
        if urlparse(self.path).path != "/api/check":
            return self._send(404, b"Not found", "text/plain")
        try:
            use_mock = not os.environ.get("SERPAPI_KEY")
            cfg = load_config(self.config_path)
            # a preview: don't write history, so the daily "vs last check" stays meaningful
            msg = build_report(cfg, available_providers(use_mock), History(self.history_path),
                               dt.date.today(), record=False)
            self._json({"ok": True, "mock": use_mock, "message": msg})
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
