"""Polite HTTP fetcher for the SCU bulletin.

- Checks robots.txt before every request and refuses disallowed URLs.
- Sends a User-Agent that names this program.
- Waits MIN_INTERVAL seconds between any two requests to scu.edu, and never
  requests the same URL more than PER_URL_LIMIT times in a 60-second window.
- Caches pages on disk so a page is normally downloaded once per CACHE_TTL.
"""

import hashlib
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit

import requests

USER_AGENT = (
    "SCU-Prereq-Map/1.0 (student course project for OMIS 107; "
    "builds course prerequisite diagrams from the public bulletin)"
)
MIN_INTERVAL = 6.0          # seconds between any two requests to the site
PER_URL_LIMIT = 3           # max requests for one URL per minute
CACHE_TTL = 7 * 24 * 3600   # the bulletin changes once a year; a week is plenty
CACHE_DIR = Path(__file__).parent / "cache" / "pages"


class RobotsDisallowed(Exception):
    pass


class PoliteFetcher:
    def __init__(self, cache_dir=CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._url_history = defaultdict(deque)
        self._robots = {}

    # ---- robots.txt -------------------------------------------------------
    def _robots_for(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = robotparser.RobotFileParser(origin + "/robots.txt")
            resp = self._raw_get(origin + "/robots.txt")
            if resp.status_code in (401, 403):
                rp.disallow_all = True
            elif resp.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(resp.text.splitlines())
            self._robots[origin] = rp
        return rp

    def allowed(self, url):
        return self._robots_for(url).can_fetch(USER_AGENT, url)

    # ---- rate limiting ----------------------------------------------------
    def _raw_get(self, url):
        """GET with the global and per-URL rate limits applied."""
        with self._lock:
            now = time.monotonic()
            hist = self._url_history[url]
            while hist and now - hist[0] > 60:
                hist.popleft()
            if len(hist) >= PER_URL_LIMIT:
                time.sleep(60 - (now - hist[0]) + 0.1)
            wait = self._last_request + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            hist.append(self._last_request)
        resp = self.session.get(url, timeout=30)
        return resp

    # ---- cached fetch -----------------------------------------------------
    def _cache_path(self, url):
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def cached(self, url, max_age=CACHE_TTL):
        path = self._cache_path(url)
        if path.exists() and time.time() - path.stat().st_mtime < max_age:
            return path.read_text(encoding="utf-8")
        return None

    def get(self, url, max_age=CACHE_TTL):
        text = self.cached(url, max_age)
        if text is not None:
            return text
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        resp = self._raw_get(url)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        text = resp.text
        self._cache_path(url).write_text(text, encoding="utf-8")
        return text
