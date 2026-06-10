"""Low-level RK9 HTTP client.

RK9 has no public API, so we scrape the server-rendered HTML. This client is
deliberately *polite*:

* every response is cached on disk (``data/cache``) keyed by URL, so re-runs and
  development never re-hit RK9;
* requests are spaced out by ``scrape.request_delay_seconds``;
* failed requests retry with backoff;
* a descriptive User-Agent identifies the bot.

URL shapes (verified shapes as of 2024–2026; adjust here if RK9 changes them):

    Listing of events ............ {host}/tournaments
    Tournament landing ........... {host}/tournament/{id}
    Pairings (all rounds) ........ {host}/pairings/{id}
    Roster / standings ........... {host}/roster/{id}
    Public team lists ............ {host}/teamlist/{id}

Because RK9's markup evolves, the *parsers* (in pairings.py / teamlists.py /
tournaments.py) are kept separate from this transport layer and are heavily
commented at the points most likely to need adjustment.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests

from ..config import Config


class RK9Client:
    def __init__(self, config: Config):
        s = config.scrape
        self.host: str = s["base_host"].rstrip("/")
        self.delay: float = float(s["request_delay_seconds"])
        self.max_retries: int = int(s["max_retries"])
        self.cache_dir: Path = config.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": s["user_agent"]})
        self._last_request = 0.0

    # -- url builders ---------------------------------------------------------
    def tournaments_url(self) -> str:
        return f"{self.host}/tournaments"

    def tournament_url(self, tid: str) -> str:
        return f"{self.host}/tournament/{tid}"

    def pairings_url(self, tid: str) -> str:
        return f"{self.host}/pairings/{tid}"

    def roster_url(self, tid: str) -> str:
        return f"{self.host}/roster/{tid}"

    def teamlist_url(self, tid: str) -> str:
        return f"{self.host}/teamlist/{tid}"

    # -- fetching -------------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.html"

    def get(self, url: str, *, use_cache: bool = True) -> str:
        cache = self._cache_path(url)
        if use_cache and cache.exists():
            return cache.read_text(encoding="utf-8", errors="ignore")

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                cache.write_text(resp.text, encoding="utf-8")
                return resp.text
            except requests.RequestException as exc:  # pragma: no cover - network
                last_err = exc
                time.sleep(self.delay * attempt)
        raise RuntimeError(f"Failed to fetch {url}: {last_err}")

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.time()
