"""
GBI Express (gbiexpress.com) scraper.

Compliance: robots.txt disallows only /stage/, /check/, /gbibackoffice/, /newapi/.
Query-string search URLs (?route=product/search&search=...) are not disallowed.

Approach: GBI is OpenCart-based. Rather than guess internal category IDs (which
can change), we drive their own search for one keyword per category we care
about and page through OpenCart's standard &page=N until a page comes back
with no product cards. This is slower than a direct category browse but only
touches allowed, documented site functionality (the same search box a customer
would use).

Proxy: gbiexpress.com puts a Cloudflare bot-challenge in front of every search
request when it's hit from GitHub Actions' shared runner IPs. connect_browser()
in _common.py routes through Bright Data's Scraping Browser instead, once the
BRIGHTDATA_AUTH GitHub Actions secret is set. Confirmed working: a live run got
past Cloudflare (real page titles and product text came back instead of "Just a
moment..."), but pages load slowly through the proxy, so navigation timeouts are
generous here (120 seconds) and a failed page load gets one retry before giving
up on that page.

Name extraction (2026-09-14): same defensive fix as ae.py, picking the LONGEST
non-boilerplate link text in each card instead of the first one, since a card
can carry wishlist/compare/etc links besides the product name. GBI was already
finding real products (706 on the last run) with the simpler "first link"
logic, so this is a safety margin against edge cases rather than a fix for a
known failure here, plus it adds the same per-card debug output as ae.py.

Category fix (2026-09-15): this used to categorize every result by whichever
SEARCH TERM found it (guess_category(term)), not by the product's own name.
GBI's own search box doesn't only return exact matches, so searching "beer"
could surface an unrelated product (e.g. a vodka mixer), and that product
would get permanently labelled "Beer" just because that's the term that found
it. Now categorizes by the product's actual scraped name instead, same as
bmmi.py and nhsc.py already did.

Out-of-stock fix (2026-09-15): GBI's own product cards keep showing a price
even when an item is out of stock, and unlike A&E there's no "Out of Stock"
wording on the card at all -- instead the add-to-cart icon's image file
switches from "cart.svg" to "cart-out.svg" (confirmed on the live "Chivas
Regal 18 Year Old Blended Whisky" and "Fireball Whisky Liqueur" listings).
Fixed by checking each card for that icon and recording in_stock
accordingly, so the site can show "Out of Stock" for GBI instead of a
live-looking price.

Run with: python scrapers/gbi.py
Requires: playwright (and `playwright install chromium` once, done in CI).
"""
import sys
from urllib.parse import quote_plus
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import brand_search_terms, term_already_covered, extract_price, guess_category, write_json, polite_sleep, connect_browser, block_heavy_resources

from playwright.sync_api import sync_playwright

BASE = "https://www.gbiexpress.com"
SEARCH_TERMS = ["whisky", "beer", "gin", "vodka", "wine", "champagne", "rum",
                "brandy", "tequila", "liqueur", "cider"]
# Raised from 15 (2026-10-10): GBI's "gin" and "wine" searches both hit the
# old 15-page cap (240 products each) and were silently cut off there.
# Pagination still stops on its own at the first page with no products.
MAX_PAGES_PER_TERM = 40
# Full-catalogue listing (see main()): 100 products per page, so 30 pages
# allows for a catalogue of up to 3,000; MIN_FULL_LISTING is the "this
# clearly worked" threshold below which the old keyword searches run instead.
MAX_LISTING_PAGES = 30
MIN_FULL_LISTING = 300
# Brand searches (see brand_search_terms() in _common.py): a single brand
# rarely runs past a few pages, so they get a lower cap, and the whole
# brand-search phase stops after BRAND_SEARCH_MINUTES so this scraper can
# never push the weekly run past GitHub's time limit. Both can be changed
# here (or via the env vars) without touching anything else.
import os, time
MAX_PAGES_PER_BRAND = int(os.environ.get("MAX_PAGES_PER_BRAND", "5"))
MAX_BRAND_TERMS = int(os.environ.get("MAX_BRAND_TERMS", "300"))
BRAND_SEARCH_MINUTES = float(os.environ.get("BRAND_SEARCH_MINUTES", "60"))
NAV_TIMEOUT = 120000  # proxy adds latency; matches Bright Data's own guidance
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

NAME_BLOCKLIST = {
    "wishlist", "wish list", "add to wishlist", "add to wish list",
    "compare", "add to compare", "add to cart", "cart", "buy now",
    "quick view", "view", "details", "more info", "more details",
    "notify me", "out of stock", "add", "share", "",
}


def safe_goto(page, url, timeout=NAV_TIMEOUT):
    """page.goto with one retry, so a single slow/interrupted navigation
    (common when running through a proxy) doesn't throw away an entire
    search term's results."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception:
        page.wait_for_timeout(3000)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)


def dismiss_age_gate(page):
    safe_goto(page, BASE)
    page.wait_for_timeout(2000)
    try:
        safe_goto(page, f"{BASE}/index.php?route=check/age/confirm")
        page.wait_for_timeout(2000)
        return
    except Exception:
        pass
    try:
        confirm_btn = page.query_selector(
            "a:has-text('Enter'), button:has-text('Enter'), a:has-text('Verify'), button:has-text('Verify'), a:has-text('Confirm'), button:has-text('Confirm')"
        )
        if confirm_btn:
            confirm_btn.click()
            page.wait_for_timeout(3000)
            return
    except Exception:
        pass
    try:
        day_field = page.query_selector("select[name='day'], input[name='day']")
        month_field = page.query_selector("select[name='month'], input[name='month']")
        year_field = page.query_selector("select[name='year'], input[name='year']")
        if day_field and month_field and year_field:
            day_field.fill("1")
            month_field.fill("1")
            year_field.fill("1990")
            submit_btn = page.query_selector("button[type='submit'], input[type='submit']")
            if submit_btn:
                submit_btn.click()
                page.wait_for_timeout(3000)
    except Exception:
        pass


def pick_card_name(card):
    """Return (name, href) for a product card, using the LONGEST non-junk
    link text instead of the first one, since real product names are
    reliably longer than the wishlist/compare/etc labels themes add."""
    best_name, best_href = None, None
    for a in card.query_selector_all("a"):
        t = a.inner_text().strip()
        if not t or t.lower() in NAME_BLOCKLIST:
            continue
        if best_name is None or len(t) > len(best_name):
            best_name = t
            best_href = a.get_attribute("href")
    return best_name, best_href


def scrape_term(page, term, debug=False, max_pages=None, url_for_page=None):
    """Reads one search term's result pages (or, with url_for_page, any
    paginated product listing) until a page comes back with no products."""
    results = []
    seen_names = set()
    for page_num in range(1, (max_pages or MAX_PAGES_PER_TERM) + 1):
        if url_for_page:
            url = url_for_page(page_num)
        else:
            url = f"{BASE}/index.php?route=product/search&search={quote_plus(term)}&page={page_num}"
        safe_goto(page, url)
        page.wait_for_timeout(1500)
        if debug and page_num == 1:
            print(f"  [DEBUG] final url: {page.url}", file=sys.stderr)
            print(f"  [DEBUG] page title: {page.title()!r}", file=sys.stderr)
            for sel in [".featured-box", ".product-thumb", ".product-layout"]:
                print(f"  [DEBUG] selector {sel!r} matches: {len(page.query_selector_all(sel))}", file=sys.stderr)
            snippet = page.inner_text("body")[:500].replace("\n", " | ")
            print(f"  [DEBUG] body snippet: {snippet!r}", file=sys.stderr)
        cards = page.query_selector_all(".featured-box, .product-thumb, .product-layout")
        if not cards:
            break
        if debug and page_num == 1:
            print(f"  [DEBUG] {len(cards)} card(s) found on page 1, checking each one:", file=sys.stderr)
        found_any = False
        for i, card in enumerate(cards):
            text = card.inner_text()
            name, href = pick_card_name(card)
            price = extract_price(text)
            # GBI marks an unavailable item by swapping the cart icon's image
            # to "cart-out.svg" instead of any wording on the card itself.
            out_of_stock = card.query_selector("img[src*='cart-out']") is not None
            if debug and page_num == 1:
                print(f"    [DEBUG] card {i}: name={name!r} price={price!r} out_of_stock={out_of_stock}", file=sys.stderr)
            if name and price is not None:
                results.append({"name": name, "price_bhd": price, "in_stock": not out_of_stock,
                                 "url": href, "retailer": "GBI Express",
                                 "category": guess_category(name)})
                found_any = True
        if not found_any:
            break
        # Stop if this page added nothing new (some sites send an
        # out-of-range page number back to the last real page, which would
        # otherwise loop until the page cap re-reading the same products).
        names_now = {r["name"] for r in results}
        if names_now == seen_names:
            break
        seen_names = names_now
        polite_sleep(1)
    return results


def main():
    all_results = []
    seen = set()
    with sync_playwright() as p:
        browser, page = connect_browser(p, USER_AGENT)
        block_heavy_resources(page)
        dismiss_age_gate(page)
        print(f"[DEBUG] After age gate, page.url = {page.url}", file=sys.stderr)
        # Full-catalogue listing (2026-10-10): checked in the shop's own site
        # that this one paginated listing shows EVERY product the shop sells
        # online, 100 per page -- GBI: the "Product filter" page (category path=0) with limit=100 returned all 599 products (6 pages) on 10 Oct 2026.
        # That's the whole range in about 6 page loads, instead of ~50
        # keyword-search pages that only found products whose name happened
        # to contain the keyword. The old keyword searches below only run
        # if this listing ever comes back suspiciously short (e.g. the shop
        # changes its site), so the scraper degrades gracefully instead of
        # returning nothing.
        print("Reading GBI full product listing...", file=sys.stderr)
        try:
            listed = scrape_term(page, "(full listing)", debug=True, max_pages=MAX_LISTING_PAGES,
                                 url_for_page=lambda page_num: f"{BASE}/index.php?route=product/category&path=0&limit=100&page={page_num}")
        except Exception as e:
            print(f"  full listing failed: {e}", file=sys.stderr)
            listed = []
        for item in listed:
            key = (item["name"], item["url"])
            if key not in seen:
                seen.add(key)
                all_results.append(item)
        print(f"Full listing found {len(all_results)} products", file=sys.stderr)
        full_listing_ok = len(all_results) >= MIN_FULL_LISTING

        first = True
        for term in ([] if full_listing_ok else SEARCH_TERMS):
            print(f"Searching GBI for '{term}'...", file=sys.stderr)
            try:
                items = scrape_term(page, term, debug=first)
            except Exception as e:
                print(f"  failed: {e}", file=sys.stderr)
                items = []
            first = False
            for item in items:
                key = (item["name"], item["url"])
                if key not in seen:
                    seen.add(key)
                    all_results.append(item)

        # Phase 2, brand searches (2026-10-10): look for the brands BMMI
        # sells, skipping any brand the category searches above already
        # found at this shop.
        names = [r["name"].lower() for r in all_results]
        # Not needed when the full listing worked: it already has every
        # product, brands included.
        brand_terms = [] if full_listing_ok else [t for t in brand_search_terms(MAX_BRAND_TERMS) if not term_already_covered(t, names)]
        print(f"Brand searches: {len(brand_terms)} brands to look for at GBI", file=sys.stderr)
        deadline = time.time() + BRAND_SEARCH_MINUTES * 60
        before = len(all_results)
        searched = 0
        for term in brand_terms:
            if time.time() > deadline:
                print(f"  Brand-search time budget ({BRAND_SEARCH_MINUTES:.0f} min) used up after {searched} brands; the rest wait for next week's run.", file=sys.stderr)
                break
            searched += 1
            try:
                items = scrape_term(page, term, max_pages=MAX_PAGES_PER_BRAND)
            except Exception as e:
                print(f"  brand '{term}' failed: {e}", file=sys.stderr)
                items = []
            for item in items:
                key = (item["name"], item["url"])
                if key not in seen:
                    seen.add(key)
                    all_results.append(item)
        print(f"Brand searches added {len(all_results) - before} new products ({searched} brands searched)", file=sys.stderr)
        browser.close()
    write_json("gbi.json", all_results)


if __name__ == "__main__":
    main()
