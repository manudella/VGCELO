"""Configuration loading.

A single :class:`Config` object is threaded through the whole pipeline so that
tuning the system (season window, Elo constants, tier colours) only ever means
editing ``config.yaml`` — never the code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Repo root = parent of the ``vgcelo`` package directory.
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = ROOT

    # -- convenience accessors -------------------------------------------------
    @property
    def site(self) -> dict[str, Any]:
        return self.raw["site"]

    @property
    def data(self) -> dict[str, Any]:
        return self.raw["data"]

    @property
    def scrape(self) -> dict[str, Any]:
        return self.raw["scrape"]

    @property
    def elo(self) -> dict[str, Any]:
        return self.raw["elo"]

    @property
    def tiers(self) -> list[dict[str, Any]]:
        return self.raw["tiers"]

    @property
    def regulations(self) -> list[dict[str, Any]]:
        return self.raw.get("regulations", [])

    def regulation_for(self, date_iso: str) -> str | None:
        """Regulation name whose window contains the given ISO date."""
        if not date_iso:
            return None
        for reg in self.regulations:
            start = reg.get("start")
            end = reg.get("end")
            if start and date_iso < start:
                continue
            if end and date_iso > end:
                continue
            return reg["name"]
        return None

    # -- resolved paths --------------------------------------------------------
    def path(self, key: str) -> Path:
        """Resolve a path from the ``data`` section relative to the repo root."""
        p = Path(self.data[key])
        return p if p.is_absolute() else self.root / p

    @property
    def db_path(self) -> Path:
        return self.path("db_path")

    @property
    def cache_dir(self) -> Path:
        return self.path("cache_dir")

    @property
    def output_dir(self) -> Path:
        return self.path("output_dir")

    @property
    def images_dir(self) -> Path:
        return self.path("images_dir")

    def tier_for_rank(self, rank: int | None) -> dict[str, Any]:
        """Return the tier dict for a ladder rank (1 = best).

        Tiers are ordered best -> worst in config; the first whose ``max_rank``
        the rank satisfies wins. A ``null`` max_rank is the catch-all bottom.
        Unranked players (no rated games) fall into the bottom tier.
        """
        if rank is None:
            return self.tiers[-1]
        for tier in self.tiers:
            mr = tier.get("max_rank")
            if mr is None or rank <= mr:
                return tier
        return self.tiers[-1]


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Config(raw=raw)
