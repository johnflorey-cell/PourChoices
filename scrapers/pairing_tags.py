"""
Food pairing tags (round 33, 20 Nov 2026).

Stamps a "pairing_tags" list onto every product in data/products.json, e.g.
    "Jacob's Creek Classic Shiraz 75cl"  -> ["bold_red"]
    "Hoegaarden Wheat Beer 33cl [6 Pack]" -> ["wheat_beer"]
    "Tanqueray London Dry Gin 70cl"      -> ["citrus_gin"]

The Drinks Planner's "What's on the menu?" choice uses these tags to pick
drinks that suit the food (Friday BBQ -> bold reds, malty beer, aged whisky).

No manual data entry: the tags come from words in each product's name and
its scraped category, using the rules in data/pairing_rules.json. The app
(index.html) reads the same rules file when a product has no tags yet, so the
two can never disagree. To change what counts as e.g. a "bold red", edit the
rules file only.

Runs at the end of scrapers/merge.py on every weekly scrape. It can also be
run by hand:  python scrapers/pairing_tags.py
"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "data" / "pairing_rules.json"
PRODUCTS_PATH = ROOT / "data" / "products.json"


def _plain(text):
    """Removes accents (rosé -> rose), so the rules match the same way here
    and in the app's JavaScript, whose word boundaries ignore accented
    letters."""
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if not unicodedata.combining(c))


def _compile(pattern):
    return re.compile(_plain(pattern), re.I) if pattern else None


def load_rules(path=RULES_PATH):
    raw = json.loads(Path(path).read_text())
    rules = {}
    for tag, r in raw["tags"].items():
        rules[tag] = {
            "categories": set(r.get("categories", [])),
            "match": _compile(r.get("match")),
            "require": _compile(r.get("require")),
            "exclude": _compile(r.get("exclude")),
            "group": r.get("group"),
            "default": bool(r.get("default")),
        }
    return rules


def _eligible(rule, name, category):
    if category not in rule["categories"]:
        return False
    if rule["require"] and not rule["require"].search(name):
        return False
    if rule["exclude"] and rule["exclude"].search(name):
        return False
    return True


def tags_for(name, category, rules):
    """The pairing tags for one product, in the order the rules list them."""
    name = _plain(name)
    found = [t for t, r in rules.items()
             if _eligible(r, name, category) and r["match"] and r["match"].search(name)]
    # "default" tags: e.g. every ordinary beer is a crisp lager, every rosé
    # a dry rosé, unless it already got another tag from the same group.
    for t, r in rules.items():
        if not r["default"] or t in found or not _eligible(r, name, category):
            continue
        if any(rules[f]["group"] == r["group"] for f in found):
            continue
        found.append(t)
    return found


def tag_products(products, rules=None):
    rules = rules or load_rules()
    for p in products:
        p["pairing_tags"] = tags_for(p.get("name"), p.get("category"), rules)
    return products


def main():
    products = json.loads(PRODUCTS_PATH.read_text())
    tag_products(products)
    PRODUCTS_PATH.write_text(json.dumps(products, indent=2))
    counts = Counter(t for p in products for t in p["pairing_tags"])
    tagged = sum(1 for p in products if p["pairing_tags"])
    print(f"Pairing tags: {tagged} of {len(products)} products tagged")
    for t, n in counts.most_common():
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
