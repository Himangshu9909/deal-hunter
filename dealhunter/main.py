"""Deal Hunter backend: poll public deal sources -> filter -> de-dupe -> FCM push + feed JSON.

Usage:  python -m dealhunter.main [--dry-run] [--data-dir data] [--config data/config.json] [--no-network-sources a,b]
"""
from __future__ import annotations
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

import hashlib
from . import fcm
from .watchlist import should_notify
from .filters import Config, dedupe_batch, passes, prune_state, select_new
from .http import fetch
from .models import Deal
from .sources import amazon, desidime, flipkart, reddit, telegram, watch as watchsrc


def rotate(items: List[str], n: int, now: int, period: int = 600) -> List[str]:
    """Pick n queries that change every `period` seconds so successive runs cover the whole list."""
    if not items or n <= 0:
        return []
    start = (now // period * n) % len(items)
    return [items[(start + i) % len(items)] for i in range(min(n, len(items)))]


def apply_history(deals: List[Deal], hist: Dict[str, list], now: int) -> None:
    """Price *crash* detection from our own observations: same product seen before at >=2x the price now."""
    for d in deals:
        if d.new_price is None:
            continue
        prev = hist.get(d.key)
        if prev and now - prev[1] < 3 * 86400 and prev[0] > 0 and d.new_price <= prev[0] * 0.5:
            if not d.old_price or prev[0] > d.old_price:
                d.old_price = prev[0]
            d.note = (d.note + " " if d.note else "") + f"seen at Rs {int(prev[0])} earlier"
            d.finalize()
        if d.pct >= 30 or d.key not in hist:
            hist[d.key] = [max(d.new_price, 0), now] if d.key not in hist or d.pct >= 30 else hist[d.key]
    for k in [k for k, v in hist.items() if now - v[1] > 3 * 86400]:
        del hist[k]
    if len(hist) > 4000:
        for k, _ in sorted(hist.items(), key=lambda kv: kv[1][1])[: len(hist) - 4000]:
            del hist[k]


def collect(cfg_raw: dict, now: int, only=None) -> Tuple[List[Deal], Dict[str, dict]]:
    """Run every source in parallel; a failing source never stops the others."""
    src = cfg_raw.get("sources", {})
    n_q = int(cfg_raw.get("queries_per_run", 5))
    jobs = {
        "desidime": lambda: desidime.fetch_all(fetch),
        "telegram": lambda: telegram.fetch_all(fetch, cfg_raw.get("telegram_channels") or None),
        "reddit": lambda: reddit.fetch_all(fetch),
        "amazon": lambda: amazon.fetch_all(fetch, rotate(cfg_raw.get("amazon_queries") or amazon.QUERIES, n_q, now), int(cfg_raw.get("amazon_min_pct", 70))),
        "flipkart": lambda: flipkart.fetch_all(fetch, rotate(cfg_raw.get("flipkart_queries") or flipkart.QUERIES, n_q, now)),
    }
    enabled = {k: v for k, v in jobs.items() if src.get(k, True) and (only is None or k in only)}
    status: Dict[str, dict] = {}

    def run(name):
        t0 = time.time()
        try:
            ds = enabled[name]()
            return name, ds, {"ok": True, "deals": len(ds), "secs": round(time.time() - t0, 1)}
        except Exception as e:  # noqa
            return name, [], {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}", "secs": round(time.time() - t0, 1)}

    deals: List[Deal] = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for name, ds, st in ex.map(run, list(enabled)):
            deals += ds
            status[name] = st
    return deals, status


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def check_watches(fs, sender, dry, now) -> List[dict]:
    """Return the notifications sent for watched links."""
    sent = []
    for item in fs.list_watch():
        try:
            r = fetch(item["url"])
            price = watchsrc.price_from_html(r.text) if r.status_code == 200 else None
        except Exception:
            price = None
        title = item.get("title") or (watchsrc.title_from_html(r.text) if price else item.get("url", ""))
        ok = should_notify(item, price, now)
        if price is not None:
            fs.mark(item["_name"], price, ok)
        if ok and item.get("token"):
            d = {"id": "w" + hashlib.sha1((item["url"] + str(price)).encode()).hexdigest()[:12], "title": f"\U0001F514 {title}", "store": "Watch", "new_price": price,
                 "old_price": None, "pct": 0, "url": item["url"], "ts": now, "kind": "watch",
                 "note": f"Target Rs {int(float(item['target']))} reached"}
            payload = fcm.build_message(d, token=item["token"])
            if not dry:
                sender.send(payload)
            sent.append(d)
    return sent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="never call FCM/Firestore; print what would be sent")
    ap.add_argument("--only", default=None, help="comma list of sources to run")
    ap.add_argument("--min-push-pct", type=int, default=None)
    args = ap.parse_args(argv)
    now = int(time.time())
    ddir = Path(args.data_dir); ddir.mkdir(parents=True, exist_ok=True)
    cfg_raw = load_json(Path(args.config) if args.config else ddir / "config.json", {})
    for env, key in (("MIN_PUSH_PCT", "min_push_pct"), ("PRICE_CEILING", "price_ceiling"), ("PRICE_FLOOR", "price_floor")):
        if os.environ.get(env):
            cfg_raw[key] = float(os.environ[env])
    if args.min_push_pct is not None:
        cfg_raw["min_push_pct"] = args.min_push_pct
    cfg = Config.from_dict(cfg_raw)
    state = load_json(ddir / "state.json", {"pushed": {}, "hist": {}})
    state.setdefault("pushed", {}); state.setdefault("hist", {})
    feed = load_json(ddir / "deals.json", {"deals": []})["deals"]

    deals, status = collect(cfg_raw, now, set(args.only.split(",")) if args.only else None)
    deals = dedupe_batch([d for d in deals if d.title])
    apply_history(deals, state["hist"], now)

    feed_new = [d for d in deals if passes(d, cfg, cfg.min_feed_pct)]
    push_cands = [d for d in feed_new if passes(d, cfg)]
    first_run = not state["pushed"] and not feed          # brand-new install: do not blast the phone with the whole backlog
    fresh = select_new(push_cands, state["pushed"], cfg, now)
    fresh.sort(key=lambda x: (-x.pct, -((x.old_price or 0) - (x.new_price or 0))))
    limit = min(cfg.max_push_per_run, 3) if first_run else cfg.max_push_per_run
    to_push, overflow, groups = [], [], {}
    for d in fresh:                       # at most 2 pushes for the same brand+price (e.g. 6 colours of the same Rs 99 socks)
        g = (d.title.split(" ")[0].lower(), int(d.new_price or 0))
        groups[g] = groups.get(g, 0) + 1
        (to_push if len(to_push) < limit and groups[g] <= 2 else overflow).append(d)

    # feed: newest first, de-duplicated by key, trimmed
    by_key = {x["key"]: x for x in feed}
    for d in feed_new:
        j = d.to_json()
        old = by_key.get(d.key)
        if old is None or (j["new_price"] is not None and (old.get("new_price") is None or j["new_price"] < old["new_price"] * 0.9)):
            by_key[d.key] = j
    items = sorted(by_key.values(), key=lambda x: -x["ts"])
    per_scan = cfg.feed_size // 6                                  # scans re-stamp "now" every run; cap each so they never swamp curated posts
    scan = [x for x in items if x["source"] == "amazon"][:per_scan] + [x for x in items if x["source"] == "flipkart"][:per_scan]
    curated = [x for x in items if x["source"] not in ("amazon", "flipkart")][: cfg.feed_size - len(scan)]
    feed = sorted(scan + curated, key=lambda x: -x["ts"])

    sender = None if args.dry_run else fcm.from_env()
    results = []
    for d in to_push:
        payload = fcm.build_message(d.to_json())
        if sender:
            try:
                results.append({"id": d.to_json()["id"], "fcm": sender.send(payload).get("name")})
            except Exception as e:
                results.append({"id": d.to_json()["id"], "error": str(e)[:200]})
        else:
            results.append({"id": d.to_json()["id"], "dry": True, "payload_bytes": len(json.dumps(payload))})
    watch_sent = []
    if sender:
        try:
            from .watchlist import Firestore
            watch_sent = check_watches(Firestore(sender), sender, False, now)
        except Exception as e:
            status["watch"] = {"ok": False, "error": str(e)[:160]}

    prune_state(state["pushed"], cfg, now)
    (ddir / "state.json").write_text(json.dumps(state, separators=(",", ":")))
    (ddir / "deals.json").write_text(json.dumps({"updated": now, "deals": feed}, ensure_ascii=False, separators=(",", ":")))
    (ddir / "status.json").write_text(json.dumps({"run_at": now, "sources": status, "scraped": len(deals), "feed_candidates": len(feed_new),
                                                  "push_candidates": len(push_cands), "pushed": len(to_push), "overflow": len(overflow),
                                                  "fcm_configured": bool(sender), "dry_run": args.dry_run, "watch_sent": len(watch_sent)}, indent=1))
    print(f"scraped={len(deals)} feed={len(feed_new)} push_candidates={len(push_cands)} pushed={len(to_push)} (overflow {len(overflow)})")
    for name, st in status.items():
        print(f"  {name:9s} {'ok  ' if st['ok'] else 'FAIL'} {st.get('deals', st.get('error'))} ({st['secs']}s)")
    for d in to_push:
        print(f"  PUSH {d.pct:3d}% off  Rs {d.new_price:g}" + (f" (was {d.old_price:g})" if d.old_price else "") + f"  [{d.store}] {d.title[:70]}  {d.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
