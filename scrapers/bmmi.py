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

Price extraction fix (2026-09-15): the site is built on Magento, and every
product page ends with a "Related Products" carousel that also lists OTHER
products' prices in the exact same "BD X.XXX" format used for the main price.
The old code searched the WHOLE page for that pattern and took the lowest
match, which was meant to handle a sale-price-vs-original-price situation on
ONE product, but instead very often grabbed a cheap RELATED product's price
off the carousel instead of the actual product's own price (or lack of one).
This is exactly why many completely different beers, of different brands and
different pack sizes, were all showing the identical price of BHD 5.450: that
figure belonged to a popular related beer that BMMI's site recommends on lots
of other beer pages, not to the product itself.

Fixed by narrowing the search to the main product area only: Magento's
default theme wraps the primary product info (name, price box, add-to-cart)
in a container with "product-info-main" in its class, which sits separately
from the "related"/"upsell"/"viewed"/"crosssell" widgets. We now look for
that container first; if a template change means it isn't found, we fall
back to removing the related/upsell/viewed/crosssell blocks from the page
before searching, so a stray related-product price can't be picked up either
way. A product with no price left after this (e.g. genuinely out of stock,
showing "BD 0.000" or nothing) is correctly skipped and shows as N/A/"not
carried" on the site, rather than borrowing someone else's price.

Out-of-stock fix (2026-09-15): "no price left" used to mean the whole
product was dropped from bmmi.json entirely, which told the site BMMI
doesn't carry it at all, exactly the same as if it genuinely wasn't in
BMMI's catalog. That's misleading when the real reason is that it's simply
out of stock right now (BMMI's own page shows "Notify Me" with no price in
that case, as confirmed on the live "Red Rose Extra Strong Beer" page
during the earlier price-bug investigation). Fixed by still recording the
product, with price_bhd set to null and in_stock set to false, so the site
can show "Out of Stock" for that retailer instead of "Not carried".
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

# Magento block naming conventions for the widgets that list OTHER products'
# prices on a product page (related items, "you may also like" upsells,
# recently-viewed carousel, cross-sell suggestions in the cart/checkout flow).
# Any element whose id or class contains one of these must be excluded before
# we search for a price, so we never mistake one of THEIR prices for this
# product's own.
OTHER_PRODUCT_WIDGET_HINTS = ("related", "upsell", "viewed-products", "crosssell")


def fetch_sitemap_urls():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")
    urls = [loc.text.strip() for loc in soup.find_all("loc")]
    product_urls = [u for u in urls if PRODUCT_SLUG_RE.search(urlparse(u).path)]
    return product_urls


def main_product_html(soup):
    """Return the HTML text to search for THIS product's own price, with any
    other-product widgets (related/upsell/viewed/crosssell) excluded."""
    main = soup.find(class_=lambda c: c and "product-info-main" in c)
    if main:
        return str(main)

    # Template didn't match what we expected; fall back to stripping out any
    # other-product widget from the whole page before searching it.
    for hint in OTHER_PRODUCT_WIDGET_HINTS:
        for el in soup.find_all(id=lambda v: v and hint in v.lower()):
            el.decompose()
        for el in soup.find_all(class_=lambda v: v and any(hint in c.lower() for c in v)):
            el.decompose()
    return str(soup)


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

    search_area = main_product_html(soup)
    prices = [float(m) for m in PRICE_RE.findall(search_area) if float(m) > 0]
    if not prices:
        # Genuinely out of stock (BMMI shows "Notify Me" with no price), not a
        # parsing failure -- we did find a real product name on a real product
        # page, there's just nothing to buy right now. Record it anyway so the
        # site can say "Out of Stock" for BMMI instead of "Not carried".
        return {"name": name, "price_bhd": None, "in_stock": False, "url": url,
                "retailer": "BMMI", "category": guess_category(name)}
    # When a sale is running the page shows both the discounted and the original
    # price; the lower of the two is what a customer actually pays.
    price = min(prices)

    return {"name": name, "price_bhd": price, "in_stock": True, "url": url,
            "retailer": "BMMI", "category": guess_category(name)}


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
                price_note = f"BD {item['price_bhd']:.3f}" if item["price_bhd"] is not None else "out of stock"
                print(f"[{i}/{len(product_urls)}] OK  {item['name']} — {price_note}", file=sys.stderr)
            else:
                print(f"[{i}/{len(product_urls)}] SKIP (no product name found) {url}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"[{i}/{len(product_urls)}] ERROR {url}: {e}", file=sys.stderr)
        time.sleep(SLEEP_SECONDS)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} products to {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
