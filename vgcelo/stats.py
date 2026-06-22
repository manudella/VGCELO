"""The statistics engine.

This turns the raw tables (tournaments / players / matches / team_pokemon) into
fully-resolved profile dictionaries that the templates can render directly,
with everything cross-linked. It is the analytical heart of the site:

* per-player: rating + peak + tier, W/L, win streaks, biggest upset, average
  opponent rating, full tournament history with every opponent, and per-Pokémon
  usage & win-rate;
* per-Pokémon: usage %, win-rate when on a team, set breakdowns (item / tera /
  ability / move / nature percentages — e.g. "% Choice Specs Flutter Mane"),
  and the top players who use it;
* per-tournament: standings, pairings round-by-round, and event usage;
* site-wide records: biggest upset ever, longest streak, highest peak, etc.

Everything is computed in-memory in a couple of passes — fast enough for many
seasons of majors, and simple to audit.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .glicko import compute_glicko
from .pokemon import image_filename, image_slug, to_showdown

# Map non-ISO country codes the source uses to ISO 3166-1 alpha-2 (what flag
# images expect). pokedata tags the United Kingdom as "UK"; the ISO code is "GB".
COUNTRY_ALIASES = {"UK": "GB"}


def _norm_country(cc: str | None) -> str | None:
    if not cc:
        return cc
    return COUNTRY_ALIASES.get(cc.upper(), cc.upper())


# -- loading ------------------------------------------------------------------

def _load(conn: sqlite3.Connection) -> dict[str, Any]:
    tournaments = {
        r["id"]: dict(r)
        for r in conn.execute("SELECT * FROM tournaments")
    }
    players = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
    matches = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM matches WHERE seq IS NOT NULL ORDER BY seq ASC"
        )
    ]
    teams = [dict(r) for r in conn.execute("SELECT * FROM teams")]
    team_pokemon = defaultdict(list)
    for r in conn.execute("SELECT * FROM team_pokemon"):
        team_pokemon[r["team_id"]].append(dict(r))
    return {
        "tournaments": tournaments,
        "players": players,
        "matches": matches,
        "teams": teams,
        "team_pokemon": team_pokemon,
    }


def _player_views(matches: list[dict]) -> list[dict]:
    """Explode each match into one row per participant (the player's POV)."""
    views = []
    for m in matches:
        # Player 1 view
        is_bye = m["p2_id"] is None
        if not is_bye:
            views.append(_view(m, "p1"))
            views.append(_view(m, "p2"))
        else:
            views.append(_view(m, "p1", bye=True))
    return views


def _view(m: dict, side: str, bye: bool = False) -> dict:
    if side == "p1":
        pid, opp = m["p1_id"], m["p2_id"]
        before, after = m["p1_before"], m["p1_after"]
        opp_before = m["p2_before"]
    else:
        pid, opp = m["p2_id"], m["p1_id"]
        before, after = m["p2_before"], m["p2_after"]
        opp_before = m["p1_before"]
    if bye:
        won = None  # not counted in rate
    elif m["winner_id"] is None:
        won = None  # tie
    else:
        won = m["winner_id"] == pid
    return {
        "match_id": m["id"],
        "seq": m["seq"],
        "pid": pid,
        "opp": opp,
        "before": before,
        "after": after,
        "opp_before": opp_before,
        "delta": (after - before) if (after is not None and before is not None) else 0.0,
        "won": won,
        "is_bye": bye,
        "tournament_id": m["tournament_id"],
        "phase": m["phase"],
        "round": m["round"],
        "table_no": m["table_no"],
        "date": m["date"],
    }


# -- public API ---------------------------------------------------------------

def build_stats(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    data = _load(conn)
    tournaments, players = data["tournaments"], data["players"]
    teams, team_pokemon = data["teams"], data["team_pokemon"]
    views = _player_views(data["matches"])

    # Index player views chronologically per player.
    by_player: dict[str, list[dict]] = defaultdict(list)
    for v in views:
        by_player[v["pid"]].append(v)
    for vs in by_player.values():
        vs.sort(key=lambda v: v["seq"])

    # (player, tournament) -> record, used for per-Pokémon win-rate attribution.
    pt_record: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"w": 0, "l": 0, "t": 0}
    )
    for v in views:
        if v["is_bye"]:
            continue
        rec = pt_record[(v["pid"], v["tournament_id"])]
        if v["won"] is True:
            rec["w"] += 1
        elif v["won"] is False:
            rec["l"] += 1
        else:
            rec["t"] += 1

    teams_by_player: dict[str, list[dict]] = defaultdict(list)
    teams_by_tournament: dict[str, list[dict]] = defaultdict(list)
    team_by_pt: dict[tuple[str, str], dict] = {}
    for t in teams:
        teams_by_player[t["player_id"]].append(t)
        teams_by_tournament[t["tournament_id"]].append(t)
        team_by_pt[(t["player_id"], t["tournament_id"])] = t

    # Tag every tournament with its regulation. Era is detected from the
    # decklist fields — SV lists carry Tera types, the post-SV (M-A) era carries
    # natures — which is more reliable than dates; the date calendar is the
    # fallback (and supplies the specific SV regulation letter).
    era_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [tera, nature]
    for t in teams:
        ec = era_counts[t["tournament_id"]]
        for tp in team_pokemon.get(t["id"], []):
            if tp.get("tera_type"):
                ec[0] += 1
            if tp.get("nature"):
                ec[1] += 1

    def _reg_for(tid: str) -> str | None:
        tera, nat = era_counts.get(tid, [0, 0])
        if nat > tera and nat > 0:
            return "Regulation M-A"
        return config.regulation_for(tournaments[tid]["start_date"])

    tournament_reg = {tid: _reg_for(tid) for tid in tournaments}

    # Regulations ordered most-recent-first (by each regulation's latest event
    # date — handles regulations that recur across multiple windows). Drives the
    # filter dropdowns and every per-player / per-Pokémon regulation list.
    reg_latest: dict[str, str] = {}
    for tid, reg in tournament_reg.items():
        if not reg:
            continue
        d = tournaments[tid]["start_date"]
        if reg not in reg_latest or d > reg_latest[reg]:
            reg_latest[reg] = d
    reg_order = sorted(reg_latest, key=lambda r: reg_latest[r], reverse=True)

    # Day-2 cut: the Swiss round at which the field is sharply reduced for the
    # second day. Detected as the first Swiss round whose participant count
    # drops to <=55% of the event's peak field (Day-1 attrition is gradual; the
    # Day-2 cut is a cliff). None => single-day event (no Day 2).
    round_field: dict[str, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    for m in data["matches"]:
        if m["phase"] != "swiss" or m["round"] is None:
            continue
        s = round_field[m["tournament_id"]][m["round"]]
        s.add(m["p1_id"])
        if m["p2_id"]:
            s.add(m["p2_id"])
    day2_start: dict[str, int | None] = {}
    for tid, rounds in round_field.items():
        counts = {r: len(p) for r, p in rounds.items()}
        peak = max(counts.values()) if counts else 0
        start = None
        for r in sorted(counts):
            if peak and counts[r] <= 0.55 * peak:
                start = r
                break
        day2_start[tid] = start

    player_profiles = {
        pid: _build_player(
            pid, players[pid], by_player[pid], tournaments, players,
            teams_by_player, team_pokemon, team_by_pt, pt_record,
            tournament_reg, day2_start, reg_order, config,
        )
        for pid in players
    }

    # Glicko-2 / GXE (Showdown-style), merged onto each profile.
    glicko = compute_glicko(conn, config)
    for pid, p in player_profiles.items():
        gx = glicko.get(pid)
        if gx:
            p["glicko"], p["rd"], p["gxe"] = gx["glicko"], gx["rd"], gx["gxe"]

    # Global rank by current Elo (only players with at least one rated game).
    ranked = sorted(
        (p for p in player_profiles.values() if p["games"] > 0),
        key=lambda p: p["current_rating"],
        reverse=True,
    )
    for i, p in enumerate(ranked, start=1):
        p["rank"] = i
    leaderboard = ranked

    # Rank-based tier (Champion = top 300, Master = top 10k, Ace = rest).
    for p in player_profiles.values():
        p["tier"] = config.tier_for_rank(p["rank"])

    # Countries present, for the ladder nationality filter.
    countries = sorted({p["country"] for p in leaderboard if p["country"]})

    pokemon_profiles = _build_pokemon(
        teams, team_pokemon, pt_record, players, player_profiles,
        tournaments, tournament_reg, reg_order, config,
    )

    tournament_profiles = {
        tid: _build_tournament(
            tid, t, data["matches"], teams_by_tournament, team_pokemon,
            players, player_profiles, tournament_reg, config,
        )
        for tid, t in tournaments.items()
    }

    records = _global_records(player_profiles, leaderboard, pokemon_profiles)
    seasons = sorted({t["season"] for t in tournaments.values()})

    return {
        "players": player_profiles,
        "leaderboard": leaderboard,
        "pokemon": pokemon_profiles,
        "pokemon_list": sorted(
            pokemon_profiles.values(), key=lambda p: p["usage_count"], reverse=True
        ),
        "tournaments": tournament_profiles,
        "tournament_list": sorted(
            tournament_profiles.values(), key=lambda t: t["start_date"], reverse=True
        ),
        "records": records,
        "seasons": seasons,
        "regulations": reg_order,
        "countries": countries,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "config": config,
    }


# -- player -------------------------------------------------------------------

def _build_player(
    pid, prow, pviews, tournaments, players, teams_by_player, team_pokemon,
    team_by_pt, pt_record, tournament_reg, day2_start, reg_order, config,
):
    decided = [v for v in pviews if not v["is_bye"] and v["won"] is not None]
    wins = sum(1 for v in decided if v["won"])
    losses = sum(1 for v in decided if v["won"] is False)
    ties = sum(1 for v in pviews if not v["is_bye"] and v["won"] is None)
    byes = sum(1 for v in pviews if v["is_bye"])
    games = wins + losses

    # Rating history (use 'after' for each played match incl. byes for continuity)
    history = []
    for v in pviews:
        if v["after"] is None:
            continue
        t = tournaments[v["tournament_id"]]
        history.append({
            "seq": v["seq"],
            "rating": round(v["after"], 1),
            "date": v["date"] or t["start_date"],
            "tournament_id": v["tournament_id"],
            "tournament_name": t["name"],
        })
    current_rating = history[-1]["rating"] if history else config.elo["initial_rating"]

    # Peak
    peak_rating = current_rating
    peak_at = None
    for h in history:
        if h["rating"] >= peak_rating:
            peak_rating = h["rating"]
            peak_at = h
    if peak_at is None and history:
        peak_at = history[-1]

    # Streaks (over decided matches in order)
    longest_win = _longest(decided, True)
    longest_loss = _longest(decided, False)

    # Day-2 qualifications and top-cut appearances (distinct tournaments).
    day2_tids, topcut_tids = set(), set()
    for v in pviews:
        tid = v["tournament_id"]
        if v["phase"] == "top_cut":
            topcut_tids.add(tid)
        elif v["phase"] == "swiss":
            ds = day2_start.get(tid)
            if ds is not None and v["round"] is not None and v["round"] >= ds:
                day2_tids.add(tid)

    # Biggest upset = the win against the opponent who was rated highest *above*
    # the player going into the match (largest pre-match Elo gap overcome).
    upset = None
    upset_gap = None
    for v in decided:
        if not v["won"] or v["before"] is None or v["opp_before"] is None:
            continue
        gap = v["opp_before"] - v["before"]
        if upset is None or gap > upset_gap:
            upset, upset_gap = v, gap
    biggest_upset = _decorate_match(upset, players, tournaments) if upset else None

    # Worst loss (largest rating drop)
    worst = None
    for v in decided:
        if v["won"] is False and (worst is None or v["delta"] < worst["delta"]):
            worst = v
    worst_loss = _decorate_match(worst, players, tournaments) if worst else None

    # Tournament history (sorted by date desc)
    tourn_hist = []
    pteams = sorted(
        teams_by_player[pid],
        key=lambda tm: tournaments[tm["tournament_id"]]["start_date"],
        reverse=True,
    )
    seen_t = set()
    # Build per-tournament match list
    matches_by_t: dict[str, list[dict]] = defaultdict(list)
    for v in pviews:
        matches_by_t[v["tournament_id"]].append(v)

    # Record by regulation: {reg: {w, l, events}}
    reg_acc: dict[str, dict] = defaultdict(lambda: {"w": 0, "l": 0, "events": 0})

    for tm in pteams:
        tid = tm["tournament_id"]
        seen_t.add(tid)
        t = tournaments[tid]
        rec = pt_record[(pid, tid)]
        reg = tournament_reg.get(tid)
        if reg:
            ra = reg_acc[reg]
            ra["w"] += rec["w"]
            ra["l"] += rec["l"]
            ra["events"] += 1
        team_rows = sorted(team_pokemon.get(tm["id"], []), key=lambda x: x["slot"] or 0)
        team = [
            {"species": tp["species"], "slug": image_slug(tp["species"]),
             "image": image_filename(tp["species"])}
            for tp in team_rows
        ]
        # Showdown/PokePaste export of the team (empty when no list was published).
        pokepaste = to_showdown([
            {"species": tp["species"], "item": tp.get("item"),
             "ability": tp.get("ability"), "tera_type": tp.get("tera_type"),
             "nature": tp.get("nature"), "moves": _safe_moves(tp.get("moves"))}
            for tp in team_rows
        ]) if team_rows else ""
        tmatches = []
        for v in sorted(matches_by_t[tid], key=lambda x: (x["seq"])):
            opp = v["opp"]
            tmatches.append({
                "round": v["round"],
                "phase": v["phase"],
                "table_no": v["table_no"],
                "opponent_id": opp,
                "opponent_name": players[opp]["name"] if opp else "Bye",
                "result": ("W" if v["won"] else "L" if v["won"] is False else
                           ("Bye" if v["is_bye"] else "T")),
                "delta": round(v["delta"], 1),
            })
        tourn_hist.append({
            "tournament_id": tid,
            "tournament_name": t["name"],
            "date": t["start_date"],
            "tier": t["tier"],
            "season": t["season"],
            "regulation": reg,
            "placement": tm["placement"],
            "record": f"{rec['w']}-{rec['l']}" + (f"-{rec['t']}" if rec["t"] else ""),
            "team": team,
            "pokepaste": pokepaste,
            "matches": tmatches,
        })

    reg_record = []
    for reg, d in reg_acc.items():
        tot = d["w"] + d["l"]
        reg_record.append({
            "regulation": reg,
            "wins": d["w"], "losses": d["l"], "events": d["events"],
            "win_rate": round(100 * d["w"] / tot, 1) if tot else None,
        })
    reg_record.sort(key=lambda x: reg_order.index(x["regulation"])
                    if x["regulation"] in reg_order else 999)

    # Per-Pokémon usage & win-rate for this player
    poke = defaultdict(lambda: {"tournaments": 0, "w": 0, "l": 0})
    for tm in teams_by_player[pid]:
        tid = tm["tournament_id"]
        rec = pt_record[(pid, tid)]
        for tp in team_pokemon.get(tm["id"], []):
            sp = tp["species"]
            poke[sp]["tournaments"] += 1
            poke[sp]["w"] += rec["w"]
            poke[sp]["l"] += rec["l"]
    pokemon_usage = []
    for sp, d in poke.items():
        tot = d["w"] + d["l"]
        pokemon_usage.append({
            "species": sp,
            "slug": image_slug(sp),
            "image": image_filename(sp),
            "tournaments": d["tournaments"],
            "wins": d["w"],
            "losses": d["l"],
            "win_rate": round(100 * d["w"] / tot, 1) if tot else None,
        })
    pokemon_usage.sort(key=lambda x: (x["tournaments"], x["wins"]), reverse=True)
    signature = pokemon_usage[0] if pokemon_usage else None

    best_placement = min(
        (tm["placement"] for tm in teams_by_player[pid] if tm["placement"]),
        default=None,
    )

    return {
        "id": pid,
        "name": prow["name"],
        "country": _norm_country(prow["country"]),
        "current_rating": round(current_rating, 1),
        "peak_rating": round(peak_rating, 1),
        "peak_at": peak_at,
        "tier": None,   # assigned by ladder rank after all players are built
        "rank": None,
        "glicko": None,
        "rd": None,
        "gxe": None,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "byes": byes,
        "games": games,
        "win_rate": round(100 * wins / games, 1) if games else 0.0,
        "tournaments_played": len(seen_t),
        "day2_count": len(day2_tids),
        "top_cut_count": len(topcut_tids),
        "best_placement": best_placement,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "biggest_upset": biggest_upset,
        "worst_loss": worst_loss,
        "rating_history": history,
        "tournament_history": tourn_hist,
        "pokemon_usage": pokemon_usage,
        "signature_pokemon": signature,
        "reg_record": reg_record,
    }


def _longest(decided: list[dict], want_win: bool) -> int:
    best = cur = 0
    for v in decided:
        if v["won"] is want_win:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _decorate_match(v: dict, players: dict, tournaments: dict) -> dict | None:
    if v is None:
        return None
    opp = v["opp"]
    t = tournaments[v["tournament_id"]]
    own_before = v["before"]
    opp_before = v.get("opp_before")
    gap = (opp_before - own_before) if (opp_before is not None and own_before is not None) else None
    return {
        "opponent_id": opp,
        "opponent_name": players[opp]["name"] if opp else "Bye",
        "tournament_id": v["tournament_id"],
        "tournament_name": t["name"],
        "round": v["round"],
        "phase": v["phase"],
        "delta": round(v["delta"], 1),
        "rating_before": round(own_before, 1) if own_before is not None else None,
        "opp_rating": round(opp_before, 1) if opp_before is not None else None,
        "gap": round(gap, 1) if gap is not None else None,
    }


# -- pokemon ------------------------------------------------------------------

def _build_pokemon(
    teams, team_pokemon, pt_record, players, player_profiles, tournaments,
    tournament_reg, reg_order, config,
):
    # Denominators = teams that actually published a list (overall + per reg).
    total_with_list = 0
    reg_list_total: Counter = Counter()
    for t in teams:
        if team_pokemon.get(t["id"]):
            total_with_list += 1
            reg = tournament_reg.get(t["tournament_id"])
            if reg:
                reg_list_total[reg] += 1
    total_with_list = total_with_list or 1

    # species -> list of (team, regulation, team_pokemon_row), one row per team.
    species_entries: dict[str, list[tuple]] = defaultdict(list)
    for t in teams:
        rows = team_pokemon.get(t["id"])
        if not rows:
            continue
        reg = tournament_reg.get(t["tournament_id"])
        for tp in rows:
            tp["_moves"] = _safe_moves(tp.get("moves"))  # parse once
            species_entries[tp["species"]].append((t, reg, tp))

    def panel(entries, denom, single_reg=False):
        """One regulation's view of a species: usage, win rate, sets, top users."""
        w = l = 0
        pw: Counter = Counter()   # player -> wins with this Pokémon
        pl: Counter = Counter()   # player -> losses
        users: set[str] = set()
        rows = [tp for (_t, _r, tp) in entries]
        for (tm, _reg, _tp) in entries:
            rec = pt_record[(tm["player_id"], tm["tournament_id"])]
            w += rec["w"]
            l += rec["l"]
            pw[tm["player_id"]] += rec["w"]
            pl[tm["player_id"]] += rec["l"]
            users.add(tm["player_id"])
        decided = w + l
        # Best players = most wins with the Pokémon (tiebreak: fewer losses).
        top = sorted(users, key=lambda uid: (pw[uid], -pl[uid]), reverse=True)[:12]
        top_players = [
            {"id": uid, "name": players[uid]["name"],
             "wins": pw[uid], "losses": pl[uid]}
            for uid in top if pw[uid] + pl[uid] > 0
        ]
        tera = _breakdown(rows, "tera_type")
        nature = _breakdown(rows, "nature")     # "stat alignment" (Champions)
        # Within a single regulation only one of the two applies; drop the
        # off-era field if it's just a handful of stray entries.
        if single_reg and tera and nature:
            tt = sum(x["count"] for x in tera)
            nt = sum(x["count"] for x in nature)
            if tt >= nt:
                nature = []
            else:
                tera = []
        return {
            "usage": len(entries),
            "pct": round(100 * len(entries) / (denom or 1), 1),
            "win_rate": round(100 * w / decided, 1) if decided else None,
            "wins": w, "losses": l, "users": len(users),
            "breakdown": {
                "item": _breakdown(rows, "item"),
                "tera_type": tera,
                "nature": nature,
                "ability": _breakdown(rows, "ability"),
                "moves": _move_breakdown(rows),
            },
            "top_players": top_players,
        }

    profiles = {}
    for species, entries in species_entries.items():
        regs_present = [r for r in reg_order
                        if any(e[1] == r for e in entries)]
        by_reg = {"all": panel(entries, total_with_list)}
        for reg in regs_present:
            sub = [e for e in entries if e[1] == reg]
            by_reg[reg] = panel(sub, reg_list_total.get(reg, 0), single_reg=True)

        slug = image_slug(species)
        allp = by_reg["all"]
        profiles[slug] = {
            "species": species,
            "slug": slug,
            "image": image_filename(species),
            # top-level = "all" view, used by the index / home / search.
            "usage_count": allp["usage"],
            "usage_pct": allp["pct"],
            "win_rate": allp["win_rate"],
            "users_count": allp["users"],
            "regs_present": regs_present,
            "by_reg": by_reg,
        }
    return profiles


def _safe_moves(raw) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _breakdown(rows: list[dict], field: str, top: int = 8) -> list[dict]:
    counter = Counter(r[field] for r in rows if r.get(field))
    total = sum(counter.values()) or 1
    return [
        {"name": name, "count": cnt, "pct": round(100 * cnt / total, 1)}
        for name, cnt in counter.most_common(top)
    ]


def _move_breakdown(rows: list[dict], top: int = 12) -> list[dict]:
    counter: Counter = Counter()
    n = 0
    for r in rows:
        moves = r.get("_moves")
        if not moves:
            continue
        n += 1
        for mv in moves:
            counter[mv] += 1
    n = n or 1
    return [
        {"name": name, "count": cnt, "pct": round(100 * cnt / n, 1)}
        for name, cnt in counter.most_common(top)
    ]


# -- tournament ---------------------------------------------------------------

def _build_tournament(
    tid, t, all_matches, teams_by_tournament, team_pokemon, players,
    player_profiles, tournament_reg, config,
):
    tmatches = [m for m in all_matches if m["tournament_id"] == tid]
    rounds: dict[tuple, list] = defaultdict(list)
    for m in sorted(tmatches, key=lambda m: (m["phase"] != "swiss", m["round"] or 0,
                                             m["table_no"] or 0)):
        key = (m["phase"], m["round"])
        rounds[key].append({
            "table_no": m["table_no"],
            "p1_id": m["p1_id"],
            "p1_name": players[m["p1_id"]]["name"] if m["p1_id"] else "Bye",
            "p2_id": m["p2_id"],
            "p2_name": players[m["p2_id"]]["name"] if m["p2_id"] else "Bye",
            "winner_id": m["winner_id"],
        })
    round_list = [
        {"phase": k[0], "round": k[1], "pairings": v}
        for k, v in rounds.items()
    ]

    standings = []
    for tm in sorted(teams_by_tournament[tid], key=lambda x: x["placement"] or 9999):
        pid = tm["player_id"]
        p = player_profiles.get(pid, {})
        team = [
            {"species": tp["species"], "slug": image_slug(tp["species"]),
             "image": image_filename(tp["species"])}
            for tp in sorted(team_pokemon.get(tm["id"], []), key=lambda x: x["slot"] or 0)
        ]
        standings.append({
            "placement": tm["placement"],
            "player_id": pid,
            "player_name": players[pid]["name"],
            "rating": p.get("current_rating"),
            "tier": p.get("tier"),
            "team": team,
        })

    # Event Pokémon usage
    usage = Counter()
    for tm in teams_by_tournament[tid]:
        for tp in team_pokemon.get(tm["id"], []):
            usage[tp["species"]] += 1
    n_teams = len(teams_by_tournament[tid]) or 1
    top_usage = [
        {"species": sp, "slug": image_slug(sp), "image": image_filename(sp),
         "count": c, "pct": round(100 * c / n_teams, 1)}
        for sp, c in usage.most_common(12)
    ]

    # Derive a city/location from the event name when the source didn't give one
    # ("2026 Indianapolis Pokémon VGC Regional…" -> "Indianapolis").
    location = t["location"]
    if not location:
        m = re.match(r"^(?:\d{4}\s+)?(.+?)\s+Pok[ée]mon", t["name"])
        location = m.group(1).strip() if (m and m.group(1).strip()) else None

    return {
        "id": tid,
        "name": t["name"],
        "start_date": t["start_date"],
        "end_date": t["end_date"],
        "location": location,
        "country": t["country"],
        "tier": t["tier"],
        "season": t["season"],
        "regulation": tournament_reg.get(tid),
        "attendance": t["attendance"],
        "format": t["format"],
        "source_url": t["source_url"],
        "rounds": round_list,
        "standings": standings,
        "top_usage": top_usage,
    }


# -- global records -----------------------------------------------------------

def _global_records(player_profiles, leaderboard, pokemon_profiles):
    players = list(player_profiles.values())
    active = [p for p in players if p["games"] > 0]

    def top(metric, key, n=10, reverse=True, filt=None):
        pool = [p for p in active if (filt(p) if filt else True)]
        return sorted(pool, key=key, reverse=reverse)[:n]

    biggest_upsets = sorted(
        (p for p in active if p["biggest_upset"] and p["biggest_upset"]["gap"] is not None),
        key=lambda p: p["biggest_upset"]["gap"], reverse=True,
    )[:10]

    return {
        "highest_peak": top("peak", lambda p: p["peak_rating"]),
        "longest_win_streak": top("win_streak", lambda p: p["longest_win_streak"]),
        "most_tournaments": top("tournaments", lambda p: p["tournaments_played"]),
        "most_games": top("games", lambda p: p["games"]),
        "best_win_rate": sorted(
            [p for p in active if p["games"] >= 20],
            key=lambda p: p["win_rate"], reverse=True,
        )[:10],
        "biggest_upsets": biggest_upsets,
        "most_used_pokemon": sorted(
            pokemon_profiles.values(), key=lambda p: p["usage_count"], reverse=True
        )[:10],
    }
