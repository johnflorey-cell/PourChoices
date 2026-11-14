"""
Builds the Google-searchable pages for pour-choices.com (added 2026-11-07).

Why this exists: the app itself (index.html) is one page that loads every
product with JavaScript after it opens, so Google sees almost nothing it can
list in search results. This script turns data/products.json into plain,
fast HTML pages that Google can read directly:

  price/<product-slug>/index.html   one page per product that can be bought
                                    now (in stock at 1+ shop, price > 0)
  prices/<category-slug>/index.html one page per category, listing its products
  prices/index.html                 the "all prices" page linking every category
  sitemap.xml                       the list of every page, for Google
  robots.txt                        tells search engines they may read the site
                                    and where the sitemap is

It runs after merge.py in the weekly workflow (and on its own from the
"Build search pages" workflow), so the pages always match the latest prices.
The price/ and prices/ folders are wiped and rebuilt every run, so a product
that disappears from every shop also disappears from Google over time.

Out-of-stock-everywhere products get no page on purpose: a page with no
price is "thin content" to Google and would not help anyone searching.

Product page addresses come from the product NAME (the same stable key the
app uses for favourites and price history), never the numeric id, which is
reassigned on every scrape.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SITE = "https://pour-choices.com"
BAHRAIN = timezone(timedelta(hours=3))

SHOPS = [
    ("bmmi", "BMMI"),
    ("ae", "African & Eastern"),
    ("gbi", "GBI Express"),
    ("nhsc", "NHSC"),
]

# category value in products.json -> (url slug, name shown on the page)
CATEGORIES = {
    "Beer": ("beer-cider", "Beer & Cider"),
    "Whisky": ("whisky", "Whisky"),
    "Gin": ("gin", "Gin"),
    "Vodka": ("vodka", "Vodka"),
    "Rum": ("rum", "Rum"),
    "Wine": ("wine", "Wine"),
    "Box Wine": ("boxed-wine", "Boxed Wine"),
    "Fortified": ("fortified-wine", "Port, Sherry & Fortified Wine"),
    "Champagne": ("champagne-sparkling-wine", "Champagne & Sparkling Wine"),
    "Other Spirits": ("other-spirits", "Liqueurs, Brandy, Tequila & Other Spirits"),
}
CATEGORY_ORDER = list(CATEGORIES)

# Query-string parts the scrapers add while paging through listings. They
# aren't needed to open a product page, so they're dropped from the shop
# links shown on our pages (product_id and similar are kept).
NOISE_PARAMS = {"search", "page", "limit"}

MAX_PER_CATEGORY_PAGE = 400   # keeps big category pages (Wine) quick to load
RELATED_COUNT = 8

esc = html.escape


# ---------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ").replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80].strip("-") or "product"


def short_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:5]


def clean_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in NOISE_PARAMS]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
    except ValueError:
        return url


def bhd(x: float) -> str:
    return f"{x:.3f}"


def offers(p: dict) -> list[dict]:
    """Every shop's listing for this product, cheapest in-stock first."""
    out = []
    for key, label in SHOPS:
        price = p.get(key)
        if price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        in_stock = bool(p.get(f"{key}_in_stock")) and price > 0
        out.append({
            "shop": label,
            "price": price,
            "in_stock": in_stock,
            "url": clean_url(p.get(f"{key}_url")),
        })
    out.sort(key=lambda o: (not o["in_stock"], o["price"]))
    return out


def buyable(p: dict) -> list[dict]:
    return [o for o in offers(p) if o["in_stock"]]


def assign_slugs(products: list[dict]) -> dict[str, str]:
    """name -> slug. Clashing slugs get a short, stable code from the name."""
    by_slug: dict[str, list[str]] = {}
    for p in products:
        by_slug.setdefault(slugify(p["name"]), []).append(p["name"])
    result = {}
    for slug, names in by_slug.items():
        if len(names) == 1:
            result[names[0]] = slug
        else:
            for n in names:
                result[n] = f"{slug}-{short_hash(n)}"
    return result


# ---------------------------------------------------------------- page shell

CSS = """
*{box-sizing:border-box}body{margin:0;background:#0b0f19;color:#e5e7eb;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:#fbbf24}main{max-width:820px;margin:0 auto;padding:16px}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid #1f2937;max-width:820px;margin:0 auto}
header a{text-decoration:none;color:#fff;font-weight:800;letter-spacing:.05em}
.tag{color:#fbbf24;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em}
h1{font-size:22px;line-height:1.25;margin:12px 0 6px;color:#fff}h2{font-size:17px;margin:24px 0 8px;color:#fff}
.crumbs{font-size:12px;color:#9ca3af}.crumbs a{color:#9ca3af}
.lead{color:#d1d5db}.best{color:#34d399;font-weight:700}
table{width:100%;border-collapse:collapse;margin:8px 0;background:#111827;border-radius:12px;overflow:hidden}
th,td{padding:10px 8px;text-align:left;border-bottom:1px solid #1f2937;font-size:14px}th{color:#9ca3af;font-weight:600;font-size:12px}
tr.cheapest td{background:#064e3b33}.oos{color:#9ca3af}.pl{color:#7dd3fc;font-size:12px}
.btn{display:inline-block;background:#fbbf24;color:#111827;font-weight:800;padding:12px 18px;border-radius:12px;text-decoration:none;margin:14px 0}
ul.list{list-style:none;padding:0;margin:0}ul.list li{padding:9px 0;border-bottom:1px solid #1f2937;display:flex;justify-content:space-between;gap:10px}
ul.list li span{color:#34d399;white-space:nowrap;font-weight:600}
.cats a{display:inline-block;margin:4px 6px 4px 0;padding:6px 12px;border:1px solid #374151;border-radius:999px;text-decoration:none;color:#e5e7eb;font-size:13px}
.pill{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;background:#064e3b;color:#6ee7b7}
footer{max-width:820px;margin:24px auto 0;padding:16px;font-size:11px;color:#9ca3af;border-top:1px solid #1f2937}
"""


def page(title: str, description: str, path: str, body: str, jsonld: list | None = None) -> str:
    canonical = SITE + path
    ld = ""
    for block in jsonld or []:
        ld += ('<script type="application/ld+json">'
               + json.dumps(block, ensure_ascii=False).replace("</", "<\\/")
               + "</script>\n")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Pour Choices">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{SITE}/icon-512.png">
<meta name="theme-color" content="#0F172A">
<link rel="icon" type="image/png" href="/icon-512.png">
<link rel="apple-touch-icon" href="/icon-512.png">
<style>{CSS}</style>
{ld}</head>
<body>
<header><a href="/">🍾 POUR CHOICES</a><span class="tag">Bahrain's independent price index</span></header>
<main>
{body}
</main>
<footer>
For adults aged 21 and over who are permitted to buy alcohol in Bahrain.
Pour Choices is an independent price-comparison directory, not a retail site. We are not affiliated with, authorized or endorsed by BMMI, African &amp; Eastern, GBI Express or NHSC. We do not sell, store, handle or deliver alcohol and we are not a party to any purchase. Any purchase must be made directly with the retailer. Prices are for information only and may have changed since they were checked; always confirm the price with the retailer before buying.
<br><br><a href="/">Open the Pour Choices app</a> · <a href="/prices/">All prices</a>
</footer>
<!-- Cloudflare Web Analytics --><script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "1973b245e38f4379a2d1729e20b2b3db"}}'></script><!-- End Cloudflare Web Analytics -->
</body>
</html>
"""


# ---------------------------------------------------------------- pages

def product_page(p: dict, slug: str, cat_slug: str, cat_name: str,
                 related: list[tuple[dict, str]], checked: str) -> str:
    name = p["name"]
    all_offers = offers(p)
    live = [o for o in all_offers if o["in_stock"]]
    best = live[0]
    vol = p.get("volume_litres")
    n_live = len(live)

    if n_live > 1:
        saving = live[-1]["price"] - best["price"]
        lead = (f'Cheapest today: <span class="best">{bhd(best["price"])} BHD at {esc(best["shop"])}</span>. '
                f'In stock at {n_live} shops in Bahrain'
                + (f', so picking the cheapest saves up to {bhd(saving)} BHD.' if saving > 0.0005
                   else ', all at the same price.'))
        desc = (f'{name} price in Bahrain: from {bhd(best["price"])} BHD at {best["shop"]}. '
                f'Compare prices at {n_live} shops: '
                + ", ".join(o["shop"] for o in live) + ".")
    else:
        lead = (f'Price today: <span class="best">{bhd(best["price"])} BHD at {esc(best["shop"])}</span>. '
                f'We compare BMMI, African &amp; Eastern, GBI Express and NHSC every week.')
        desc = (f'{name} price in Bahrain: {bhd(best["price"])} BHD at {best["shop"]}. '
                f'Compared weekly across BMMI, African & Eastern, GBI Express and NHSC.')

    trend = ""
    if p.get("price_trend") == "down" and p.get("price_last_seen"):
        trend = f'<p><span class="pill">↓ Down from {bhd(float(p["price_last_seen"]))} BHD last week</span></p>'

    rows = []
    for o in all_offers:
        per_l = ""
        if vol and o["price"] > 0:
            per_l = f'<div class="pl">{bhd(o["price"] / float(vol))} BHD per litre</div>'
        price_cell = f'{bhd(o["price"])} BHD{per_l}' if o["in_stock"] else '<span class="oos">Out of stock</span>'
        link = (f'<a href="{esc(o["url"])}" rel="nofollow noopener" target="_blank">Shop page</a>'
                if o["url"] else "")
        cls = ' class="cheapest"' if (o is best and n_live > 1) else ""
        rows.append(f"<tr{cls}><td>{esc(o['shop'])}</td><td>{price_cell}</td><td>{link}</td></tr>")
    shown = {o["shop"] for o in all_offers}
    for _, label in SHOPS:
        if label not in shown:
            rows.append(f'<tr><td>{esc(label)}</td><td class="oos">Not listed</td><td></td></tr>')

    rel_html = ""
    if related:
        items = "".join(
            f'<li><a href="/price/{rs}/">{esc(rp["name"])}</a><span>from {bhd(buyable(rp)[0]["price"])} BHD</span></li>'
            for rp, rs in related)
        rel_html = f'<h2>More {esc(cat_name)} prices</h2><ul class="list">{items}</ul>'

    app_link = "/?q=" + quote(name)
    body = f"""
<p class="crumbs"><a href="/prices/">All prices</a> › <a href="/prices/{cat_slug}/">{esc(cat_name)}</a></p>
<h1>{esc(name)} price in Bahrain</h1>
<p class="lead">{lead}</p>
{trend}
<table><thead><tr><th>Shop</th><th>Price</th><th></th></tr></thead><tbody>
{''.join(rows)}
</tbody></table>
<p class="crumbs">Prices checked {esc(checked)}. Updated every week.</p>
<a class="btn" href="{esc(app_link)}">Compare in the Pour Choices app</a>
{rel_html}
"""
    url = f"{SITE}/price/{slug}/"
    jsonld = [{
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "category": cat_name,
        "url": url,
        "offers": {
            "@type": "AggregateOffer",
            "priceCurrency": "BHD",
            "lowPrice": bhd(best["price"]),
            "highPrice": bhd(live[-1]["price"]),
            "offerCount": n_live,
            "availability": "https://schema.org/InStock",
        },
    }, {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "All prices", "item": f"{SITE}/prices/"},
            {"@type": "ListItem", "position": 2, "name": cat_name, "item": f"{SITE}/prices/{cat_slug}/"},
            {"@type": "ListItem", "position": 3, "name": name, "item": url},
        ],
    }]
    title = f"{name} price in Bahrain | Pour Choices"
    return page(title, desc[:300], f"/price/{slug}/", body, jsonld)


def category_page(cat_slug: str, cat_name: str, items: list[tuple[dict, str]], checked: str) -> str:
    compared = sum(1 for p, _ in items if len(buyable(p)) > 1)
    shown = items[:MAX_PER_CATEGORY_PAGE]
    lis = "".join(
        f'<li><a href="/price/{s}/">{esc(p["name"])}</a><span>from {bhd(buyable(p)[0]["price"])} BHD</span></li>'
        for p, s in shown)
    more = ""
    if len(items) > len(shown):
        more = (f'<p>Showing the {len(shown)} most compared of {len(items)} products. '
                f'<a href="/">Search all of them in the app</a>.</p>')
    desc = (f"{cat_name} prices in Bahrain: {len(items)} products compared across BMMI, "
            f"African & Eastern, GBI Express and NHSC, updated every week.")
    body = f"""
<p class="crumbs"><a href="/prices/">All prices</a> › {esc(cat_name)}</p>
<h1>{esc(cat_name)} prices in Bahrain</h1>
<p class="lead">{len(items)} products you can buy now, {compared} of them at more than one shop.
Prices checked {esc(checked)}.</p>
<a class="btn" href="/">Compare in the Pour Choices app</a>
<ul class="list">{lis}</ul>
{more}
"""
    return page(f"{cat_name} prices in Bahrain | Pour Choices", desc, f"/prices/{cat_slug}/", body)


def index_page(cats: list[tuple[str, str, int]], total: int, checked: str) -> str:
    links = "".join(f'<a href="/prices/{s}/">{esc(n)} ({c})</a>' for s, n, c in cats)
    desc = (f"Drinks prices in Bahrain compared: {total} drinks across BMMI, African & Eastern, "
            f"GBI Express and NHSC. Find the cheapest alcohol prices in Bahrain for beer, wine, whisky, gin and more.")
    body = f"""
<h1>Drinks prices in Bahrain, compared</h1>
<p class="lead">Pour Choices checks the prices at BMMI, African &amp; Eastern, GBI Express and NHSC every week,
so you can see which shop is cheapest before you buy. It is the easy way to check drinks and alcohol prices
in Bahrain for beer, wine, whisky, gin, vodka and rum. {total} products in stock today.
Prices checked {esc(checked)}.</p>
<div class="cats">{links}</div>
<a class="btn" href="/">Open the Pour Choices app</a>
"""
    return page("Drinks Prices in Bahrain Compared | Pour Choices", desc, "/prices/", body)


# ---------------------------------------------------------------- main

def main() -> int:
    products = json.loads((DATA_DIR / "products.json").read_text(encoding="utf-8"))
    try:
        updated = json.loads((DATA_DIR / "last_updated.json").read_text())["updated_at"]
        updated_dt = datetime.fromisoformat(updated).astimezone(BAHRAIN)
    except Exception:
        updated_dt = datetime.now(BAHRAIN)
    checked = updated_dt.strftime("%-d %B %Y")
    lastmod = updated_dt.date().isoformat()

    live = [p for p in products if p.get("name") and buyable(p)]
    # Two rows can share a name (shops kept apart by merge.py's price guards,
    # e.g. Robert Mondavi Cabernet). One page per name: keep the row in stock
    # at the most shops, then the cheaper one.
    best_row: dict[str, dict] = {}
    for p in live:
        cur = best_row.get(p["name"])
        if cur is None or (-len(buyable(p)), buyable(p)[0]["price"]) < (-len(buyable(cur)), buyable(cur)[0]["price"]):
            best_row[p["name"]] = p
    live = list(best_row.values())
    if len(live) < 200:
        # Same idea as the scrapers' safety net: never wipe the site's pages
        # because of a bad run with almost no data.
        print(f"Only {len(live)} buyable products, keeping the existing pages.")
        return 1

    slugs = assign_slugs(live)

    # Most-compared first, then cheapest, so category pages lead with the
    # products where Pour Choices is most useful.
    def rank(p):
        b = buyable(p)
        return (-len(b), b[0]["price"], p["name"].lower())

    by_cat: dict[str, list[dict]] = {}
    for p in live:
        cat = p.get("category") if p.get("category") in CATEGORIES else "Other Spirits"
        by_cat.setdefault(cat, []).append(p)
    for cat in by_cat:
        by_cat[cat].sort(key=rank)

    for folder in ("price", "prices"):
        shutil.rmtree(ROOT / folder, ignore_errors=True)

    urls = [f"{SITE}/", f"{SITE}/prices/"]
    cat_summary = []
    count = 0
    for cat in CATEGORY_ORDER:
        items = by_cat.get(cat, [])
        if not items:
            continue
        cat_slug, cat_name = CATEGORIES[cat]
        pairs = [(p, slugs[p["name"]]) for p in items]
        for i, (p, s) in enumerate(pairs):
            # related: the neighbours in the category's ranked list
            window = pairs[max(0, i - RELATED_COUNT // 2): i] + pairs[i + 1: i + 1 + RELATED_COUNT]
            related = window[:RELATED_COUNT]
            out = ROOT / "price" / s / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(product_page(p, s, cat_slug, cat_name, related, checked), encoding="utf-8")
            urls.append(f"{SITE}/price/{s}/")
            count += 1
        out = ROOT / "prices" / cat_slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(category_page(cat_slug, cat_name, pairs, checked), encoding="utf-8")
        urls.append(f"{SITE}/prices/{cat_slug}/")
        cat_summary.append((cat_slug, cat_name, len(items)))

    (ROOT / "prices" / "index.html").write_text(index_page(cat_summary, len(live), checked), encoding="utf-8")

    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append(f"<url><loc>{esc(u)}</loc><lastmod>{lastmod}</lastmod></url>")
    sm.append("</urlset>")
    (ROOT / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")

    (ROOT / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nDisallow: /scrapers/\n\nSitemap: " + SITE + "/sitemap.xml\n",
        encoding="utf-8")

    print(f"Built {count} product pages, {len(cat_summary)} category pages, "
          f"sitemap with {len(urls)} addresses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
