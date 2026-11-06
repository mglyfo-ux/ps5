print("=== VINTED PS5 BOT STARTING ===", flush=True)

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ==========================================================
# SETTINGS
# ==========================================================

SEARCH_URL = os.environ["VINTED_SEARCH_URL"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MIN_PRICE = float(os.getenv("MIN_PRICE", "200"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "300"))

STATE_FILE = Path("vinted_seen.json")

INCLUDE_TERMS = (
    "ps5",
    "playstation 5",
    "playstation5",
)

EXCLUDE_TERMS = (
    "controller",
    "dualsense",
    "headset",
    "headphones",
    "charging station",
    "charging dock",
    "stand",
    "faceplate",
    "cover",
    "skin",
    "cable",
    "empty box",
    "box only",
    "wanted",
    "looking for",
    "faulty",
    "broken",
    "spares",
    "repair only",
    "deposit",
)

ITEM_RE = re.compile(
    r"/items/(\d+)(?:-|/|$)",
    re.IGNORECASE,
)

PRICE_RE = re.compile(
    r"(?:£|GBP\s*)(\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
    }
)


# ==========================================================
# SAVED LISTINGS
# ==========================================================

def load_seen():
    try:
        return set(
            json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
        )
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return set()


def save_seen(seen):
    STATE_FILE.write_text(
        json.dumps(
            sorted(seen),
            indent=2,
        ),
        encoding="utf-8",
    )


# ==========================================================
# LISTING FILTERS
# ==========================================================

def parse_price(text):
    text = text.replace("\xa0", " ")

    match = PRICE_RE.search(text)

    if not match:
        return None

    try:
        return float(
            match.group(1).replace(",", ".")
        )
    except ValueError:
        return None


def looks_like_ps5(text):
    text = " ".join(
        text.lower().split()
    )

    has_ps5 = any(
        term in text
        for term in INCLUDE_TERMS
    )

    excluded = any(
        term in text
        for term in EXCLUDE_TERMS
    )

    return has_ps5 and not excluded


# ==========================================================
# VINTED
# ==========================================================

def get_vinted_page():

    logging.info("Checking Vinted...")

    response = session.get(
        SEARCH_URL,
        timeout=30,
    )

    logging.info(
        "Vinted HTTP status: %s",
        response.status_code,
    )

    if response.status_code == 429:
        logging.warning(
            "Vinted rate limited this check. "
            "Stopping until the next GitHub run."
        )
        return None

    if response.status_code == 403:
        logging.warning(
            "Vinted rejected this request. "
            "Stopping until the next GitHub run."
        )
        return None

    response.raise_for_status()

    return response.text


def extract_listings(html):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    listings = {}

    item_links = soup.select(
        'a[href*="/items/"]'
    )

    logging.info(
        "Found %s item link(s) in page HTML",
        len(item_links),
    )

    for anchor in item_links:

        href = anchor.get(
            "href",
            "",
        )

        item_match = ITEM_RE.search(
            href
        )

        if not item_match:
            continue

        item_id 
        
if __name__ == "__main__":
    main()
