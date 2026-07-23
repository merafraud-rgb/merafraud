"""
MeraFraud - Shared Fraud Intelligence Network (infrastructure)
--------------------------------------------------------------------
This is the seed of MeraFraud's biggest long-term moat: a cross-merchant
blacklist, like FraudLabsPro's network of 2.8M+ reported IPs. It only
becomes powerful with real scale and real merchants — but the schema and
pipeline need to exist NOW so that:
  1. Every confirmed-fraud report from day one accumulates instead of
     being thrown away
  2. Future merchants benefit retroactively from everything reported
     before they even signed up
  3. When there's enough real data, this can be exposed as a genuine
     product feature/differentiator

How it works:
  - A merchant confirms a transaction was fraud (e.g. after a chargeback)
    and calls POST /api/blacklist/report with the IP/email/device involved
  - This increments a shared counter — NOT tied to which merchant reported
    it (for privacy: we store a report count and which tenants reported,
    not the transaction details themselves)
  - On every future /predict call across ALL tenants, if the submitted
    IP/customer_id matches an entry with enough independent reports, the
    score gets boosted and a reason is attached

Privacy note: only report identifiers (IP, email domain+hash, device ID)
are stored — never full transaction details, names, or card data.
"""

import json
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

BLACKLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "shared_blacklist.json"
_lock = threading.Lock()

# How many DIFFERENT tenants must report the same identifier before it's
# trusted as a network-wide signal (prevents one bad-faith report, or a
# single merchant's grudge, from blacklisting an innocent customer).
MIN_INDEPENDENT_REPORTS = 2
NETWORK_SCORE_BOOST = 0.30


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash_identifier(value: str) -> str:
    """Store a hash, not the raw value — so even this internal file
    doesn't hold plaintext emails/IPs at rest."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:24]


def _load():
    if not BLACKLIST_PATH.exists():
        return {"ips": {}, "emails": {}, "devices": {}}
    with open(BLACKLIST_PATH) as f:
        return json.load(f)


def _save(data):
    BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BLACKLIST_PATH, "w") as f:
        json.dump(data, f, indent=2)


def report_fraud(tenant_id: str, ip: str | None = None, email: str | None = None, device_id: str | None = None) -> dict:
    """A tenant confirms an identifier was involved in fraud. Safe to call
    multiple times — reports are deduplicated per tenant per identifier."""
    with _lock:
        data = _load()
        reported = []

        for category, raw_value in (("ips", ip), ("emails", email), ("devices", device_id)):
            if not raw_value:
                continue
            key = _hash_identifier(raw_value)
            entry = data[category].setdefault(key, {"reporting_tenants": [], "first_reported": _now(), "last_reported": _now()})
            if tenant_id not in entry["reporting_tenants"]:
                entry["reporting_tenants"].append(tenant_id)
            entry["last_reported"] = _now()
            reported.append({"category": category, "report_count": len(entry["reporting_tenants"])})

        _save(data)
        return {"status": "ok", "reported": reported}


def check_identifier(ip: str | None = None, email: str | None = None, device_id: str | None = None) -> dict:
    """Checks whether any submitted identifier is flagged by the network.
    Returns which ones matched and how many independent tenants reported them."""
    data = _load()
    matches = []

    for category, raw_value in (("ips", ip), ("emails", email), ("devices", device_id)):
        if not raw_value:
            continue
        key = _hash_identifier(raw_value)
        entry = data[category].get(key)
        if entry and len(entry["reporting_tenants"]) >= MIN_INDEPENDENT_REPORTS:
            matches.append({"category": category, "report_count": len(entry["reporting_tenants"])})

    return {"is_flagged": len(matches) > 0, "matches": matches}


def apply_network_risk_adjustment(base_score: float, network_check: dict) -> tuple[float, list[str]]:
    if not network_check["is_flagged"]:
        return base_score, []

    score = min(0.99, base_score + NETWORK_SCORE_BOOST)
    categories = ", ".join(m["category"] for m in network_check["matches"])
    reason = f"Flagged by the MeraFraud merchant network ({categories}) — reported as fraud by other stores"
    return score, [reason]


def network_stats() -> dict:
    """Aggregate, anonymous stats for the dashboard — how big the shared
    network is, without exposing any individual identifier or tenant."""
    data = _load()
    return {
        "total_reported_ips": len(data["ips"]),
        "total_reported_emails": len(data["emails"]),
        "total_reported_devices": len(data["devices"]),
    }
