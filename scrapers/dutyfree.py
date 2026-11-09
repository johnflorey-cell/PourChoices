"""Bahrain Duty Free (Arrivals shop) liquor prices -> data/dutyfree.json

Added 2026-11-09. Duty Free is not one of the four shop boxes on a card. It
shows as one extra line at the bottom of a card, only when Duty Free sells
the same bottle, and it never counts as the "cheapest" price or the savings
badge (only travellers can buy there, within the customs allowance).

The Arrivals liquor list (https://bdutyfree.com/arrival/liquor.html) is a
plain server-rendered shop page, 30 drinks a page, around 430 in all, no
login needed. The site has no robots.txt at all (checked 2026-11-08), so no
pages are off limits; we still read only the liquor list pages, one at a
time with a pause, and never the cart, account or checkout pages.

First try: a plain web request (cheap, no proxy credit used). If that is
blocked from GitHub's servers, it falls back to the Bright Data browser the
A&E and GBI scrapers already use (BRIGHTDATA_AUTH secret), reading the same
pages. Each page is small, so the proxy cost is tiny.
"""
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_json, connect_browser, block_heavy_resources

BASE_URL = "https://bdutyfree.com/arrival/liquor.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PourChoicesBot/1.0; +https://pour-choices.com/; price comparison directory)",
    "Accept-Language": "en",
}
SLEEP_SECONDS = 2.0
MAX_PAGES = 40  # safety stop; the list was 15 pages on 2026-11-09


def parse_page(html):
    """All products on one list page: name, price, link, in-stock flag."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for card in soup.select("li.product-item"):
        link = card.select_one("a.product-item-link")
        price_el = card.select_one('[data-price-type="finalPrice"]')
        if not link or not price_el:
            continue
        name = " ".join(link.get_text(" ", strip=True).split())
        try:
            price = round(float(price_el.get("data-price-amount")), 3)
        except (TypeError, ValueError):
            continue
        if not name or price <= 0:
            continue
        # Each card carries a stock label for the shop's analytics
        # (data-dimension10="In stock"); fall back to the visible text.
        stock_el = card.select_one("[data-dimension10]")
        stock = (stock_el.get("data-dimension10") if stock_el else card.get_text(" ", strip=True)).lower()
        items.append({
            "name": name,
            "price_bhd": price,
            "in_stock": "out of stock" not in stock,
            "url": link.get("href"),
        })
    return items


def page_url(n):
    return BASE_URL if n == 1 else f"{BASE_URL}?p={n}"


def crawl(get_html):
    """Reads page 1, 2, 3... until a page is empty or only repeats products
    already seen (Magento shows the last page again past the end)."""
    results, seen = [], set()
    for n in range(1, MAX_PAGES + 1):
        page_items = parse_page(get_html(page_url(n)))
        new = [i for i in page_items if i["url"] not in seen]
        print(f"  page {n}: {len(page_items)} products, {len(new)} new", flush=True)
        if not new:
            break
        for i in new:
            seen.add(i["url"])
        results.extend(new)
        time.sleep(SLEEP_SECONDS)
    return results


def get_plain(url):
    resp = requests.get(url, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    return resp.text


def crawl_with_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, page = connect_browser(p, HEADERS["User-Agent"])
        block_heavy_resources(page)

        def get_html(url):
            page.goto(url, wait_until="domcontentloaded")
            return page.content()
        try:
            return crawl(get_html)
        finally:
            browser.close()


def main():
    results = []
    try:
        print("Reading Bahrain Duty Free (Arrivals) with a plain web request...")
        results = crawl(get_plain)
    except Exception as e:
        print(f"Plain request failed ({e}).")
    if len(results) < 50:
        print("Too few products from a plain request, trying the Bright Data browser...")
        try:
            results = crawl_with_browser()
        except Exception as e:
            print(f"Browser read failed too ({e}).")
    if not results:
        print("No Duty Free products read; keeping last week's data/dutyfree.json.")
        raise SystemExit(1)
    write_json("dutyfree.json", results)


if __name__ == "__main__":
    main()
