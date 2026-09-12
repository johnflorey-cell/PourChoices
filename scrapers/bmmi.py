"""
BMMI (bmmishops.com) scraper.

Compliance notes (checked against https://www.bmmishops.com/robots.txt on 2026-09-12):
  - We only fetch: /sitemap.xml (explicitly listed as a Sitemap: directive) and the
    clean product-slug pages it lists (e.g. /moet-and-chandon-brut-imperial-non-vintage-75cl.html).
  - We never touch /catalog/, /catalogsearch/, /catalog/category/view/,
    /catalog/product/view/, any "?"-query URL, /graphql/, /rest/, or any other
    disallowed path. This script deliberately has no code path that could hit those.
  - One request at a time with a short delay between requests (see SLEEP_SECONDS)
    and a descriptive User-Agent, to be a polite, low-impact crawler.

Output: data/bmmi.json, a flat list of {name, price_bhd, url, retailer: "BMMI"}.

Note on selectors: BMMI's product pages have no JSON-LD/structured data, so this
parses the plain HTML. If BMMI changes their template, the price regex below (it
matches "BD" followed by a number, e.g. "BD 36.750") is the most likely thing to
need adjusting; run this once locally and eyeball a few entries against the live
site before trusting a fresh scrape.
"""
import json
import re
import time
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import guess_category

SITEMAP_URL = "https://www.bmmishops.com/sitemap.xml"
HEADERS = {
    "User-Agent": "PourChoicesBot/1.0 (+price comparison directory; contact via GitHub repo issues; respects robots.txt)"
}
SLEEP_SECONDS = 1.5
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "bmmi.json"

# Sitemap entries that are informational/category pages, not individual products.
# A product page always ends in a size token like -75cl.html / -1l.html / -24-pack.html etc.
PRODUCT_SLUG_RE = re.compile(
    r"-(?:\d+(?:-\d+)?(?:cl|l|ml)|mini-\d+cl|\d+-pack)\.html$", re.IGNORECASE
)

PRICE_RE = re.compile(r"BD\s*([0-9]+\.[0-9]{3})")


def fetch_sitemap_urls():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")
    urls = [loc.text.strip() for loc in soup.find_all("loc")]
    product_urls = [u for u in urls if PRODUCT_SLUG_RE.search(urlparse(u).path)]
    return product_urls


def parse_product_page(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else None
    if not name:
        title_tag = soup.find("title")
        if title_tag:
            name = title_tag.get_text(strip=True).split("|")[-1].strip()
    if not name:
        return None

    prices = [float(m) for m in PRICE_RE.findall(resp.text)if float(m)>0]
    if not prices:
        return None
    # When a sale is running the page shows both the discounted and the original
    # price; the lower of the two is what a customer actually pays.
    price = min(prices)

    return {"name": name, "price_bhd": price, "url": url, "retailer": "BMMI",
            "category": guess_category(name)}


def main():
    print("Fetching BMMI sitemap...", file=sys.stderr)
    product_urls = fetch_sitemap_urls()
    print(f"Found {len(product_urls)} candidate product URLs.", file=sys.stderr)

    results = []
    for i, url in enumerate(product_urls, 1):
        try:
            item = parse_product_page(url)
            if item:
                results.append(item)
                print(f"[{i}/{len(product_urls)}] OK  {item['name']} — BD {item['price_bhd']:.3f}", file=sys.stderr)
            else:
                print(f"[{i}/{len(product_urls)}] SKIP (no name/price found) {url}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"[{i}/{len(product_urls)}] ERROR {url}: {e}", file=sys.stderr)
        time.sleep(SLEEP_SECONDS)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} products to {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
