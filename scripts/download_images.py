"""Download Pokémon official artwork (the "standard art" PNGs).

Given a list of species display names, this resolves each to its PokeAPI form
and saves the official-artwork PNG as ``<slug>.png`` into the images directory,
matching the filenames the site already references.

A small manifest (``_resolved.json``) records which PokeAPI form each file was
downloaded from. A file is re-fetched when it's missing, when ``--force`` is
given, or when its resolved PokeAPI name has *changed* since last time (e.g. we
fixed a form mapping) — so corrections heal automatically without re-downloading
everything every run.

Run standalone:
    python -m scripts.download_images            # uses species in the DB
    python -m scripts.download_images Aegislash "Slowking (Galarian)"
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

# Allow running both as a module and as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vgcelo.pokemon import image_slug, pokeapi_name  # noqa: E402

POKEAPI = "https://pokeapi.co/api/v2/pokemon/{name}"
HEADERS = {"User-Agent": "vgcelo-ladder/1.0 image-fetch"}
MANIFEST = "_resolved.json"


def _artwork_url(api_name: str) -> str | None:
    try:
        resp = requests.get(POKEAPI.format(name=api_name), headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        other = (data.get("sprites") or {}).get("other") or {}
        return (other.get("official-artwork") or {}).get("front_default")
    except requests.RequestException:
        return None


def download_one(species: str, images_dir: Path, *, force: bool = False,
                 manifest: dict | None = None) -> bool:
    images_dir.mkdir(parents=True, exist_ok=True)
    slug = image_slug(species)
    target = images_dir / f"{slug}.png"
    api_name = pokeapi_name(species)

    # Up to date? (file present AND resolved from the same PokeAPI form)
    if (target.exists() and not force
            and manifest is not None and manifest.get(slug) == api_name):
        return False

    url = _artwork_url(api_name)
    # Fallback only for genuinely-unknown forms (not regional ones we map):
    # try the bare base species so we at least show something.
    if not url and "-" in api_name:
        url = _artwork_url(api_name.split("-")[0])
    if not url:
        print(f"  ? no artwork for {species} (tried '{api_name}')")
        return False

    try:
        img = requests.get(url, headers=HEADERS, timeout=30)
        img.raise_for_status()
        target.write_bytes(img.content)
        if manifest is not None:
            manifest[slug] = api_name
        print(f"  + {species} -> {target.name} ({api_name})")
        return True
    except requests.RequestException as exc:
        print(f"  ! failed {species}: {exc}")
        return False


def download_all(species_list, images_dir, *, force: bool = False,
                 delay: float = 0.3) -> int:
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    mpath = images_dir / MANIFEST
    manifest: dict = {}
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}

    count = 0
    for species in sorted(set(species_list)):
        if download_one(species, images_dir, force=force, manifest=manifest):
            count += 1
            time.sleep(delay)  # be kind to PokeAPI

    mpath.write_text(json.dumps(manifest, indent=0, sort_keys=True), encoding="utf-8")
    return count


def _main(argv):
    from vgcelo.config import load_config
    config = load_config()
    if argv:
        species = argv
    else:
        from vgcelo.db import session
        with session(config.db_path) as conn:
            species = [r["species"] for r in conn.execute(
                "SELECT DISTINCT species FROM team_pokemon")]
    n = download_all(species, config.images_dir)
    print(f"Done: {n} images saved to {config.images_dir}")


if __name__ == "__main__":
    _main(sys.argv[1:])
