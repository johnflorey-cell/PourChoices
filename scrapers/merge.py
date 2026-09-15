"""
Merges the 4 retailers' scraped JSON files into one data/products.json in the
shape the frontend expects, matching the same product across retailers by
fuzzy name similarity (there's no shared SKU/barcode across these 4 sites, so
exact matching isn't possible; this is a best-effort automated match, not a
guarantee every cluster is 100% the same product/size).

Also writes data/last_updated.json with the timestamp of this run, which the
site displays as "Prices last updated: ..." so visitors can see how fresh the
data is.

Run this after all 4 scrapers have produced their per-retailer JSON files.

Matching fix (2026-09-15): the old matching only compared full normalized
names character-by-character (difflib's SequenceMatcher ratio). That let two
completely different products get merged into one row whenever the SURROUNDING
words happened to line up closely even if the actual brand name didn't: BMMI's
"Red Rose Extra Strong Beer 12% Can 50cl [24 Pack]" and GBI Express's
"Red Horse Extra Strong 33cl Cans x24" scored as a "match" because both share
"Red", "Extra Strong" and "Can(s)", even though "Rose" and "Horse" are
different brands entirely. Fixed by also requiring the two names' SIGNIFICANT
words (ignoring generic filler words like "extra", "strong", "can", "beer",
"pack") to actually overlap by more than half; "Red Rose" vs "Red Horse" now
correctly fails this check (their only shared significant word is "red") even
though the old character-based score still looks close. This is a stricter
rule than before, so a few genuinely-matching products that used to cluster
might now show up as separate rows instead; that's the safer trade-off, since
a wrong price merged into the wrong product is worse than two rows that could
have been one.

File-corruption fix (2026-09-15): this whole file had been accidentally
overwritten with a copy of _common.py's content (pasted into the wrong file
on GitHub), some hours after the matching fix above was first written. Since
_common.py only defines helper functions and never calls a main() of its
own, running "python scrapers/merge.py" silently did nothing at all: no
error, no output, and critically no updated products.json or
last_updated.json, even though all 4 scrapers kept refreshing their own
per-retailer files every day. That's why "Prices last updated" stopped
advancing and the live comparison table stayed frozen on old data (including
old cross-retailer mismatches and stale prices) despite the daily scrape
still technically "succeeding". Restored from the last good commit.

Out-of-stock passthrough fix (2026-09-15): even before the corruption above,
this file only ever copied a retailer's price_bhd into the merged row and
threw away its in_stock flag entirely. Each scraper already works out
per-retailer whether an item is genuinely out of stock (BMMI, A&E, GBI
Express, NHSC each have their own out-of-stock detection, see their own
docstrings), but that information never reached products.json, so the
frontend had no way to know a listed price was stale/unavailable and showed
it as a normal, purchasable price regardless. Fixed by also carrying a
"{retailer}_in_stock" flag through into each merged row, so the frontend can
show "Out of Stock" for that one retailer instead of a live-looking price,
and can leave an out-of-stock price out of the "lowest price" comparison
(see the matching index.html fix).

Size-blind matching fix (2026-09-15): normalize() strips out size tokens
("75cl", "1L", "4-pack", ...) before comparing two names, on the theory that
one site might write "Famous Grouse 75cl" and another "Famous Grouse Scotch
Whisky" without a size at all, and those should still count as a match. But
stripping the size ALSO means two names that both DO carry a size, and
whose sizes actually differ, could still be treated as a match purely on
the strength of the rest of the name -- confirmed live: "The Famous Grouse
Scotch Whisky 75cl" (A&E, BD 14.200), "The Famous Grouse Scotch Whisky 1L"
(GBI Express, BD 12.000) and "FAMOUS GROUSE SCOTCH WHISKY 1LTR" (NHSC, BD
16.500) were all merged into one row and displayed under the 75cl product's
name, even though the GBI and NHSC listings are actually 1 litre bottles,
not 75cl -- a completely different, larger size at a different price. Fixed
by parsing each name's own size separately (extract_size_ml, recognising
cl/ml/l/ltr/litre) and refusing to merge two items whose sizes are BOTH
known and clearly different (see sizes_conflict); when a size can't be read
from one or both names, the existing name-similarity check still decides,
same as before.
"""
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RETAILER_FILES = {
    "bmmi": "bmmi.json",
    "ae": "ae.json",
    "gbi": "gbi.json",
    "nhsc": "nhsc.json",
}
RETAILER_LABELS = {"bmmi": "BMMI", "ae": "African & Eastern", "gbi": "GBI Express", "nhsc": "NHSC"}

SIZE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:cl|l|ml)\b|\b\d+[- ]pack\b", re.IGNORECASE)
NOISE_RE = re.compile(r"\b(non[- ]vintage|vintage|bottle|original)\b", re.IGNORECASE)
PUNCT_RE = re.compile(r"[^a-z0-9 ]+")

# Recognises a bottle/can size written into the product name itself, in any
# of the unit spellings the 4 sites use between them (cl, ml, l, ltr, litre,
# liter), so it can be compared separately from the rest of the name -- see
# the "Size-blind matching fix" note above.
SIZE_TOKEN_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(cl|ml|ltr|litre|liter|l)s?\b", re.IGNORECASE)
UNIT_TO_ML = {"cl": 10, "ml": 1, "l": 1000, "ltr": 1000, "litre": 1000, "liter": 1000}

# Two known sizes are treated as genuinely different bottles once they're
# more than this fraction apart, which allows for small rounding differences
# in how a site prints the same nominal size (e.g. "750ml" vs "0.75L") without
# letting an actually-different size (750ml vs 1000ml, a 33% difference)
# through as a "match".
SIZE_TOLERANCE = 0.05

MATCH_THRESHOLD = 0.72

# Generic words that show up across many different brands/products and so
# don't help tell two DIFFERENT products apart (e.g. both "Red Rose" and
# "Red Horse" are "Extra Strong" beers sold in "Cans"). Excluded when checking
# whether two names' significant words actually overlap.
GENERIC_WORDS = {
    "extra", "strong", "beer", "beers", "can", "cans", "bottle", "bottles",
    "pack", "packs", "case", "cases", "x", "of", "the", "and", "a", "in",
}

# The two names must share more than this fraction of their significant words
# (Jaccard similarity) on top of the character-level score, or they're treated
# as different products even if the character score alone looked close.
SIGNIFICANT_WORD_OVERLAP_THRESHOLD = 0.5


def normalize(name):
    n = name.lower()
    n = SIZE_RE.sub("", n)
    n = NOISE_RE.sub("", n)
    n = PUNCT_RE.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def significant_words(norm_name):
    return {w for w in norm_name.split() if w not in GENERIC_WORDS}


def word_overlap(words_a, words_b):
    """Jaccard similarity between two sets of significant words. 1.0 if both
    are empty (nothing to disagree on); 0.0 if one is empty and the other isn't."""
    if not words_a and not words_b:
        return 1.0
    union = words_a | words_b
    if not union:
        return 1.0
    return len(words_a & words_b) / len(union)


def extract_size_ml(name):
    """Best-effort bottle/can size in millilitres, read straight from the
    product name (e.g. "75cl" -> 750.0, "1L" -> 1000.0, "1LTR" -> 1000.0).
    Returns None when no size is found in the name at all, meaning "unknown"
    rather than "zero" -- callers must treat unknown as "can't compare", not
    as a mismatch."""
    m = SIZE_TOKEN_RE.search(name)
    if not m:
        return None
    value, unit = m.groups()
    try:
        return float(value) * UNIT_TO_ML[unit.lower()]
    except (TypeError, ValueError, KeyError):
        return None


def sizes_conflict(size_a, size_b):
    """True only when BOTH items have a known size and those sizes are
    clearly different bottles (see SIZE_TOLERANCE). When either size is
    unknown, this returns False (not a conflict) and the existing
    name-similarity/word-overlap checks are left to decide, same as before
    this fix."""
    if size_a is None or size_b is None:
        return False
    if size_a <= 0 or size_b <= 0:
        return False
    return abs(size_a - size_b) / max(size_a, size_b) > SIZE_TOLERANCE


def _dedupe_key(item):
    """A stable identity for one item within a single retailer's own list.

    Duplicate rows fix (2026-09-15): GBI Express's crawler visits the site
    once per search term (e.g. "whisky", "gin", ...), and the same product
    page turns up under more than one term, so it gets scraped twice with
    two different URLs that point at the exact same page and differ only in
    a tracking query string: ".../the-famous-grouse-scotch-whisky-75-cl
    ?search=whisky&page=3" and the same path "?search=gin&page=12". Using
    the URL as-is for de-duping never caught this, since the two URLs
    genuinely aren't identical text, which is why "The Famous Grouse Scotch
    Whisky 75cl" (and others) were showing up as two separate rows on the
    site with the same price. Fixed by stripping the query string (and any
    #fragment) before comparing, so both crawls of the same page collapse
    into one."""
    url = item.get("url")
    if url:
        return urlsplit(url)._replace(query="", fragment="").geturl()
    return item["name"]


def load_retailer(key):
    path = DATA_DIR / RETAILER_FILES[key]
    if not path.exists():
        return []
    items = json.loads(path.read_text())
    # De-dupe within one retailer's own list (a search-based crawl can surface
    # the same product under more than one search term).
    seen = set()
    deduped = []
    for item in items:
        dedup_key = _dedupe_key(item)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        item["_norm"] = normalize(item["name"])
        item["_sig_words"] = significant_words(item["_norm"])
        item["_size_ml"] = extract_size_ml(item["name"])
        deduped.append(item)
    return deduped


def cluster(retailer_items):
    """retailer_items: dict of retailer_key -> list of items (each with _norm).
    Returns a list of clusters, each a dict retailer_key -> item (or absent)."""
    pool = []
    for key, items in retailer_items.items():
        for item in items:
            pool.append((key, item))

    clusters = []
    used = set()
    for i, (key_a, item_a) in enumerate(pool):
        if i in used:
            continue
        cluster_map = {key_a: item_a}
        used.add(i)
        for j, (key_b, item_b) in enumerate(pool):
            if j in used or key_b in cluster_map:
                continue
            ratio = SequenceMatcher(None, item_a["_norm"], item_b["_norm"]).ratio()
            if ratio < MATCH_THRESHOLD:
                continue
            overlap = word_overlap(item_a["_sig_words"], item_b["_sig_words"])
            if overlap <= SIGNIFICANT_WORD_OVERLAP_THRESHOLD:
                continue
            if sizes_conflict(item_a["_size_ml"], item_b["_size_ml"]):
                # Same-ish name, but a 75cl bottle and a 1L bottle are not
                # the same product -- see the "Size-blind matching fix" note.
                continue
            cluster_map[key_b] = item_b
            used.add(j)
        clusters.append(cluster_map)
    return clusters


def build_product_row(idx, cluster_map):
    # Prefer the longest name as the display name (tends to carry the most detail).
    display_name = max((item["name"] for item in cluster_map.values()), key=len)
    categories = [item.get("category") for item in cluster_map.values() if item.get("category")]
    category = max(set(categories), key=categories.count) if categories else "Other Spirits"

    row = {"id": idx, "name": display_name, "category": category}
    carried_by, missing_from, out_of_stock_at = [], [], []
    for key in RETAILER_FILES:
        item = cluster_map.get(key)
        row[key] = item["price_bhd"] if item else None
        # in_stock is True/False when the retailer's own scraper knows either
        # way, or None when this retailer doesn't carry/match the product at
        # all (no item in this cluster), so the frontend can tell "carried
        # but out of stock" apart from "not carried here".
        row[f"{key}_in_stock"] = item.get("in_stock", True) if item else None
        row[f"{key}_url"] = (item.get("url") if item else None) or {
            "bmmi": "https://www.bmmishops.com",
            "ae": "https://www.africanandeastern.com",
            "gbi": "https://www.gbiexpress.com",
            "nhsc": "https://www.nhscbahrain.com",
        }[key]
        if item and item.get("in_stock", True) is False:
            out_of_stock_at.append(RETAILER_LABELS[key])
        (carried_by if item else missing_from).append(RETAILER_LABELS[key])

    notes = []
    if missing_from:
        notes.append(f"Not carried by {', '.join(missing_from)} (or not matched by name).")
    if out_of_stock_at:
        notes.append(f"Currently out of stock at {', '.join(out_of_stock_at)}.")
    row["note"] = " ".join(notes) if notes else "Carried by all 4 retailers."
    return row


def main():
    retailer_items = {key: load_retailer(key) for key in RETAILER_FILES}
    for key, items in retailer_items.items():
        print(f"{RETAILER_LABELS[key]}: {len(items)} scraped products")

    clusters = cluster(retailer_items)
    products = [build_product_row(i + 1, c) for i, c in enumerate(clusters)]
    # Most useful products first: ones carried by more retailers (real price
    # comparisons) ahead of single-retailer-only listings.
    products.sort(key=lambda p: sum(1 for k in RETAILER_FILES if p[k] is not None), reverse=True)
    for i, p in enumerate(products, 1):
        p["id"] = i

    out_path = DATA_DIR / "products.json"
    out_path.write_text(json.dumps(products, indent=2))
    print(f"Merged into {len(products)} products -> {out_path}")

    last_updated_path = DATA_DIR / "last_updated.json"
    last_updated_path.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat()}))
    print(f"Wrote {last_updated_path}")


if __name__ == "__main__":
    main()
