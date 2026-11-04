"""Flipkart search sorted by discount (public HTML; class names are obfuscated so we parse by structure)."""
from __future__ import annotations
import re
from typing import Dict, List
from urllib.parse import quote_plus, parse_qs, urlparse
from bs4 import BeautifulSoup
from ..models import Deal, parse_price

QUERIES = ["earphones", "headphones", "smart watch", "power bank", "charger", "mobile cover", "bluetooth speaker",
           "t shirt", "shoes", "sandals", "backpack", "wallet", "kitchen", "water bottle", "led light", "trimmer",
           "toys", "tools", "keyboard", "mouse", "cable", "watch men", "kurti", "saree", "jacket", "bedsheet",
           "sunglasses", "fan", "mixer grinder", "tv", "laptop", "tablet", "shirt men", "jeans", "socks", "mattress"]


def search_url(q: str) -> str:
    return f"https://www.flipkart.com/search?q={quote_plus(q)}&sort=discount_desc"


def parse(html: str) -> List[Deal]:
    soup = BeautifulSoup(html, "lxml")
    cards: Dict[str, dict] = {}
    for a in soup.select('a[href*="/p/"]'):
        href = a["href"]
        pid = parse_qs(urlparse(href).query).get("pid", [""])[0]
        if not pid:
            continue
        c = cards.setdefault(pid, {"href": href.split("?")[0], "title": "", "nums": None})
        if a.get("title") and len(a["title"]) > len(c["title"]):
            c["title"] = a["title"]
        txt = a.get_text("|", strip=True)
        m = re.search(r"\u20b9\s*([\d,]+)\|?\u20b9?\|?([\d,]+)\|(\d{1,2})% off", txt)
        if m:
            c["nums"] = (parse_price(m.group(1)), parse_price(m.group(2)), int(m.group(3)))
    out = []
    for pid, c in cards.items():
        if not c["nums"]:
            continue
        new, old, pct = c["nums"]
        title = c["title"] or c["href"].strip("/").split("/")[0].replace("-", " ").title()
        out.append(Deal(title=title, url=f"https://www.flipkart.com{c['href']}?pid={pid}", store="Flipkart",
                        new_price=new, old_price=old, pct=pct, source="flipkart", key=f"fk:{pid}").finalize())
    return out


def fetch_all(fetch, queries: List[str]) -> List[Deal]:
    out, fails = [], 0
    for q in queries:
        r = fetch(search_url(q))
        if r.status_code != 200:
            fails += 1
            continue
        out += parse(r.text)
    if fails == len(queries) and queries:
        raise RuntimeError("flipkart blocked/failed for all queries")
    return out
