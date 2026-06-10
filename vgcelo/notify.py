"""Announce newly-added majors on X (Twitter).

When a new major is ingested, this posts a tweet linking to its page on the site.
It is safe and optional:

* If the four X API credentials aren't set in the environment, it does nothing
  (so normal builds for anyone without X configured are unaffected).
* It records every announced tournament in the ``announced`` table, so each major
  is posted exactly once — even though ratings are recomputed every run.
* The first time it runs it *baselines* (marks all existing majors as announced
  without posting), so enabling it never floods the timeline with back-catalogue.

Credentials (set as GitHub Actions secrets, OAuth 1.0a user context with
read+write):  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from .config import Config
from .db import get_meta, set_meta

X_TWEETS_URL = "https://api.twitter.com/2/tweets"
TWEET_LIMIT = 280


def _credentials() -> dict | None:
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    vals = {k: os.environ.get(k) for k in keys}
    if all(vals.values()):
        return vals
    return None


def _post_tweet(creds: dict, text: str) -> tuple[bool, str]:
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        return False, "requests-oauthlib not installed"
    oauth = OAuth1Session(
        creds["X_API_KEY"], creds["X_API_SECRET"],
        creds["X_ACCESS_TOKEN"], creds["X_ACCESS_SECRET"],
    )
    try:
        resp = oauth.post(X_TWEETS_URL, json={"text": text}, timeout=30)
    except Exception as exc:  # network etc.
        return False, str(exc)
    if resp.status_code in (200, 201):
        return True, resp.json().get("data", {}).get("id", "ok")
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


# -- tweet content ------------------------------------------------------------

def _regulation(conn, config: Config, tid: str, start_date: str) -> str | None:
    """Era-aware regulation: nature-bearing lists => Champions (M-A), else date."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN tera_type IS NOT NULL THEN 1 ELSE 0 END) tera, "
        "SUM(CASE WHEN nature IS NOT NULL THEN 1 ELSE 0 END) nat "
        "FROM team_pokemon tp JOIN teams t ON t.id = tp.team_id "
        "WHERE t.tournament_id = ?", (tid,)).fetchone()
    tera, nat = (row["tera"] or 0), (row["nat"] or 0)
    if nat > tera and nat > 0:
        return "Regulation M-A"
    return config.regulation_for(start_date)


def _winner(conn, tid: str) -> str | None:
    row = conn.execute(
        "SELECT p.name FROM teams t JOIN players p ON p.id = t.player_id "
        "WHERE t.tournament_id = ? AND t.placement = 1 LIMIT 1", (tid,)).fetchone()
    return row["name"] if row else None


def _compose(t: dict, winner: str | None, regulation: str | None,
             site_url: str) -> str:
    link = f"{site_url.rstrip('/')}/tournament/{t['id']}.html"
    lines = [f"🆕 {t['name']}"]
    if winner:
        lines.append(f"🏆 Winner: {winner}")
    meta = []
    if t.get("attendance"):
        meta.append(f"👥 {t['attendance']} players")
    if regulation:
        meta.append(regulation)
    if meta:
        lines.append(" · ".join(meta))
    lines.append("")
    lines.append(f"Elo ratings, usage & team lists 👉 {link}")
    lines.append("")
    lines.append("#VGC #PokemonVGC")
    text = "\n".join(lines)
    if len(text) > TWEET_LIMIT:  # trim the title if we somehow overflow
        over = len(text) - TWEET_LIMIT
        lines[0] = "🆕 " + t["name"][: max(0, len(t["name"]) - over - 1)] + "…"
        text = "\n".join(lines)
    return text


# -- main ---------------------------------------------------------------------

def announce(conn: sqlite3.Connection, config: Config, *, dry_run: bool = False,
             max_posts: int = 10) -> dict:
    creds = _credentials()
    if creds is None and not dry_run:
        return {"skipped": "no X credentials set"}

    now = datetime.now(timezone.utc).isoformat()
    announced = {r["tournament_id"]
                 for r in conn.execute("SELECT tournament_id FROM announced")}
    majors = [dict(r) for r in conn.execute(
        "SELECT id, name, start_date, attendance FROM tournaments "
        "ORDER BY start_date ASC")]
    pending = [t for t in majors if t["id"] not in announced]

    # First-ever run: baseline the back-catalogue silently. Skipped in dry-run
    # so you can preview what *would* be posted.
    if not dry_run and not get_meta(conn, "announce_initialized"):
        for t in majors:
            conn.execute("INSERT OR IGNORE INTO announced VALUES (?, ?)",
                         (t["id"], now))
        set_meta(conn, "announce_initialized", "1")
        conn.commit()
        return {"baselined": len(majors), "posted": 0}

    if not pending:
        return {"posted": 0, "pending": 0}

    site_url = config.site.get("url", "")
    posted, errors = 0, []
    for t in pending[:max_posts]:
        text = _compose(t, _winner(conn, t["id"]),
                        _regulation(conn, config, t["id"], t["start_date"]),
                        site_url)
        if dry_run:
            print("---- would tweet ----\n" + text + "\n")
            posted += 1
            continue
        ok, info = _post_tweet(creds, text)
        if ok:
            conn.execute("INSERT OR IGNORE INTO announced VALUES (?, ?)",
                         (t["id"], now))
            conn.commit()
            posted += 1
            print(f"  tweeted: {t['name']} ({info})")
        else:
            errors.append(f"{t['name']}: {info}")
            print(f"  ! failed: {t['name']}: {info}")

    return {"posted": posted, "pending": len(pending), "errors": errors}
