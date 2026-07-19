"""
MeraFraud - Transaction Log & Report Export
-----------------------------------------------
Keeps a rolling log of scored transactions per tenant, so merchants can
export their history as a CSV report (Settings page → "Export Report").

This is intentionally simple (a capped JSON list per tenant, not a real
database/warehouse) — fine for an MVP's reporting needs. For high-volume
production use, swap this for a proper table (e.g. PostgreSQL) so exports
can be filtered/paginated instead of loading everything into memory.
"""

import json
import csv
import io
import threading
from pathlib import Path
from datetime import datetime, timezone

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "transaction_log.json"
_lock = threading.Lock()
MAX_ROWS_PER_TENANT = 1000  # oldest rows drop off past this, keeps the file small


def _load():
    if not LOG_PATH.exists():
        return {}
    with open(LOG_PATH) as f:
        return json.load(f)


def _save(data):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(data, f)


def log_transaction(tenant_id: str, row: dict, risk_score: float, risk_level: str, customer_id: str | None):
    with _lock:
        data = _load()
        data.setdefault(tenant_id, [])
        data[tenant_id].append({
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "customer_id": customer_id or "",
            "transaction_amount": row.get("transaction_amount"),
            "risk_score": round(risk_score, 4),
            "risk_level": risk_level,
        })
        data[tenant_id] = data[tenant_id][-MAX_ROWS_PER_TENANT:]  # cap growth
        _save(data)


def get_transactions(tenant_id: str) -> list:
    data = _load()
    return data.get(tenant_id, [])


def to_csv(tenant_id: str) -> str:
    rows = get_transactions(tenant_id)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["scored_at", "customer_id", "transaction_amount", "risk_score", "risk_level"])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
