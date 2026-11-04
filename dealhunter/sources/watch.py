"""'Watch my link': read the current price of an Amazon / Flipkart product page and compare with the target."""
from __future__ import annotations
import json, re
from typing import Optional
from bs4 import BeautifulSoup
from ..models import parse_price


def price_from_html(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "lxml")
    # 1) schema.org JSON-LD (Flipkart + many shops)
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]):
            offers = node.get("offers") if isinstance(node, dict) else None
            for o in (offers if isinstance(offers, list) else [offers] if offers else []):
                p = parse_price(str(o.get("price", ""))) if isinstance(o, dict) else None
                if p:
                    return p
    # 2) Amazon price blocks
    for sel in ("#corePrice_feature_div .a-price .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
                ".priceToPay .a-offscreen", "span.a-price .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice"):
        el = soup.select_one(sel)
        if el and parse_price(el.get_text()):
            return parse_price(el.get_text())
    # 3) og/product meta
    m = soup.select_one('meta[property="product:price:amount"], meta[itemprop="price"]')
    if m and m.get("content"):
        return parse_price(m["content"])
    return None


def title_from_html(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    el = s.select_one("#productTitle")
    if el:
        return el.get_text(" ", strip=True)
    og = s.select_one('meta[property="og:title"]')
    return (og["content"] if og and og.get("content") else (s.title.get_text(strip=True) if s.title else ""))[:200]
