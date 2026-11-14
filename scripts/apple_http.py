"""Shared HTTP helpers for talking to Apple's documentation API politely.

- AdaptiveRateLimiter: serialized limiter that halves its rate when the server
  pushes back (429/5xx) and slowly creeps back up after sustained success (AIMD).
- fetch(): one request with capped exponential-backoff retries.
- normalize_etag(): compare/send ETags regardless of W/ prefix or compression suffix.
"""

import asyncio
import re
import time
from typing import Optional, Tuple

import aiohttp

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 6          # backoff waits: 5, 10, 20, 40, 80, 120 s
BACKOFF_BASE = 5.0
BACKOFF_CAP = 120.0


class AdaptiveRateLimiter:
    """Rate limiter shared by all concurrent tasks in a script.

    Calls to wait() are serialized, so the rate is a true upper bound. The rate
    starts at `rate`, is halved (down to `min_rate`) when throttle() is called,
    and grows by `step` after every `increase_every` consecutive successes (up to
    `max_rate`).
    """

    def __init__(self, rate: float = 10.0, min_rate: float = 1.0, max_rate: float = 15.0,
                 increase_every: int = 200, step: float = 0.5):
        self.rate = rate
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.increase_every = increase_every
        self.step = step
        self.last_request = 0.0
        self._successes = 0
        self._last_throttle = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            delay = 1.0 / self.rate
            since = time.monotonic() - self.last_request
            if since < delay:
                await asyncio.sleep(delay - since)
            self.last_request = time.monotonic()

    def success(self):
        self._successes += 1
        if self._successes >= self.increase_every and self.rate < self.max_rate:
            self.rate = min(self.max_rate, self.rate + self.step)
            self._successes = 0

    def throttle(self):
        self._successes = 0
        now = time.monotonic()
        # A whole in-flight batch failing together should halve the rate once, not ten times
        if now - self._last_throttle > 5:
            self.rate = max(self.min_rate, self.rate / 2)
            self._last_throttle = now
            print(f"\nServer pushed back; slowing to {self.rate:.1f} req/s")


def normalize_etag(etag: str) -> str:
    """Strip weak prefix, quotes and compression suffixes (-df, -gzip, -br).

    Apple returns the same entity tag as W/"x", "x" or "x-df" depending on the
    request's Accept-Encoding; If-None-Match only matches the bare "x" form.
    """
    etag = (etag or '').strip()
    if etag.startswith('W/'):
        etag = etag[2:]
    etag = etag.strip('"')
    return re.sub(r'-(df|gzip|br)$', '', etag)


async def fetch(session: aiohttp.ClientSession, limiter: AdaptiveRateLimiter, url: str,
                user_agent: str, method: str = 'GET',
                etag: Optional[str] = None) -> Optional[Tuple[int, Optional[str], "CIMultiDict"]]:
    """Make one request, retrying 429/5xx/network errors with capped exponential backoff.

    Returns (status, body_text, headers); body_text is only set for a 200 GET and
    headers is a case-insensitive CIMultiDict.
    Returns None if every attempt raised an exception.
    """
    headers = {'User-Agent': user_agent}
    if etag:
        headers['If-None-Match'] = f'"{normalize_etag(etag)}"'

    for attempt in range(MAX_RETRIES + 1):
        await limiter.wait()
        delay = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** attempt)
        try:
            async with session.request(method, url, headers=headers) as response:
                status = response.status
                if status in RETRY_STATUSES:
                    limiter.throttle()
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(delay)
                        continue
                    return status, None, response.headers.copy()
                limiter.success()
                text = await response.text() if (status == 200 and method == 'GET') else None
                return status, text, response.headers.copy()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                continue
            print(f"\nGiving up on {url}: {e!r}")
            return None
    return None
