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

Note on selectors: every BMMI product page carries a schema.org JSON-LD block
(`<script type="application/ld+json">` with `"@type": "Product"`) whose
`offers.price` is that product's own price, scoped to exactly one product
with no risk of picking up anything else on the page. That is now the
primary price source (see `jsonld_price` below). The old "BD X.XXX" regex
scan of the HTML is kept only as a fallback for the rare page where the
JSON-LD block is missing or malformed.

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

First attempted fix (2026-09-15, superseded below): narrow the search to the
main product area only, on the theory that Magento's default theme wraps the
primary product info (name, price box, add-to-cart) in a container with
"product-info-main" in its class, separate from the "related"/"upsell"/
"viewed"/"crosssell" widgets, falling back to stripping those widgets out of
the whole page if that container wasn't found.

Price extraction fix, part 2 (2026-09-15): the "product-info-main" fix above
did not actually work, because on this theme the Related Products carousel
(`<section id="catalog_product_related" ...>`) is nested INSIDE
"product-info-main", not outside it -- so narrowing to that container still
included every related product's price, and the fallback-stripping code
never even ran because the container was always found. Caught when "Paragon
Timur Berry 48.5cl" scraped as BD 8.700 (a price shared by several unrelated
Bols-brand liqueurs in its related-products carousel) when the live page's
own price box, itemprop meta tags, and JSON-LD all agreed on BD 9.800.
Fixed properly this time by reading the price straight out of the page's
JSON-LD `offers.price` instead of trying to scope a regex search to a
container at all -- see `jsonld_price`. The HTML container/regex approach is
kept only as a last-resort fallback for a page with no usable JSON-LD, and in
that fallback path the related/upsell/viewed/crosssell widgets are always
stripped out first (never trusted to a container lookup) so a stray
related-product price can't be picked up either way. A product with no price
left after this (e.g. genuinely out of stock, showing "BD 0.000" or nothing)
is correctly skipped and shows as N/A/"not carried" on the site, rather than
borrowing someone else's price.

Out-of-stock fix (2026-09-15): "no price left" used to mean the whole
product was dropped from bmmi.json entirely, which told the site BMMI
doesn't carry it at all, exactly the same as if it genuinely wasn't in
BMMI's catalog. That's misleading when the real reason is that it's simply
out of stock right now (BMMI's own page shows "Notify Me" with no price in
that case, as confirmed on the live "Red Rose Extra Strong Beer" page
during the earlier price-bug investigation). Fixed by still recording the
product, with price_bhd set to null and in_stock set to false, so the site
can show "Out of Stock" for that retailer instead of "Not carried".

Price extraction fix, part 3 (2026-09-15): after switching to JSON-LD, a
fresh run showed the SAME bleed signature again, but much worse and on a
huge scale: 1,716 products collapsed down to only 58 distinct prices, with
one single price (BD 4.300) shared by 347 completely unrelated wines. Two
things were going on. First, product pages that are genuinely out of stock
(confirmed live: "Grey Goose Vodka Original 37.5cl" and "...4.5L" both show
"BD 0.000" and a "Notify Me" button) don't carry a JSON-LD Product block at
all, so every one of them fell through to the old regex fallback, which
scans the ENTIRE page text for "BD X.XXX" and takes the smallest match --
and that fallback doesn't just risk the Related Products carousel, it was
also catching whatever this theme's price-range/layered-navigation filter
widget prints ("BD 3.300 - BD 4.300" style bucket boundaries), which repeats
the exact same handful of round numbers across thousands of otherwise
unrelated pages. That fully explains the 58-distinct-prices pattern.
Fixed by dropping the whole-page text-regex fallback entirely (it has now
caused this exact class of bug twice) in favour of a second precise,
structured source: schema.org microdata (itemprop="price"), which -- unlike
plain "BD X.XXX" text -- a filter widget has no reason to carry, since
microdata exists specifically to mark up one real entity's own property,
not decorative UI text. That microdata search is still scoped to the
product-info-main container with the related/upsell/viewed/crosssell
widgets stripped out of a disposable copy first (never trusting the
container by itself, since it's already confirmed to nest the Related
Products carousel on this theme), so it inherits the same protection the
JSON-LD path already has. If NEITHER JSON-LD nor this microdata check finds
a price, the product is recorded as out of stock/no price rather than
guessing from raw text again.
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

# Magento block naming conventions for the widgets that list OTHER products'
# prices on a product page (related items, "you may also like" upsells,
# recently-viewed carousel, cross-sell suggestions in the cart/checkout flow).
# Any element whose id or class contains one of these must be excluded before
# we search for a price, so we never mistake one of THEIR prices for this
# product's own.
OTHER_PRODUCT_WIDGET_HINTS = ("related", "upsell", "viewed-products", "crosssell",
                               "filter", "layered", "sidebar", "toolbar")


def fetch_sitemap_urls():
    resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")
    urls = [loc.text.strip() for loc in soup.find_all("loc")]
    product_urls = [u for u in urls if PRODUCT_SLUG_RE.search(urlparse(u).path)]
    return product_urls


def _iter_jsonld_nodes(data):
    """Walk a parsed JSON-LD payload and yield every dict node in it,
    however it's wrapped: a single object, a list of objects, or an object
    that bundles several types together under "@graph" (a common pattern
    when a page emits Product + BreadcrumbList + Organization etc. as one
    script tag instead of separate ones)."""
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                yield from _iter_jsonld_nodes(node)
    elif isinstance(data, list):
        for node in data:
            yield from _iter_jsonld_nodes(node)


def jsonld_price(soup):
    """Return this product's own price (float) from the page's schema.org
    JSON-LD Product/Offer block, or None if it's missing/unparsable.

    This is scoped to exactly one product per page by construction -- there
    is no "Related Products" carousel or other widget to accidentally read
    from, unlike a regex scan of the rendered HTML."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (TypeError, ValueError):
            continue
        for node in _iter_jsonld_nodes(data):
            if not isinstance(node, dict):
                continue
            if node.get("@type") != "Product":
                continue
            offers = node.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if not isinstance(offers, dict):
                continue
            price = offers.get("price")
            if price is None:
                continue
            try:
                return float(price)
            except (TypeError, ValueError):
                continue
    return None


def _widget_stripped_copy(area_html):
    """Parse area_html into its own disposable soup and strip out any
    element whose id/class hints at being an OTHER product's widget
    (related/upsell/viewed/crosssell), so whatever we search next can't
    accidentally read one of THEIR prices."""
    area = BeautifulSoup(area_html, "html.parser")
    for hint in OTHER_PRODUCT_WIDGET_HINTS:
        for el in area.find_all(id=lambda v: v and hint in v.lower()):
            el.decompose()
        for el in area.find_all(class_=lambda v: v and any(hint in c.lower() for c in v)):
            el.decompose()
    return area


def microdata_price(soup):
    """Fallback used only when the page has no usable JSON-LD: look for
    schema.org microdata (itemprop="price") scoped to the main product only.

    Magento's default theme wraps the primary product info in a container
    with "product-info-main" in its class, but on this site that same
    container also nests the Related Products carousel, so the container
    is never trusted by itself -- the widgets are always stripped from a
    disposable copy first (see _widget_stripped_copy).

    Deliberately NOT a text regex scan of "BD X.XXX" anywhere on the page:
    an earlier version of this file did that and it kept reading OTHER
    things that happen to print that same pattern (the related carousel,
    and -- confirmed on a live run -- this theme's price-range/layered-
    navigation filter widget, e.g. "BD 3.300 - BD 4.300", which repeats the
    same handful of round numbers across thousands of unrelated pages).
    itemprop="price" is a deliberate, structured marker for one entity's
    own price, so a filter widget's decorative text has no reason to carry
    it, which is exactly why this is safe where a raw text scan wasn't."""
    main = soup.find(class_=lambda c: c and "product-info-main" in c)
    area = _widget_stripped_copy(str(main) if main else str(soup))
    tag = area.find(attrs={"itemprop": "price"})
    if not tag:
        return None
    raw = tag.get("content") or tag.get_text(strip=True)
    if not raw:
        return None
    try:
        return float(re.sub(r"[^0-9.]", "", raw))
    except (TypeError, ValueError):
        return None


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

    price = jsonld_price(soup)
    if price is None:
        # No usable JSON-LD on this page (confirmed: this is common on
        # genuinely out-of-stock pages); fall back to the scoped microdata
        # check instead of scanning the page's text (see microdata_price).
        price = microdata_price(soup)

    if price is None or price <= 0:
        # Genuinely out of stock (BMMI shows "Notify Me" with no price), not a
        # parsing failure -- we did find a real product name on a real product
        # page, there's just nothing to buy right now. Record it anyway so the
        # site can say "Out of Stock" for BMMI instead of "Not carried".
        return {"name": name, "price_bhd": None, "in_stock": False, "url": url,
                "retailer": "BMMI", "category": guess_category(name)}

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
                print(f"[{i}/{len(product_urls)}] OK  {item['name']} - {price_note}", file=sys.stderr)
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
