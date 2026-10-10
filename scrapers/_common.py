"""Shared helpers for the Playwright-based scrapers (African & Eastern, GBI Express, NHSC).

These three sites render their catalogs client-side (confirmed during manual
investigation: a plain HTTP fetch returns an empty shell or the age-gate only),
so we drive a real headless browser instead of requests/BeautifulSoup.

Price parsing: all three sites price in Bahraini Dinar, almost always printed as
"BD 12.345" or "BHD 12.345" or just "12.345" next to a currency symbol. We match
broadly and take the smallest number found near each product card, since a
discounted item shows both its sale price and its struck-through original price,
and the smallest is what a customer actually pays.

Category guessing fix #1 (2026-09-15): guess_category() used to check whether a
keyword like "ale" appeared ANYWHERE inside the text, including in the middle
of an unrelated word. That's why wines and other non-beer products were
showing up under the Beer category: "Ducale", "Salento", "Moncigale", "Whale"
and "Pale" all contain the letters "ale" as a substring even though none of
them have anything to do with beer. Fixed by only matching a keyword when it
appears as its own whole word.

Category guessing fix #2 (2026-09-15): the keyword lists only covered generic
category words ("beer", "lager", "whisky", ...), so a well-known brand whose
own product name doesn't happen to include one of those words fell through to
"Other Spirits" even though any shopper would recognise it instantly: "Heineken
Can 33cl", "Budweiser Cans 35.5cl", "Carlsberg Cans 50cl" and "Stella Artois
Cans" all have no literal "beer" or "lager" in the name, and "Johnnie Walker
Double Black 1LTR" has no literal "whisky" in the name either. Fixed by adding
the major brand names sold by these 4 retailers directly to the relevant
category's keyword list, so the brand itself is enough to categorise correctly
even when the generic category word is missing from that particular listing.

Out-of-stock detection (2026-09-15): BMMI's own fix already skips a product
with no price left after removing the related-products carousel (see
bmmi.py) rather than borrowing another product's price, but until now a
genuinely out-of-stock item on A&E/GBI/NHSC was still recorded as if it were
a normal, purchasable listing, with no way for the site to tell a visitor
"this retailer carries it but it's currently out of stock" instead of
showing a plain price as if it were available right now. Added
looks_out_of_stock() below as a shared text-phrase check the Playwright
scrapers can use (each site's own out-of-stock signal still needs checking
against a real run; see the note in looks_out_of_stock() itself and each
scraper's own docstring for what's confirmed vs. best-effort per retailer).

Category guessing fix #3 (2026-09-15): confirmed live that "Red Horse 50cl
Cans X 24" and "Red Horse Extra Strong 33cl Cans X24" (both genuine beers --
GBI's own product page describes "Red Horse" as a "Strong Lager") were
landing in "Other Spirits" because the product name has no literal "beer" or
"lager" in it, and "red horse" wasn't in the brand list added by fix #2 above.
The same scan turned up a dozen more real beer brands sold across these 4
sites that were falling into the same gap for the same reason: Benediktiner,
Bira 91 (its "Boom Strong" cans, specifically -- the "Blonde Summer Lager"
listings already matched on the word "lager"), Bitburger, Budvar, Buzz
(the canned beer, not "Buzzballz", a separate tequila-based drink that
doesn't share the whole word "buzz"), Carling, Greenberg, Kalyani, Kilkenny,
Malayali, Singha, VITALSBERG, and "San Mig" as a shorthand for San Miguel
(San Miguel itself was already in the list, but "San Mig Light 33cl Bottles
X24" abbreviates the name enough that it didn't match). All of these are
added to the Beer brand list below, the same fix as #2, just catching up on
brands #2 missed rather than a new mechanism.

Note: the product's own page on GBI's site (as opposed to the search-results
list this scraper actually reads) prints a fuller description that also says
"Strong Lager" in so many words. That description text isn't available here
because the scraper only visits GBI's search-results pages -- reading each
product's own page too would mean one extra page load per product (roughly
1,700+ more requests through the paid Bright Data proxy every single scrape,
on top of what's already fetched), so the brand-list approach above is the
cheaper general fix and was preferred over visiting every product page.

Champagne & Sparkling Wines rename (2026-09-18): the category itself is
still stored/matched here as the plain string "Champagne" -- only the
NAME shown to a visitor changed to "Champagne & Sparkling Wines" (see
index.html's CATEGORY_DISPLAY_NAMES), so this rename shows up immediately
without waiting on a fresh scrape. The keyword list below was broadened at
the same time with more of the category's real-world vocabulary: Prosecco,
Sparkling Wine(s), Cremant/Crémant, Spritz, Franciacorta, Asti Spumante,
Lambrusco, Trentodoc, Cava, Brut, Semi-Brut.

Rose ordering fix (2026-09-18): confirmed live that "Moet & Chandon Imperial
Brut Rose 75cl" and "Veuve Clicquot Rose 75cl" -- both champagnes -- were
being categorised as plain "Wine" instead, because the Wine tuple (which
matches on the bare word "rose") was being checked BEFORE the Champagne
tuple, and guess_category() returns on the first tuple that matches at all.
Fixed by moving the Champagne & Sparkling Wines tuple ahead of Wine, so a
name that says "moet"/"veuve"/"brut"/etc. is caught there first regardless
of whether it also happens to contain "rose".

Sparkling water fix (2026-09-18): while broadening the keyword list above,
found that the existing bare "sparkling" keyword was also catching plain
sparkling WATER, not just sparkling wine -- "Acqua Morelli Sparkling Water
250ml" was landing in Champagne & Sparkling Wines. Fixed by skipping the
"sparkling" keyword specifically (only that one; every other keyword in the
list is unaffected) whenever the name also contains the word "water"
anywhere in it.
Wine and brand-gap fix (2026-10-10): a wine's listing very often names only
its grape ("Campo Viejo Tempranillo"), its region ("Beaujolais", "Chianti
Classico", "Saint-Estephe") or its estate ("Chateau Labegorce Margaux"),
never the word "wine" itself, so roughly a third of everything sitting in
Other Spirits was actually wine. Added a second Wine keyword list of grape
varieties, appellations, wine-label terms (DOC, AOC, Cru, Chateau, Domaine,
Cuvee, ...) and a few well-known estates, checked LAST, after every spirit
keyword, so a spirit sharing a word with a wine label ("Chateau de
Montifaud Cognac", "Rhum Blanc", "Grand Marnier Cordon Rouge") is still
caught as a spirit first. Same pass added whisky distilleries, gin/vodka
brands and beer styles found the same way (Talisker, Yamazaki, Tanqueray,
Absolut, Almaza, IPA, ...). Keyword matching now also ignores accents, so
"cotes" matches "Côtes" and "rose" matches "Rosè". merge.py re-applies
guess_category_strict() to every merged product name on each run, so these
keyword changes take effect on the next merge, not only after a full
re-scrape.
"""
import json
import os
import re
import unicodedata
import time
from pathlib import Path

PRICE_RE = re.compile(r"(?:BD|BHD)?\s*([0-9]{1,3}\.[0-9]{3})\b")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Maps a search term or a keyword found in a product name to one of the app's
# 7 category chips (Whisky, Beer, Gin, Vodka, Wine, Champagne, Other Spirits).
# Generic category words come first in each list; well-known brand names are
# appended afterwards so a listing missing the generic word (e.g. "Heineken
# Can 33cl" has no literal "beer") still lands in the right place.
CATEGORY_KEYWORDS = [
    (("whisky", "whiskey", "scotch", "bourbon", "single malt",
      # Brand names that don't always include "whisky" in the listing itself.
      "johnnie walker", "jack daniel", "chivas", "jameson", "glenfiddich",
      "glenlivet", "macallan", "ballantine", "grouse", "bushmills",
      "teacher", "dewar", "dewars", "jack daniels",
      # Added 2026-10-10 (see "Wine and brand-gap fix" above): whisky
      # distilleries/brands found sitting in Other Spirits because their
      # listing never says "whisky".
      "talisker", "laphroaig", "lagavulin", "ardbeg", "bowmore", "glenmorangie",
      "glengoyne", "glen grant", "glenfarclas", "glendronach", "glen moray",
      "dalmore", "balvenie", "aberlour", "yamazaki", "hibiki", "hakushu", "nikka",
      "jim beam", "gentleman jack", "maker's mark", "makers mark", "monkey shoulder",
      "blenders pride", "royal challenge", "royal stag", "royal salute",
      "dalwhinnie", "cardhu", "oban", "singleton", "auchentoshan", "highland park",
      "bruichladdich", "springbank", "kavalan", "amrut", "paul john"), "Whisky"),
    (("beer", "lager", "ale", "cider", "stout", "pilsner", "pils", "ipa",
      "weissbier", "hefeweizen",
      # Major beer brands whose own product name often skips the word "beer".
      "heineken", "budweiser", "bud light", "carlsberg", "amstel", "corona",
      "stella", "guinness", "kingfisher", "san miguel", "san mig", "tiger",
      "asahi", "sapporo", "fosters", "coors", "miller", "peroni", "beck",
      "grolsch", "hoegaarden", "erdinger", "tsingtao", "leffe",
      # Added by category guessing fix #3: more beer brands found missing
      # the same way (name has no "beer"/"lager" wording of its own).
      "red horse", "benediktiner", "bira", "bitburger", "budvar", "buzz",
      "carling", "greenberg", "kalyani", "kilkenny", "malayali", "singha",
      "vitalsberg", "almaza", "dos equis", "kronenbourg", "efes", "estrella damm",
      "speckled hen"), "Beer"),
    (("gin", "tanqueray", "bombay sapphire", "hendrick's", "beefeater"), "Gin"),
    (("vodka", "ketel one", "grey goose", "belvedere", "absolut", "beluga",
      "stolichnaya", "ciroc", "smirnoff red", "smirnoff blue", "smirnoff black"), "Vodka"),
    # Champagne & Sparkling Wines is checked BEFORE Wine (see the "Rose
    # ordering fix" note above): a rosé champagne like "Moet & Chandon
    # Imperial Brut Rose 75cl" or "Veuve Clicquot Rose 75cl" contains the
    # Wine tuple's "rose" keyword too, and whichever tuple is checked first
    # wins, so with Wine ahead of it every rosé champagne/sparkling wine was
    # being mis-bucketed as plain "Wine" even though "moet"/"veuve"/"brut"
    # right there in the same name say otherwise.
    (("champagne", "sparkling wine", "sparkling wines", "prosecco", "cava", "moet", "veuve",
      "cremant", "crémant", "spritz", "franciacorta", "asti spumante", "lambrusco",
      "trentodoc", "brut", "semi-brut", "semi brut", "blanc de blancs", "spumante",
      # Bare "sparkling" stays last and guarded by looks_like_sparkling_water()
      # below, not by the plain \b...\b check every other keyword here uses --
      # see the "Sparkling water fix" note above.
      "sparkling"), "Champagne"),
    (("wine", "wines", "shiraz", "cabernet", "merlot", "chardonnay", "sauvignon", "rose", "rosé"), "Wine"),
    (("rum", "rhum", "ron", "brandy", "cognac", "armagnac", "calvados", "tequila", "mezcal",
      "liqueur", "liquer", "liquore", "baileys", "sambuca", "amaretto", "amaro", "limoncello",
      "vermouth", "sherry", "port", "absinthe", "grappa", "pisco", "cachaca", "ouzo", "arak",
      "soju", "sake", "schnapps", "grand marnier", "aperitivo", "bitters"), "Other Spirits"),
    # Wine, second pass (2026-10-10, see "Wine and brand-gap fix" above):
    # a wine's listing very often names only its grape, its region or its
    # estate -- "Beaujolais", "Chianti Classico", "Chateau Labegorce
    # Margaux", "Campo Viejo Tempranillo" -- never the word "wine" itself,
    # so all of these used to fall through to Other Spirits. Checked LAST,
    # after the spirits keywords above, so a spirit that happens to share a
    # word with a wine name ("Chateau de Montifaud Cognac", "Rhum Blanc",
    # "Grand Marnier Cordon Rouge") is still caught as a spirit first.
    (("pinot", "noir", "grigio", "gris", "riesling", "malbec", "tempranillo", "chenin",
      "syrah", "zinfandel", "grenache", "garnacha", "sangiovese", "nebbiolo", "primitivo",
      "montepulciano", "viognier", "gewurztraminer", "traminer", "carmenere", "pinotage",
      "verdejo", "albarino", "moscato", "muscat", "semillon", "torrontes", "godello",
      "mourvedre", "cinsault", "gamay", "colombard", "barbera", "dolcetto", "aglianico",
      "nero d'avola", "fiano", "vermentino", "trebbiano", "chablis", "beaujolais",
      "bordeaux", "bourgogne", "burgundy", "rioja", "chianti", "barolo", "barbaresco",
      "valpolicella", "amarone", "ripasso", "sancerre", "medoc", "margaux", "pauillac",
      "saint emilion", "saint-emilion", "st emilion", "saint estephe", "saint-estephe",
      "saint julien", "saint-julien", "pomerol", "pessac", "graves", "sauternes", "cotes",
      "côtes", "chateauneuf", "châteauneuf", "macon", "mâcon", "pouilly", "muscadet",
      "soave", "bardolino", "ribera", "douro", "priorat", "rueda", "crozes", "hermitage",
      "gigondas", "vacqueyras", "fitou", "minervois", "corbieres", "languedoc", "provence",
      "alsace", "rully", "meursault", "montrachet", "nuits", "gevrey", "volnay", "pommard",
      "beaune", "fleurie", "morgon", "brunello", "montalcino", "bolgheri", "toscana",
      "marlborough", "barossa", "napa", "mendoza", "stellenbosch", "rhone", "rhône",
      "loire", "vouvray", "mosel", "tokaji", "vinho", "vino", "vin", "tinto", "rosato",
      "doc", "docg", "aoc", "aop", "igt", "dop", "cru", "chateau", "château", "domaine",
      "bodega", "bodegas", "tenuta", "cantina", "weingut", "quinta", "vineyard",
      "vineyards", "winery", "cuvee", "cuvée", "crianza", "blanc", "rouge",
      "sweet white", "sweet red", "dry white", "dry red", "sassicaia", "ornellaia",
      "tignanello", "penfolds", "rothschild", "cloudy bay", "kim crawford",
      "oyster bay", "yellow tail", "jacob's creek", "vina", "viña", "vieilles vignes",
      "opus one", "catena", "torres"), "Wine"),
]

# "Sparkling" alone also matches non-alcoholic sparkling water (confirmed
# live: "Hildon Gently Sparkling 330ml", "Acqua Morelli Sparkling Water
# 250ml" were both landing in Champagne & Sparkling Wines), so that one
# keyword gets an extra check the rest of the list doesn't need. See the
# "Sparkling water fix" note above.
SPARKLING_WATER_RE = re.compile(r"\bsparkling\s+water\b|\bwater\b", re.IGNORECASE)


def _category_text_variants(text):
    """The lowercased name in the forms guess_category() checks keywords
    against: as written, and with accents folded off ("Côtes" -> "cotes",
    "Rosè" -> "rose") so one plain-English keyword covers every spelling."""
    lowered = text.lower()
    folded = unicodedata.normalize("NFD", lowered)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return (lowered, folded) if folded != lowered else (lowered,)


def guess_category_strict(text):
    """Like guess_category(), but returns None (instead of the "Other
    Spirits" catch-all) when no keyword matched at all. merge.py uses this
    to tell "the name genuinely says this is a rum" apart from "nothing in
    the name told us anything", so it only overrides a scraper's own
    category guess in the first case."""
    variants = _category_text_variants(text)
    for keywords, category in CATEGORY_KEYWORDS:
        for kw in keywords:
            for lowered in variants:
                if kw == "sparkling" and SPARKLING_WATER_RE.search(lowered):
                    # Bare "sparkling" is too generic on its own -- see the
                    # "Sparkling water fix" note above -- so it's skipped
                    # whenever the name also says "water" anywhere in it.
                    continue
                if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", lowered):
                    return category
    return None


def guess_category(text):
    """Best-effort category guess from a search term or product name. Falls
    back to "Other Spirits" (the app's catch-all) when nothing matches.

    Matches each keyword as a whole word only, not as a substring, so a
    keyword like "ale" matches the word "ale" but not the "ale" hiding
    inside "Ducale", "Salento", "Whale" or "Pale". A multi-word keyword
    like "johnnie walker" or "bud light" matches the same way, as a whole
    phrase with a word boundary on each side."""
    return guess_category_strict(text) or "Other Spirits"


def extract_price(text):
    """Return the smallest BD-style price found in a chunk of text, or None."""
    matches = [float(m) for m in PRICE_RE.findall(text)]
    return min(matches) if matches else None


# Phrases these sites use on a product card to say an item can't currently be
# bought, even though a price is often still shown alongside it (A&E keeps
# showing the price with "Out Of Stock" printed right next to it). Checked as
# whole words/phrases, case-insensitive, against the card's own visible text.
OUT_OF_STOCK_PHRASES = (
    "out of stock", "sold out", "notify me", "notify when back",
    "currently unavailable", "unavailable",
)


def looks_out_of_stock(text):
    """True if any known out-of-stock phrase appears in this card/page's own
    visible text. Best-effort: confirmed against African & Eastern's own
    wording ("Out Of Stock" printed on the card); GBI Express instead marks
    an unavailable item with a different cart-button icon (handled directly
    in gbi.py, not through this text check) rather than any wording, so this
    function won't catch GBI's case. NHSC's own wording wasn't confirmed
    directly, so treat a False from this function for NHSC as "no known
    out-of-stock phrase seen", not a confirmed in-stock status, until a real
    run has been eyeballed against the live site."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in OUT_OF_STOCK_PHRASES)


def write_json(filename, results):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    path.write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} products to {path}")


def polite_sleep(seconds=1.5):
    time.sleep(seconds)


def connect_browser(p, user_agent):
    """Start a browser and a page.

    If the BRIGHTDATA_AUTH environment variable is set, use it as the FULL
    Bright Data Scraping Browser connection string, exactly as copied from
    the Bright Data dashboard's "Test API" step for Puppeteer/Playwright
    (the whole "wss://brd-customer-...-zone-...:password@brd.superproxy.io:9222"
    line, password included, no editing needed). That routes through Bright
    Data's proxy pool over CDP instead of a plain local Chromium launch,
    which is what gets past the Cloudflare bot-challenge that blocks African
    & Eastern and GBI Express on GitHub Actions' own IPs. When BRIGHTDATA_AUTH
    isn't set (e.g. running locally without a Bright Data account, or for
    scrapers that don't need a proxy at all), this falls back to the same
    local launch every scraper used before.
    """
    endpoint = os.environ.get("BRIGHTDATA_AUTH")
    if endpoint:
        browser = p.chromium.connect_over_cdp(endpoint)
        page = browser.new_page(user_agent=user_agent)
        page.set_default_navigation_timeout(120000)  # proxy adds latency; Bright Data's own guidance
    else:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        page = browser.new_page(user_agent=user_agent)
    return browser, page


def block_heavy_resources(page):
    """Abort image/font/media requests on this page.

    A Bright Data Scraping Browser session is billed by bandwidth (~$8/GB),
    and none of these scrapers need images or fonts to read a product's name
    and price, so blocking them keeps real usage far below the free 1GB/month
    tier. Harmless to call even when not running through a proxy.
    """
    def _handle(route):
        if route.request.resource_type in ("image", "font", "media"):
            route.abort()
        else:
            route.continue_()
    page.route("**/*", _handle)

# --- Brand searches (added 2026-10-10) ------------------------------------
# A&E and GBI are scraped through each shop's own search box, and that box
# only finds products whose NAME contains the word searched for. Searching
# "beer" never finds "Heineken Can 33cl", searching "cognac" never finds
# "Hennessy VS 70cl", and so on -- which is why GBI's "beer" search came
# back with just 3 products and why only ~35 products had prices from more
# than one shop. BMMI is the one shop read from its complete product list
# (its sitemap), so its brand names are the best available list of what to
# look for at the other shops. Searching A&E and GBI for those brands finds
# exactly the products a comparison needs.

# Leading words that aren't a brand on their own ("The Famous Grouse",
# "Old Monk", "Chateau Margaux"): when a name starts with one of these, the
# first TWO words are used as the search term instead.
_BRAND_PREFIX_WORDS = {
    "the", "old", "black", "red", "white", "blue", "royal", "grand", "st", "saint",
    "de", "la", "le", "les", "el", "los", "chateau", "domaine", "dr", "mr", "sir",
    "captain", "jack", "jim", "johnnie", "glen", "j", "mg", "top", "original",
    "mount", "san", "casa", "tenuta", "bodegas", "marques", "baron", "don",
    "magic", "four", "david", "ken", "grey", "michel", "maison", "just", "chateau la",
}
# Words that are never useful as a brand search (too generic, or units).
_BRAND_STOP_WORDS = {
    "beer", "wine", "gin", "vodka", "rum", "whisky", "whiskey", "brandy", "tequila",
    "liqueur", "cider", "champagne", "red", "white", "rose", "can", "cans", "bottle",
    "pack", "case", "mini", "new", "classic", "premium", "special", "extra", "gift",
    # Soft drinks, mixers and water BMMI also sells: not what this app is
    # comparing, so not worth proxy credit to search for at other shops.
    "coke", "coca-cola", "fanta", "sprite", "schweppes", "red bull", "hildon", "kdd",
    "goldberg", "acqua", "evocus", "tau", "pepsi", "7up", "mirinda", "the berry",
    "perrier", "evian", "san pellegrino", "aquafina", "lipton", "monster", "rani",
    "fever-tree", "fever", "thomas", "london essence", "lacnor", "masafi",
}


def _clean_word(word):
    word = word.lower().replace("\u2019", "'")
    word = re.sub(r"'s$", "", word)          # "Gordon's" -> "gordon"
    word = re.sub(r"[^a-z0-9&'-]", "", word)
    return word.strip("'-")


def brand_search_terms(max_terms=300, min_products=1):
    """Brand search terms built from BMMI's scraped catalogue (data/bmmi.json,
    written earlier in the same workflow run, or the last committed copy).
    Returns up to max_terms terms, most common brand first (a brand BMMI
    stocks 20 products of is far more likely to also be at A&E/GBI than one
    it stocks a single bottle of). Empty list if bmmi.json isn't there."""
    path = DATA_DIR / "bmmi.json"
    try:
        items = json.loads(path.read_text())
    except Exception:
        return []
    counts = {}
    for item in items:
        words = [w for w in (_clean_word(w) for w in str(item.get("name", "")).split()) if w]
        if not words:
            continue
        first = words[0]
        if first in _BRAND_PREFIX_WORDS and len(words) > 1:
            term = f"{first} {words[1]}"
            if term in _BRAND_PREFIX_WORDS and len(words) > 2:
                term = f"{term} {words[2]}"
        else:
            term = first
        if term in _BRAND_STOP_WORDS or len(term) < 3 or term.isdigit():
            continue
        if re.fullmatch(r"[0-9.]+(cl|ml|l|ltr)?", term):
            continue
        counts[term] = counts.get(term, 0) + 1
    ranked = sorted((t for t, c in counts.items() if c >= min_products), key=lambda t: (-counts[t], t))
    ranked = ranked[:max_terms]
    # The brand-search phase has a time budget, so on a slow week it may not
    # reach the end of this list. The top 100 brands are always searched
    # first; the rest are rotated by week number, so a brand that didn't get
    # reached this week moves up next week instead of never being searched.
    fixed, rest = ranked[:100], ranked[100:]
    if rest:
        from datetime import date
        shift = (date.today().isocalendar()[1] * 60) % len(rest)
        rest = rest[shift:] + rest[:shift]
    return fixed + rest


def term_already_covered(term, scraped_names):
    """True when a product already scraped from this shop has the brand term
    in its name, i.e. the category searches already found that brand there,
    so searching for it again would only spend proxy credit re-reading the
    same products."""
    t = term.lower()
    return any(t in n for n in scraped_names)
