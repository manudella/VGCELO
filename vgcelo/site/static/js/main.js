// VGC Elo — lightweight client behaviour: instant search + rating sparklines.

(function () {
  "use strict";

  // ---- search -------------------------------------------------------------
  const input = document.getElementById("search-input");
  const results = document.getElementById("search-results");
  let index = null;

  async function ensureIndex() {
    if (index) return index;
    const url = (window.SEARCH_URL || "/search.json");
    const res = await fetch(url);
    index = await res.json();
    return index;
  }

  function render(matches) {
    if (!matches.length) { results.classList.remove("open"); results.innerHTML = ""; return; }
    results.innerHTML = matches.map(function (m) {
      return '<a href="' + m.u + '"><span><span class="kind muted">' + m.t +
        "</span><br>" + m.n + '</span><span class="muted small">' + (m.m || "") + "</span></a>";
    }).join("");
    results.classList.add("open");
  }

  if (input) {
    input.addEventListener("input", async function () {
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) { results.classList.remove("open"); return; }
      const idx = await ensureIndex();
      const matches = idx.filter(function (m) {
        return m.n.toLowerCase().indexOf(q) !== -1;
      }).slice(0, 12);
      render(matches);
    });
    input.addEventListener("focus", ensureIndex);
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".search-box")) results.classList.remove("open");
    });
  }

  // ---- client-side ladder (full field, paginated + filterable) -----------
  const body = document.getElementById("ladder-body");
  if (body && window.LADDER_URL) {
    const base = window.URLBASE || "/";
    const cf = document.getElementById("country-filter");
    const sf = document.getElementById("ladder-search");
    const pager = document.getElementById("ladder-pager");
    const countEl = document.getElementById("ladder-count");
    const PAGE = 100;
    let all = [], view = [], page = 0;

    const cell = (v) => (v === "" || v === null ? "—" : v);
    function flag(cc) {
      if (!cc) return "";
      var fc = cc.toLowerCase() === "uk" ? "gb" : cc.toLowerCase();
      return '<img class="flag" loading="lazy" alt="' + cc + '" src="https://flagcdn.com/24x18/' + fc + '.png">';
    }
    // row: [rank,id,name,cc,elo,gxe,glicko,rd,wins,losses,winrate,events,best,tier]
    function rowHtml(r) {
      const t = r[13];
      return '<tr class="row-' + t + '">' +
        '<td class="rank t-' + t + '">' + r[0] + "</td>" +
        '<td>' + flag(r[3]) + '<a href="' + base + "player/" + r[1] + '.html">' + r[2] + "</a></td>" +
        '<td class="num rating t-' + t + '">' + r[4] + "</td>" +
        '<td class="num">' + cell(r[5]) + "</td>" +
        '<td class="num muted">' + cell(r[6]) + "</td>" +
        '<td class="num muted small">' + (r[7] === "" ? "—" : "±" + r[7]) + "</td>" +
        '<td class="num">' + r[8] + "–" + r[9] + "</td>" +
        '<td class="num">' + r[10] + "%</td>" +
        '<td class="num">' + r[11] + "</td>" +
        '<td class="num">' + (r[12] === "" ? "—" : "#" + r[12]) + "</td></tr>";
    }
    function renderPage() {
      const start = page * PAGE;
      const slice = view.slice(start, start + PAGE);
      body.innerHTML = slice.length ? slice.map(rowHtml).join("")
        : '<tr><td colspan="10" class="muted">No players match.</td></tr>';
      const pages = Math.max(1, Math.ceil(view.length / PAGE));
      countEl.textContent = view.length.toLocaleString() + " players";
      pager.innerHTML =
        '<button id="pg-prev"' + (page <= 0 ? " disabled" : "") + ">‹ Prev</button>" +
        '<span class="muted small">Page ' + (page + 1) + " / " + pages + "</span>" +
        '<button id="pg-next"' + (page >= pages - 1 ? " disabled" : "") + ">Next ›</button>";
      const pv = document.getElementById("pg-prev"), nx = document.getElementById("pg-next");
      if (pv) pv.onclick = () => { if (page > 0) { page--; renderPage(); window.scrollTo(0, 0); } };
      if (nx) nx.onclick = () => { if (page < pages - 1) { page++; renderPage(); window.scrollTo(0, 0); } };
    }
    function applyFilters() {
      const c = cf ? cf.value : "";
      const q = sf ? sf.value.trim().toLowerCase() : "";
      view = all.filter((r) => (!c || r[3] === c) && (!q || r[2].toLowerCase().indexOf(q) !== -1));
      page = 0;
      renderPage();
    }
    fetch(window.LADDER_URL).then((r) => r.json()).then((data) => {
      all = view = data;
      renderPage();
      if (cf) cf.addEventListener("change", applyFilters);
      if (sf) sf.addEventListener("input", applyFilters);
    }).catch(() => { body.innerHTML = '<tr><td colspan="10" class="muted">Failed to load ladder.</td></tr>'; });
  }

  // ---- pokemon usage index (filterable by regulation) --------------------
  const pbody = document.getElementById("pokemon-body");
  if (pbody && window.POKEMON_URL) {
    const base = window.URLBASE || "/";
    const rf = document.getElementById("reg-filter");
    const psf = document.getElementById("pokemon-search");
    const pcount = document.getElementById("pokemon-count");
    let store = null;
    const ph = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><circle cx='20' cy='20' r='18' fill='%23eef0f5'/></svg>";
    // row: [slug, species, image, count, pct, winrate, users]
    function rowHtml(r, i) {
      return '<tr><td class="rank">' + (i + 1) + "</td>" +
        '<td><a class="poke" href="' + base + "pokemon/" + r[0] + '.html">' +
        '<img class="poke-img sm" loading="lazy" alt="" src="' + base + "static/pokemon/" + r[2] +
        '" onerror="this.src=\'' + ph + '\'"><span>' + r[1] + "</span></a></td>" +
        '<td class="num rating">' + r[4] + "%</td>" +
        '<td class="num">' + r[3] + "</td>" +
        '<td class="num">' + (r[5] === "" ? "—" : r[5] + "%") + "</td>" +
        '<td class="num">' + r[6] + "</td></tr>";
    }
    function render() {
      const reg = rf ? rf.value : "all";
      const q = psf ? psf.value.trim().toLowerCase() : "";
      let rows = (store.data[reg] || []);
      if (q) rows = rows.filter((r) => r[1].toLowerCase().indexOf(q) !== -1);
      pbody.innerHTML = rows.length ? rows.map(rowHtml).join("")
        : '<tr><td colspan="6" class="muted">No Pokémon match.</td></tr>';
      pcount.textContent = rows.length + " Pokémon";
    }
    fetch(window.POKEMON_URL).then((r) => r.json()).then((d) => {
      store = d; render();
      if (rf) rf.addEventListener("change", render);
      if (psf) psf.addEventListener("input", render);
    }).catch(() => { pbody.innerHTML = '<tr><td colspan="6" class="muted">Failed to load.</td></tr>'; });
  }

  // ---- tournaments regulation filter -------------------------------------
  const trf = document.getElementById("tourn-reg-filter");
  if (trf) {
    const cards = Array.prototype.slice.call(document.querySelectorAll(".tourn-card"));
    const seasons = Array.prototype.slice.call(document.querySelectorAll(".tourn-season"));
    const tcount = document.getElementById("tourn-count");
    trf.addEventListener("change", function () {
      const reg = trf.value;
      let shown = 0;
      cards.forEach(function (c) {
        const ok = !reg || c.getAttribute("data-reg") === reg;
        c.style.display = ok ? "" : "none";
        if (ok) shown++;
      });
      // hide season sections with no visible cards
      seasons.forEach(function (s) {
        const any = s.querySelectorAll(".tourn-card:not([style*='none'])").length;
        s.style.display = any ? "" : "none";
      });
      tcount.textContent = reg ? (shown + " events") : "";
    });
  }

  // ---- pokemon detail: regulation-filtered stats -------------------------
  const monSel = document.getElementById("mon-reg");
  if (monSel && window.MON) {
    const base = window.URLBASE || "/";
    const chips = document.getElementById("mon-chips");
    const setsEl = document.getElementById("mon-sets");
    const topEl = document.getElementById("mon-top");

    function bars(title, rows, cls) {
      if (!rows || !rows.length) return "";
      var inner = rows.map(function (r) {
        return '<div class="bar ' + (cls || "") + '"><div class="label">' + r.name +
          '</div><div class="track"><div class="fill" style="width:' + r.pct +
          '%"></div></div><div class="pct">' + r.pct + "%</div></div>";
      }).join("");
      return '<div class="card"><h3>' + title + "</h3>" + inner + "</div>";
    }
    function render(reg) {
      var d = window.MON[reg] || window.MON["all"];
      chips.innerHTML =
        '<span class="chip">' + d.pct + "% usage</span>" +
        '<span class="chip">' + d.usage + " teams</span>" +
        (d.win_rate === null ? "" : '<span class="chip">' + d.win_rate + "% win rate</span>") +
        '<span class="chip">' + d.users + " players</span>";
      var b = d.breakdown;
      setsEl.innerHTML =
        bars("Items", b.item, "gold") +
        bars("Tera type (SV)", b.tera_type, "red") +
        bars("Stat Alignment (Champions)", b.nature, "") +
        bars("Abilities", b.ability, "") +
        bars("Most common moves", b.moves, "");
      topEl.innerHTML = d.top_players.length ? d.top_players.map(function (p) {
        return '<tr><td><a href="' + base + "player/" + p.id + '.html">' + p.name +
          "</a></td><td class=\"num rating\">" + p.wins + " W</td>" +
          '<td class="num muted">' + p.wins + "–" + p.losses + "</td></tr>";
      }).join("") : '<tr><td class="muted">No match data.</td></tr>';
    }
    monSel.addEventListener("change", function () { render(monSel.value); });
    render("all");
  }

  // ---- sparklines ---------------------------------------------------------
  document.querySelectorAll("[data-spark]").forEach(function (el) {
    let pts;
    try { pts = JSON.parse(el.getAttribute("data-spark")); } catch (e) { return; }
    if (!pts || pts.length < 2) return;
    const w = el.clientWidth || 600, h = 60, pad = 4;
    const min = Math.min.apply(null, pts), max = Math.max.apply(null, pts);
    const span = (max - min) || 1;
    const step = (w - pad * 2) / (pts.length - 1);
    const coords = pts.map(function (v, i) {
      const x = pad + i * step;
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("class", "spark");
    const area = document.createElementNS(ns, "polygon");
    area.setAttribute("points", pad + "," + h + " " + coords.join(" ") + " " + (pad + (pts.length - 1) * step) + "," + h);
    area.setAttribute("fill", "rgba(76,58,145,.10)");
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", coords.join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#4c3a91");
    line.setAttribute("stroke-width", "2");
    line.setAttribute("stroke-linejoin", "round");
    svg.appendChild(area); svg.appendChild(line);
    el.appendChild(svg);
  });
})();
