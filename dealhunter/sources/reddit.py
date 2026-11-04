"""r/dealsforindia via the public Atom feed. Reddit often blocks cloud IPs (429/403): treated as best-effort."""
from __future__ import annotations
import re
from typing import List
from bs4 import BeautifulSoup
from ..models import Deal, parse_price, detect_store

FEEDS = ["https://www.reddit.com/r/dealsforindia/new/.rss"]
_P = r"(?:\u20b9|Rs\.?)\s*([\d][\d,]*(?:\.\d+)?)"


def parse(xml: bytes | str) -> List[Deal]:
    out = []
    for e in BeautifulSoup(xml, "xml").find_all("entry"):
        title = e.find("title").get_text(" ", strip=True)
        page = e.find("link")["href"]
        body = BeautifulSoup(e.find("content").get_text() if e.find("content") else "", "lxml")
        text = body.get_text(" ", strip=True)
        links = [a["href"] for a in body.select("a[href]") if "reddit.com" not in a["href"]]
        blob = f"{title} {text}"
        pct_m = re.search(r"(\d{1,2})\s?%\s*off", blob, re.I)
        old_m = re.search(r"(?:MRP|mrp|was)\D{0,6}([\d][\d,]*)", blob)
        new_m = re.search(r"(?:@|at|for)\s*" + _P, title + " " + text, re.I)
        new = parse_price(new_m.group(1)) if new_m else None
        old = parse_price(old_m.group(1)) if old_m else None
        pct = int(pct_m.group(1)) if pct_m else 0
        if not links or (not pct and not (old and new)):
            continue
        out.append(Deal(title=title, url=links[0], page=page, new_price=new, old_price=old, pct=pct,
                        source="reddit", store=detect_store(links[0], blob), key="").finalize())
    return out


def fetch_all(fetch) -> List[Deal]:
    out = []
    for u in FEEDS:
        r = fetch(u)
        if r.status_code != 200:
            raise RuntimeError(f"reddit HTTP {r.status_code}")
        out += parse(r.content)
    return out
