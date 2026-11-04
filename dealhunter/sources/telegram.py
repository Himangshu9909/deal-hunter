"""Public Telegram deal channels via the web preview (t.me/s/<channel>) - no login, no API key."""
from __future__ import annotations
import re, time
from datetime import datetime
from typing import List
from bs4 import BeautifulSoup
from ..models import Deal, parse_price, detect_store

CHANNELS = ["desidimeHot", "desidime", "TodayPriceDrop", "dealbee", "idoffers"]
OPTIONAL = ["flipshope", "lootdealsapp"]   # post mostly price-only (no % off) -> rarely usable
_SKIP_HOSTS = ("t.me", "hcti.io", "telegram.me", "telegram.org")
_PRICE = r"(?:\u20b9|Rs\.?|INR)\s*([\d][\d,]*(?:\.\d+)?)"


def _clean_lines(node) -> List[str]:
    for br in node.select("br"):
        br.replace_with("\n")
    lines = [re.sub(r"\s+", " ", l).strip() for l in node.get_text("\n").split("\n")]
    return [l for l in lines if l]


def parse_message(text_lines: List[str], links: List[str], post: str, channel: str, image: str = "") -> Deal | None:
    blob = " \n".join(text_lines)
    # the product's own discount ("| 84% OFF", "/ 39% off", "Discount:53% Off") beats a headline "Upto 85% off"
    pct_m = (re.search(r"(?:Discount\s*:?\s*|[|/]\s*)(\d{1,2})\s?%\s*off", blob, re.I)
             or re.search(r"(\d{1,2})\s?%\s*off", blob, re.I))
    old_m = re.search(r"(?:MRP|M\.R\.P|mrp|was|original|Price Showing|Market Price[^\d\u20b9R]*)[^\d\u20b9R]{0,12}" + _PRICE, blob)
    if not old_m:
        old_m = re.search(r"(?:MRP|mrp)\D{0,6}([\d][\d,]*)", blob)
    new = None
    dp = re.search(r"Deal Price\s*:?\s*(?:\u20b9|Rs\.?)?\s*([\d][\d,]*(?:\.\d+)?)", blob, re.I)
    t_m = dp or re.search(r"(?:@|at|for|just|only|now)\s*" + _PRICE, blob, re.I) or re.search(r"(?:@|at|for)\s*([\d][\d,]*)\s*/-", blob) or re.search(r"\bat\s+([\d][\d,]*)\b(?!\s*%)", blob)
    if t_m:
        new = parse_price(t_m.group(1))
    else:
        # layout like "\ud83d\udcb0 \n \u20b912,249 \n / 39% off"
        p_m = re.search(_PRICE, blob)
        if p_m:
            new = parse_price(p_m.group(1))
    if new is None:  # "... - Rs. 68" tail form used by the DesiDime channel
        t2 = re.search(r"-\s*Rs\.?\s*([\d,]+(?:\.\d+)?)\s*$", text_lines[0] if text_lines else "")
        new = parse_price(t2.group(1)) if t2 else None
    old = parse_price(old_m.group(1)) if old_m else None
    buy = [l for l in links if not any(h in l for h in _SKIP_HOSTS)]
    if not buy:
        return None
    # "Read More" link is first on DesiDime posts, "Buy Now" last
    page = buy[0] if len(buy) > 1 else ""
    url = buy[-1]
    title = ""
    for l in text_lines:
        l2 = re.sub(r"^[^\w\u20b9]+", "", l)
        if len(l2) > 12 and l2.lower() != channel.lower() and not l2.lower().startswith(("read more", "buy now", "link", "follow")) and "http" not in l2:
            title = l2
            break
    title = re.sub(r"^\d{1,2}% off on\s*-\s*", "", title)
    pct = int(pct_m.group(1)) if pct_m else 0
    if not title or (not pct and not (old and new)):
        return None
    return Deal(title=title, url=url, page=page, new_price=new, old_price=old, pct=pct, source=f"tg:{channel}",
                store=detect_store(url, blob), image=image, key="").finalize()


def _ts(w) -> int | None:
    t = w.select_one("time[datetime]")
    if not t:
        return None
    try:
        return int(datetime.fromisoformat(t["datetime"]).timestamp())
    except ValueError:
        return None


def parse(html: str, channel: str, now: int | None = None, max_age_h: float = 36) -> List[Deal]:
    now = now or int(time.time())
    out = []
    for w in BeautifulSoup(html, "lxml").select(".tgme_widget_message_wrap"):
        t = w.select_one(".tgme_widget_message_text")
        if not t:
            continue
        links = [a["href"] for a in t.select("a[href]")]
        msg = w.select_one(".tgme_widget_message")
        post = msg.get("data-post", "") if msg else ""
        ts = _ts(w)
        if ts is not None and now - ts > max_age_h * 3600:
            continue          # stale / dead channel (some "deal" channels have not posted since 2019)
        d = parse_message(_clean_lines(t), links, post, channel)
        if d:
            if ts:
                d.ts = ts
            if post:
                d.note = f"https://t.me/{post}"
            out.append(d)
    return out


def fetch_all(fetch, channels=None) -> List[Deal]:
    deals, errs = [], []
    for ch in channels or CHANNELS:
        try:
            r = fetch(f"https://t.me/s/{ch}")
            if r.status_code == 200:
                deals += parse(r.text, ch)
            else:
                errs.append(f"{ch}:{r.status_code}")
        except Exception as e:
            errs.append(f"{ch}:{type(e).__name__}")
    if errs and not deals:
        raise RuntimeError("telegram all failed " + ",".join(errs))
    return deals
