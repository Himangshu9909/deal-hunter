"""Core data model + small helpers shared by every source."""
from __future__ import annotations
import hashlib, re, time
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse, parse_qs

_NUM = re.compile(r"[\d][\d,]*(?:\.\d+)?")


def parse_price(text: Optional[str]) -> Optional[float]:
    """'₹ 12,249' -> 12249.0 ; 'Rs. 96.67' -> 96.67 ; None when no number."""
    if not text:
        return None
    m = _NUM.search(text.replace("\u20b9", " ").replace("\xa0", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def calc_pct(old: Optional[float], new: Optional[float]) -> Optional[int]:
    if old and new is not None and old > 0 and 0 <= new <= old:
        return int(round((1 - new / old) * 100))
    return None


def detect_store(url: str = "", text: str = "") -> str:
    h = (urlparse(url).netloc or "").lower()
    blob = f"{h} {text}".lower()
    for needle, name in (
        ("amazon", "Amazon"), ("amzn", "Amazon"), ("flipkart", "Flipkart"), ("fkrt", "Flipkart"),
        ("myntra", "Myntra"), ("ajio", "Ajio"), ("meesho", "Meesho"), ("nykaa", "Nykaa"),
        ("croma", "Croma"), ("tatacliq", "Tata CLiQ"), ("reliancedigital", "Reliance Digital"),
        ("swiggy", "Swiggy"), ("zepto", "Zepto"), ("blinkit", "Blinkit"), ("bigbasket", "BigBasket"),
        ("jiomart", "JioMart"), ("snapdeal", "Snapdeal"), ("firstcry", "FirstCry"), ("pharmeasy", "PharmEasy"),
    ):
        if needle in blob:
            return name
    return "Other"


@dataclass
class Deal:
    title: str
    url: str                         # buy link
    store: str = "Other"
    new_price: Optional[float] = None
    old_price: Optional[float] = None
    pct: int = 0
    source: str = ""
    page: str = ""                   # details page (deal thread etc.)
    image: str = ""
    ts: int = field(default_factory=lambda: int(time.time()))
    key: str = ""
    note: str = ""

    def finalize(self) -> "Deal":
        self.title = re.sub(r"\s+", " ", (self.title or "").replace("\ufffd", "")).strip()[:200]
        if self.old_price and self.new_price is not None and self.old_price < self.new_price:
            self.old_price = None      # list price lower than price = we parsed a per-unit/other number
        c = calc_pct(self.old_price, self.new_price)
        if c is not None:
            self.pct = c
        if self.store in ("", "Other"):
            self.store = detect_store(self.url, self.title)
        if not self.key:
            self.key = canonical_key(self)
        return self

    def to_json(self) -> dict:
        d = asdict(self)
        d["id"] = hashlib.sha1((self.key + str(self.new_price)).encode()).hexdigest()[:16]
        return d


def canonical_key(d: Deal) -> str:
    """Stable identity of a *product*, independent of price, so a repeat of the same price (or a re-post) is de-duped."""
    u = d.url or ""
    p = urlparse(u)
    host = p.netloc.lower()
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", u)
    if "amazon" in host and m:
        return "amz:" + m.group(1)
    if "flipkart" in host:
        pid = parse_qs(p.query).get("pid", [""])[0]
        if pid:
            return "fk:" + pid
    if u:
        return "url:" + host + p.path.rstrip("/")
    return "t:" + title_key(d.title)


def title_key(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return "-".join(words[:8])
