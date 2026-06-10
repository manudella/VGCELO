# VGC Elo

A competitive **Elo rating system for Pokémon VGC**, built from RK9 majors
(Regional Championships and above) from the 2023 season onward — the point at
which the Scarlet/Violet data format stabilised.

It produces a fast, minimal, fully cross-linked **static website**:

- a **player ladder** with Elo, **GXE and Glicko-2** (the metrics Pokémon
  Showdown uses), peak, win rate and best finish — filterable by nationality,
  with national flags beside each name;
- a **profile per player** — rating history, biggest upmatch, longest win/loss
  streaks, every major they played, every opponent in every run, and their
  per-Pokémon win rate + signature Pokémon;
- a **page per Pokémon** — usage %, win rate, top players, and **set breakdowns**
  (item / Tera / ability / move percentages, e.g. *“% of Flutter Mane holding
  Choice Specs”*), illustrated with official artwork;
- a **page per tournament** — standings, round-by-round pairings, and event usage;
- a **methodology page** documenting exactly how Elo and every metric is computed.

Everything is linked: players ↔ opponents ↔ tournaments ↔ Pokémon.

### Colour system

The ladder is colour-coded by **rank** (no text badge — just the colour),
following the pokéball hierarchy:

| Colour | Rank |
|--------|------|
| **Champion** — Champions gold | top 300 |
| **Master** — Masterball purple | top 3,000 |
| **Player** — Pokéball red | everyone else |

Thresholds live in `config.yaml` and are sized for the real deployed scale
(thousands of players across all majors since 2023).

---

## Quick start (no scraping needed)

```bash
pip install -r requirements.txt

python -m vgcelo.cli demo      # generate sample data -> Elo -> build site
python -m scripts.download_images   # fetch official artwork for those Pokémon
python -m vgcelo.cli build     # rebuild so the artwork is included
python -m vgcelo.cli serve     # preview at http://localhost:8000
```

`demo` fabricates a realistic, multi-season dataset (fictional players, real
Pokémon, believable sets) so you can explore the entire site before touching
RK9. To run on **real data** instead, see below and [DEPLOY.md](DEPLOY.md).

---

## Commands

| Command | What it does |
|---------|--------------|
| `python -m vgcelo.cli demo` | sample data → Elo → build (offline showcase) |
| `python -m vgcelo.cli scrape` | scrape RK9 majors into the database |
| `python -m vgcelo.cli elo` | recompute all Elo ratings |
| `python -m vgcelo.cli build` | render the static site into `dist/` |
| `python -m vgcelo.cli images` | download artwork for Pokémon in the DB |
| `python -m vgcelo.cli update` | scrape → Elo → images → build (what CI runs) |
| `python -m vgcelo.cli serve` | preview the built site locally |

`scrape` is incremental: already-ingested events are skipped, so a routine run
only does work when a **new major** has been published. Use `--refresh` to
re-scrape, `--limit N` to cap how many new events to ingest, `--no-cache` to
bypass the on-disk HTML cache.

---

## How it works

```
pokedata.ovh (JSON per event: standings + team lists + round-by-round results)
  └─ scraper/pokedata.py ── discover majors (Regional+ since 2023) ─┐
                            parse matches + decklists ──────────────┤
                                                                    ▼
                              SQLite (data/vgcelo.sqlite3)
                                                 │
                       elo.py  (replay all matches chronologically)
                                                 │
                      stats.py (profiles, streaks, usage, breakdowns)
                                                 │
                    site/build.py (Jinja2 → dist/ static HTML)
```

- **Elo** is recomputed from scratch each run by replaying every match in order,
  with a tier-weighted K-factor, a provisional/elite K schedule, and soft
  seasonal regression. See the in-app **Methodology** page for the exact maths.
- **Artwork** is the Pokémon official artwork, resolved from species names via
  PokeAPI and cached locally as `<slug>.png`.

Everything is tuned in **`config.yaml`** (season window, tier cut-off, Elo
constants, tier thresholds, site title/base URL) — no code changes needed.

---

## Project layout

```
config.yaml                 all tuning lives here
vgcelo/
  cli.py                    command-line entry point
  config.py  db.py  util.py
  pokemon.py                name normalisation + artwork resolution
  elo.py                    the rating engine
  stats.py                  all derived statistics
  sample_data.py            synthetic dataset generator
  scraper/                  RK9 client + parsers (tournaments/pairings/teamlists)
  site/
    build.py                static-site generator
    templates/              Jinja2 templates
    static/                 css, js, and downloaded pokemon/ artwork
scripts/download_images.py  official-artwork downloader
.github/workflows/update.yml  autonomous scrape + deploy
dist/                       generated site (git-ignored)
```

---

## Using real data

```bash
python -m vgcelo.cli scrape     # ingest all majors (Regional+) since 2023
python -m vgcelo.cli elo        # compute Elo/Glicko
python -m vgcelo.cli images     # download official artwork
python -m vgcelo.cli build      # render the site
python -m vgcelo.cli serve
```

…or just `python -m vgcelo.cli update`, which does scrape → elo → images → build.

> **Data source.** The default source is
> [pokedata.ovh](https://www.pokedata.ovh/standingsVGC/), which publishes a JSON
> per event (placings, full team lists, and round-by-round opponents + results).
> It has no robots restrictions and offers the JSON for download. `scrape` is
> incremental and caches every response, so routine runs only fetch new events.
> A legacy RK9 HTML scraper is also included (`source: rk9` in config) but RK9's
> robots.txt disallows the pages it needs, so pokedata is the default.
>
> Some older (2023) events carry results only (no team lists yet); usage/set
> stats fill in from the events that include lists. Tera type and EV spreads are
> not in the source, so those breakdowns aren't shown.

---

## Deployment

For free, hands-off hosting that **auto-updates after every major**, see
**[DEPLOY.md](DEPLOY.md)** — GitHub Pages + a scheduled GitHub Action. It also
covers the optional **auto-post to X** that announces each new major.

Live site: **https://manudella.github.io/VGCELO/**

---

## Contributing

Contributions are welcome — bug fixes, new stats, design, data improvements, docs.
See **[CONTRIBUTING.md](CONTRIBUTING.md)** for local setup and the PR workflow.
Quick start:

```bash
git clone https://github.com/manudella/VGCELO.git
cd VGCELO
pip install -r requirements.txt
python -m vgcelo.cli demo && python -m vgcelo.cli serve   # http://localhost:8000
```

Good first issues: normalising a Pokémon form, adding a country flag alias,
adjusting a regulation window in `config.yaml`, or a stats/visual tweak.

## License

[MIT](LICENSE) © Manuel (manudella). Fan project — **not** affiliated with The
Pokémon Company, Nintendo, Game Freak, or RK9. Pokémon and all related artwork
are © their respective owners; tournament data comes from public results.
