# Pour Choices — self-updating pipeline

This turns Pour Choices into a real, live-updating price comparison site: a
scheduled job scrapes BMMI, African & Eastern, GBI Express and NHSC once a
day, merges the results, and the site reads that data live. Everything here
runs on GitHub's free tier, no paid services, no separate database.

## What's in this repo

- `index.html` — the app itself (same look as before, now loads `data/products.json` instead of a hardcoded list).
- `scrapers/` — the Python scripts that fetch prices from each retailer.
- `.github/workflows/scrape.yml` — runs the scrapers once a day (03:00 UTC / 06:00 Bahrain time) and commits the refreshed data.
- `data/` — the scraped output. Starts as an empty `[]` until the first scrape runs.

## One-time setup (about 10 minutes, no coding required)

1. **Create a free GitHub account** at github.com if you don't already have one.
2. **Create a new repository**: click the `+` in the top right, "New repository". Name it `pour-choices` (or anything you like). Either Public or Private is fine and free either way; Public gives you unlimited free Actions minutes, Private gives you 2,000 free minutes/month, which is far more than this needs (a daily run takes a few minutes).
3. **Upload these files**: on the new repo's page, click "uploading an existing file" and drag in everything from this folder, *keeping the folder structure* (the `scrapers` folder, the `.github` folder and its `workflows` subfolder, `data`, `index.html`, `requirements.txt`, this `README.md`). GitHub's drag-and-drop preserves folders as long as you drag the whole folder tree in, or you can use "Add file → Upload files" a folder at a time.
4. **Turn on GitHub Pages**: in the repo, go to Settings → Pages. Under "Build and deployment", set Source to "Deploy from a branch", branch `main`, folder `/ (root)`. Save. GitHub will give you a live URL (something like `https://<your-username>.github.io/pour-choices/`) within a minute or two. That's the link to share with customers.
5. **Turn on Actions** (usually on by default for a new repo): go to the "Actions" tab and confirm workflows are enabled. You'll see "Scrape retailer prices" listed.
6. **Run it once manually** to get real data flowing immediately instead of waiting for tomorrow's 06:00 run: go to the Actions tab, click "Scrape retailer prices" in the left list, click "Run workflow" (top right), confirm. It takes a few minutes. When it finishes, refresh your Pages URL and you should see real, live products instead of "Loading live prices...".

After that, it runs on its own every day, no further action needed.

## What this does and doesn't do

- It respects every retailer's `robots.txt` (checked individually for each site). It never touches disallowed paths, uses a descriptive User-Agent, and waits between requests so it's a polite, low-impact crawler, not a hammering one.
- Product matching across retailers (working out that "Chivas Regal 12 Year Old" at one shop is the same as "Chivas 12yo" at another) is done automatically by comparing names. It's a best-effort match, not a guarantee: occasionally two different products with very similar names could get grouped together, or the same product might not get matched if the names differ too much. Worth a periodic manual glance at `data/products.json`.
- BMMI's scraper reads their sitemap directly (which lists every product page), so it should stay fairly complete as their catalog changes. African & Eastern and GBI Express are searched by category keyword (whisky, gin, vodka, etc.) since their exact category structure wasn't confirmed during setup; if you notice gaps, the search terms list at the top of `scrapers/ae.py` / `scrapers/gbi.py` is the place to add more.
- If a retailer's site is briefly down or changes its page layout, that one scraper can fail without blocking the other three (see `continue-on-error` in the workflow) — you'd just be missing that retailer's prices until it's fixed.
- This is real scraping code that I could not fully test end-to-end against the live sites from my own environment (its network is restricted, deliberately, from reaching arbitrary external sites). It's built from careful inspection of each site's real structure, but expect to spot-check the first live run and adjust the price/name matching in `scrapers/_common.py` if something looks off.

## Costs

£0 / month, as long as you stay within GitHub's free tier for Actions minutes (2,000/month on a private repo, unlimited on a public one) and Pages (free for any public repo, and for private repos on GitHub's free plan Pages is public-only too — the site itself will be publicly viewable either way, which is what you want for customers).
