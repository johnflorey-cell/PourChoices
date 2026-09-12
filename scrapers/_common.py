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
