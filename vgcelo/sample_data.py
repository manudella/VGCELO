"""Realistic synthetic dataset generator.

So the whole site can be built and explored *before* you ever scrape RK9, this
module fabricates a plausible multi-season history of VGC majors: fictional
players (deliberately not real people — the stats are invented), real Pokémon
with believable sets, Swiss + top-cut matches resolved by a hidden skill rating,
and varied item/tera choices so the per-Pokémon "set breakdown" stats are
non-trivial (e.g. a realistic split of Choice Specs vs Booster Energy on Flutter
Mane).

It is fully deterministic given the seed.
"""
from __future__ import annotations

import itertools
import json
import random
import sqlite3
from datetime import date, timedelta

from .config import Config
from .util import player_id

# -- name pools (fictional) ---------------------------------------------------
_FIRST = [
    "Alex", "Jordan", "Sam", "Riley", "Marco", "Lena", "Hiro", "Yuki", "Diego",
    "Sofia", "Liam", "Noah", "Emma", "Mateo", "Ren", "Kai", "Nora", "Theo",
    "Luca", "Maya", "Finn", "Ivy", "Owen", "Zoe", "Caleb", "Aria", "Ezra",
    "Mila", "Gael", "Nina", "Bruno", "Elif", "Tomas", "Sara", "Pavel", "Hana",
    "Dario", "Greta", "Ravi", "Suki", "Otto", "Vera", "Niko", "Lara", "Cyrus",
    "Talia", "Bjorn", "Anya", "Drew", "Remy",
]
_LAST = [
    "Hartley", "Voss", "Marsh", "Okada", "Romero", "Bianchi", "Nakamura",
    "Keller", "Sato", "Lindqvist", "Park", "Mendez", "Dubois", "Schmidt",
    "Rossi", "Olsen", "Costa", "Hayashi", "Novak", "Reyes", "Fischer",
    "Tanaka", "Moreau", "Ibrahim", "Petrov", "Walsh", "Kowalski", "Ferro",
    "Aoki", "Bauer", "Castro", "Doyle", "Engel", "Falk", "Greco", "Holt",
    "Ito", "Jansen", "Kato", "Larsen", "Mori", "Nash", "Ortiz", "Pace",
    "Quill", "Rana", "Stein", "Tran", "Ueda", "Vogel",
]
_COUNTRIES = ["US", "JP", "IT", "DE", "FR", "ES", "GB", "KR", "BR", "AU", "CA", "SE"]

# -- Pokémon archetypes (real species, believable sets) -----------------------
# Each entry: (species, ability, [item options w/ weights], [tera options], moves)
W = lambda *pairs: list(pairs)  # noqa: E731 small helper for (value, weight)

ARCHETYPES = {
    "Miraidon Hands": [
        ("Miraidon", "Hadron Engine", W(("Choice Specs", 5), ("Magnet", 3), ("Life Orb", 2)),
         W(("Electric", 5), ("Fairy", 3), ("Dragon", 2)),
         ["Electro Drift", "Draco Meteor", "Volt Switch", "Dazzling Gleam"]),
        ("Iron Hands", "Quark Drive", W(("Assault Vest", 6), ("Sitrus Berry", 4)),
         W(("Grass", 4), ("Bug", 3), ("Water", 3)),
         ["Fake Out", "Wild Charge", "Drain Punch", "Volt Switch"]),
        ("Flutter Mane", "Protosynthesis", W(("Choice Specs", 5), ("Booster Energy", 4), ("Focus Sash", 1)),
         W(("Fairy", 5), ("Ground", 3), ("Stellar", 2)),
         ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"]),
        ("Iron Bundle", "Quark Drive", W(("Booster Energy", 6), ("Focus Sash", 4)),
         W(("Water", 5), ("Ice", 3), ("Ghost", 2)),
         ["Freeze-Dry", "Hydro Pump", "Icy Wind", "Protect"]),
        ("Landorus (Incarnate)", "Sheer Force", W(("Life Orb", 6), ("Choice Scarf", 4)),
         W(("Poison", 4), ("Flying", 3), ("Steel", 3)),
         ["Earth Power", "Sludge Bomb", "Sandsear Storm", "Protect"]),
        ("Amoonguss", "Regenerator", W(("Rocky Helmet", 5), ("Sitrus Berry", 5)),
         W(("Water", 4), ("Grass", 3), ("Dark", 3)),
         ["Spore", "Rage Powder", "Pollen Puff", "Protect"]),
    ],
    "Calyrex-Shadow Control": [
        ("Calyrex (Shadow)", "As One", W(("Life Orb", 5), ("Focus Sash", 3), ("Spell Tag", 2)),
         W(("Ghost", 5), ("Normal", 3), ("Fairy", 2)),
         ["Astral Barrage", "Pollen Puff", "Nasty Plot", "Protect"]),
        ("Incineroar", "Intimidate", W(("Safety Goggles", 4), ("Sitrus Berry", 4), ("Assault Vest", 2)),
         W(("Grass", 4), ("Water", 3), ("Ghost", 3)),
         ["Fake Out", "Knock Off", "Parting Shot", "Flare Blitz"]),
        ("Urshifu (Rapid Strike)", "Unseen Fist", W(("Choice Scarf", 4), ("Focus Sash", 4), ("Mystic Water", 2)),
         W(("Water", 5), ("Stellar", 3), ("Grass", 2)),
         ["Surging Strikes", "Close Combat", "Aqua Jet", "Detect"]),
        ("Tornadus (Incarnate)", "Prankster", W(("Focus Sash", 5), ("Covert Cloak", 5)),
         W(("Flying", 4), ("Dark", 3), ("Ghost", 3)),
         ["Bleakwind Storm", "Tailwind", "Taunt", "Protect"]),
        ("Rillaboom", "Grassy Surge", W(("Assault Vest", 5), ("Miracle Seed", 3), ("Sitrus Berry", 2)),
         W(("Grass", 4), ("Fire", 3), ("Normal", 3)),
         ["Fake Out", "Grassy Glide", "Wood Hammer", "U-turn"]),
        ("Whimsicott", "Prankster", W(("Focus Sash", 5), ("Covert Cloak", 5)),
         W(("Dark", 4), ("Fairy", 3), ("Ghost", 3)),
         ["Tailwind", "Moonblast", "Encore", "Light Screen"]),
    ],
    "Koraidon Goodstuff": [
        ("Koraidon", "Orichalcum Pulse", W(("Clear Amulet", 4), ("Life Orb", 4), ("Choice Band", 2)),
         W(("Fire", 4), ("Fighting", 3), ("Stellar", 3)),
         ["Collision Course", "Flame Charge", "Flare Blitz", "Protect"]),
        ("Flutter Mane", "Protosynthesis", W(("Booster Energy", 6), ("Choice Specs", 3), ("Focus Sash", 1)),
         W(("Fairy", 5), ("Ground", 3), ("Stellar", 2)),
         ["Moonblast", "Shadow Ball", "Icy Wind", "Protect"]),
        ("Ogerpon (Hearthflame)", "Mold Breaker", W(("Hearthflame Mask", 10),),
         W(("Fire", 8), ("Grass", 2)),
         ["Ivy Cudgel", "Wood Hammer", "Follow Me", "Spiky Shield"]),
        ("Urshifu (Rapid Strike)", "Unseen Fist", W(("Focus Sash", 5), ("Mystic Water", 3), ("Choice Scarf", 2)),
         W(("Water", 5), ("Stellar", 3), ("Grass", 2)),
         ["Surging Strikes", "Close Combat", "Aqua Jet", "Detect"]),
        ("Landorus (Incarnate)", "Sheer Force", W(("Life Orb", 6), ("Choice Scarf", 4)),
         W(("Poison", 4), ("Flying", 3), ("Steel", 3)),
         ["Earth Power", "Sludge Bomb", "Sandsear Storm", "Protect"]),
        ("Rillaboom", "Grassy Surge", W(("Assault Vest", 5), ("Miracle Seed", 5)),
         W(("Grass", 4), ("Fire", 3), ("Normal", 3)),
         ["Fake Out", "Grassy Glide", "Wood Hammer", "High Horsepower"]),
    ],
    "Kyogre Rain": [
        ("Kyogre", "Drizzle", W(("Choice Specs", 5), ("Mystic Water", 3), ("Choice Scarf", 2)),
         W(("Water", 4), ("Grass", 3), ("Fairy", 3)),
         ["Water Spout", "Origin Pulse", "Ice Beam", "Protect"]),
        ("Pelipper", "Drizzle", W(("Focus Sash", 5), ("Covert Cloak", 5)),
         W(("Ground", 4), ("Flying", 3), ("Water", 3)),
         ["Hurricane", "Weather Ball", "Tailwind", "Protect"]),
        ("Archaludon", "Stamina", W(("Assault Vest", 6), ("Power Herb", 4)),
         W(("Grass", 4), ("Flying", 3), ("Steel", 3)),
         ["Electro Shot", "Draco Meteor", "Flash Cannon", "Body Press"]),
        ("Basculegion", "Swift Swim", W(("Choice Band", 5), ("Mystic Water", 3), ("Focus Sash", 2)),
         W(("Water", 5), ("Ghost", 3), ("Grass", 2)),
         ["Wave Crash", "Last Respects", "Aqua Jet", "Protect"]),
        ("Iron Hands", "Quark Drive", W(("Assault Vest", 6), ("Sitrus Berry", 4)),
         W(("Grass", 4), ("Bug", 3), ("Water", 3)),
         ["Fake Out", "Thunder Punch", "Drain Punch", "Wild Charge"]),
        ("Amoonguss", "Regenerator", W(("Rocky Helmet", 5), ("Sitrus Berry", 5)),
         W(("Water", 4), ("Grass", 3), ("Dark", 3)),
         ["Spore", "Rage Powder", "Pollen Puff", "Protect"]),
    ],
    "Zamazenta Balance": [
        ("Zamazenta", "Dauntless Shield", W(("Rusted Shield", 4), ("Leftovers", 4), ("Covert Cloak", 2)),
         W(("Dragon", 4), ("Fighting", 3), ("Grass", 3)),
         ["Behemoth Bash", "Body Press", "Crunch", "Protect"]),
        ("Chien-Pao", "Sword of Ruin", W(("Focus Sash", 5), ("Life Orb", 3), ("Choice Band", 2)),
         W(("Ghost", 4), ("Dark", 3), ("Stellar", 3)),
         ["Icicle Crash", "Sucker Punch", "Sacred Sword", "Protect"]),
        ("Raging Bolt", "Protosynthesis", W(("Booster Energy", 6), ("Assault Vest", 4)),
         W(("Electric", 4), ("Fairy", 3), ("Water", 3)),
         ["Thunderclap", "Draco Meteor", "Dragon Pulse", "Protect"]),
        ("Incineroar", "Intimidate", W(("Safety Goggles", 4), ("Sitrus Berry", 4), ("Assault Vest", 2)),
         W(("Grass", 4), ("Water", 3), ("Ghost", 3)),
         ["Fake Out", "Knock Off", "Parting Shot", "Flare Blitz"]),
        ("Ogerpon (Wellspring)", "Water Absorb", W(("Wellspring Mask", 10),),
         W(("Water", 9), ("Grass", 1)),
         ["Ivy Cudgel", "Horn Leech", "Follow Me", "Spiky Shield"]),
        ("Farigiraf", "Armor Tail", W(("Sitrus Berry", 5), ("Electric Seed", 3), ("Throat Spray", 2)),
         W(("Fairy", 4), ("Water", 3), ("Ground", 3)),
         ["Trick Room", "Psychic", "Helping Hand", "Protect"]),
    ],
}

ARCHETYPE_NAMES = list(ARCHETYPES.keys())

# -- tournament calendar ------------------------------------------------------
# (name, iso date, tier, location, country, attendance)
TOURNAMENTS = [
    ("Bilbao Regional Championships", "2023-02-18", "regional", "Bilbao, Spain", "ES", 480),
    ("Knoxville Regional Championships", "2023-02-25", "regional", "Knoxville, TN", "US", 360),
    ("EUIC 2023", "2023-04-14", "international", "London, UK", "GB", 720),
    ("Pokémon World Championships 2023", "2023-08-11", "worlds", "Yokohama, Japan", "JP", 1024),
    ("Stuttgart Regional Championships", "2024-01-20", "regional", "Stuttgart, Germany", "DE", 540),
    ("Portland Regional Championships", "2024-02-17", "regional", "Portland, OR", "US", 410),
    ("OCIC 2024", "2024-03-08", "international", "Brisbane, Australia", "AU", 300),
    ("LAIC 2024", "2024-11-15", "international", "São Paulo, Brazil", "BR", 650),
    ("Pokémon World Championships 2024", "2024-08-16", "worlds", "Honolulu, HI", "US", 1100),
    ("Lille Regional Championships", "2025-01-25", "regional", "Lille, France", "FR", 600),
    ("Sacramento Regional Championships", "2025-02-22", "regional", "Sacramento, CA", "US", 430),
    ("EUIC 2025", "2025-04-11", "international", "London, UK", "GB", 800),
]


def _wpick(rng: random.Random, options):
    values, weights = zip(*options)
    return rng.choices(values, weights=weights, k=1)[0]


def build_sample(conn: sqlite3.Connection, config: Config, *, seed: int = 7,
                 n_players: int = 420) -> dict:
    rng = random.Random(seed)

    # wipe
    for tbl in ["team_pokemon", "matches", "teams", "tournaments", "players"]:
        conn.execute(f"DELETE FROM {tbl}")

    # players with a hidden skill (mean 0, sd ~200) used to resolve matches
    names = _unique_names(rng, n_players)
    players = []
    for name in names:
        skill = rng.gauss(0, 220)
        players.append({
            "id": player_id(name), "name": name,
            "country": rng.choice(_COUNTRIES), "skill": skill,
            "archetype": rng.choice(ARCHETYPE_NAMES),  # a "main" they favour
            "pro": False,
        })
    # A handful of dominant "pros" who attend every major — they consistently
    # break into the Champion (gold) tier, so the full colour hierarchy shows.
    for i, p in enumerate(sorted(players, key=lambda x: x["skill"], reverse=True)[:6]):
        p["pro"] = True
        p["skill"] = 480 + (5 - i) * 35  # ~480..655
    for p in players:
        conn.execute("INSERT OR IGNORE INTO players(id,name,country) VALUES(?,?,?)",
                     (p["id"], p["name"], p["country"]))
    by_id = {p["id"]: p for p in players}

    # Track who hasn't played yet so every player gets at least one event
    # (otherwise unrated players never appear on the ladder).
    unseen = {p["id"] for p in players if not p["pro"]}
    for tmeta in TOURNAMENTS:
        _run_tournament(conn, rng, tmeta, players, by_id, unseen)

    conn.commit()
    return {"players": len(players), "tournaments": len(TOURNAMENTS)}


def _run_tournament(conn, rng, tmeta, players, by_id, unseen):
    name, dstr, tier, location, country, attendance = tmeta
    tid = player_id(name)[:24] + "-" + dstr.replace("-", "")
    season = int(dstr[:4]) + (1 if int(dstr[5:7]) >= 9 else 0)
    fmt = {"regional": "Regulation", "international": "Regulation",
           "worlds": "Regulation"}[tier]

    conn.execute(
        """INSERT INTO tournaments
           (id,name,start_date,end_date,location,country,tier,season,
            attendance,format,source_url,scraped_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, name, dstr, dstr, location, country, tier, season,
         attendance, fmt, f"https://rk9.gg/tournament/SAMPLE-{tid}", None),
    )

    # field: sample a subset of players (bigger events = bigger field).
    # Pros attend every major; the rest of the field prioritises players who
    # haven't played yet so everyone lands on the ladder.
    field_size = min(len(players), max(16, attendance // 12))
    pros = [p for p in players if p["pro"]]
    others = [p for p in players if not p["pro"]]
    need = max(0, field_size - len(pros))

    unseen_others = [p for p in others if p["id"] in unseen]
    rng.shuffle(unseen_others)
    pick = unseen_others[:need]
    if len(pick) < need:
        chosen = {p["id"] for p in pick}
        remaining = [p for p in others if p["id"] not in chosen]
        pick += rng.sample(remaining, min(need - len(pick), len(remaining)))
    for p in pick:
        unseen.discard(p["id"])
    field = pros + pick

    # assign each entrant a team for this event + persist team list
    entrant_team = {}
    for p in field:
        arch = p["archetype"] if rng.random() < 0.6 else rng.choice(ARCHETYPE_NAMES)
        team_id = _insert_team(conn, tid, p["id"], None)
        _insert_team_pokemon(conn, rng, team_id, arch)
        entrant_team[p["id"]] = team_id

    # Swiss
    n_rounds = _rounds_for(field_size)
    scores = {p["id"]: 0 for p in field}
    for rnd in range(1, n_rounds + 1):
        pairs = _swiss_pairings(rng, field, scores)
        for a, b in pairs:
            if b is None:  # bye
                conn.execute(
                    """INSERT OR IGNORE INTO matches
                       (tournament_id,phase,round,table_no,date,p1_id,p2_id,winner_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (tid, "swiss", rnd, None, dstr, a["id"], None, a["id"]))
                scores[a["id"]] += 1
                continue
            winner = _resolve(rng, a, b)
            conn.execute(
                """INSERT OR IGNORE INTO matches
                   (tournament_id,phase,round,table_no,date,p1_id,p2_id,winner_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (tid, "swiss", rnd, None, dstr, a["id"], b["id"], winner["id"]))
            scores[winner["id"]] += 1

    # Top cut: top 8 by score (skill as tiebreak), single elimination
    standings = sorted(field, key=lambda p: (scores[p["id"]], p["skill"]),
                       reverse=True)
    cut = standings[:8]
    placement = {p["id"]: i + 1 for i, p in enumerate(standings)}

    bracket = cut[:]
    cut_round = 1
    while len(bracket) > 1:
        next_round = []
        # standard 1v8,4v5,2v7,3v6 style seeding kept simple: adjacent fold
        half = len(bracket) // 2
        pairs = list(zip(bracket[:half], bracket[half:][::-1]))
        for a, b in pairs:
            winner = _resolve(rng, a, b)
            loser = b if winner is a else a
            conn.execute(
                """INSERT OR IGNORE INTO matches
                   (tournament_id,phase,round,table_no,date,p1_id,p2_id,winner_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (tid, "top_cut", cut_round, None, dstr, a["id"], b["id"], winner["id"]))
            next_round.append(winner)
        bracket = next_round
        cut_round += 1
    if cut:
        champion = bracket[0]
        # refine top-cut placements by elimination depth
        placement[champion["id"]] = 1

    # write placements
    for p in field:
        conn.execute("UPDATE teams SET placement=? WHERE tournament_id=? AND player_id=?",
                     (placement[p["id"]], tid, p["id"]))


def _resolve(rng, a, b):
    """Logistic model on hidden skill decides the winner."""
    pa = 1.0 / (1.0 + 10 ** ((b["skill"] - a["skill"]) / 400.0))
    return a if rng.random() < pa else b


def _swiss_pairings(rng, field, scores):
    buckets = {}
    for p in field:
        buckets.setdefault(scores[p["id"]], []).append(p)
    ordered = []
    for score in sorted(buckets, reverse=True):
        group = buckets[score][:]
        rng.shuffle(group)
        ordered.extend(group)
    pairs = []
    i = 0
    while i < len(ordered) - 1:
        pairs.append((ordered[i], ordered[i + 1]))
        i += 2
    if len(ordered) % 2 == 1:
        pairs.append((ordered[-1], None))  # bye
    return pairs


def _rounds_for(n: int) -> int:
    if n <= 16:
        return 5
    if n <= 32:
        return 6
    if n <= 64:
        return 7
    if n <= 128:
        return 8
    return 9


def _insert_team(conn, tid, pid, placement) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO teams(tournament_id,player_id,placement) VALUES(?,?,?)",
        (tid, pid, placement))
    return conn.execute(
        "SELECT id FROM teams WHERE tournament_id=? AND player_id=?",
        (tid, pid)).fetchone()["id"]


def _insert_team_pokemon(conn, rng, team_id, archetype):
    for slot, (species, ability, items, teras, moves) in enumerate(
            ARCHETYPES[archetype], start=1):
        conn.execute(
            """INSERT INTO team_pokemon
               (team_id,slot,species,item,ability,tera_type,nature,moves,evs)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (team_id, slot, species, _wpick(rng, items), ability,
             _wpick(rng, teras), rng.choice(["Modest", "Timid", "Adamant", "Jolly", "Bold"]),
             json.dumps(moves), json.dumps({})))


def _unique_names(rng, n):
    combos = list(itertools.product(_FIRST, _LAST))
    rng.shuffle(combos)
    return [f"{f} {l}" for f, l in combos[:n]]
