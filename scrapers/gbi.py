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
request when it's hit from GitHub Actions' shared runner IPs (confirmed via
live runs: page title "Just a moment...", a Cloudflare Ray ID, and 0 products
every time, regardless of selectors). connect_browser() in _common.py routes
through Bright Data's Scraping Browser instead, once the BRIGHTDATA_AUTH
GitHub Actions secret is set; see the setup notes in this repo for how to get
that credential and add the secret.

Run with: python scrapers/gbi.py
Requires: playwright (and `playwright install chromium` once, done in CI).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import extract_price, guess_category, write_json, polite_sleep, connect_browser, block_heavy_resources

from playwright.sync_api import sync_playwright

BASE = "https://www.gbiexpress.com"
SEARCH_TERMS = ["whisky", "beer", "gin", "vodka", "wine", "champagne", "rum",
                "brandy", "tequila", "liqueur", "cider"]
MAX_PAGES_PER_TERM = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def dismiss_age_gate(page):
    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    try:
        page.goto(f"{BASE}/index.php?route=check/age/confirm", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        return
    except Exception:
        pass
    try:
        confirm_btn = page.query_selector(
            "a:has-text('Enter'), button:has-text('Enter'), a:has-text('Verify'), button:has-text('Verify'), a:has-text('Confirm'), button:has-text('Confirm')"
        )
        if confirm_btn:
            confirm_btn.click()
            page.wait_for_timeout(2000)
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
                page.wait_for_timeout(2000)
    except Exception:
        pass


def scrape_term(page, term, debug=False):
    results = []
    for page_num in range(1, MAX_PAGES_PER_TERM + 1):
        url = f"{BASE}/index.php?route=product/search&search={term}&page={page_num}"
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
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
        found_any = False
        for card in cards:
            text = card.inner_text()
            name_el = card.query_selector(".title a, h4 a, .caption h4 a, a")
            name = name_el.inner_text().strip() if name_el else None
            link_el = card.query_selector(".title a, a")
            href = link_el.get_attribute("href") if link_el else None
            price = extract_price(text)
            if name and price is not None:
                results.append({"name": name, "price_bhd": price, "url": href, "retailer": "GBI Express",
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
        browser.close()
    write_json("gbi.json", all_results)


if __name__ == "__main__":
    main()
