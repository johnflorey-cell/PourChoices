"""
NHSC (nhscbahrain.com) scraper.

Compliance: robots.txt is permissive (Allow: /, disallows only /beta/, /admin/,
/nhsbackoffice/).

NHSC's real catalog only renders after a JS-driven "Verify" click on the age
gate sets a session cookie, so this needs a real browser (Playwright), not a
plain HTTP fetch. Once the gate is dismissed, its category pages paginate via
?limit=100 in the same browser session/cookie.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import extract_price, guess_category, write_json, polite_sleep

from playwright.sync_api import sync_playwright

BASE = "https://www.nhscbahrain.com"
CATEGORY_URLS = [
    f"{BASE}/Spirits-NHSC-bahrain?limit=100",
    f"{BASE}/Beers-nhsc-bahrain/beers-and-ciders-nhsc-bahrain?limit=100",
    f"{BASE}/wines-wine?limit=100",
    f"{BASE}/wines-wine/sparkling?limit=100",
]
USER_AGENT = "PourChoicesBot/1.0 (+price comparison directory; respects robots.txt)"


def dismiss_age_gate(page):
    page.goto(BASE, wait_until="networkidle", timeout=30000)
    verify_link = page.query_selector("a:has-text('Verify')")
    if verify_link:
        verify_link.click()
        page.wait_for_timeout(1500)


def scrape_category(page, url):
    results = []
    page.goto(url, wait_until="networkidle", timeout=30000)
    cards = page.query_selector_all("[class*=product], .item, .product-item, .product-card")
    for card in cards:
        text = card.inner_text()
        name_el = card.query_selector("a")
        name = name_el.inner_text().strip() if name_el else None
        href = name_el.get_attribute("href") if name_el else None
        price = extract_price(text)
        if name and price is not None:
            results.append({"name": name, "price_bhd": price, "url": href, "retailer": "NHSC",
                             "category": guess_category(name)})
    return results


def main():
    all_results = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        dismiss_age_gate(page)
        for url in CATEGORY_URLS:
            print(f"Scraping NHSC category: {url}", file=sys.stderr)
            try:
                items = scrape_category(page, url)
            except Exception as e:
                print(f"  failed: {e}", file=sys.stderr)
                items = []
            for item in items:
                key = (item["name"], item["url"])
                if key not in seen:
                    seen.add(key)
                    all_results.append(item)
            polite_sleep(1.5)
        browser.close()
    write_json("nhsc.json", all_results)


if __name__ == "__main__":
    main()
