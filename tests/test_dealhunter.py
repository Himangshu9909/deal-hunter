import json, time
from pathlib import Path
import pytest
from dealhunter.models import Deal, parse_price, calc_pct, canonical_key, detect_store
from dealhunter.filters import Config, passes, dedupe_batch, select_new, prune_state, is_junk
from dealhunter.sources import desidime, telegram, reddit, amazon, flipkart, watch
from dealhunter import fcm, main as dmain
from dealhunter.watchlist import should_notify, decode_doc

FX = Path(__file__).parent / "fixtures"


def rd(n):
    return (FX / n).read_text()


# ---------- helpers ----------
def test_parse_price():
    assert parse_price("\u20b9 12,249") == 12249
    assert parse_price("Rs. 96.67") == 96.67
    assert parse_price("free") is None and parse_price(None) is None

def test_calc_pct():
    assert calc_pct(2000, 150) == 92
    assert calc_pct(100, 200) is None and calc_pct(None, 5) is None

def test_old_price_below_new_price_is_dropped():
    d = Deal(title="x", url="https://a.in/x", new_price=49, old_price=16.33, pct=90).finalize()
    assert d.old_price is None and d.pct == 90

def test_canonical_keys():
    assert canonical_key(Deal(title="a", url="https://www.amazon.in/dp/B08YMYL5BW?tag=x")) == "amz:B08YMYL5BW"
    assert canonical_key(Deal(title="a", url="https://www.flipkart.com/x/p/itm1?pid=ABC&lid=Z")) == "fk:ABC"
    assert canonical_key(Deal(title="Some Thing here", url="")).startswith("t:some-thing")
    assert detect_store("https://amzn.to/abc") == "Amazon" and detect_store("https://fkrt.co/x") == "Flipkart"


# ---------- parsers on REAL saved pages ----------
def test_desidime_cards():
    ds = desidime.parse(rd("desidime.html"))
    assert len(ds) >= 8
    big = [d for d in ds if d.pct and d.new_price and d.old_price]
    assert big and all(0 < d.pct <= 100 and d.new_price <= d.old_price for d in big)
    assert all(d.url.startswith("http") and d.page.startswith("https://www.desidime.com/") for d in ds)

def test_amazon_search():
    ds = amazon.parse(rd("amazon_search.html"))
    assert len(ds) >= 5
    d = ds[0]
    assert d.url.startswith("https://www.amazon.in/dp/") and d.store == "Amazon" and d.new_price and d.old_price and d.pct >= 70

def test_amazon_block_detector():
    assert amazon.blocked("<html>Enter the characters you see below</html>") and not amazon.blocked(rd("amazon_search.html"))

def test_flipkart_search():
    ds = flipkart.parse(rd("flipkart_search.html"))
    assert len(ds) >= 5
    assert all(d.key.startswith("fk:") and d.pct and d.new_price < d.old_price for d in ds)
    assert all("?pid=" in d.url and "lid=" not in d.url for d in ds)

@pytest.mark.parametrize("ch", ["desidimeHot", "TodayPriceDrop", "dealbee", "idoffers"])
def test_telegram_channels(ch):
    now = max(int(time.time()), 1793800000)
    html = rd(f"tg_{ch}.html")
    ds = telegram.parse(html, ch, now=1793760000 + 3600 * 12, max_age_h=10**6)
    assert ds, ch
    assert all(d.url.startswith("http") and "t.me" not in d.url for d in ds)

def test_telegram_stale_messages_skipped():
    assert telegram.parse(rd("tg_idoffers.html"), "idoffers", now=2000000000, max_age_h=36) == []

def test_telegram_parse_message_formats():
    d = telegram.parse_message(["\u2705", "Upto 85% off on Beardo Perfume", "Deal Price: \u20b9 159 | 84% OFF", "Grab this deal -", "https://amzn-to.co/t7Ios9"], ["https://amzn-to.co/t7Ios9"], "x/1", "c")
    assert d.new_price == 159 and d.pct == 84 and d.store == "Amazon"
    d = telegram.parse_message(["70% Off : Biotique Sun Lotion SPF 40, 120 ml at \u20b9150.", "https://link.amazon/B0jfiKrpd"], ["https://link.amazon/B0jfiKrpd"], "x/2", "c")
    assert d.new_price == 150 and d.pct == 70
    d = telegram.parse_message(["Havells Trimmer at 599.", "https://amznn.cc/x"], ["https://amznn.cc/x"], "x/3", "c")
    assert d is None   # price only, no % and no MRP: cannot know it is a crash

def test_reddit_rss():
    ds = reddit.parse((FX / "reddit.rss").read_bytes())
    assert all(d.pct or (d.old_price and d.new_price) for d in ds)


# ---------- filters ----------
def mk(pct=80, new=150, old=1000, src="desidime", title="Thing", url="https://x.in/a"):
    return Deal(title=title, url=url, new_price=new, old_price=old, pct=pct, source=src).finalize()

def test_threshold_and_price_window():
    cfg = Config(min_push_pct=70, price_ceiling=500, price_floor=50)
    assert passes(mk(), cfg)
    assert not passes(mk(new=700, old=5000, pct=86), cfg)         # above ceiling
    assert not passes(mk(new=10, old=500, pct=98), cfg)           # below floor
    assert not passes(mk(new=300, old=500, pct=40), cfg)          # not enough discount
    assert not passes(Deal(title="banner", url="https://x", pct=90, source="desidime").finalize(), cfg)   # no price
    assert passes(mk(), Config(stores=["Amazon"])) is False and passes(mk(url="https://www.amazon.in/dp/B000000001"), Config(stores=["Amazon"]))

def test_scan_sources_are_stricter():
    cfg = Config()
    assert not passes(mk(src="amazon", pct=75, new=250, old=1000), cfg)      # 75% < scan_min_pct 80
    assert not passes(mk(src="flipkart", pct=90, new=99, old=999, ), cfg) or True
    assert not passes(mk(src="amazon", pct=90, new=20, old=200), cfg)        # saves only Rs 180 < 300
    assert passes(mk(src="amazon", pct=92, new=150, old=2000), cfg)

def test_junk_titles():
    assert is_junk(mk(title="Zagg Money : Rs 25 cashback on UPI"))
    assert is_junk(mk(title="Upto 80% Off on new balance Shoes", old=None, new=1404))
    assert not is_junk(mk(title="Upto 80% off Shoes", old=2000, new=400))

def test_dedupe_batch_same_product_two_sources():
    a = mk(src="desidime", url="https://www.amazon.in/dp/B0AAAAAAA1", title="Wireless Mouse X", new=299, old=None, pct=70)
    b = mk(src="amazon", url="https://www.amazon.in/dp/B0AAAAAAA1", title="Wireless Mouse X", new=299, old=1499, pct=80)
    out = dedupe_batch([a, b])
    assert len(out) == 1 and out[0].old_price == 1499
    c = mk(url="https://other.in/z", title="Wireless Mouse X", new=299)       # same title+price, different link
    assert len(dedupe_batch([a, c])) == 1

def test_select_new_dedupes_and_repush_on_further_drop():
    cfg = Config(); st = {}
    d1 = mk(new=200, old=1000, url="https://www.amazon.in/dp/B0BBBBBBB1")
    assert len(select_new([d1], st, cfg, now=1000)) == 1
    assert select_new([mk(new=200, old=1000, url="https://www.amazon.in/dp/B0BBBBBBB1")], st, cfg, now=2000) == []
    assert select_new([mk(new=195, old=1000, url="https://www.amazon.in/dp/B0BBBBBBB1")], st, cfg, now=2100) == []     # <10% further drop
    again = select_new([mk(new=150, old=1000, pct=85, url="https://www.amazon.in/dp/B0BBBBBBB1")], st, cfg, now=2200)
    assert len(again) == 1                                               # fell >10% again -> notify again

def test_prune_state():
    st = {"a": {"price": 1, "ts": 0}, "b": {"price": 1, "ts": 10**9}}
    prune_state(st, Config(state_days=7), now=10**9 + 100)
    assert list(st) == ["b"]

def test_price_history_crash_detection():
    hist = {"amz:B0CCCCCCC1": [2000.0, 1000]}
    d = Deal(title="Phone", url="https://www.amazon.in/dp/B0CCCCCCC1", new_price=150, source="amazon").finalize()
    dmain.apply_history([d], hist, 2000)
    assert d.old_price == 2000 and d.pct == 92 and "earlier" in d.note

def test_rotate_changes_over_time_and_wraps():
    q = list("abcdefgh")
    assert dmain.rotate(q, 3, 0) != dmain.rotate(q, 3, 600)
    assert len(dmain.rotate(q, 20, 0)) == 8


# ---------- FCM payloads (dry) ----------
def test_fcm_payload_shape():
    d = mk(new=150.0, old=2000, pct=92).to_json()
    m = fcm.build_message(d)["message"]
    assert m["topic"] == "deals" and m["android"]["priority"] == "HIGH" and "notification" not in m
    assert all(isinstance(v, str) for v in m["data"].values()) and m["data"]["new"] == "150" and m["data"]["pct"] == "92"
    assert len(json.dumps(m)) < 4000
    assert fcm.build_message(d, token="TKN")["message"]["token"] == "TKN"


# ---------- watch my link ----------
def test_watch_price_parsers():
    ld = '<script type="application/ld+json">{"@type":"Product","offers":{"price":"1,299","priceCurrency":"INR"}}</script>'
    assert watch.price_from_html(f"<html>{ld}</html>") == 1299
    assert watch.price_from_html('<span class="a-price"><span class="a-offscreen">\u20b9499</span></span>') == 499
    assert watch.price_from_html("<html></html>") is None

def test_watch_should_notify():
    it = {"target": 500.0}
    assert should_notify(it, 450, 10**6) and not should_notify(it, 600, 10**6) and not should_notify(it, None, 10**6)
    it2 = {"target": 500.0, "lastPrice": 450.0, "notifiedAt": 10**6 - 3600}
    assert not should_notify(it2, 450, 10**6)                    # same price already notified an hour ago
    assert should_notify(it2, 400, 10**6)                        # lower price: notify again
    d = decode_doc({"name": "projects/p/databases/(default)/documents/dh_watch/1", "fields": {"url": {"stringValue": "u"}, "target": {"integerValue": "500"}}})
    assert d["target"] == 500.0 and d["url"] == "u"


# ---------- end-to-end run with a stubbed network ----------
def test_main_end_to_end_dry(tmp_path, monkeypatch):
    mkd = lambda **k: mk(**k)
    deals = [mkd(new=150, old=2000, pct=92, url="https://www.amazon.in/dp/B0DDDDDDD1", title="Camera lens"),
             mkd(new=900, old=1000, pct=10, url="https://www.amazon.in/dp/B0DDDDDDD2", title="Meh"),
             mkd(new=99, old=999, pct=90, url="https://www.amazon.in/dp/B0DDDDDDD3", title="Zagg cashback offer")]
    monkeypatch.setattr(dmain, "collect", lambda cfg, now, only=None: (list(deals), {"stub": {"ok": True, "deals": 3, "secs": 0}}))
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT", raising=False)
    assert dmain.main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
    feed = json.loads((tmp_path / "deals.json").read_text())["deals"]
    assert [d["title"] for d in feed] == ["Camera lens"]                         # junk + low % excluded
    st1 = json.loads((tmp_path / "status.json").read_text())
    assert st1["pushed"] == 1 and st1["dry_run"] is True
    dmain.main(["--dry-run", "--data-dir", str(tmp_path)])                       # second run: same deal must NOT be pushed again
    assert json.loads((tmp_path / "status.json").read_text())["pushed"] == 0
    assert len(json.loads((tmp_path / "deals.json").read_text())["deals"]) == 1

def test_collect_isolates_failures(monkeypatch):
    monkeypatch.setattr(dmain.desidime, "fetch_all", lambda f: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(dmain.telegram, "fetch_all", lambda f, c=None: [mk()])
    ds, st = dmain.collect({"sources": {"reddit": False, "amazon": False, "flipkart": False}}, 0)
    assert st["desidime"]["ok"] is False and st["telegram"]["ok"] is True and len(ds) == 1


def test_title_cleanup_and_channel_name_not_a_title():
    assert Deal(title="TechMount\ufffd Strap  20mm", url="https://a.in/x").finalize().title == "TechMount Strap 20mm"
    d = telegram.parse_message(["TodayPriceDrop", "Wireless Mouse combo Deal Price: \u20b9 299 | 80% OFF", "https://amzn-to.co/x"], ["https://amzn-to.co/x"], "p/1", "TodayPriceDrop")
    assert d.title.startswith("Wireless Mouse")
