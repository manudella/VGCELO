# Contributing to VGC Elo

Thanks for your interest in improving VGC Elo! Contributions of all sizes are
welcome — bug fixes, new stats, design tweaks, data-source improvements, docs.

## Ways to help

- **Report a bug or request a feature** → open an [issue](../../issues). Include
  steps to reproduce, what you expected, and (for data bugs) a link to the
  affected player/Pokémon/tournament page.
- **Fix something** → open a pull request (see workflow below).
- **Improve the data** → e.g. a missing regulation window, a Pokémon form that
  isn't normalised correctly, or a country code that needs an ISO alias.

## Project layout

See the [README](README.md#project-layout). In short:

```
config.yaml          all tuning (seasons, Elo K, tiers, regulations)
vgcelo/              the package: scraper / elo / glicko / stats / site builder
  scraper/pokedata.py   the data adapter
  elo.py  glicko.py     rating engines
  stats.py              all derived statistics
  site/                 Jinja2 templates + static assets + builder
scripts/             helper scripts (artwork downloader)
.github/workflows/   the autonomous update + deploy pipeline
```

## Local setup

```bash
git clone https://github.com/manudella/VGCELO.git
cd VGCELO
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Fastest way to see it running (synthetic data, no network):
python -m vgcelo.cli demo
python -m vgcelo.cli serve        # http://localhost:8000

# Or build from real data:
python -m vgcelo.cli scrape       # ingest majors since 2023
python -m vgcelo.cli elo
python -m vgcelo.cli images
python -m vgcelo.cli build
python -m vgcelo.cli serve
```

## Development workflow

1. Fork the repo and create a branch: `git checkout -b my-change`.
2. Make your change. Keep it focused and match the surrounding style.
3. Verify it builds end-to-end: `python -m vgcelo.cli demo` (or a real
   `scrape`/`build`) should complete and the affected pages should render.
4. Commit with a clear message and open a PR describing **what** and **why**.

## Guidelines

- **No secrets in commits.** API tokens/keys go in GitHub Actions secrets, never
  in the repo. `data/`, `dist/`, and downloaded artwork are git-ignored on purpose.
- **Be polite to data sources.** Keep the scraper's caching and rate-limiting
  intact (`scrape.request_delay_seconds`). Don't remove the on-disk cache.
- **Keep tuning in `config.yaml`.** Prefer config over hard-coded constants.
- **Determinism.** Elo/Glicko are recomputed from scratch each run; keep them
  reproducible (no reliance on wall-clock or random state).
- **Style.** Plain, readable Python; small functions; comments where the *why*
  isn't obvious. Templates stay simple; heavy logic belongs in `stats.py`.

## Code of conduct

Be respectful and constructive. Assume good faith. Harassment or discrimination
of any kind isn't tolerated.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
