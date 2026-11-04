"""Firebase Cloud Messaging HTTP v1 sender (service-account OAuth). FCM is free; no billing needed."""
from __future__ import annotations
import json, os
from typing import Dict, Optional

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging", "https://www.googleapis.com/auth/datastore"]


def deal_data(d: dict) -> Dict[str, str]:
    """FCM data values must all be strings and total payload < 4 KB."""
    def s(x):
        return "" if x is None else str(int(x)) if isinstance(x, float) and x == int(x) else str(x)
    return {"id": s(d.get("id")), "title": s(d.get("title"))[:140], "store": s(d.get("store")),
            "old": s(d.get("old_price")), "new": s(d.get("new_price")), "pct": s(d.get("pct")),
            "url": s(d.get("url"))[:900], "page": s(d.get("page"))[:300], "ts": s(d.get("ts")), "kind": s(d.get("kind") or "deal"),
            "note": s(d.get("note"))[:200]}


def build_message(d: dict, topic: Optional[str] = "deals", token: Optional[str] = None) -> dict:
    msg: dict = {"data": deal_data(d),
                 "android": {"priority": "HIGH", "ttl": "7200s"}}
    if token:
        msg["token"] = token
    else:
        msg["topic"] = topic
    return {"message": msg}


class Sender:
    def __init__(self, service_account_json: str, project_id: Optional[str] = None):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        info = json.loads(service_account_json)
        self.project_id = project_id or info["project_id"]
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.session = AuthorizedSession(creds)

    def send(self, payload: dict) -> dict:
        r = self.session.post(f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send", json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"FCM {r.status_code}: {r.text[:300]}")
        return r.json()


def from_env() -> Optional[Sender]:
    raw = os.environ.get("FCM_SERVICE_ACCOUNT", "").strip()
    if not raw:
        return None
    return Sender(raw, os.environ.get("FIREBASE_PROJECT_ID") or None)
