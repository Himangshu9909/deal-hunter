"""Filtering + de-duplication (pure functions, unit-tested)."""
from __future__ import annotations
import re, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .models import Deal, title_key

JUNK = re.compile(r"cashback|cash back|\bcb\b|recharge|referral|refer &|bill payment|\bupi\b|coupon code only|"
                  r"\bgift card\b|credit card offer|free trial|\bcontest\b|giveaway", re.I)


@dataclass
class Config:
    min_push_pct: int = 70           # push only deals at/above this discount
    min_feed_pct: int = 50           # keep in the app's feed at/above this
    price_floor: float = 0           # ignore prices below this (error "Rs 1" listings are still shown if floor=0)
    price_ceiling: float = 0         # 0 = no ceiling
    max_push_per_run: int = 8
    repush_drop: float = 0.10        # re-notify an already-seen product only if the price fell by >=10% again
    state_days: int = 7
    feed_size: int = 150
    scan_min_pct: int = 80           # Amazon/Flipkart search scans are noisy (inflated MRPs) -> stricter bar than curated feeds
    min_drop_rs: float = 300         # scanned items must also save at least this many rupees (ignores Rs 99 -> Rs 10 trinkets)
    allow_junk: bool = False
    stores: List[str] = field(default_factory=list)   # empty = all stores

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        c = cls()
        for k, v in (d or {}).items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c


_UPTO = re.compile(r"\b(?:up ?to|min(?:imum)?|flat)\s*\d{1,2}\s?%", re.I)


def is_junk(d: Deal) -> bool:
    if JUNK.search(d.title):
        return True
    # "Upto 80% off on <brand> clothing" with no real MRP is a category banner, not a product price crash
    return bool(_UPTO.search(d.title)) and not d.old_price


def is_scan(d: Deal) -> bool:
    return d.source in ("amazon", "flipkart")


def passes(d: Deal, cfg: Config, min_pct: Optional[int] = None) -> bool:
    mp = cfg.min_push_pct if min_pct is None else min_pct
    if d.pct < mp or d.pct > 100:
        return False
    if d.new_price is None:           # a discount % with no price is a category banner, not a product
        return False
    if is_scan(d) and (d.pct < cfg.scan_min_pct or (d.old_price or 0) - d.new_price < cfg.min_drop_rs) and mp >= cfg.min_push_pct:
        return False
    if d.new_price < cfg.price_floor:
        return False
    if cfg.price_ceiling and d.new_price > cfg.price_ceiling:
        return False
    if cfg.stores and d.store not in cfg.stores:
        return False
    if not cfg.allow_junk and is_junk(d):
        return False
    return True


def dedupe_batch(deals: List[Deal]) -> List[Deal]:
    """Same product from several sources in one run: keep the one with most info (lowest price, has old price)."""
    best: Dict[str, Deal] = {}
    tkeys: Dict[str, str] = {}
    for d in deals:
        k = d.key
        tk = title_key(d.title) + "@" + str(int(d.new_price or 0))
        if tk in tkeys:
            k = tkeys[tk]
        cur = best.get(k)
        if cur is None or (d.old_price and not cur.old_price) or (d.new_price is not None and cur.new_price is not None and d.new_price < cur.new_price):
            if cur is not None and not d.page and cur.page:
                d.page = cur.page
            best[k] = d
        tkeys.setdefault(tk, k)
    return list(best.values())


def select_new(deals: List[Deal], state: Dict[str, dict], cfg: Config, now: Optional[int] = None) -> List[Deal]:
    """Return deals not yet notified (or whose price dropped again) and update `state` in place."""
    now = now or int(time.time())
    fresh: List[Deal] = []
    for d in deals:
        prev = state.get(d.key)
        if prev is not None:
            last = prev.get("price")
            if last is None or d.new_price is None or d.new_price > last * (1 - cfg.repush_drop):
                continue
        state[d.key] = {"price": d.new_price, "ts": now}
        fresh.append(d)
    fresh.sort(key=lambda x: (-x.pct, x.new_price or 0))
    return fresh


def prune_state(state: Dict[str, dict], cfg: Config, now: Optional[int] = None) -> Dict[str, dict]:
    now = now or int(time.time())
    cutoff = now - cfg.state_days * 86400
    for k in [k for k, v in state.items() if v.get("ts", 0) < cutoff]:
        del state[k]
    return state
