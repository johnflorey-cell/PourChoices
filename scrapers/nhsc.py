"""
NHSC (nhscbahrain.com) scraper.

Compliance: robots.txt is permissive (Allow: /, disallows only /beta/, /admin/,
/nhsbackoffice/).

NHSC's real catalog only renders after a JS-driven "Verify" click on the age
gate sets a session cookie, so this needs a real browser (Playwright), not a
plain HTTP fetch. Once the gate is dismissed, its category pages paginate via
?limit=100, plus &page=N for later pages, in the same browser session/cookie.

Card structure: each product sits in a <div class="slider-item">. Rather than
guessing one fixed selector for the name link (which broke twice, since NHSC's
different page templates nest the link differently), scrape_category() scans
every <a> inside the card and takes the first one with real visible text.

Coverage check: NHSC's own category pages print their true total in the page
text (e.g. "Showing 1 to 100 of 203 (3 Pages)"). extract_reported_total() reads
that number back out and main() compares it to how many distinct products we
actually kept for that category, printing a clear OK/WARNING line every run so
under-scraping (like the earlier 100-item-per-category cap) shows up in the
log immediately instead of needing a manual card count.
"""
import re
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def dismiss_age_gate(page):
    page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)
    try:
        page.evaluate("document.forms['default'].submit()")
        page.wait_for_timeout(2000)
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
        page.wait_for_timeout(2000)
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
        page.wait_for_timeout(2000)


def extract_reported_total(page):
    """Read NHSC's own "Showing 1 to 100 of 203 (3 Pages)" text back out, so
    we can compare the site's stated total against what we actually kept."""
    text = page.inner_text("body")
    m = re.search(r"of\s+([\d,]+)\s*\(", text)
    if not m:
        m = re.search(r"of\s+([\d,]+)\s+item", text, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def scrape_category(page, url, debug=False):
    results = []
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    if debug:
        print(f"  [DEBUG] final url: {page.url}", file=sys.stderr)
        print(f"  [DEBUG] page title: {page.title()!r}", file=sys.stderr)
        print(f"  [DEBUG] selector '.slider-item' matches: {len(page.query_selector_all('.slider-item'))}", file=sys.stderr)
        snippet = page.inner_text("body")[:500].replace("\n", " | ")
        print(f"  [DEBUG] body snippet: {snippet!r}", file=sys.stderr)
    cards = page.query_selector_all(".slider-item")
    if not cards:
        cards = page.query_selector_all("[class*=product], .item, .product-item, .product-card")
    skipped_no_name = 0
    skipped_no_price = 0
    for i, card in enumerate(cards):
        text = card.inner_text()
        # Don't guess a single fixed selector for the name link: scan every
        # <a> inside the card and take the first one that actually has
        # visible text. (The earlier bug: the card's FIRST <a> was an
        # image-wrapping link with no text, so a hard-coded ":scope > a, a"
        # or ".title a, a" selector silently grabbed the wrong, empty anchor
        # and every product got skipped.)
        name = None
        href = None
        for a in card.query_selector_all("a"):
            t = a.inner_text().strip()
            if t:
                name = t
                href = a.get_attribute("href")
                break
        price = extract_price(text)
        if debug and i < 3:
            outer = card.evaluate("el => el.outerHTML")[:400]
            print(f"  [DEBUG] card {i}: name={name!r} price={price!r} outerHTML={outer!r}", file=sys.stderr)
        if not name:
            skipped_no_name += 1
            continue
        if price is None:
            skipped_no_price += 1
            continue
        results.append({"name": name, "price_bhd": price, "url": href, "retailer": "NHSC",
                         "category": guess_category(name)})
    if debug:
        print(f"  [DEBUG] cards={len(cards)} kept={len(results)} skipped_no_name={skipped_no_name} skipped_no_price={skipped_no_price}", file=sys.stderr)
    return results


MAX_PAGES_PER_CATEGORY = 20


def main():
    all_results = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        page = browser.new_page(user_agent=USER_AGENT)
        dismiss_age_gate(page)
        print(f"[DEBUG] After age gate, page.url = {page.url}", file=sys.stderr)
        first = True
        for base_url in CATEGORY_URLS:
            category_new_count = 0
            reported_total = None
            for page_num in range(1, MAX_PAGES_PER_CATEGORY + 1):
                # NHSC's OpenCart-style pagination: page 1 is the bare
                # ?limit=100 URL, later pages add &page=N. A category with
                # more than 100 products (confirmed happening, e.g. Spirits
                # showed "203" items across "3 Pages") was previously never
                # having its later pages requested at all, so anything past
                # the first 100 per category was silently missed.
                url = base_url if page_num == 1 else f"{base_url}&page={page_num}"
                print(f"Scraping NHSC category: {url}", file=sys.stderr)
                try:
                    items = scrape_category(page, url, debug=(first and page_num == 1))
                    if page_num == 1:
                        reported_total = extract_reported_total(page)
                except Exception as e:
                    print(f"  failed: {e}", file=sys.stderr)
                    items = []
                if page_num == 1:
                    first = False
                new_count = 0
                for item in items:
                    key = (item["name"], item["url"])
                    if key not in seen:
                        seen.add(key)
                        all_results.append(item)
                        new_count += 1
                category_new_count += new_count
                polite_sleep(1.5)
                # Stop paginating this category once a page brings back no
                # products at all, or no NEW ones (some sites clamp an
                # out-of-range page back to page 1, which would otherwise
                # loop forever re-adding the same items).
                if not items or new_count == 0:
                    break
            category_name = base_url.split("?")[0].rsplit("/", 1)[-1]
            if reported_total is not None:
                status = "OK" if category_new_count >= reported_total else "WARNING: fewer than site reports, likely incomplete"
                print(f"  NHSC coverage check [{category_name}]: site reports {reported_total}, captured {category_new_count} -> {status}", file=sys.stderr)
            else:
                print(f"  NHSC coverage check [{category_name}]: could not read a site-reported total, captured {category_new_count} (unverified)", file=sys.stderr)
        browser.close()
    write_json("nhsc.json", all_results)


if __name__ == "__main__":
    main()
