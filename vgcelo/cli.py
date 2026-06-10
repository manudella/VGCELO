"""Command-line entry point.

Typical flows
-------------
First look / no scraping needed:
    python -m vgcelo.cli demo            # sample data -> elo -> build site
    python -m vgcelo.cli serve           # preview at http://localhost:8000

Production / autonomous update (what CI runs):
    python -m vgcelo.cli update          # scrape new majors -> elo -> build
    python -m vgcelo.cli images          # fetch any missing official artwork

Individual steps are also available: sample / scrape / elo / build / serve.
"""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .db import reset_ratings, session
from .elo import compute_elo
from .stats import build_stats


def _cmd_sample(args, config):
    from .sample_data import build_sample
    with session(config.db_path) as conn:
        res = build_sample(conn, config, seed=args.seed)
    print(f"Sample data: {res['players']} players, {res['tournaments']} tournaments.")


def _cmd_scrape(args, config):
    source = config.scrape.get("source", "pokedata")
    if source == "rk9":
        from .scraper.ingest import scrape_all
    else:
        from .scraper.pokedata import scrape_all
    with session(config.db_path) as conn:
        res = scrape_all(conn, config, refresh=args.refresh, limit=args.limit,
                         use_cache=not args.no_cache)
    print(f"Scrape ({source}): discovered {res['discovered']}, "
          f"ingested {res['ingested']}.")


def _cmd_elo(args, config):
    with session(config.db_path) as conn:
        reset_ratings(conn)
        compute_elo(conn, config)
    print("Elo recomputed.")


def _cmd_build(args, config):
    from .site.build import build_site
    with session(config.db_path) as conn:
        stats = build_stats(conn, config)
    out = build_site(stats, config)
    print(f"Site built -> {out}")


def _cmd_images(args, config):
    from scripts.download_images import download_all
    with session(config.db_path) as conn:
        species = [r["species"] for r in conn.execute(
            "SELECT DISTINCT species FROM team_pokemon")]
    n = download_all(species, config.images_dir, force=args.force)
    print(f"Images: {n} fetched into {config.images_dir}")


def _cmd_demo(args, config):
    _cmd_sample(args, config)
    _cmd_elo(args, config)
    _cmd_build(args, config)


def _cmd_update(args, config):
    _cmd_scrape(args, config)
    _cmd_elo(args, config)
    _cmd_images(args, config)
    _cmd_build(args, config)


def _cmd_announce(args, config):
    from .notify import announce
    with session(config.db_path) as conn:
        res = announce(conn, config, dry_run=args.dry_run, max_posts=args.max)
    print(f"Announce: {res}")


def _cmd_serve(args, config):
    import functools
    import http.server
    import socketserver

    directory = str(config.output_dir)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=directory)
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(f"Serving {directory} at http://localhost:{args.port} (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main(argv=None):
    # Windows consoles default to cp1252; event/player names contain accented
    # and non-Latin characters. Make stdout tolerant so progress never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="vgcelo", description=__doc__)
    parser.add_argument("--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sample", help="generate synthetic dataset")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=_cmd_sample)

    p = sub.add_parser("scrape", help="scrape RK9 majors into the database")
    p.add_argument("--refresh", action="store_true", help="re-scrape known events")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=_cmd_scrape)

    p = sub.add_parser("elo", help="recompute Elo ratings")
    p.set_defaults(func=_cmd_elo)

    p = sub.add_parser("build", help="build the static site")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("images", help="download Pokémon official artwork")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_images)

    p = sub.add_parser("demo", help="sample -> elo -> build")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=_cmd_demo)

    p = sub.add_parser("update", help="scrape -> elo -> images -> build (CI)")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_update)

    p = sub.add_parser("announce", help="post newly-added majors to X (Twitter)")
    p.add_argument("--dry-run", action="store_true", help="print tweets, don't post")
    p.add_argument("--max", type=int, default=10, help="max posts per run")
    p.set_defaults(func=_cmd_announce)

    p = sub.add_parser("serve", help="preview the built site")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    config = load_config(args.config)
    args.func(args, config)


if __name__ == "__main__":
    sys.exit(main())
