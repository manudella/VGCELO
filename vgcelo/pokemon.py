"""Pokémon name handling and official-artwork resolution.

Two separate concerns live here:

1. **Display / slug** — turning raw team-list text ("Urshifu-Rapid-Strike",
   "Flutter Mane") into a stable display name and a URL/file slug used across
   the site and to name the cached artwork PNG.

2. **PokeAPI resolution** — only the image downloader needs this: mapping a slug
   to the exact PokeAPI ``/pokemon/{name}`` form so we can fetch the correct
   official artwork for alternate forms (Ogerpon masks, Calyrex riders, etc.).
"""
from __future__ import annotations

import re

from .util import slugify

# Slugs whose PokeAPI form name differs from a naive slugify. Only the awkward
# VGC-relevant forms need an entry; everything else resolves slug == api name.
FORM_MAP: dict[str, str] = {
    "urshifu": "urshifu-single-strike",
    "urshifu-single-strike": "urshifu-single-strike",
    "urshifu-rapid-strike": "urshifu-rapid-strike",
    "calyrex-shadow": "calyrex-shadow-rider",
    "calyrex-ice": "calyrex-ice-rider",
    "ogerpon": "ogerpon",
    "ogerpon-wellspring": "ogerpon-wellspring-mask",
    "ogerpon-hearthflame": "ogerpon-hearthflame-mask",
    "ogerpon-cornerstone": "ogerpon-cornerstone-mask",
    "zacian": "zacian-crowned",
    "zamazenta": "zamazenta-crowned",
    "tornadus": "tornadus-incarnate",
    "tornadus-therian": "tornadus-therian",
    "landorus": "landorus-incarnate",
    "landorus-therian": "landorus-therian",
    "thundurus": "thundurus-incarnate",
    "thundurus-therian": "thundurus-therian",
    "indeedee": "indeedee-male",
    "indeedee-f": "indeedee-female",
    "indeedee-female": "indeedee-female",
    "tauros-paldea-combat": "tauros-paldea-combat-breed",
    "tauros-paldea-blaze": "tauros-paldea-blaze-breed",
    "tauros-paldea-aqua": "tauros-paldea-aqua-breed",
    # Tauros Paldean breeds as the data actually spells them ("paldean").
    "tauros-paldean-combat": "tauros-paldea-combat-breed",
    "tauros-paldean-blaze": "tauros-paldea-blaze-breed",
    "tauros-paldean-aqua": "tauros-paldea-aqua-breed",
    "tatsugiri": "tatsugiri-curly",
    "maushold": "maushold-family-of-four",
    "palafin": "palafin-zero",
    "rotom-wash": "rotom-wash",
    "rotom-heat": "rotom-heat",
    "lycanroc": "lycanroc-midday",
    "basculegion": "basculegion-male",
    "toxtricity": "toxtricity-amped",
    # Pokémon whose bare species name 404s on PokeAPI (their default "form" has
    # its own resource name) — map to the standard battle form's artwork.
    "aegislash": "aegislash-shield",
    "mimikyu": "mimikyu-disguised",
    "eiscue": "eiscue-ice",
    "morpeko": "morpeko-full-belly",
    "darmanitan": "darmanitan-standard",
    "meowstic": "meowstic-male",
    "wishiwashi": "wishiwashi-solo",
    "oricorio": "oricorio-baile",
    "squawkabilly": "squawkabilly-green-plumage",
}

# Regional-form adjectives (as the data spells them) -> PokeAPI's region suffix.
_REGION_SUFFIX = {
    "galarian": "galar",
    "hisuian": "hisui",
    "alolan": "alola",
    "paldean": "paldea",
}

# Common raw -> display fixups for separators RK9/Showdown use.
_SEP_FIXUPS = {
    "-rapid-strike": " (Rapid Strike)",
    "-single-strike": " (Single Strike)",
    "-therian": " (Therian)",
    "-incarnate": " (Incarnate)",
    "-hearthflame": " (Hearthflame)",
    "-wellspring": " (Wellspring)",
    "-cornerstone": " (Cornerstone)",
    "-shadow": " (Shadow)",
    "-ice": " (Ice)",
}


# Form descriptors that are purely cosmetic in VGC — merge them into the base
# species so usage stats don't fragment (e.g. all Maushold count as Maushold).
_COSMETIC_FORM_HINTS = (
    "eternal flower", "unremarkable", "masterpiece", "family of", "segment",
    "plumage", "curly", "droopy", "stretchy",
)
# Words to strip from a *significant* form to match our display convention,
# e.g. "Hearthflame Mask" -> "Hearthflame", "Rapid Strike Style" -> "Rapid Strike".
_FORM_NOISE = re.compile(r"\b(mask|style|forme|form|rider|breed)\b", re.I)


def normalize_pokedata_species(raw: str) -> str:
    """Normalise pokedata's ``Name [Form]`` species strings.

    Cosmetic forms collapse to the base species; competitively-distinct forms
    become ``Base (Form)`` matching the rest of the site's convention
    (e.g. ``Ogerpon (Hearthflame)``, ``Urshifu (Rapid Strike)``).
    """
    if not raw:
        return "Unknown"
    m = re.match(r"^(.*?)\s*\[(.*?)\]\s*$", raw.strip())
    if not m:
        return normalize_species(raw)
    base, form = m.group(1).strip(), m.group(2).strip()
    fl = form.lower()
    if base.lower() == "palafin":          # Zero/Hero are the same competitively
        return "Palafin"
    if any(h in fl for h in _COSMETIC_FORM_HINTS):
        return _titleize(base)
    short = _FORM_NOISE.sub("", form).strip()
    # Drop a repeated base word, e.g. form "Wash Rotom" with base "Rotom".
    base_words = {w.lower() for w in base.split()}
    short = " ".join(w for w in short.split() if w.lower() not in base_words)
    short = " ".join(w.capitalize() for w in re.sub(r"\s+", " ", short).split())
    return f"{_titleize(base)} ({short})" if short else _titleize(base)


def normalize_species(raw: str) -> str:
    """Best-effort canonical display name from raw team-list text."""
    if not raw:
        return "Unknown"
    name = raw.strip()
    # Showdown/RK9 often hyphenate forms: "Urshifu-Rapid-Strike".
    low = name.lower()
    for suffix, repl in _SEP_FIXUPS.items():
        if low.endswith(suffix):
            base = name[: -len(suffix)]
            return _titleize(base) + repl
    return _titleize(name)


def _titleize(name: str) -> str:
    parts = name.replace("_", " ").replace("-", " ").split()
    small = {"of", "the"}
    out = []
    for i, p in enumerate(parts):
        out.append(p.lower() if (p.lower() in small and i > 0) else p.capitalize())
    return " ".join(out)


def image_slug(display: str) -> str:
    """Slug used for the artwork filename and any per-form URL bits."""
    return slugify(display)


def image_filename(display: str) -> str:
    return f"{image_slug(display)}.png"


def pokeapi_name(display: str) -> str:
    """Resolve the PokeAPI ``/pokemon/{name}`` form for a display name."""
    slug = image_slug(display)
    if slug in FORM_MAP:
        return FORM_MAP[slug]
    # Regional forms: the data says "-galarian/-hisuian/-alolan/-paldean" but
    # PokeAPI uses "-galar/-hisui/-alola/-paldea". Without this they 404 and the
    # downloader falls back to the *base* species' (wrong) artwork.
    for adj, suffix in _REGION_SUFFIX.items():
        if adj in slug:
            return slug.replace(adj, suffix)
    return slug
