"""DesiDime deal cards (public HTML). Gives title, store, price, MRP, % and a buy link."""
from __future__ import annotations
import re
from typing import List
from bs4 import BeautifulSoup
from ..models import Deal, parse_price, detect_store

BASE = "https://www.desidime.com"
PAGES = ["/deals?order_by=new", "/deals?order_by=hot"]


def parse(html: str) -> List[Deal]:
    out: List[Deal] = []
    for card in BeautifulSoup(html, "lxml").select(".deal-card"):
        text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
        m = re.search(r"\u20b9\s*([\d,\.]+)\s*\u20b9\s*([\d,\.]+)\s*\((\d+)%\)", text)
        title_el = card.select_one("a[href^='/deals/']")
        title = ""
        for a in card.select("a[href^='/deals/']"):
            t = a.get_text(" ", strip=True)
            if len(t) > len(title) and "comment" not in a.get("href", ""):
                title = t
        if not title:
            img = card.select_one("img[alt]:not([alt='hotness-icon'])")
            title = img["alt"] if img else ""
        title = re.sub(r"\s*\.\.\.\s*more$", "…", title)
        permalink = card.get("data-permalink") or (title_el["href"].lstrip("/") if title_el else "")
        if not permalink or not title:
            continue
        visit = next((a["href"] for a in card.select("a[href]") if "visit.desidime.com" in a["href"]), "")
        page = f"{BASE}/{permalink}"
        new = old = None
        pct = 0
        if m:
            new, old, pct = parse_price(m.group(1)), parse_price(m.group(2)), int(m.group(3))
        else:  # price only in the title, e.g. "... @ \u20b996.67"
            m2 = re.search(r"(\d{1,2})% off", title, re.I)
            pct = int(m2.group(1)) if m2 else 0
            new = parse_price(re.search(r"(?:@|at|for)\s*(?:\u20b9|Rs\.?)\s*[\d,\.]+", title).group(0)) if re.search(r"(?:@|at|for)\s*(?:\u20b9|Rs\.?)\s*[\d,\.]+", title) else None
        store = card.get("data-gtm-store") or detect_store(visit, title)
        img = card.select_one("img[src*='attachments']")
        out.append(Deal(title=title, url=visit or page, page=page, store=(detect_store("", store) if detect_store("", store) != "Other" else (store or "Other")),
                        new_price=new, old_price=old, pct=pct, source="desidime",
                        image=img["src"] if img else "", key="dd:" + permalink.split("/")[-1].rsplit("-", 1)[-1]).finalize())
    return out


def fetch_all(fetch) -> List[Deal]:
    deals: List[Deal] = []
    for p in PAGES:
        r = fetch(BASE + p)
        if r.status_code == 200:
            deals += parse(r.text)
        else:
            raise RuntimeError(f"desidime {p} -> HTTP {r.status_code}")
    return deals
