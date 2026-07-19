"""
MeraFraud - Customer Order History Tracking
------------------------------------------------
Payment fraud (a single suspicious transaction) and ORDER ABUSE (a real
customer who repeatedly places and cancels orders, burning your shipping/
handling costs) are different problems. The ML model scores a single
transaction in isolation — it has no memory. This module gives MeraFraud
that memory, per merchant (tenant), per customer.

How it works:
  1. The merchant calls POST /api/orders/outcome whenever an order's status
     changes (placed, cancelled, fulfilled) — this is the merchant's own
     order system telling MeraFraud what actually happened.
  2. MeraFraud keeps a running total_orders / cancelled_orders count per
     customer, scoped to that merchant.
  3. When /api/predict is called with a customer_id, MeraFraud checks this
     history. If the customer's cancellation rate is high, it adds a risk
     boost and a plain-language reason — on top of the ML model's score.

IMPORTANT: this is a rule-based adjustment layered on top of the ML score,
NOT something the trained model itself learned (we don't have historical
labeled cancellation data to train on yet). It's a transparent, tunable
business rule — see SERIAL_CANCELLER_* constants below.
"""

import json
import threading
from pathlib import Path
from datetime import datetime, timezone

HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "customer_history.json"
_lock = threading.Lock()

# Tunable thresholds for flagging a "serial canceller"
SERIAL_CANCELLER_MIN_ORDERS = 3       # need at least this many orders before judging a rate
SERIAL_CANCELLER_RATE_THRESHOLD = 0.4  # cancel rate above this is flagged
SERIAL_CANCELLER_SCORE_BOOST = 0.25    # added to the ML risk score when flagged


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load():
    if not HISTORY_PATH.exists():
        return {}
    with open(HISTORY_PATH) as f:
        return json.load(f)


def _save(data):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(data, f, indent=2)


def record_order_outcome(tenant_id: str, customer_id: str, outcome: str, order_id: str | None = None) -> dict:
    """outcome: 'placed' | 'cancelled' | 'fulfilled'"""
    with _lock:
        data = _load()
        data.setdefault(tenant_id, {})
        record = data[tenant_id].setdefault(customer_id, {
            "total_orders": 0,
            "cancelled_orders": 0,
            "fulfilled_orders": 0,
            "last_order_id": None,
            "last_outcome": None,
            "last_updated": None,
        })

        if outcome == "placed":
            record["total_orders"] += 1
        elif outcome == "cancelled":
            record["cancelled_orders"] += 1
        elif outcome == "fulfilled":
            record["fulfilled_orders"] += 1

        record["last_order_id"] = order_id
        record["last_outcome"] = outcome
        record["last_updated"] = _now()

        _save(data)
        return compute_risk(record)


def get_customer_history(tenant_id: str, customer_id: str) -> dict:
    data = _load()
    record = data.get(tenant_id, {}).get(customer_id)
    if not record:
        return {
            "total_orders": 0, "cancelled_orders": 0, "fulfilled_orders": 0,
            "cancellation_rate": 0.0, "is_serial_canceller": False,
        }
    return compute_risk(record)


def compute_risk(record: dict) -> dict:
    total = record["total_orders"]
    cancelled = record["cancelled_orders"]
    rate = (cancelled / total) if total > 0 else 0.0
    is_flagged = total >= SERIAL_CANCELLER_MIN_ORDERS and rate >= SERIAL_CANCELLER_RATE_THRESHOLD
    return {
        **record,
        "cancellation_rate": round(rate, 3),
        "is_serial_canceller": is_flagged,
    }


def apply_customer_risk_adjustment(base_score: float, customer_risk: dict) -> tuple[float, list[str]]:
    """Blends the customer's historical behavior into the ML model's score.
    Returns (adjusted_score, extra_reasons)."""
    if not customer_risk.get("is_serial_canceller"):
        return base_score, []

    rate_pct = round(customer_risk["cancellation_rate"] * 100)
    reason = f"Customer has cancelled {rate_pct}% of past orders ({customer_risk['cancelled_orders']}/{customer_risk['total_orders']})"
    adjusted = min(0.99, base_score + SERIAL_CANCELLER_SCORE_BOOST)
    return adjusted, [reason]
