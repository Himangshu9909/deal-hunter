import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dealhunter import fcm
from dealhunter.models import Deal
from dealhunter.watchlist import Firestore


def fake_sa():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "project_id": "edufee-fa59b", "private_key_id": "k1", "private_key": pem,
                       "client_email": "firebase-adminsdk-x@edufee-fa59b.iam.gserviceaccount.com", "client_id": "1",
                       "token_uri": "https://oauth2.googleapis.com/token"})


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, json.dumps(body)
    def json(self):
        return self._b
    def raise_for_status(self):
        assert self.status_code < 400


def test_service_account_parses_and_signs_jwt_offline():
    """Proves the secret format (Firebase 'Generate new private key' JSON) is accepted and a JWT assertion can be signed.
    (The token exchange itself needs Google's servers and was NOT run - no real key here.)"""
    s = fcm.Sender(fake_sa())
    assert s.project_id == "edufee-fa59b"
    jwt = s.session.credentials._make_authorization_grant_assertion()
    assert jwt.count(b".") == 2


def test_send_posts_v1_endpoint_with_data_only_high_priority(monkeypatch):
    s = fcm.Sender(fake_sa())
    seen = {}
    def post(url, json=None, timeout=None):
        seen["url"], seen["body"] = url, json
        return Resp(200, {"name": "projects/edufee-fa59b/messages/123"})
    monkeypatch.setattr(s.session, "post", post)
    d = Deal(title="Camera lens", url="https://www.amazon.in/dp/B0DDDDDDD1", new_price=150, old_price=2000, source="amazon").finalize().to_json()
    out = s.send(fcm.build_message(d))
    assert out["name"].endswith("/123")
    assert seen["url"] == "https://fcm.googleapis.com/v1/projects/edufee-fa59b/messages:send"
    m = seen["body"]["message"]
    assert m["topic"] == "deals" and m["android"]["priority"] == "HIGH" and m["data"]["pct"] == "92"


def test_send_error_is_raised_with_google_message(monkeypatch):
    s = fcm.Sender(fake_sa())
    monkeypatch.setattr(s.session, "post", lambda *a, **k: Resp(403, {"error": {"message": "FCM API disabled"}}))
    try:
        s.send({"message": {}})
        assert False
    except RuntimeError as e:
        assert "403" in str(e) and "FCM API disabled" in str(e)


def test_firestore_watch_list_and_mark(monkeypatch):
    s = fcm.Sender(fake_sa())
    calls = []
    class Sess:
        def get(self, url, timeout=None):
            calls.append(("GET", url))
            return Resp(200, {"documents": [{"name": "projects/p/databases/(default)/documents/dh_watch/w1",
                                              "fields": {"url": {"stringValue": "https://www.amazon.in/dp/B0AAAAAAA1"}, "target": {"doubleValue": 499.0},
                                                         "token": {"stringValue": "TKN"}}}]})
        def patch(self, url, json=None, timeout=None):
            calls.append(("PATCH", url, json)); return Resp(200, {})
    s.session = Sess()
    fs = Firestore(s)
    items = fs.list_watch()
    assert items[0]["target"] == 499.0 and items[0]["token"] == "TKN" and items[0]["_name"].endswith("dh_watch/w1")
    fs.mark(items[0]["_name"], 450.0, True)
    assert calls[1][0] == "PATCH" and "updateMask.fieldPaths=notifiedAt" in calls[1][1] and calls[1][2]["fields"]["lastPrice"]["doubleValue"] == 450.0


def test_check_watches_sends_token_message(monkeypatch):
    from dealhunter import main as dm
    sent = []
    class FS:
        def list_watch(self): return [{"_name": "n/1", "url": "https://shop.test/p", "target": 500.0, "token": "TKN", "title": "My phone"}]
        def mark(self, *a): sent.append(("mark",) + a)
    class Snd:
        def send(self, p): sent.append(("fcm", p))
    class R:
        status_code = 200
        text = '<script type="application/ld+json">{"offers":{"price":"450"}}</script>'
    monkeypatch.setattr(dm, "fetch", lambda u: R())
    out = dm.check_watches(FS(), Snd(), False, 1_000_000)
    assert len(out) == 1
    msg = [x for x in sent if x[0] == "fcm"][0][1]["message"]
    assert msg["token"] == "TKN" and msg["data"]["kind"] == "watch" and msg["data"]["new"] == "450"
