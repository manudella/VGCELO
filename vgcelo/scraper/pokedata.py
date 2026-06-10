"""pokedata.ovh adapter — the primary data source.

pokedata.ovh publishes, for every VGC event and age division, a single JSON file
containing each player's placing, full decklist, and **round-by-round opponents
and results**. That last part is what makes a true match-based Elo possible from
a compliant source (pokedata has no robots restrictions and offers the JSON via
an explicit download button).

Discovery: the VGC index lists every event as a button linking to a numeric
event directory. We parse it, classify each event's tier and season from its
name/date, keep the *majors* (Regional and above) from the configured first
season, and for each fetch ``{code}/masters/{code}_Masters.json``.

Per event we populate the same tables the rest of the app expects:
tournaments, players, teams, team_pokemon, and matches (reconstructed by pairing
up the per-player round entries).

Data notes vs. RK9: pokedata exposes nature (``stat_alignment``) and moves
(``badges``) but **not Tera type or EV spreads**, so those breakdowns are absent.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from dateutil import parser as dateparser

from ..config import Config
from ..db import set_meta
from ..pokemon import normalize_pokedata_species
from ..util import slugify
from .rk9 import RK9Client  # generic cached/throttled HTTP client (get(full_url))

_EVENT_RE = re.compile(r"location\.href='(\d+)/'\"[^>]*>([^<]+)</button>", re.S)
_DATE_RE = re.compile(r"([A-Z][a-z]+\.?\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?,\s*\d{4})")
_NAME_COUNTRY_RE = re.compile(r"^(.*?)\s*\[([A-Za-z]{2})\]\s*$")

# Top cut never has more than this many participants in a round; Swiss at a major
# always has far more. Used to label a round's phase without an extra request.
_TOPCUT_MAX = 16


@dataclass
class EventMeta:
    code: str
    name: str
    start_date: str
    tier: str
    season: int


def classify_tier(name: str) -> str | None:
    n = name.lower()
    if "world championship" in n:
        return "worlds"
    if "international championship" in n:
        return "international"
    if "special" in n:                      # Special Event / Special Championships
        return "special"
    if "regional" in n:
        return "regional"
    return None                              # Cups / locals -> skip


def season_for(date_iso: str) -> int:
    d = dateparser.parse(date_iso)
    return d.year + 1 if d.month >= 9 else d.year


def _parse_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"\s*[-–]\s*\d{1,2}", "", m.group(1))  # range -> first day
    try:
        return dateparser.parse(raw).strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def discover(client: RK9Client, host: str, *, min_tier: str, first_season: int,
             use_cache: bool = True) -> list[EventMeta]:
    from .tournaments import TIER_ORDER
    min_rank = TIER_ORDER[min_tier]
    html = client.get(f"{host}/standingsVGC/", use_cache=use_cache)
    out: list[EventMeta] = []
    for code, raw in _EVENT_RE.findall(html):
        text = re.sub(r"\s+", " ", raw).strip()
        date_iso = _parse_date(text)
        if not date_iso:
            continue
        name = text.split(" - ")[0].strip() if " - " in text else text
        tier = classify_tier(name)
        if not tier or TIER_ORDER[tier] < min_rank:
            continue
        season = season_for(date_iso)
        if season < first_season:
            continue
        out.append(EventMeta(code=code, name=name, start_date=date_iso,
                             tier=tier, season=season))
    return sorted(out, key=lambda e: e.start_date)


# Non-ISO country codes the source uses -> ISO 3166-1 alpha-2 (for flags).
_COUNTRY_ALIASES = {"UK": "GB"}


def _split_name(raw: str) -> tuple[str, str | None]:
    raw = raw.strip()
    m = _NAME_COUNTRY_RE.match(raw)
    if m:
        cc = m.group(2).upper()
        return m.group(1).strip(), _COUNTRY_ALIASES.get(cc, cc)
    return raw, None


# Non-player tokens pokedata uses for byes / no-shows / late submissions.
_BYE_TOKENS = {"", "bye", "no show", "noshow", "late", "none", "drop",
               "dropped", "dq", "default", "loss", "forfeit", "----", "---"}


def _is_bye(name: str | None) -> bool:
    if not name:
        return True
    n = _NAME_COUNTRY_RE.sub(r"\1", name).strip().lower()
    return n in _BYE_TOKENS or "bye" in n


def scrape_all(conn, config: Config, *, refresh: bool = False,
               limit: int | None = None, use_cache: bool = True) -> dict:
    s = config.scrape
    host = s.get("pokedata_host", "https://www.pokedata.ovh").rstrip("/")
    client = RK9Client(config)
    metas = discover(client, host, min_tier=s["min_tier"],
                     first_season=s["first_season"], use_cache=use_cache)

    existing = ({r["id"] for r in conn.execute("SELECT id FROM tournaments")}
                if not refresh else set())

    processed = 0
    for meta in metas:
        tid = f"pd-{meta.code}"
        if tid in existing:
            continue
        if limit is not None and processed >= limit:
            break
        try:
            _ingest_event(conn, client, host, meta, tid, use_cache=use_cache)
            processed += 1
            print(f"  + {meta.name} ({meta.start_date}) [{meta.tier}]")
        except Exception as exc:
            conn.rollback()   # discard this event's partial inserts only
            print(f"  ! failed {meta.code} ({meta.name}): {exc}")

    set_meta(conn, "last_scrape", datetime.now(timezone.utc).isoformat())
    conn.commit()
    return {"discovered": len(metas), "ingested": processed}


def _ingest_event(conn, client, host, meta: EventMeta, tid: str, *,
                  use_cache: bool) -> None:
    url = f"{host}/standingsVGC/{meta.code}/masters/{meta.code}_Masters.json"
    players = json.loads(client.get(url, use_cache=use_cache))
    if not isinstance(players, list) or not players:
        raise ValueError("empty or unexpected JSON")

    fmt = _detect_format(players)
    conn.execute(
        """INSERT INTO tournaments
           (id,name,start_date,end_date,location,country,tier,season,
            attendance,format,source_url,scraped_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,
             start_date=excluded.start_date, tier=excluded.tier,
             season=excluded.season, attendance=excluded.attendance,
             scraped_at=excluded.scraped_at""",
        (tid, meta.name, meta.start_date, meta.start_date, None, None,
         meta.tier, meta.season, len(players), fmt,
         f"{host}/standingsVGC/{meta.code}/masters/",
         datetime.now(timezone.utc).isoformat()),
    )

    # Round -> phase, inferred from how many players appear in that round.
    round_counts: Counter = Counter()
    for pr in players:
        for rkey in (pr.get("rounds") or {}):
            round_counts[rkey] += 1

    # Pass 1: players, teams, decklists.
    known: set[str] = set()
    for pr in players:
        name, country = _split_name(pr.get("name", ""))
        if not name:
            continue
        pid = slugify(name)
        _upsert_player(conn, pid, name, country)
        known.add(pid)
        team_id = _upsert_team(conn, tid, pid, pr.get("placing"))
        conn.execute("DELETE FROM team_pokemon WHERE team_id=?", (team_id,))
        for slot, mon in enumerate(pr.get("decklist") or [], start=1):
            species = normalize_pokedata_species(mon.get("name", ""))
            # SV-era lists expose Tera type ("teratype"); the post-SV (M-A) era
            # exposes nature ("stat_alignment") instead. Store whichever is given.
            conn.execute(
                """INSERT INTO team_pokemon
                   (team_id,slot,species,item,ability,tera_type,nature,moves,evs)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (team_id, slot, species, mon.get("item") or None,
                 mon.get("ability") or None, mon.get("teratype") or None,
                 mon.get("stat_alignment") or None,
                 json.dumps(mon.get("badges") or []), json.dumps({})),
            )

    # Pass 2: reconstruct matches from per-player round entries (dedup per pair).
    seen: set[tuple] = set()
    for pr in players:
        a_name, _ = _split_name(pr.get("name", ""))
        if not a_name:
            continue
        aid = slugify(a_name)
        for rkey, rd in (pr.get("rounds") or {}).items():
            result = (rd.get("result") or "").strip().upper()[:1]
            if result not in {"W", "L", "T"}:
                continue
            try:
                rnum = int(rkey)
            except ValueError:
                continue
            phase = "top_cut" if round_counts[rkey] <= _TOPCUT_MAX else "swiss"
            table = _table_no(rd.get("table"))

            if _is_bye(rd.get("name")):
                key = ("bye", aid, rnum)
                if key in seen:
                    continue
                seen.add(key)
                conn.execute(
                    """INSERT OR IGNORE INTO matches
                       (tournament_id,phase,round,table_no,date,p1_id,p2_id,winner_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (tid, phase, rnum, table, meta.start_date, aid, None, aid))
                continue

            b_name, b_country = _split_name(rd["name"])
            bid = slugify(b_name)
            if bid not in known:   # opponent who dropped / not in final standings
                _upsert_player(conn, bid, b_name, b_country)
                known.add(bid)
            pair = tuple(sorted((aid, bid)))
            key = (pair, rnum)
            if key in seen:
                continue
            seen.add(key)
            winner = aid if result == "W" else (bid if result == "L" else None)
            p1, p2 = pair
            conn.execute(
                """INSERT OR IGNORE INTO matches
                   (tournament_id,phase,round,table_no,date,p1_id,p2_id,winner_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (tid, phase, rnum, table, meta.start_date, p1, p2, winner))
    conn.commit()


def _detect_format(players: list) -> str | None:
    """Best-effort regulation/format label if present in the data."""
    for pr in players:
        for key in ("format", "regulation", "Format"):
            if pr.get(key):
                return str(pr[key])
    return None


def _table_no(val) -> int | None:
    if val is None:
        return None
    m = re.search(r"\d+", str(val))
    return int(m.group()) if m else None


def _upsert_player(conn, pid, name, country) -> str:
    conn.execute(
        "INSERT INTO players(id,name,country) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
        "country=COALESCE(excluded.country, players.country)",
        (pid, name, country))
    return pid


def _upsert_team(conn, tid, pid, placement) -> int:
    conn.execute(
        "INSERT INTO teams(tournament_id,player_id,placement) VALUES(?,?,?) "
        "ON CONFLICT(tournament_id,player_id) DO UPDATE SET "
        "placement=COALESCE(excluded.placement, teams.placement)",
        (tid, pid, placement))
    return conn.execute(
        "SELECT id FROM teams WHERE tournament_id=? AND player_id=?",
        (tid, pid)).fetchone()["id"]
