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
"""
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

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
        dedup_key = item.get("url") or item["name"]
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        item["_norm"] = normalize(item["name"])
        item["_sig_words"] = significant_words(item["_norm"])
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
    carried_by, missing_from = [], []
    for key in RETAILER_FILES:
        item = cluster_map.get(key)
        row[key] = item["price_bhd"] if item else None
        row[f"{key}_url"] = (item.get("url") if item else None) or {
            "bmmi": "https://www.bmmishops.com",
            "ae": "https://www.africanandeastern.com",
            "gbi": "https://www.gbiexpress.com",
            "nhsc": "https://www.nhscbahrain.com",
        }[key]
        (carried_by if item else missing_from).append(RETAILER_LABELS[key])

    if missing_from:
        row["note"] = f"Not carried by {', '.join(missing_from)} (or not matched by name)."
    else:
        row["note"] = "Carried by all 4 retailers."
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
