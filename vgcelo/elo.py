"""The Elo rating engine.

Ratings are recomputed from scratch every run by replaying all matches in strict
chronological order. This is deterministic, easy to reason about, and cheap.

The K-factor starts high for an unknown player and decreases linearly to a stable
value over their first few rated games, so newcomers converge on their true level
quickly and then stop bouncing around:

    K(g) = k_initial - (k_initial - k_final) * (g / k_decay_games)   for g < k_decay_games
    K(g) = k_final                                                    otherwise

where ``g`` is the number of rated games the player had *before* this match.
Each match row is stamped with both players' ratings before and after, plus a
global sequence number, so the full rating history is reconstructable.
"""
from __future__ import annotations

import sqlite3

from .config import Config

# Order phases within a single tournament: Swiss first, then top cut.
_PHASE_RANK = {"swiss": 0, "top_cut": 1}


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probability that A beats B under the logistic Elo curve."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _k_for(games: int, elo_cfg: dict) -> float:
    k0 = float(elo_cfg["k_initial"])
    k1 = float(elo_cfg["k_final"])
    decay = float(elo_cfg["k_decay_games"])
    if decay <= 0 or games >= decay:
        return k1
    return k0 - (k0 - k1) * (games / decay)


def compute_elo(conn: sqlite3.Connection, config: Config) -> None:
    """Replay all matches and write Elo snapshots back into the matches table."""
    elo_cfg = config.elo
    base = float(elo_cfg["initial_rating"])

    rows = conn.execute(
        """
        SELECT m.id, m.phase, m.round, m.table_no,
               m.p1_id, m.p2_id, m.winner_id, t.start_date
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        ORDER BY t.start_date ASC, t.id ASC
        """
    ).fetchall()

    rows = sorted(
        rows,
        key=lambda r: (
            r["start_date"],
            r["id"] if r["round"] is None else 0,
            _PHASE_RANK.get(r["phase"], 0),
            r["round"] or 0,
            r["table_no"] or 0,
            r["id"],
        ),
    )

    ratings: dict[str, float] = {}
    games: dict[str, int] = {}
    seq = 0
    updates: list[tuple] = []

    for r in rows:
        p1, p2, winner = r["p1_id"], r["p2_id"], r["winner_id"]
        ra = ratings.get(p1, base)

        # Byes (no opponent) don't move rating and don't count as a rated game.
        if p2 is None:
            updates.append((seq, ra, None, ra, None, r["id"]))
            seq += 1
            continue

        rb = ratings.get(p2, base)
        ka = _k_for(games.get(p1, 0), elo_cfg)
        kb = _k_for(games.get(p2, 0), elo_cfg)

        ea = expected_score(ra, rb)
        eb = 1.0 - ea
        if winner == p1:
            sa, sb = 1.0, 0.0
        elif winner == p2:
            sa, sb = 0.0, 1.0
        else:  # tie / unresolved
            sa, sb = 0.5, 0.5

        ra_new = ra + ka * (sa - ea)
        rb_new = rb + kb * (sb - eb)

        ratings[p1] = ra_new
        ratings[p2] = rb_new
        games[p1] = games.get(p1, 0) + 1
        games[p2] = games.get(p2, 0) + 1

        updates.append((seq, ra, rb, ra_new, rb_new, r["id"]))
        seq += 1

    conn.executemany(
        "UPDATE matches SET seq = ?, p1_before = ?, p2_before = ?, "
        "p1_after = ?, p2_after = ? WHERE id = ?",
        updates,
    )
    conn.commit()
