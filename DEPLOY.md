# Deploying VGC Elo (free, auto-updating)

This guide gets the site live on **GitHub Pages** and keeps it **updating itself**
after every major is published on RK9 — no server, no cost, nothing to babysit.

The mechanism: a scheduled **GitHub Action** runs every few hours. Each run
scrapes **pokedata.ovh** for any *new* majors, recomputes Elo + Glicko, rebuilds
the static site, and publishes it. Because the scraper skips events it already
has, almost every run is a cheap no-op — until a new Regional/International/Worlds
appears, at which point the ladder updates within hours of the results going up.

---

## 1. Put the project on GitHub

```bash
cd VGCELO
git init
git add .
git commit -m "Initial commit: VGC Elo ladder"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

> The `.gitignore` excludes the database, the raw scrape cache, the downloaded
> artwork, and `dist/` — **none of those go in git.** The workflow persists the
> database/cache/artwork in the **GitHub Actions cache** between runs (the DB is
> well over GitHub's 100 MB per-file limit, so it can't live in git anyway), and
> publishes `dist/` straight to Pages as an artifact. So your repo stays small:
> just the code, config, and templates.

---

## 2. Set the base URL

GitHub **project** pages are served from `https://<you>.github.io/<repo>/`, so
the site needs to know its sub-path. Edit `config.yaml`:

```yaml
site:
  base_url: "/<repo>"      # e.g. "/vgcelo"   (leave "" for a user/org or custom-domain site)
```

Commit and push the change.

---

## 3. Enable GitHub Pages (GitHub Actions source)

In the repo on GitHub:

1. **Settings → Pages**
2. **Build and deployment → Source: GitHub Actions**

That's it — the included workflow ([`.github/workflows/update.yml`](.github/workflows/update.yml))
handles building and deploying. No `gh-pages` branch needed.

---

## 4. First run

Trigger it manually so you don't have to wait for the schedule:

**Actions → “Update ladder” → Run workflow.**

The run will:

1. restore prior state from the Actions cache (empty on the very first run),
2. scrape majors → ingest into SQLite,
3. recompute Elo + Glicko,
4. download official artwork for every Pokémon seen,
5. build the site,
6. save the updated state back to the cache,
7. deploy to Pages.

When it finishes, your site is at `https://<you>.github.io/<repo>/`.

> **First run is the slow one** (~10–15 min): the cache is empty, so it scrapes
> every major from 2023 on and downloads all artwork. Every run after that is
> near-instant unless a new major has been posted, because the cache keeps both
> the database and the raw responses.

---

## 5. How the autonomous update works

The schedule is in the workflow:

```yaml
on:
  schedule:
    - cron: "0 */8 * * *"   # every 8 hours (UTC)
```

- Change the cadence by editing that cron line (e.g. `"0 6 * * *"` = once daily
  at 06:00 UTC). Every 8 hours is a good balance — frequent enough to catch a
  major the day results post, infrequent enough to be polite to the source.
- Each run calls `python -m vgcelo.cli update`, which is **incremental**: it only
  ingests majors not already in the database, and the cached raw responses mean a
  no-op run just fetches the small event index. Almost free until a new event lands.
- After ingesting a new event, Elo + Glicko are recomputed over the *entire*
  history (fast and deterministic), so a new major correctly shifts every rating.

You can also hit **Run workflow** any time to force an immediate refresh right
after a big event.

---

## 6. About the data source

The default source is **pokedata.ovh**, which serves a structured JSON per event
(placings, full team lists, and round-by-round opponents + results). That's far
more robust than HTML scraping — there are no CSS selectors to break — so there's
no per-event validation step. If pokedata ever changes its JSON shape, the only
file to touch is `vgcelo/scraper/pokedata.py`, which is small and commented.

Raw responses are cached in `data/cache/`, so re-runs and local development never
re-hit the source. Some older (2023) events include results only (no team lists);
usage/set stats fill in from events that include lists.

---

## 7. Costs & limits

- **GitHub Pages + Actions are free** for public repos. A no-op run takes ~1
  minute; a run that ingests a new major and fetches artwork takes a few minutes.
  This is far under the free Actions minutes for public repositories.
- The scraper caches responses and rate-limits itself
  (`scrape.request_delay_seconds` in `config.yaml`).

---

## 8. Optional: auto-post new majors to X (Twitter)

The workflow can tweet a link to each new major automatically. It stays off until
you add credentials (the step no-ops without them) and it never floods your
timeline: the **first run with credentials silently baselines** all existing
majors, then only genuinely new events get posted (one tweet each, with winner,
attendance, regulation, and a link to that event's page).

**Setup:**

1. Use or create the X account that will do the posting.
2. At [developer.x.com](https://developer.x.com), sign up for a (free) developer
   account and create a **Project → App**.
3. In the app's **User authentication settings**, turn on OAuth 1.0a with
   **Read and write** permission.
4. On the **Keys and tokens** tab, generate:
   - **API Key** + **API Key Secret** (the consumer keys), and
   - **Access Token** + **Access Token Secret** — generate/regenerate these
     *after* setting Read and write, or they'll be read-only.
5. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret** — add all four (exact names):
   `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.
6. That's it. The next scheduled/manual run baselines silently; from then on each
   new major is tweeted.

**Preview the wording locally (no posting):**

```bash
python -m vgcelo.cli announce --dry-run --max 3
```

The free X API tier comfortably covers this (majors are infrequent). To stop
posting, just delete the secrets.

---

## Alternative hosting

The output in `dist/` is plain static files, so anything works:

| Host | Auto-update approach |
|------|----------------------|
| **GitHub Pages** (recommended) | the included scheduled Action |
| **Netlify / Cloudflare Pages** | a scheduled function or external cron hitting a deploy hook that runs `vgcelo.cli update` |
| **Any VPS / S3 + CloudFront** | a system `cron` running `python -m vgcelo.cli update` then syncing `dist/` |

The only requirement for autonomous updates is *something on a timer* that runs
`python -m vgcelo.cli update` and publishes `dist/`. GitHub Actions just happens
to give you that for free.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| CSS/links 404 on Pages | `site.base_url` must be `"/<repo>"` for project sites |
| Empty ladder after scrape | check `data/cache/` has JSON; re-run `scrape --no-cache` |
| Missing Pokémon images | run `python -m vgcelo.cli images`; check the name in `FORM_MAP` in `vgcelo/pokemon.py` |
| Ratings look off | tune the K-factor in `config.yaml` (`elo:`), then `python -m vgcelo.cli elo && python -m vgcelo.cli build` |
| Re-scrapes every run from scratch | the Actions cache was evicted (7-day idle limit / 10 GB repo cap). Harmless — it just rebuilds the cache. Keep the schedule active to keep it warm. |
| Pages deploy fails on size | site must stay under ~1 GB; it's ~0.7 GB now. |
