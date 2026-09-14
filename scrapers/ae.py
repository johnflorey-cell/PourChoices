"""
African & Eastern (africanandeastern.com) scraper.

Compliance: robots.txt is wide open (Allow: /, no disallows found).

Approach: same search-driven method as GBI (see gbi.py) since the exact category
IDs weren't confirmed during investigation and a search-based crawl only touches
the same public search box a customer uses. Adjust SEARCH_TERMS or swap in
confirmed category URLs later if you find a more direct path (e.g. a specific
/whisky or /beer category listing that supports ?page=N, which earlier manual
checks did find worked for brand-slug pages like /jacobs-creek/...).

Proxy: africanandeastern.com puts a Cloudflare bot-challenge in front of every
search request when it's hit from GitHub Actions' shared runner IPs.
connect_browser() in _common.py routes through Bright Data's Scraping Browser
instead, once the BRIGHTDATA_AUTH GitHub Actions secret is set. Confirmed
working: a live run got past Cloudflare (real page titles and product text
came back instead of "Just a moment..."), but pages load slowly through the
proxy, so navigation timeouts are generous here (120 seconds) and a failed
page load gets one retry before giving up on that page.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import extract_price, guess_category, write_json, polite_sleep, connect_browser, block_heavy_resources

from playwright.sync_api import sync_playwright

BASE = "https://www.africanandeastern.com"
SEARCH_TERMS = ["whisky", "beer", "gin", "vodka", "wine", "champagne", "rum",
                "brandy", "tequila", "liqueur", "cider", "baileys"]
MAX_PAGES_PER_TERM = 15
NAV_TIMEOUT = 120000  # proxy adds latency; matches Bright Data's own guidance
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


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
    page.wait_for_timeout(1500)
    try:
        page.evaluate("document.forms['default'].submit()")
        page.wait_for_timeout(4000)
        return
    except Exception:
        pass

    confirm_btn = page.query_selector(
        "button:has-text('Verify'),a:has-text('Verify'),"
        "button:has-text('Enter'),a:has-text('Enter'),"
        "button:has-text('Confirm'),a:has-text('Confirm')"
    )
    if confirm_btn:
        confirm_btn.click()
        page.wait_for_timeout(4000)
        return

    day_field = page.query_selector("select[name*='day' i], input[name*='day' i]")
    month_field = page.query_selector("select[name*='month' i], input[name*='month' i]")
    year_field = page.query_selector("select[name*='year' i], input[name*='year' i]")
    if day_field and month_field and year_field:
        try:
            day_field.select_option("1")
        except Exception:
            day_field.fill("1")
        try:
            month_field.select_option("1")
        except Exception:
            month_field.fill("1")
        try:
            year_field.select_option("1990")
        except Exception:
            year_field.fill("1990")
        submit_btn = page.query_selector(
            "button[type=submit], input[type=submit], "
            "button:has-text('Submit'),button:has-text('Continue'),button:has-text('Confirm')"
        )
        if submit_btn:
            submit_btn.click()
        page.wait_for_timeout(4000)


def scrape_term(page, term, debug=False):
    results = []
    for page_num in range(1, MAX_PAGES_PER_TERM + 1):
        url = f"{BASE}/index.php?route=product/search&search={term}&page={page_num}"
        safe_goto(page, url)
        page.wait_for_timeout(1500)
        if debug and page_num == 1:
            print(f"  [DEBUG] final url: {page.url}", file=sys.stderr)
            print(f"  [DEBUG] page title: {page.title()!r}", file=sys.stderr)
            for sel in [".featured-box", ".product-thumb", ".product-layout", ".product-item"]:
                print(f"  [DEBUG] selector {sel!r} matches: {len(page.query_selector_all(sel))}", file=sys.stderr)
            snippet = page.inner_text("body")[:500].replace("\n", " | ")
            print(f"  [DEBUG] body snippet: {snippet!r}", file=sys.stderr)
        cards = page.query_selector_all(".featured-box, .product-thumb, .product-layout, .product-item")
        if not cards:
            break
        found_any = False
        for card in cards:
            text = card.inner_text()
            name = None
            href = None
            for a in card.query_selector_all("a"):
                t = a.inner_text().strip()
                if t:
                    name = t
                    href = a.get_attribute("href")
                    break
            price = extract_price(text)
            if name and price is not None:
                results.append({"name": name, "price_bhd": price, "url": href, "retailer": "African & Eastern",
                                 "category": guess_category(term)})
                found_any = True
        if not found_any:
            break
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
        first = True
        for term in SEARCH_TERMS:
            print(f"Searching A&E for '{term}'...", file=sys.stderr)
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
        browser.close()
    write_json("ae.json", all_results)


if __name__ == "__main__":
    main()
