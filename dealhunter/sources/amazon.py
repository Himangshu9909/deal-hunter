"""Amazon India search pages filtered to >=N% off. Direct fetch works from some IPs; may be CAPTCHA'd from others."""
from __future__ import annotations
import re
from typing import List
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from ..models import Deal, parse_price

QUERIES = ["earphones", "headphones", "smart watch", "power bank", "charger", "mobile cover", "bluetooth speaker",
           "t shirt", "shoes", "sandals", "backpack", "wallet", "kitchen", "water bottle", "led light", "trimmer",
           "toys", "tools", "keyboard", "mouse", "cable", "watch men", "kurti", "saree", "perfume", "jacket",
           "bedsheet", "sunglasses", "fan", "mixer grinder", "tv", "laptop", "tablet", "shirt men", "jeans",
           "socks", "mattress", "books", "dumbbell", "yoga mat"]


def search_url(q: str, min_pct: int = 70) -> str:
    return f"https://www.amazon.in/s?k={quote_plus(q)}&rh=p_n_pct-off-with-tax%3A{min_pct}-&s=price-asc-rank"


def blocked(html: str) -> bool:
    return "api-services-support@amazon.com" in html or "Enter the characters you see below" in html


def parse(html: str) -> List[Deal]:
    out = []
    for c in BeautifulSoup(html, "lxml").select('div[data-component-type="s-search-result"]'):
        asin = c.get("data-asin")
        h2 = c.select_one("h2")
        cur = c.select_one(".a-price:not(.a-text-price) .a-offscreen")
        lst = c.select_one(".a-text-price .a-offscreen")
        if not (asin and h2 and cur):
            continue
        new, old = parse_price(cur.get_text()), parse_price(lst.get_text()) if lst else None
        m = re.search(r"(\d{1,2})% off", c.get_text(" "))
        img = c.select_one("img.s-image")
        title = h2.get_text(" ", strip=True)
        if img and img.get("alt") and len(img["alt"]) > len(title):   # some layouts only put the brand in <h2>
            title = img["alt"]
        out.append(Deal(title=title, url=f"https://www.amazon.in/dp/{asin}", store="Amazon",
                        new_price=new, old_price=old, pct=int(m.group(1)) if m else 0, source="amazon",
                        image=img["src"] if img else "", key=f"amz:{asin}").finalize())
    return out


def fetch_all(fetch, queries: List[str], min_pct: int = 70) -> List[Deal]:
    out, fails = [], 0
    for q in queries:
        r = fetch(search_url(q, min_pct))
        if r.status_code != 200 or blocked(r.text):
            fails += 1
            continue
        out += parse(r.text)
    if fails == len(queries) and queries:
        raise RuntimeError("amazon blocked/failed for all queries")
    return out
