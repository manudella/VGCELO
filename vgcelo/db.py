"""SQLite storage layer.

The schema is deliberately small and normalised:

    tournaments --< matches >-- players
    tournaments --< teams >-- players
                     teams --< team_pokemon

Elo is *not* stored as a single mutable number on the player. Instead every
match records the rating of both players *before* and *after* it, in global
chronological order (``matches.seq``). That makes the whole rating history
reconstructable and powers rating charts, peak detection and "biggest upset".
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tournaments (
    id           TEXT PRIMARY KEY,         -- RK9 tournament id
    name         TEXT NOT NULL,
    start_date   TEXT NOT NULL,            -- ISO yyyy-mm-dd
    end_date     TEXT,
    location     TEXT,
    country      TEXT,
    tier         TEXT NOT NULL,            -- regional | special | international | worlds
    season       INTEGER NOT NULL,
    attendance   INTEGER,
    format       TEXT,                     -- e.g. "Regulation H"
    source_url   TEXT,
    scraped_at   TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id       TEXT PRIMARY KEY,             -- stable slug, e.g. "wolfe-glick"
    name     TEXT NOT NULL,
    country  TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id TEXT NOT NULL REFERENCES tournaments(id),
    player_id     TEXT NOT NULL REFERENCES players(id),
    placement     INTEGER,                 -- final standing (1 = winner)
    UNIQUE(tournament_id, player_id)
);

CREATE TABLE IF NOT EXISTS team_pokemon (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id   INTEGER NOT NULL REFERENCES teams(id),
    slot      INTEGER,
    species   TEXT NOT NULL,               -- normalised display name
    item      TEXT,
    ability   TEXT,
    tera_type TEXT,
    nature    TEXT,
    moves     TEXT,                         -- JSON array of move names
    evs       TEXT                          -- JSON object {stat: value}
);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    seq           INTEGER,                  -- global chronological order (Elo)
    tournament_id TEXT NOT NULL REFERENCES tournaments(id),
    phase         TEXT NOT NULL,            -- swiss | top_cut
    round         INTEGER,
    table_no      INTEGER,
    date          TEXT,
    p1_id         TEXT NOT NULL REFERENCES players(id),
    p2_id         TEXT REFERENCES players(id),   -- NULL = bye
    winner_id     TEXT,                     -- NULL = tie / unfinished / bye
    p1_before     REAL,
    p2_before     REAL,
    p1_after      REAL,
    p2_after      REAL,
    UNIQUE(tournament_id, phase, round, p1_id, p2_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Tournaments already announced on social, so we never post the same one twice.
CREATE TABLE IF NOT EXISTS announced (
    tournament_id TEXT PRIMARY KEY REFERENCES tournaments(id),
    announced_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_seq    ON matches(seq);
CREATE INDEX IF NOT EXISTS idx_matches_p1     ON matches(p1_id);
CREATE INDEX IF NOT EXISTS idx_matches_p2     ON matches(p2_id);
CREATE INDEX IF NOT EXISTS idx_matches_tourn  ON matches(tournament_id);
CREATE INDEX IF NOT EXISTS idx_teams_player   ON teams(player_id);
CREATE INDEX IF NOT EXISTS idx_tp_species     ON team_pokemon(species);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def session(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# -- small helpers ------------------------------------------------------------

def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def reset_ratings(conn: sqlite3.Connection) -> None:
    """Clear all stored Elo snapshots so the engine can recompute from scratch."""
    conn.execute(
        "UPDATE matches SET seq = NULL, p1_before = NULL, p2_before = NULL, "
        "p1_after = NULL, p2_after = NULL"
    )
    conn.commit()
