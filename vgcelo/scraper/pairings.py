"""Parser for an RK9 pairings page.

A single pairings page (``/pairings/{id}``) contains *every* round for the
event, grouped by age division (Masters/Senior/Junior) and round. For the Elo
ladder we only care about Masters.

Each pairing exposes the two players, the table number, and — once the match is
reported — which player won (RK9 marks the winner, e.g. a "winner" CSS class or
a trophy/check glyph, and/or strikes through the loser).

⚠️ The exact CSS classes are the part most likely to drift between RK9 redesigns.
The selectors below are factored into small helpers and commented so you can
re-point them after inspecting a live page (open one cached file in
``data/cache`` and search for a known player's name).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag


@dataclass
class MatchRow:
    phase: str          # "swiss" | "top_cut"
    round: int
    table_no: int | None
    p1_name: str
    p2_name: str | None     # None = bye
    winner_name: str | None  # None = tie / unreported / bye


# Division header text -> we only keep Masters for the ladder.
_MASTERS_RE = re.compile(r"master", re.I)
_ROUND_RE = re.compile(r"(?:round|r)\s*(\d+)", re.I)
_TOPCUT_RE = re.compile(r"top\s*\d+|single elim|playoff|day ?[23]", re.I)


def parse_pairings(html: str, *, masters_only: bool = True) -> list[MatchRow]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[MatchRow] = []

    # RK9 groups matches under round containers. We locate each match block and
    # read its nearest round/division headers. A "match" block is identified by
    # containing exactly two player elements (or one + a bye).
    for match_el in _iter_match_blocks(soup):
        division = _nearest_header(match_el, kind="division")
        if masters_only and division and not _MASTERS_RE.search(division):
            continue

        round_no, phase = _round_and_phase(match_el)
        if round_no is None:
            continue

        players = _players(match_el)
        if not players:
            continue
        p1 = players[0]
        p2 = players[1] if len(players) > 1 else None

        rows.append(MatchRow(
            phase=phase,
            round=round_no,
            table_no=_table_no(match_el),
            p1_name=p1["name"],
            p2_name=(p2["name"] if p2 else None),
            winner_name=_winner(p1, p2),
        ))
    return rows


# -- block / field extraction (adjust selectors here if RK9 changes markup) ---

def _iter_match_blocks(soup: BeautifulSoup):
    # Primary: elements whose class mentions "match" or "pairing".
    blocks = soup.select('[class*="match"], [class*="pairing"]')
    if blocks:
        return blocks
    # Fallback: table rows that contain two player cells.
    return soup.select("tr")


def _players(el: Tag) -> list[dict]:
    out = []
    # Player nodes usually carry a "player" class; each contains a name and may
    # carry a "winner"/"loser" marker class.
    nodes = el.select('[class*="player"]')
    if not nodes:
        # Fallback for plain table rows: take the link cells.
        nodes = el.select("td a") or el.select("td")
    for n in nodes:
        name = n.get_text(" ", strip=True)
        if not name or name.lower() in {"bye", "table"}:
            if name and name.lower() == "bye":
                out.append({"name": None, "winner": False, "el": n})
            continue
        classes = " ".join(n.get("class", []))
        out.append({
            "name": _clean_name(name),
            "winner": "winner" in classes.lower() or _has_win_glyph(n),
            "loser": "loser" in classes.lower(),
            "el": n,
        })
    return out


def _winner(p1: dict, p2: dict | None) -> str | None:
    if p2 is None:
        return p1["name"]   # bye -> p1 advances (not Elo-rated; engine ignores)
    if p1.get("winner") and not p2.get("winner"):
        return p1["name"]
    if p2.get("winner") and not p1.get("winner"):
        return p2["name"]
    if p1.get("loser") and not p2.get("loser"):
        return p2["name"]
    if p2.get("loser") and not p1.get("loser"):
        return p1["name"]
    return None  # unreported or tie


def _has_win_glyph(node: Tag) -> bool:
    # Trophy / check icons RK9 has used to mark winners.
    return bool(node.select_one(
        'i[class*="trophy"], i[class*="check"], svg[class*="trophy"], '
        '.fa-trophy, .fa-check'
    ))


def _table_no(el: Tag) -> int | None:
    node = el.select_one('[class*="table"]')
    text = node.get_text(" ", strip=True) if node else el.get_text(" ", strip=True)
    m = re.search(r"(?:table\s*)?#?\s*(\d{1,4})", text, re.I)
    return int(m.group(1)) if m else None


def _round_and_phase(el: Tag) -> tuple[int | None, str]:
    header = _nearest_header(el, kind="round")
    if not header:
        return None, "swiss"
    phase = "top_cut" if _TOPCUT_RE.search(header) else "swiss"
    m = _ROUND_RE.search(header)
    return (int(m.group(1)) if m else None), phase


def _nearest_header(el: Tag, kind: str) -> str | None:
    """Walk previous siblings/ancestors looking for a round/division header."""
    target = _ROUND_RE if kind == "round" else _MASTERS_RE
    cur = el
    for _ in range(40):
        prev = cur.find_previous(["h1", "h2", "h3", "h4", "h5", "a", "div", "span"])
        if prev is None:
            break
        text = prev.get_text(" ", strip=True)
        if kind == "round" and (_ROUND_RE.search(text) or _TOPCUT_RE.search(text)):
            return text
        if kind == "division" and _MASTERS_RE.search(text):
            return text
        cur = prev
    return None


def _clean_name(name: str) -> str:
    # Drop trailing flags / records RK9 sometimes appends ("(3-1)", country).
    name = re.sub(r"\(\d+-\d+(?:-\d+)?\)", "", name)
    return re.sub(r"\s+", " ", name).strip()
