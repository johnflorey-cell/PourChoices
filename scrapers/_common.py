"""Shared helpers for the Playwright-based scrapers (African & Eastern, GBI Express, NHSC).

These three sites render their catalogs client-side (confirmed during manual
investigation: a plain HTTP fetch returns an empty shell or the age-gate only),
so we drive a real headless browser instead of requests/BeautifulSoup.

Price parsing: all three sites price in Bahraini Dinar, almost always printed as
"BD 12.345" or "BHD 12.345" or just "12.345" next to a currency symbol. We match
broadly and take the smallest number found near each product card, since a
discounted item shows both its sale price and its struck-through original price,
and the smallest is what a customer actually pays.

Category guessing fix #1 (2026-09-15): guess_category() used to check whether a
keyword like "ale" appeared ANYWHERE inside the text, including in the middle
of an unrelated word. That's why wines and other non-beer products were
showing up under the Beer category: "Ducale", "Salento", "Moncigale", "Whale"
and "Pale" all contain the letters "ale" as a substring even though none of
them have anything to do with beer. Fixed by only matching a keyword when it
appears as its own whole word.

Category guessing fix #2 (2026-09-15): the keyword lists only covered generic
category words ("beer", "lager", "whisky", ...), so a well-known brand whose
own product name doesn't happen to include one of those words fell through to
"Other Spirits" even though any shopper would recognise it instantly: "Heineken
Can 33cl", "Budweiser Cans 35.5cl", "Carlsberg Cans 50cl" and "Stella Artois
Cans" all have no literal "beer" or "lager" in the name, and "Johnnie Walker
Double Black 1LTR" has no literal "whisky" in the name either. Fixed by adding
the major brand names sold by these 4 retailers directly to the relevant
category's keyword list, so the brand itself is enough to categorise correctly
even when the generic category word is missing from that particular listing.

Out-of-stock detection (2026-09-15): BMMI's own fix already skips a product
with no price left after removing the related-products carousel (see
bmmi.py) rather than borrowing another product's price, but until now a
genuinely out-of-stock item on A&E/GBI/NHSC was still recorded as if it were
a normal, purchasable listing, with no way for the site to tell a visitor
"this retailer carries it but it's currently out of stock" instead of
showing a plain price as if it were available right now. Added
looks_out_of_stock() below as a shared text-phrase check the Playwright
scrapers can use (each site's own out-of-stock signal still needs checking
against a real run; see the note in looks_out_of_stock() itself and each
scraper's own docstring for what's confirmed vs. best-effort per retailer).

Category guessing fix #3 (2026-09-15): confirmed live that "Red Horse 50cl
Cans X 24" and "Red Horse Extra Strong 33cl Cans X24" (both genuine beers --
GBI's own product page describes "Red Horse" as a "Strong Lager") were
landing in "Other Spirits" because the product name has no literal "beer" or
"lager" in it, and "red horse" wasn't in the brand list added by fix #2 above.
The same scan turned up a dozen more real beer brands sold across these 4
sites that were falling into the same gap for the same reason: Benediktiner,
Bira 91 (its "Boom Strong" cans, specifically -- the "Blonde Summer Lager"
listings already matched on the word "lager"), Bitburger, Budvar, Buzz
(the canned beer, not "Buzzballz", a separate tequila-based drink that
doesn't share the whole word "buzz"), Carling, Greenberg, Kalyani, Kilkenny,
Malayali, Singha, VITALSBERG, and "San Mig" as a shorthand for San Miguel
(San Miguel itself was already in the list, but "San Mig Light 33cl Bottles
X24" abbreviates the name enough that it didn't match). All of these are
added to the Beer brand list below, the same fix as #2, just catching up on
brands #2 missed rather than a new mechanism.

Note: the product's own page on GBI's site (as opposed to the search-results
list this scraper actually reads) prints a fuller description that also says
"Strong Lager" in so many words. That description text isn't available here
because the scraper only visits GBI's search-results pages -- reading each
product's own page too would mean one extra page load per product (roughly
1,700+ more requests through the paid Bright Data proxy every single scrape,
on top of what's already fetched), so the brand-list approach above is the
cheaper general fix and was preferred over visiting every product page.
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
# Generic category words come first in each list; well-known brand names are
# appended afterwards so a listing missing the generic word (e.g. "Heineken
# Can 33cl" has no literal "beer") still lands in the right place.
CATEGORY_KEYWORDS = [
    (("whisky", "whiskey", "scotch", "bourbon",
      # Brand names that don't always include "whisky" in the listing itself.
      "johnnie walker", "jack daniel", "chivas", "jameson", "glenfiddich",
      "glenlivet", "macallan", "ballantine", "grouse", "bushmills",
      "teacher", "dewar"), "Whisky"),
    (("beer", "lager", "ale", "cider", "stout",
      # Major beer brands whose own product name often skips the word "beer".
      "heineken", "budweiser", "bud light", "carlsberg", "amstel", "corona",
      "stella", "guinness", "kingfisher", "san miguel", "san mig", "tiger",
      "asahi", "sapporo", "fosters", "coors", "miller", "peroni", "beck",
      "grolsch", "hoegaarden", "erdinger", "tsingtao", "leffe",
      # Added by category guessing fix #3: more beer brands found missing
      # the same way (name has no "beer"/"lager" wording of its own).
      "red horse", "benediktiner", "bira", "bitburger", "budvar", "buzz",
      "carling", "greenberg", "kalyani", "kilkenny", "malayali", "singha",
      "vitalsberg"), "Beer"),
    (("gin",), "Gin"),
    (("vodka",), "Vodka"),
    (("wine", "shiraz", "cabernet", "merlot", "chardonnay", "sauvignon", "rose", "rosé"), "Wine"),
    (("champagne", "sparkling", "prosecco", "cava", "moet", "veuve"), "Champagne"),
    (("rum", "brandy", "cognac", "tequila", "liqueur", "baileys", "sambuca", "amaretto",
      "vermouth", "sherry", "absinthe"), "Other Spirits"),
]


def guess_category(text):
    """Best-effort category guess from a search term or product name. Falls
    back to "Other Spirits" (the app's catch-all) when nothing matches.

    Matches each keyword as a whole word only (using \\b word boundaries), not
    as a substring, so a keyword like "ale" matches the word "ale" but not
    the "ale" hiding inside "Ducale", "Salento", "Whale" or "Pale". A
    multi-word keyword like "johnnie walker" or "bud light" matches the same
    way, as a whole phrase with a word boundary on each side."""
    lowered = text.lower()
    for keywords, category in CATEGORY_KEYWORDS:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", lowered):
                return category
    return "Other Spirits"


def extract_price(text):
    """Return the smallest BD-style price found in a chunk of text, or None."""
    matches = [float(m) for m in PRICE_RE.findall(text)]
    return min(matches) if matches else None


# Phrases these sites use on a product card to say an item can't currently be
# bought, even though a price is often still shown alongside it (A&E keeps
# showing the price with "Out Of Stock" printed right next to it). Checked as
# whole words/phrases, case-insensitive, against the card's own visible text.
OUT_OF_STOCK_PHRASES = (
    "out of stock", "sold out", "notify me", "notify when back",
    "currently unavailable", "unavailable",
)


def looks_out_of_stock(text):
    """True if any known out-of-stock phrase appears in this card/page's own
    visible text. Best-effort: confirmed against African & Eastern's own
    wording ("Out Of Stock" printed on the card); GBI Express instead marks
    an unavailable item with a different cart-button icon (handled directly
    in gbi.py, not through this text check) rather than any wording, so this
    function won't catch GBI's case. NHSC's own wording wasn't confirmed
    directly, so treat a False from this function for NHSC as "no known
    out-of-stock phrase seen", not a confirmed in-stock status, until a real
    run has been eyeballed against the live site."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in OUT_OF_STOCK_PHRASES)


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
