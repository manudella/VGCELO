"""Glicko-2 ratings + GXE, the system Pokémon Showdown uses.

Glicko-2 improves on Elo by tracking, alongside the rating (R), a *rating
deviation* (RD, how uncertain we are) and a *volatility* (how erratic the player
is). It updates in discrete **rating periods** — here, one period per tournament:
all of a player's matches at an event are applied together, and inactive players'
RD grows between periods.

From the final (R, RD) we derive **GXE** ("X-Act estimate") exactly as Showdown
does: the player's estimated chance of beating an average-rated opponent,
expressed as a percentage.

Reference: Glickman, "Example of the Glicko-2 system" (2013).
"""
from __future__ import annotations

import math
import sqlite3
from collections import OrderedDict, defaultdict

from .config import Config

SCALE = 173.7178  # Glicko <-> Glicko-2 conversion constant
LN10 = math.log(10)


def compute_glicko(conn: sqlite3.Connection, config: Config) -> dict[str, dict]:
    g = config.raw.get("glicko", {})
    tau = float(g.get("tau", 0.5))
    rd0 = float(g.get("initial_rd", 350.0))
    vol0 = float(g.get("initial_vol", 0.06))
    base = float(g.get("initial_rating", 1500.0))
    phi_max = rd0 / SCALE

    rows = conn.execute(
        """
        SELECT m.p1_id, m.p2_id, m.winner_id, t.start_date, m.tournament_id
        FROM matches m JOIN tournaments t ON t.id = m.tournament_id
        WHERE m.p2_id IS NOT NULL
        ORDER BY t.start_date ASC, t.id ASC
        """
    ).fetchall()

    # Group matches into periods (one per tournament), preserving date order.
    periods: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        periods.setdefault(r["tournament_id"], []).append(r)

    # player -> [mu, phi, sigma]  (mu is the Glicko-2 internal rating; mu=0 <-> base)
    players: dict[str, list[float]] = {}

    def ensure(pid: str) -> None:
        players.setdefault(pid, [0.0, phi_max, vol0])

    for _tid, ms in periods.items():
        results: dict[str, list[tuple[str, float]]] = defaultdict(list)
        active: set[str] = set()
        for m in ms:
            p1, p2, w = m["p1_id"], m["p2_id"], m["winner_id"]
            if w is None:
                s1 = s2 = 0.5
            else:
                s1 = 1.0 if w == p1 else 0.0
                s2 = 1.0 - s1
            results[p1].append((p2, s1))
            results[p2].append((p1, s2))
            active.add(p1)
            active.add(p2)

        for pid in active:
            ensure(pid)
        snapshot = {pid: list(v) for pid, v in players.items()}

        for pid in active:
            mu, phi, sigma = snapshot[pid]
            v_inv = 0.0
            dsum = 0.0
            for opp, s in results[pid]:
                muj, phij, _ = snapshot[opp]
                gj = 1.0 / math.sqrt(1.0 + 3.0 * phij * phij / (math.pi ** 2))
                ej = 1.0 / (1.0 + math.exp(-gj * (mu - muj)))
                v_inv += gj * gj * ej * (1.0 - ej)
                dsum += gj * (s - ej)
            if v_inv <= 0:
                continue
            v = 1.0 / v_inv
            delta = v * dsum
            sigma_new = _new_volatility(phi, v, delta, sigma, tau)
            phi_star = math.sqrt(phi * phi + sigma_new * sigma_new)
            phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
            mu_new = mu + phi_new * phi_new * dsum
            players[pid] = [mu_new, phi_new, sigma_new]

        # Inactive players: only their RD grows (capped at the initial RD).
        for pid, (mu, phi, sigma) in snapshot.items():
            if pid in active:
                continue
            phi_star = math.sqrt(phi * phi + sigma * sigma)
            players[pid] = [mu, min(phi_star, phi_max), sigma]

    out: dict[str, dict] = {}
    for pid, (mu, phi, sigma) in players.items():
        r = SCALE * mu + base
        rd = SCALE * phi
        out[pid] = {
            "glicko": round(r, 1),
            "rd": round(rd, 1),
            "vol": round(sigma, 4),
            "gxe": gxe(r, rd),
        }
    return out


def gxe(rating: float, rd: float) -> float:
    """Showdown's GXE: estimated % chance of beating an average ladder player."""
    denom = math.sqrt(
        3.0 * LN10 * LN10 * rd * rd
        + 2500.0 * (64.0 * math.pi * math.pi + 147.0 * LN10 * LN10)
    )
    val = 10000.0 / (1.0 + 10.0 ** (((1500.0 - rating) * math.pi) / denom))
    return round(val / 100.0, 1)


def _new_volatility(phi: float, v: float, delta: float, sigma: float,
                    tau: float) -> float:
    """Illinois-algorithm root find for the new volatility (Glicko-2 step 5)."""
    a = math.log(sigma * sigma)
    eps = 1e-6

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (tau * tau)

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    while abs(B - A) > eps:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA = fA / 2.0
        B, fB = C, fC
    return math.exp(A / 2.0)
