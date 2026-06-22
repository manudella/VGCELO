"""Render the statistics bundle into a self-contained static website.

Output layout (under ``dist/``)::

    index.html              leaderboard + site-wide records
    players.html            full searchable player ladder
    player/<id>.html        one profile per player
    pokemon.html            usage ranking
    pokemon/<slug>.html     one page per Pokémon (sets, win-rate, users)
    tournaments.html        all majors
    tournament/<id>.html    standings + pairings + event usage
    methodology.html        how Elo & every metric is computed
    search.json             client-side search index
    static/...              css, js, and Pokémon official artwork

Everything is plain HTML/CSS so it can be hosted free on GitHub Pages / Netlify
and rebuilt unattended by CI.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"


def build_site(stats: dict, config: Config) -> Path:
    out = config.output_dir
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    base_url = config.site.get("base_url", "").rstrip("/")

    def url(path: str) -> str:
        return f"{base_url}/{path.lstrip('/')}" if base_url else "/" + path.lstrip("/")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Changes every build, so browsers fetch fresh CSS/JS/JSON after each deploy
    # instead of serving stale cached copies.
    asset_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    env.globals.update(
        site=config.site,
        url=url,
        generated_at=stats["generated_at"],
        seasons=stats["seasons"],
        tier_meta={t["key"]: t for t in config.tiers},
        regulations=stats["regulations"],
        asset_version=asset_version,
    )
    env.filters["sign"] = lambda v: (f"+{v}" if v and v > 0 else str(v))

    # Collapse inter-tag whitespace. Safe here: no <pre>/<textarea> templates.
    _ws = re.compile(r">\s+<")

    def render(template: str, dest: str, **ctx) -> None:
        html = env.get_template(template).render(**ctx)
        html = _ws.sub("><", html)
        target = out / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")

    # -- pages ----------------------------------------------------------------
    render("index.html", "index.html",
           leaderboard=stats["leaderboard"][:100],
           records=stats["records"],
           pokemon_top=stats["pokemon_list"][:12],
           tournaments=stats["tournament_list"][:8])

    render("players.html", "players.html", leaderboard=stats["leaderboard"],
           countries=stats["countries"])

    for p in stats["players"].values():
        render("player.html", f"player/{p['id']}.html", player=p, page_toc=True,
               history_json=json.dumps([h["rating"] for h in p["rating_history"]]))

    render("pokemon_index.html", "pokemon.html", pokemon=stats["pokemon_list"])
    for pk in stats["pokemon"].values():
        render("pokemon.html", f"pokemon/{pk['slug']}.html", mon=pk, page_toc=True)

    render("tournament_index.html", "tournaments.html",
           tournaments=stats["tournament_list"])
    for t in stats["tournaments"].values():
        render("tournament.html", f"tournament/{t['id']}.html", t=t, page_toc=True)

    render("methodology.html", "methodology.html", config=config, page_toc=True)

    # -- search index ---------------------------------------------------------
    search = [
        {"t": "player", "n": p["name"], "u": url(f"player/{p['id']}.html"),
         "m": f"#{p['rank']} · {p['current_rating']}"}
        for p in stats["leaderboard"]
    ] + [
        {"t": "pokemon", "n": pk["species"], "u": url(f"pokemon/{pk['slug']}.html"),
         "m": f"{pk['usage_pct']}% usage"}
        for pk in stats["pokemon_list"]
    ] + [
        {"t": "tournament", "n": t["name"], "u": url(f"tournament/{t['id']}.html"),
         "m": t["start_date"]}
        for t in stats["tournament_list"]
    ]
    (out / "search.json").write_text(json.dumps(search), encoding="utf-8")

    # -- ladder index (rendered client-side so the full field stays fast) ------
    # Compact rows; column order mirrors players.html / main.js renderLadder().
    ladder = [
        [p["rank"], p["id"], p["name"], p["country"] or "", p["current_rating"],
         p["gxe"] if p["gxe"] is not None else "",
         p["glicko"] if p["glicko"] is not None else "",
         p["rd"] if p["rd"] is not None else "",
         p["wins"], p["losses"], p["win_rate"], p["tournaments_played"],
         p["best_placement"] or "", p["tier"]["key"], p["peak_rating"]]
        for p in stats["leaderboard"]
    ]
    (out / "ladder.json").write_text(json.dumps(ladder), encoding="utf-8")

    # -- pokemon usage index, filterable by regulation (client-side) ----------
    # row columns: [slug, species, image, count, usage_pct, win_rate, users]
    def _prow(p, count, pct, wr, users):
        return [p["slug"], p["species"], p["image"], count, pct,
                (wr if wr is not None else ""), users]

    mons = stats["pokemon"]
    pdata = {}
    for reg in ["all"] + stats["regulations"]:
        rows = []
        for p in mons.values():
            panel = p["by_reg"].get(reg)
            if panel:
                rows.append(_prow(p, panel["usage"], panel["pct"],
                                  panel["win_rate"], panel["users"]))
        pdata[reg] = sorted(rows, key=lambda r: -r[3])
    (out / "pokemon.json").write_text(
        json.dumps({"regs": ["all"] + stats["regulations"], "data": pdata}),
        encoding="utf-8")

    # -- static assets --------------------------------------------------------
    shutil.copytree(STATIC, out / "static", dirs_exist_ok=True)

    return out
