"""Orchestrates a full scrape into the SQLite database.

Pipeline per tournament:
    discover -> pairings (matches) + roster (placements) + team lists (sets)
    -> normalise player names to stable ids -> upsert rows.

Already-scraped tournaments are skipped unless ``refresh=True``, so the nightly
job only does work when a *new* major has been published.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..config import Config
from ..db import set_meta
from ..util import player_id
from .pairings import parse_pairings
from .rk9 import RK9Client
from .teamlists import parse_roster, parse_teamlists
from .tournaments import discover


def scrape_all(conn: sqlite3.Connection, config: Config, *,
               refresh: bool = False, limit: int | None = None,
               use_cache: bool = True) -> dict:
    client = RK9Client(config)
    s = config.scrape
    metas = discover(
        client, min_tier=s["min_tier"], first_season=s["first_season"],
        use_cache=use_cache,
    )

    existing = {
        r["id"] for r in conn.execute("SELECT id FROM tournaments")
    } if not refresh else set()

    processed = 0
    for meta in metas:
        if meta.id in existing:
            continue
        if limit is not None and processed >= limit:
            break
        try:
            _ingest_one(conn, client, meta, use_cache=use_cache)
            processed += 1
            print(f"  + {meta.name} ({meta.start_date})")
        except Exception as exc:  # keep going; one bad event shouldn't abort all
            print(f"  ! failed {meta.id} ({meta.name}): {exc}")

    set_meta(conn, "last_scrape", datetime.now(timezone.utc).isoformat())
    conn.commit()
    return {"discovered": len(metas), "ingested": processed}


def _ingest_one(conn, client: RK9Client, meta, *, use_cache: bool) -> None:
    # 1. tournament row
    conn.execute(
        """
        INSERT INTO tournaments
            (id, name, start_date, end_date, location, country, tier, season,
             attendance, format, source_url, scraped_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, start_date=excluded.start_date,
            tier=excluded.tier, season=excluded.season,
            source_url=excluded.source_url, scraped_at=excluded.scraped_at
        """,
        (meta.id, meta.name, meta.start_date, meta.end_date, meta.location,
         meta.country, meta.tier, meta.season, None, None, meta.source_url,
         datetime.now(timezone.utc).isoformat()),
    )

    # 2. roster / standings
    roster = parse_roster(client.get(client.roster_url(meta.id), use_cache=use_cache))
    placements = {player_id(r.name): r.placement for r in roster}
    for r in roster:
        _upsert_player(conn, r.name, r.country)

    # 3. team lists -> teams + team_pokemon
    try:
        teamlists = parse_teamlists(
            client.get(client.teamlist_url(meta.id), use_cache=use_cache)
        )
    except Exception:
        teamlists = {}
    for name, mons in teamlists.items():
        pid = _upsert_player(conn, name, None)
        team_id = _upsert_team(conn, meta.id, pid, placements.get(pid))
        conn.execute("DELETE FROM team_pokemon WHERE team_id = ?", (team_id,))
        for mon in mons:
            conn.execute(
                """INSERT INTO team_pokemon
                   (team_id, slot, species, item, ability, tera_type, nature,
                    moves, evs)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (team_id, mon.slot, mon.species, mon.item, mon.ability,
                 mon.tera_type, mon.nature, json.dumps(mon.moves),
                 json.dumps(mon.evs)),
            )

    # Ensure every placed player at least has a (possibly teamless) team row so
    # standings render even when their list wasn't published.
    for r in roster:
        pid = player_id(r.name)
        _upsert_team(conn, meta.id, pid, r.placement)

    # 4. pairings -> matches
    pairings = parse_pairings(client.get(client.pairings_url(meta.id), use_cache=use_cache))
    for mr in pairings:
        p1 = _upsert_player(conn, mr.p1_name, None)
        p2 = _upsert_player(conn, mr.p2_name, None) if mr.p2_name else None
        winner = player_id(mr.winner_name) if mr.winner_name else None
        conn.execute(
            """INSERT OR IGNORE INTO matches
               (tournament_id, phase, round, table_no, date,
                p1_id, p2_id, winner_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (meta.id, mr.phase, mr.round, mr.table_no, meta.start_date,
             p1, p2, winner),
        )
    conn.commit()


def _upsert_player(conn, name: str, country: str | None) -> str:
    pid = player_id(name)
    conn.execute(
        "INSERT INTO players(id, name, country) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
        "country=COALESCE(excluded.country, players.country)",
        (pid, name, country),
    )
    return pid


def _upsert_team(conn, tid: str, pid: str, placement: int | None) -> int:
    conn.execute(
        "INSERT INTO teams(tournament_id, player_id, placement) VALUES(?,?,?) "
        "ON CONFLICT(tournament_id, player_id) DO UPDATE SET "
        "placement=COALESCE(excluded.placement, teams.placement)",
        (tid, pid, placement),
    )
    row = conn.execute(
        "SELECT id FROM teams WHERE tournament_id=? AND player_id=?", (tid, pid)
    ).fetchone()
    return row["id"]
