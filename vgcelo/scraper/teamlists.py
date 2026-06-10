"""Parsers for RK9 roster (standings) and public team-list pages.

* ``parse_roster`` -> final placement + name for each Masters player.
* ``parse_teamlists`` -> for each player, their six Pokémon with item / ability /
  tera type / moves (and EV spread/nature when RK9 exposes them).

These feed the per-Pokémon usage and "set breakdown" statistics (e.g. the
fraction of Flutter Mane holding Choice Specs).

⚠️ Like the pairings parser, the selectors are the brittle part. They are
isolated in small helpers with comments so you can re-point them after viewing a
cached page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from ..pokemon import normalize_species


@dataclass
class RosterEntry:
    placement: int | None
    name: str
    country: str | None = None


@dataclass
class PokemonEntry:
    species: str
    slot: int
    item: str | None = None
    ability: str | None = None
    tera_type: str | None = None
    nature: str | None = None
    moves: list[str] = field(default_factory=list)
    evs: dict[str, int] = field(default_factory=dict)


_MASTERS_RE = re.compile(r"master", re.I)


# -- roster / standings -------------------------------------------------------

def parse_roster(html: str, *, masters_only: bool = True) -> list[RosterEntry]:
    soup = BeautifulSoup(html, "lxml")
    entries: list[RosterEntry] = []

    # Standings rows usually carry a placement number + player name. We look for
    # table rows or list items beginning with a rank integer.
    for row in soup.select("tr, li, .standing, [class*='roster']"):
        text = row.get_text(" ", strip=True)
        if not text:
            continue
        m = re.match(r"^#?\s*(\d{1,4})\b[.\)]?\s+(.+)$", text)
        if not m:
            continue
        placement = int(m.group(1))
        name = _clean_name(m.group(2))
        if not name:
            continue
        entries.append(RosterEntry(placement=placement, name=name))

    # De-dup by name keeping best (lowest) placement.
    best: dict[str, RosterEntry] = {}
    for e in entries:
        cur = best.get(e.name)
        if cur is None or (e.placement or 9999) < (cur.placement or 9999):
            best[e.name] = e
    return sorted(best.values(), key=lambda e: e.placement or 9999)


# -- team lists ---------------------------------------------------------------

def parse_teamlists(html: str) -> dict[str, list[PokemonEntry]]:
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, list[PokemonEntry]] = {}

    # Each player's team is typically wrapped in a card/section that contains the
    # player's name as a header and up to six Pokémon sub-blocks.
    for card in soup.select('[class*="teamlist"], [class*="team-list"], .card, section'):
        name_el = card.select_one("h1, h2, h3, h4, .player, [class*='name']")
        if not name_el:
            continue
        player = _clean_name(name_el.get_text(" ", strip=True))
        if not player:
            continue
        mons = _parse_team_card(card)
        if mons:
            result[player] = mons
    return result


def _parse_team_card(card: Tag) -> list[PokemonEntry]:
    mons: list[PokemonEntry] = []
    blocks = card.select('[class*="pokemon"], [class*="mon"], li')
    slot = 0
    for b in blocks:
        species_el = b.select_one('[class*="species"], [class*="name"], strong, b')
        species_raw = species_el.get_text(" ", strip=True) if species_el else ""
        if not species_raw:
            continue
        slot += 1
        mons.append(PokemonEntry(
            species=normalize_species(species_raw),
            slot=slot,
            item=_field(b, "item"),
            ability=_field(b, "ability"),
            tera_type=_field(b, "tera"),
            nature=_field(b, "nature"),
            moves=_moves(b),
            evs=_evs(b),
        ))
        if slot >= 6:
            break
    return mons


def _field(b: Tag, key: str) -> str | None:
    el = b.select_one(f'[class*="{key}"]')
    if el:
        text = el.get_text(" ", strip=True)
        # Strip a leading label like "Item:".
        return re.sub(rf"^{key}\s*:?\s*", "", text, flags=re.I).strip() or None
    return None


def _moves(b: Tag) -> list[str]:
    container = b.select_one('[class*="move"]')
    moves: list[str] = []
    if container:
        for li in container.select("li, span, div"):
            mv = li.get_text(" ", strip=True)
            if mv and mv.lower() != "moves":
                moves.append(mv)
    return moves[:4]


def _evs(b: Tag) -> dict[str, int]:
    el = b.select_one('[class*="ev"], [class*="spread"]')
    if not el:
        return {}
    text = el.get_text(" ", strip=True)
    evs: dict[str, int] = {}
    for val, stat in re.findall(r"(\d{1,3})\s*(HP|Atk|Def|SpA|SpD|Spe)", text, re.I):
        evs[stat.upper().replace("SPA", "SpA").replace("SPD", "SpD")] = int(val)
    return evs


def _clean_name(name: str) -> str:
    name = re.sub(r"\(\d+-\d+(?:-\d+)?\)", "", name)
    name = re.sub(r"\[[^\]]*\]", "", name)
    return re.sub(r"\s+", " ", name).strip()
