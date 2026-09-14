"""Shared helpers for the Playwright-based scrapers (African & Eastern, GBI Express, NHSC).

These three sites render their catalogs client-side (confirmed during manual
investigation: a plain HTTP fetch returns an empty shell or the age-gate only),
so we drive a real headless browser instead of requests/BeautifulSoup.

Price parsing: all three sites price in Bahraini Dinar, almost always printed as
"BD 12.345" or "BHD 12.345" or just "12.345" next to a currency symbol. We match
broadly and take the smallest number found near each product card, since a
discounted item shows both its sale price and its struck-through original price,
and the smallest is what a customer actually pays.
"""
import json
import os
import re
import time
from pathlib import Path

PRICE_RE = re.compile(r"(?:BD|BHD)?\s*([0-9]{1,3}\.[0-9]{3})\b")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Maps a search term or a keyword found in a product name to one of the app's
# 7 category chips (Whisky, Beer, Gin, Vodka, Wine, Champagne, Other Spirits).
CATEGORY_KEYWORDS = [
    (("whisky", "whiskey", "scotch", "bourbon"), "Whisky"),
    (("beer", "lager", "ale", "cider", "stout"), "Beer"),
    (("gin",), "Gin"),
    (("vodka",), "Vodka"),
    (("wine", "shiraz", "cabernet", "merlot", "chardonnay", "sauvignon", "rose", "rosé"), "Wine"),
    (("champagne", "sparkling", "prosecco", "cava", "moet", "veuve"), "Champagne"),
    (("rum", "brandy", "cognac", "tequila", "liqueur", "baileys", "sambuca", "amaretto",
      "vermouth", "sherry", "absinthe"), "Other Spirits"),
]


def guess_category(text):
    """Best-effort category guess from a search term or product name. Falls
    back to "Other Spirits" (the app's catch-all) when nothing matches."""
    lowered = text.lower()
    for keywords, category in CATEGORY_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return category
    return "Other Spirits"


def extract_price(text):
    """Return the smallest BD-style price found in a chunk of text, or None."""
    matches = [float(m) for m in PRICE_RE.findall(text)]
    return min(matches) if matches else None


def write_json(filename, results):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    path.write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} products to {path}")


def polite_sleep(seconds=1.5):
    time.sleep(seconds)


def connect_browser(p, user_agent):
    """Start a browser and a page.

    If the BRIGHTDATA_AUTH environment variable is set, use it as the FULL
    Bright Data Scraping Browser connection string, exactly as copied from
    the Bright Data dashboard's "Test API" step for Puppeteer/Playwright
    (the whole "wss://brd-customer-...-zone-...:password@brd.superproxy.io:9222"
    line, password included, no editing needed). That routes through Bright
    Data's proxy pool over CDP instead of a plain local Chromium launch,
    which is what gets past the Cloudflare bot-challenge that blocks African
    & Eastern and GBI Express on GitHub Actions' own IPs. When BRIGHTDATA_AUTH
    isn't set (e.g. running locally without a Bright Data account, or for
    scrapers that don't need a proxy at all), this falls back to the same
    local launch every scraper used before.
    """
    endpoint = os.environ.get("BRIGHTDATA_AUTH")
    if endpoint:
        browser = p.chromium.connect_over_cdp(endpoint)
        page = browser.new_page(user_agent=user_agent)
        page.set_default_navigation_timeout(120000)  # proxy adds latency; Bright Data's own guidance
    else:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        page = browser.new_page(user_agent=user_agent)
    return browser, page


def block_heavy_resources(page):
    """Abort image/font/media requests on this page.

    A Bright Data Scraping Browser session is billed by bandwidth (~$8/GB),
    and none of these scrapers need images or fonts to read a product's name
    and price, so blocking them keeps real usage far below the free 1GB/month
    tier. Harmless to call even when not running through a proxy.
    """
    def _handle(route):
        if route.request.resource_type in ("image", "font", "media"):
            route.abort()
        else:
            route.continue_()
    page.route("**/*", _handle)
