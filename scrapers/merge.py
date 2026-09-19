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

Swapped-word fix (2026-09-15): confirmed live that "Johnnie Walker Black
Label 75cl" (BMMI, BD 48.725; GBI Express, BD 61.000) had picked up NHSC's
"JOHNNIE WALKER BLUE LABEL 75CL" (BD 236.500) into the same row -- Black
Label and Blue Label are two completely different whiskies (roughly 5x the
price apart), not the same product. The word-overlap check above only asks
whether MOST of the significant words match (more than half), and "black"
vs "blue" is just one word out of four ("johnnie", "walker", "___", "label"),
so it scored 60% overlap and passed, the same way "Red Rose" vs "Red Horse"
used to before the matching fix higher up in this file. Rather than special-
casing "black"/"blue" (or "red"/"rose"/"horse", or the next pair someone
finds), this is fixed generally: whenever BOTH names have at least one
significant word the other one doesn't (a word was swapped for a different
word, not just added or dropped), they're now never treated as a match, no
matter how high the overlap score is otherwise (see words_conflict). A name
that's simply a fuller/shorter version of the other (e.g. "Famous Grouse"
vs "Famous Grouse Scotch Whisky", where every word on the short side also
appears on the long side) is unaffected by this and still governed by the
existing overlap-ratio check, since nothing was swapped there, only added.

Pack-count fix (2026-09-15): confirmed live that BMMI's "Smirnoff Ice 27.5cl
[24 Pack]" (BD 48.725) had picked up African & Eastern's "Smirnoff Ice
[6-Pack]" (BD 16.137) into the same row -- a box of 24 and a box of 6 are
not the same purchase, never mind the same price. This happened because
the size check only ever looked at the size of ONE bottle/can (both are
27.5cl Smirnoff Ice cans), and had no idea how many of them came in the
box, so a 6-pack and a 24-pack of literally the same can looked identical
to it. Fixed the same way as the bottle-size fix: extract_pack_count()
reads the pack size straight out of the name (covers every phrasing seen
across all 4 sites' real listings: "24 Pack", "6-Pack", "Case of 24", "24 X
33CL", "Cans X24"), and two listings are never merged when their pack
counts disagree. Unlike bottle size, a listing that doesn't mention a pack
count at all is treated as a single bottle/can (pack count 1), since that's
what "no pack wording" means on every site checked here, so a plain
single-bottle listing on one site still matches a plain single-bottle
listing on another.

Terse-vs-descriptive name fix (2026-09-16): confirmed live that BMMI's
"Famous Grouse 75cl" was NOT being merged with the row that GBI's "The
Famous Grouse Scotch Whisky 75cl" and A&E's own listing had already
formed, even though every one of these is the same 75cl bottle --
reported directly, with these exact two names, as one of the app's open
issues. Root cause: the word-overlap check only counted a word as
"generic" (ignorable) if it was a wrapping/packaging word like "can" or
"pack" -- it had no idea that "scotch", "whisky", "whiskey", "scotland"
and "blended" are just as generic across whisky listings on these sites,
so BMMI's terse "Famous Grouse" (2 significant words) was being compared
against GBI's fuller "Famous Grouse Scotch Whisky" (4 significant words)
as if "scotch" and "whisky" were brand-identifying facts. Fixed by adding
those five words to GENERIC_WORDS, so both names reduce to the exact same
significant-word set, {"famous", "grouse"}.

Age-phrasing fix (2026-09-16): the same investigation found "Chivas Regal
18 Year Old Blended Whisky 75cl" (one retailer's phrasing) failing to
match "Chivas Regal Scotch Whisky Scotland 18 YO Blended 75cl" (another
retailer's phrasing for the exact same 18-year-old bottle) -- "18 Year
Old" tokenizes into three separate words ("18", "year", "old") while "18
YO" tokenizes into two ("18", "yo"), so "year"/"old" on one side and "yo"
on the other looked like a swapped/conflicting fact even though they mean
the same age statement. Fixed by canonicalising every age phrasing found
in a name -- "18 Year(s) Old", "18 YO", "18 Yrs", "18 Y.O." and
already-fused "18YO" -- into one consistent "18yo" token before any other
comparison runs, so different sites' phrasing of the same age reduces to
the same significant word instead of looking like a factual difference.

Exact-match-only rewrite (2026-09-16): fixing the two cases above by
loosening word_overlap()'s old ">50% of the words in common" threshold
opened a worse hole, caught by testing rather than a user report: a bare
"Chivas Regal 75cl" (no age, no edition stated) started matching BOTH
"Chivas Regal Scotch Whisky Scotland 12YO Blended 75cl" (a specific age)
AND completely unrelated "Chivas Regal Ultis Scotch Whisky" (a distinct,
much pricier limited-edition bottling) -- in both cases the fuller name's
one extra significant word ("12yo" / "ultis") wasn't enough, on its own,
to fail a >50%-overlap check, even though it's exactly the kind of fact
that makes them different products. Whatever that extra word turns out to
be next time isn't something a fixed word list can anticipate. So rather
than special-casing it (an age-presence guard alone would have caught the
12YO case but not "Ultis"), the matching rule is now simply: two names
must reduce to the EXACT SAME set of significant words to be considered
the same product at all -- no partial-overlap fraction, no "one name is
just a shorter version of the other" allowance. This is provably safe
against every failure mode above: words_conflict() (a plain factual
swap, like Black Label vs Blue Label) already blocks the case where each
side has something the other lacks; the only gap was the OTHER case, one
side's words being a subset of the other's with something extra left
over, and requiring exact equality closes that gap directly. It doesn't
lose either real fix above -- "Famous Grouse" (both sides reduce to
identical sets once "scotch"/"whisky" are generic) and "18 Year Old" vs
"18 YO" (both reduce to identical sets once the age is canonicalised)
both still match exactly, since they always were equal sets, never
merely overlapping ones.
"""
import json
import re
from datetime import datetime, timezone
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

SIZE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:cl|ml|ltr|litre|liter|l)s?\b|\b\d+[- ]pack\b", re.IGNORECASE)
NOISE_RE = re.compile(r"\b(non[- ]vintage|vintage|bottle|original)\b", re.IGNORECASE)
PUNCT_RE = re.compile(r"[^a-z0-9 ]+")

# Recognises every age phrasing seen across these 4 sites -- "18 Year Old",
# "18 Years Old", "18 Yrs", "18 YO", "18 Y.O.", already-fused "18YO" -- and
# canonicalises whichever one appears into a single "18yo"-style token, so
# two sites describing the same age don't look like they disagree just
# because they phrased it differently (see "Age-phrasing fix" above).
AGE_RE = re.compile(r"\b(\d{1,2})\s*(?:years?|yrs?|y\.?o\.?)(?:\s*old)?\b", re.IGNORECASE)

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

# Recognises how many bottles/cans a listing is FOR, in every phrasing seen
# across the 4 sites' real product names: "24 Pack", "6-Pack", "4pack",
# "Case of 24", "24 X 33CL", "Cans X24". Capped at 3 digits (1-999) so it
# never mistakes a 4-digit vintage year ("...Reserve X 2019") for a pack
# count. See the "Pack-count fix" note above.
PACK_TOKEN_RE = re.compile(
    r"\b(\d{1,3})\s*-?\s*pack\b"
    r"|\bcase\s+of\s+(\d{1,3})\b"
    r"|\b(\d{1,3})\s*x\b"
    r"|\bx\s*(\d{1,3})\b",
    re.IGNORECASE,
)

# Generic words that show up across many different brands/products and so
# don't help tell two DIFFERENT products apart (e.g. both "Red Rose" and
# "Red Horse" are "Extra Strong" beers sold in "Cans"). Excluded when checking
# whether two names' significant words actually overlap.
GENERIC_WORDS = {
    "extra", "strong", "beer", "beers", "can", "cans", "bottle", "bottles",
    "pack", "packs", "case", "cases", "x", "of", "the", "and", "a", "in",
    # Category-generic descriptor words added by the "Terse-vs-descriptive
    # name fix" -- these just restate the category (every whisky is
    # "scotch"/"whisky"/"whiskey", most Chivas-style blends say "blended",
    # "scotland" just names the country), so they don't help tell two
    # DIFFERENT products apart and were penalising a terse name (e.g. "Famous
    # Grouse 75cl") against a fuller one (e.g. "The Famous Grouse Scotch
    # Whisky 75cl") for no real reason.
    "scotch", "whisky", "whiskey", "scotland", "blended", "irish",
}

def normalize(name):
    n = name.lower()
    n = AGE_RE.sub(lambda m: m.group(1) + "yo", n)
    n = SIZE_RE.sub("", n)
    n = NOISE_RE.sub("", n)
    n = PUNCT_RE.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def significant_words(norm_name):
    return {w for w in norm_name.split() if w not in GENERIC_WORDS}


def word_overlap(words_a, words_b):
    """Jaccard similarity between two sets of significant words -- kept only
    for anything that still wants a similarity score to look at (e.g. ad hoc
    debugging); cluster() itself no longer uses this as a threshold check,
    see the "Exact-match-only rewrite" note above. 1.0 if both are empty
    (nothing to disagree on); 0.0 if one is empty and the other isn't."""
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


def extract_pack_count(name):
    """How many bottles/cans this listing is for (e.g. "24 Pack" -> 24,
    "Case of 6" -> 6, "24 X 33CL" -> 24). Unlike extract_size_ml, a missing
    pack indicator defaults to 1 rather than "unknown" -- almost every
    single-bottle listing across these 4 sites simply doesn't mention a
    pack count at all, so treating that silence as "this is one bottle" is
    the safe, common-case reading, and it's what lets a single-bottle
    listing on one site still match a single-bottle listing on another that
    also says nothing about pack size."""
    m = PACK_TOKEN_RE.search(name)
    if not m:
        return 1
    for group in m.groups():
        if group is not None:
            try:
                return int(group)
            except (TypeError, ValueError):
                return 1
    return 1


def words_conflict(sig_words_a, sig_words_b):
    """True when the two names' significant-word sets aren't identical --
    either a word was swapped for a different one ("Johnnie Walker Black
    Label" vs "...Blue Label"), or one side simply has an extra word the
    other lacks ("Chivas Regal 75cl" vs "Chivas Regal Scotch Whisky
    Scotland 12YO Blended 75cl", once the generic "scotch"/"whisky"/etc.
    words are stripped from both, still differ by "12yo"). See the
    "Exact-match-only rewrite" note above for why this now requires exact
    equality rather than a partial-overlap fraction: a name that's purely a
    generic-word-padded version of the other (e.g. "Famous Grouse" vs "The
    Famous Grouse Scotch Whisky", once "the"/"scotch"/"whisky" are stripped
    as generic) reduces to the SAME set on both sides, so it still passes
    here same as before -- only a genuine one-sided extra WORD (not just
    extra filler) now blocks the match."""
    return sig_words_a != sig_words_b


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
        item["_pack_count"] = extract_pack_count(item["name"])
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
            if words_conflict(item_a["_sig_words"], item_b["_sig_words"]):
                # The two names' significant words aren't exactly the same
                # set -- either a word was swapped for a different one (e.g.
                # "Black" Label vs "Blue" Label) or one side simply has an
                # extra word the other doesn't (e.g. a bare "Chivas Regal
                # 75cl" vs a specific "...12YO..." or "...Ultis..."
                # bottling). See the "Exact-match-only rewrite" note above --
                # only two names that reduce to the identical significant-
                # word set (after generic words and age phrasing are
                # normalised away) are treated as the same product.
                continue
            if sizes_conflict(item_a["_size_ml"], item_b["_size_ml"]):
                # Same-ish name, but a 75cl bottle and a 1L bottle are not
                # the same product -- see the "Size-blind matching fix" note.
                continue
            if item_a["_pack_count"] != item_b["_pack_count"]:
                # A 6-pack and a 24-pack of the same drink are two different
                # things to actually buy -- see the "Pack-count fix" note.
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
