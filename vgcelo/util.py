"""Small shared utilities."""
from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """ASCII, lowercase, hyphen-separated slug suitable for ids and URLs.

    "Wolfe Glick" -> "wolfe-glick"; "Flutter Mane" -> "flutter-mane".
    """
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def player_id(name: str) -> str:
    """Stable id for a player. RK9 sometimes abbreviates; we key on full name."""
    return slugify(name)


def pct(part: float, whole: float) -> float:
    return (100.0 * part / whole) if whole else 0.0
