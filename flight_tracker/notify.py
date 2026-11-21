"""Telegram delivery."""

from __future__ import annotations

import requests

MAX_LEN = 4000  # Telegram's limit is 4096 characters per message


def send_telegram(token: str, chat_id: str, html: str) -> None:
    for chunk in _chunks(html):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram error ({resp.status_code}): {resp.text[:300]}")


def _chunks(text: str) -> list[str]:
    """Split on blank lines so no trip block is cut in half."""
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > MAX_LEN and current:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts
