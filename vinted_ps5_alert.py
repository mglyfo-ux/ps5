import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

SEARCH_URL = os.environ.get("VINTED_SEARCH_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

MIN_PRICE = float(os.environ.get("MIN_PRICE", "200"))
MAX_PRICE = float(os.environ.get("MAX_PRICE", "300"))

STATE_FILE = Path("vinted_seen.json")

INCLUDE_TERMS = (
    "ps5",
    "playstation 5",
    "playstation5",
)

EXCLUDE_TERMS = (
    "controller",
    "controllers",
    "dualsense",
    "headset",
    "headphones",
    "earphones",
    "charging station",
    "charging dock",
    "charger",
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
    "games only",
    "game only",
    "photo only",
)

ITEM_ID_PATTERN = re.compile(
    r"/items/(\d+)(?:[-/?]|$)",
    re.IGNORECASE,
)

PRICE_PATTERN = re.compile(
    r"(?:£|GBP\s*)(\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


# ============================================================
# VALIDATION
# ============================================================

def validate_configuration():
    missing_values = []

    if not SEARCH_URL:
        missing_values.append("VINTED_SEARCH_URL")

    if not BOT_TOKEN:
        missing_values.append("TELEGRAM_BOT_TOKEN")

    if not CHAT_ID:
        missing_values.append("TELEGRAM_CHAT_ID")

    if missing_values:
        raise RuntimeError(
            "Missing GitHub secret(s): "
            + ", ".join(missing_values)
        )

    parsed_url = urlparse(SEARCH_URL)

    if parsed_url.scheme not in ("http", "https"):
        raise RuntimeError(
            "VINTED_SEARCH_URL is not a valid HTTP/HTTPS URL."
        )

    if "vinted.co.uk" not in parsed_url.netloc.lower():
        raise RuntimeError(
            "VINTED_SEARCH_URL must point to vinted.co.uk."
        )

    if MIN_PRICE > MAX_PRICE:
        raise RuntimeError(
            "MIN_PRICE cannot be higher than MAX_PRICE."
        )


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_seen_items():
    if not STATE_FILE.exists():
        return set()

    try:
        saved_data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(saved_data, list):
            logger.warning(
                "State file is not a list. Starting with empty state."
            )
            return set()

        return {str(item_id) for item_id in saved_data}

    except (json.JSONDecodeError, OSError) as error:
        logger.warning(
            "Could not read %s: %s",
            STATE_FILE,
            error,
        )
        return set()


def save_seen_items(seen_items):
    sorted_items = sorted(
        seen_items,
        key=lambda value: int(value) if value.isdigit() else value,
    )

    STATE_FILE.write_text(
        json.dumps(sorted_items, indent=2),
        encoding="utf-8",
    )


# ============================================================
# TEXT AND PRICE FILTERING
# ============================================================

def normalise_text(value):
    return " ".join(
        value.replace("\xa0", " ").lower().split()
    )


def parse_prices(text):
    prices = []

    for price_match in PRICE_PATTERN.findall(text):
        try:
            price = float(
                price_match.replace(",", ".")
            )
            prices.append(price)
        except ValueError:
            continue

    return prices


def find_price_in_range(text):
    prices = parse_prices(text)

    for price in prices:
        if MIN_PRICE <= price <= MAX_PRICE:
            return price

    return None


def looks_like_ps5_console(text):
    normalised = normalise_text(text)

    contains_ps5 = any(
        term in normalised
        for term in INCLUDE_TERMS
    )

    contains_exclusion = any(
        term in normalised
        for term in EXCLUDE_TERMS
    )

    return contains_ps5 and not contains_exclusion


# ============================================================
# VINTED REQUEST
# ============================================================

def create_vinted_session():
    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,image/avif,"
                "image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    return session


def download_vinted_page():
    logger.info("Checking Vinted search page.")

    session = create_vinted_session()

    response = session.get(
        SEARCH_URL,
        timeout=30,
        allow_redirects=True,
    )

    logger.info(
        "Vinted returned HTTP status %s.",
        response.status_code,
    )

    if response.status_code == 403:
        raise RuntimeError(
            "Vinted returned HTTP 403 and rejected the GitHub runner."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Vinted returned HTTP 429 and rate limited the request."
        )

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError(
            "Vinted returned an empty response."
        )

    logger.info(
        "Downloaded %s bytes from Vinted.",
        len(response.content),
    )

    return response.text


# ============================================================
# LISTING EXTRACTION
# ============================================================

def extract_title(anchor, surrounding_text):
    possible_values = (
        anchor.get("title"),
        anchor.get("aria-label"),
        anchor.get_text(" ", strip=True),
    )

    for value in possible_values:
        if value and value.strip():
            return " ".join(value.split())[:250]

    return " ".join(surrounding_text.split())[:250]


def find_listing_container(anchor):
    container = anchor
    best_container = anchor

    for _ in range(8):
        if container.parent is None:
            break

        container = container.parent

        text = " ".join(
            container.get_text(" ", strip=True).split()
        )

        if text:
            best_container = container

        if (
            len(text) >= 20
            and PRICE_PATTERN.search(text)
        ):
            return container

    return best_container


def clean_item_url(href):
    absolute_url = urljoin(
        "https://www.vinted.co.uk",
        href,
    )

    parsed_url = urlparse(absolute_url)

    return (
        f"{parsed_url.scheme}://"
        f"{parsed_url.netloc}"
        f"{parsed_url.path}"
    )


def extract_listings(html):
    soup = BeautifulSoup(html, "html.parser")

    item_links = soup.select('a[href*="/items/"]')

    logger.info(
        "Found %s Vinted item link(s) in the HTML.",
        len(item_links),
    )

    if not item_links:
        raise RuntimeError(
            "Vinted returned HTTP 200, but no item links were found. "
            "The page may be dynamically rendered or its HTML structure "
            "may have changed."
        )

    matching_listings = {}

    for anchor in item_links:
        href = anchor.get("href", "")

        item_id_match = ITEM_ID_PATTERN.search(href)

        if not item_id_match:
            continue

        item_id = item_id_match.group(1)
        item_url = clean_item_url(href)

        container = find_listing_container(anchor)

        surrounding_text = " ".join(
            container.get_text(" ", strip=True).split()
        )

        title = extract_title(
            anchor,
            surrounding_text,
        )

        combined_text = f"{title} {surrounding_text}"

        if not looks_like_ps5_console(combined_text):
            continue

        price = find_price_in_range(combined_text)

        if price is None:
            continue

        matching_listings[item_id] = {
            "id": item_id,
            "title": title,
            "price": price,
            "url": item_url,
        }

    return list(matching_listings.values())


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):
    endpoint = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
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


def send_listing_notification(item):
    message = (
        "🎮 NEW PS5 ON VINTED\n\n"
        f"💷 Price: £{item['price']:.2f}\n\n"
        f"📦 {item['title']}\n\n"
        f"🔗 {item['url']}\n\n"
        "Check the description, photos, seller feedback "
        "and final checkout price before buying."
    )

    send_telegram_message(message)

    logger.info(
        "Telegram notification sent for Vinted item %s.",
        item["id"],
    )


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():
    logger.info("=== VINTED PS5 BOT STARTING ===")
    logger.info(
        "Configured price range: £%.2f to £%.2f.",
        MIN_PRICE,
        MAX_PRICE,
    )

    validate_configuration()

    state_existed_before_run = STATE_FILE.exists()
    seen_items = load_seen_items()

    logger.info(
        "Loaded %s previously seen item(s).",
        len(seen_items),
    )

    html = download_vinted_page()
    listings = extract_listings(html)

    logger.info(
        "Found %s matching PS5 listing(s) between £%.2f and £%.2f.",
        len(listings),
        MIN_PRICE,
        MAX_PRICE,
    )

    if not state_existed_before_run:
        for item in listings:
            seen_items.add(item["id"])

        save_seen_items(seen_items)

        logger.info(
            "First successful run completed. "
            "%s existing listing(s) were recorded without notifications.",
            len(listings),
        )

        return

    new_listings = [
        item
        for item in listings
        if item["id"] not in seen_items
    ]

    logger.info(
        "Found %s new matching listing(s).",
        len(new_listings),
    )

    for item in reversed(new_listings):
        send_listing_notification(item)
        seen_items.add(item["id"])

    save_seen_items(seen_items)

    logger.info("=== VINTED PS5 CHECK COMPLETED ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logger.exception(
            "The Vinted PS5 monitor failed: %s",
            error,
        )
        sys.exit(1)
