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

Run with: python scrapers/gbi.py
Requires: playwright (and `playwright install chromium` once, done in CI).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import extract_price, guess_category, write_json, polite_sleep

from playwright.sync_api import sync_playwright

BASE = "https://www.gbiexpress.com"
SEARCH_TERMS = ["whisky", "beer", "gin", "vodka", "wine", "champagne", "rum",
                "brandy", "tequila", "liqueur", "cider"]
MAX_PAGES_PER_TERM = 15
USER_AGENT = "PourChoicesBot/1.0 (+price comparison directory; respects robots.txt)"


def scrape_term(page, term):
    results = []
    for page_num in range(1, MAX_PAGES_PER_TERM + 1):
        url = f"{BASE}/index.php?route=product/search&search={term}&page={page_num}"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        cards = page.query_selector_all(".product-thumb, .product-layout")
        if not cards:
            break
        found_any = False
        for card in cards:
            text = card.inner_text()
            name_el = card.query_selector("h4 a, .caption h4 a, a")
            name = name_el.inner_text().strip() if name_el else None
            link_el = card.query_selector("a")
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
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        for term in SEARCH_TERMS:
            print(f"Searching GBI for '{term}'...", file=sys.stderr)
            try:
             items = scrape_term(page,
                                  term)
        except Exception as e:
          print(f" failed:{e}",
                file=sys.stderr)
          items = []
          for item in items:
                key = (item["name"], item["url"])
                if key not in seen:
                    seen.add(key)
                    all_results.append(item)
        browser.close()
    write_json("gbi.json", all_results)


if __name__ == "__main__":
    main()
