"""'Watch my link' items live in Firestore collection dh_watch (written by the app, read here with the service account)."""
from __future__ import annotations
from typing import Dict, List, Optional


def _val(v: dict):
    for k in ("stringValue", "integerValue", "doubleValue", "booleanValue"):
        if k in v:
            x = v[k]
            return float(x) if k in ("integerValue", "doubleValue") else x
    return None


def decode_doc(doc: dict) -> dict:
    out = {k: _val(v) for k, v in doc.get("fields", {}).items()}
    out["_name"] = doc["name"]
    return out


class Firestore:
    def __init__(self, sender):
        self.s = sender.session
        self.base = f"https://firestore.googleapis.com/v1/projects/{sender.project_id}/databases/(default)/documents"

    def list_watch(self) -> List[dict]:
        r = self.s.get(f"{self.base}/dh_watch?pageSize=200", timeout=30)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return [decode_doc(d) for d in r.json().get("documents", [])]

    def mark(self, name: str, price: float, notified: bool):
        fields = {"lastPrice": {"doubleValue": price}}
        mask = "updateMask.fieldPaths=lastPrice"
        if notified:
            import time
            fields["notifiedAt"] = {"integerValue": str(int(time.time()))}
            mask += "&updateMask.fieldPaths=notifiedAt"
        url = f"https://firestore.googleapis.com/v1/{name}?{mask}"
        self.s.patch(url, json={"fields": fields}, timeout=30).raise_for_status()


def should_notify(item: dict, price: Optional[float], now: int) -> bool:
    """Price at/below target, and not already notified for this price in the last 12h."""
    if price is None or not item.get("target"):
        return False
    if price > float(item["target"]):
        return False
    last = item.get("lastPrice")
    nt = item.get("notifiedAt") or 0
    if last is not None and abs(float(last) - price) < 0.5 and now - float(nt) < 12 * 3600:
        return False
    return True
