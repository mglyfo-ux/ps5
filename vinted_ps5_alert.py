#!/usr/bin/env python3
"""Monitor a Vinted search URL and send new PS5 listings to Telegram.

Set VINTED_SEARCH_URL to a search URL copied from your browser after applying
£200-£300 filters and sorting newest first. The monitor checks every five
minutes, deduplicates item IDs, and backs off on 403/429 responses.
"""

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SEARCH_URL = os.environ["VINTED_SEARCH_URL"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CHECK_SECONDS = int(os.getenv("CHECK_SECONDS", "300"))
MIN_PRICE = float(os.getenv("MIN_PRICE", "200"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "300"))
STATE_FILE = Path(os.getenv("STATE_FILE", "vinted_seen.json"))

INCLUDE = ("ps5", "playstation 5", "playstation5")
EXCLUDE = (
    "controller", "dualsense", "headset", "charging", "stand", "faceplate",
    "cover", "skin", "cable", "empty box", "box only", "wanted",
    "looking for", "faulty", "broken", "spares", "repair only", "deposit",
)
ITEM_RE = re.compile(r"/items/(\d+)(?:-|/|$)", re.I)
PRICE_RE = re.compile(r"(?:£|GBP\s*)(\d+(?:[.,]\d{1,2})?)", re.I)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def load_seen() -> set[str]:
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def save_seen(seen: set[str]) -> None:
    # Keep state bounded while retaining plenty of history.
    STATE_FILE.write_text(
        json.dumps(list(seen)[-10000:], indent=2), encoding="utf-8"
    )


def parse_price(text: str) -> float | None:
    match = PRICE_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def looks_like_console(title: str) -> bool:
    value = " ".join(title.lower().split())
    return any(x in value for x in INCLUDE) and not any(x in value for x in EXCLUDE)


def extract_listings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    listings: dict[str, dict] = {}

    # Vinted page structure changes occasionally, so identify item links first
    # and derive title/price from the nearest listing-sized ancestor.
    for anchor in soup.select('a[href*="/items/"]'):
        href = anchor.get("href", "")
        match = ITEM_RE.search(href)
        if not match:
            continue

        item_id = match.group(1)
        url = urljoin("https://www.vinted.co.uk", href.split("?")[0])

        container = anchor
        for _ in range(6):
            if container.parent is None:
                break
            container = container.parent
            text = " ".join(container.get_text(" ", strip=True).split())
            if len(text) >= 20 and parse_price(text) is not None:
                break

        text = " ".join(container.get_text(" ", strip=True).split())
        title = (
            anchor.get("title")
            or anchor.get("aria-label")
            or anchor.get_text(" ", strip=True)
            or text[:180]
        )
        title = " ".join(title.split())
        price = parse_price(text)

        if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        if not looks_like_console(f"{title} {text}"):
            continue

        listings[item_id] = {
            "id": item_id,
            "title": title[:240],
            "price": price,
            "url": url,
        }

    return list(listings.values())


def send_telegram(item: dict) -> None:
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    message = (
        "🎮 New Vinted PS5 listing\n\n"
        f"{item['title']}\n"
        f"Price: £{item['price']:.2f}\n"
        f"{item['url']}\n\n"
        "Check seller feedback, description, photos and checkout total."
    )
    response = requests.post(
        endpoint,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()


def fetch() -> tuple[list[dict], int | None]:
    response = session.get(SEARCH_URL, timeout=30)

    if response.status_code in (403, 429):
        logging.warning("Vinted returned HTTP %s; entering extended backoff", response.status_code)
        return [], response.status_code

    response.raise_for_status()
    return extract_listings(response.text), None


def main() -> None:
    seen = load_seen()
    first_run = not STATE_FILE.exists()
    blocked_backoff = 30 * 60

    logging.info("Monitoring Vinted every approximately %s seconds", CHECK_SECONDS)

    while True:
        wait = CHECK_SECONDS + random.randint(-20, 20)
        try:
            listings, blocked_status = fetch()

            if blocked_status:
                wait = blocked_backoff + random.randint(0, 300)
                blocked_backoff = min(blocked_backoff * 2, 6 * 60 * 60)
            else:
                blocked_backoff = 30 * 60
                new_items = [item for item in listings if item["id"] not in seen]

                # On first launch, record existing results without flooding Telegram.
                if first_run:
                    for item in listings:
                        seen.add(item["id"])
                    save_seen(seen)
                    first_run = False
                    logging.info("Initialised with %s existing matching listing(s)", len(listings))
                else:
                    for item in reversed(new_items):
                        send_telegram(item)
                        seen.add(item["id"])
                        save_seen(seen)
                        logging.info("Alerted: %s | £%.2f", item["title"], item["price"])
                        time.sleep(2)

                    logging.info("Checked %s matching listing(s); %s new", len(listings), len(new_items))

        except requests.RequestException as exc:
            logging.error("Network/API error: %s", exc)
            wait = max(wait, 15 * 60)
        except Exception:
            logging.exception("Unexpected error")
            wait = max(wait, 10 * 60)

        logging.info("Next check in %s seconds", wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
