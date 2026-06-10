"""Tournament discovery and classification.

Turns the RK9 events listing into a filtered set of *VGC majors* (Regional and
above) from the configured first season onward. Tier and season are inferred
from the event name and date because RK9's listing does not label them in a
single machine field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .rk9 import RK9Client

# Tiers ranked low -> high. Anything not matching is a "local" and is dropped.
TIER_ORDER = {"regional": 1, "special": 2, "international": 3, "worlds": 4}


@dataclass
class TournamentMeta:
    id: str
    name: str
    start_date: str            # ISO yyyy-mm-dd
    end_date: str | None
    location: str | None
    country: str | None
    tier: str
    season: int
    game: str
    source_url: str


def classify_tier(name: str) -> str | None:
    n = name.lower()
    if "world championship" in n or re.search(r"\bworlds?\b", n):
        return "worlds"
    if "international championship" in n or " ic" in n or "intercontinental" in n:
        return "international"
    if "special event" in n or "spe " in n:
        return "special"
    if "regional" in n:
        return "regional"
    return None  # League Cup / Challenge / Premier Challenge / local -> skip


def detect_game(text: str) -> str | None:
    t = text.lower()
    if "vgc" in t or "video game" in t or "pokémon video game" in t:
        return "VGC"
    if "tcg" in t or "trading card" in t:
        return "TCG"
    return None


def season_for(date_iso: str) -> int:
    """Play! Pokémon season: rolls over after Worlds (~September).

    A March 2023 regional is season 2023; a September 2023 event is season 2024.
    """
    d = dateparser.parse(date_iso)
    return d.year + 1 if d.month >= 9 else d.year


def discover(client: RK9Client, *, min_tier: str, first_season: int,
             use_cache: bool = True) -> list[TournamentMeta]:
    """Parse the listing page and return qualifying VGC majors.

    NOTE: RK9's listing markup is the single most likely thing to drift. The
    selectors below target the documented structure (a table/list of event rows
    each linking to ``/tournament/{id}``); adjust the row/field extraction here
    if RK9 changes its layout.
    """
    html = client.get(client.tournaments_url(), use_cache=use_cache)
    soup = BeautifulSoup(html, "lxml")
    min_rank = TIER_ORDER[min_tier]

    found: dict[str, TournamentMeta] = {}
    for link in soup.select('a[href*="/tournament/"]'):
        href = link.get("href", "")
        m = re.search(r"/tournament/([A-Za-z0-9]+)", href)
        if not m:
            continue
        tid = m.group(1)
        name = link.get_text(" ", strip=True)
        # Walk up to the row to grab date / location text near the link.
        row = link.find_parent(["tr", "li", "div"]) or link
        row_text = row.get_text(" ", strip=True)

        tier = classify_tier(name) or classify_tier(row_text)
        if not tier or TIER_ORDER[tier] < min_rank:
            continue
        game = detect_game(name) or detect_game(row_text) or "VGC"
        if game != "VGC":
            continue

        date_iso = _extract_date(row_text)
        if not date_iso:
            continue
        season = season_for(date_iso)
        if season < first_season:
            continue

        found[tid] = TournamentMeta(
            id=tid, name=name, start_date=date_iso, end_date=None,
            location=_extract_location(row_text), country=None,
            tier=tier, season=season, game="VGC",
            source_url=client.tournament_url(tid),
        )
    return sorted(found.values(), key=lambda t: t.start_date)


_DATE_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2}(?:\s*[-–]\s*\d{1,2})?,?\s*\d{4})"
)


def _extract_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    # Use the first day of a range like "February 24-26, 2024".
    raw = re.sub(r"\s*[-–]\s*\d{1,2}", "", raw)
    try:
        return dateparser.parse(raw).strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def _extract_location(text: str) -> str | None:
    # Best-effort: a trailing "City, ST" or "City, Country" fragment.
    m = re.search(r"([A-Z][A-Za-z .'-]+,\s*[A-Z][A-Za-z .'-]+)$", text.strip())
    return m.group(1) if m else None
