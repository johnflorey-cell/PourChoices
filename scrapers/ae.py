"""
African & Eastern (africanandeastern.com) scraper.

Compliance: robots.txt is wide open (Allow: /, no disallows found).

Approach: same search-driven method as GBI (see gbi.py) since the exact category
IDs weren't confirmed during investigation and a search-based crawl only touches
the same public search box a customer uses. Adjust SEARCH_TERMS or swap in
confirmed category URLs later if you find a more direct path (e.g. a specific
/whisky or /beer category listing that supports ?page=N, which earlier manual
checks did find worked for brand-slug pages like /jacobs-creek/...).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import extract_price, guess_category, write_json, polite_sleep

from playwright.sync_api import sync_playwright

BASE = "https://www.africanandeastern.com"
SEARCH_TERMS = ["whisky", "beer", "gin", "vodka", "wine", "champagne", "rum",
                "brandy", "tequila", "liqueur", "cider", "baileys"]
MAX_PAGES_PER_TERM = 15
USER_AGENT = "PourChoicesBot/1.0 (+price comparison directory; respects robots.txt)"


def scrape_term(page, term):
    results = []
    for page_num in range(1, MAX_PAGES_PER_TERM + 1):
        url = f"{BASE}/index.php?route=product/search&search={term}&page={page_num}"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        cards = page.query_selector_all(".product-thumb, .product-layout, .product-item")
        if not cards:
            break
        found_any = False
        for card in cards:
            text = card.inner_text()
            name_el = card.query_selector("h4 a, h3 a, .caption a, a")
            name = name_el.inner_text().strip() if name_el else None
            href = name_el.get_attribute("href") if name_el else None
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
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        for term in SEARCH_TERMS:
            print(f"Searching A&E for '{term}'...", file=sys.stderr)
            try:
                items = scrape_term(page, term)
            except Exception as e:
                print(f"  failed: {e}", file=sys.stderr)
                items = []
            for item in items:
                key = (item["name"], item["url"])
                if key not in seen:
                    seen.add(key)
                    all_results.append(item)
        browser.close()
    write_json("ae.json", all_results)


if __name__ == "__main__":
    main()
