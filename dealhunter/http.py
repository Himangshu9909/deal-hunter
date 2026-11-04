"""Polite HTTP helper: browser-like headers, retries, per-source failure isolation."""
from __future__ import annotations
import random, time
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def fetch(url: str, retries: int = 2, timeout: int = 25, **kw) -> requests.Response:
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, **kw)
            if r.status_code in (429, 503) and i < retries:
                time.sleep(2 + 3 * i + random.random())
                continue
            return r
        except requests.RequestException as e:   # network hiccup
            last = e
            time.sleep(1 + i)
    raise last or RuntimeError("fetch failed")
