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

Data storage: PostgreSQL (DATABASE_URL) — same database tenants.py uses.
Previously this was a local JSON file, which meant every history record
was wiped on Render's free tier whenever the service redeployed or woke
from sleep. Moved here for the same reason tenants.py was moved.
"""

import os
import threading
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

_lock = threading.Lock()
_DB_INITIALIZED = False
_init_lock = threading.Lock()

# Tunable thresholds for flagging a "serial canceller"
SERIAL_CANCELLER_MIN_ORDERS = 3       # need at least this many orders before judging a rate
SERIAL_CANCELLER_RATE_THRESHOLD = 0.4  # cancel rate above this is flagged
SERIAL_CANCELLER_SCORE_BOOST = 0.25    # added to the ML risk score when flagged


def _now():
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it in your .env (local) or in "
            "Render's Environment tab (production) — see .env.example."
        )
    conn = psycopg2.connect(database_url)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _init_lock:
        if _DB_INITIALIZED:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_history (
                    tenant_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    total_orders INTEGER NOT NULL DEFAULT 0,
                    cancelled_orders INTEGER NOT NULL DEFAULT 0,
                    fulfilled_orders INTEGER NOT NULL DEFAULT 0,
                    last_order_id TEXT,
                    last_outcome TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (tenant_id, customer_id)
                )
            """)
        conn.commit()
        _DB_INITIALIZED = True


def record_order_outcome(tenant_id: str, customer_id: str, outcome: str, order_id: str | None = None) -> dict:
    """outcome: 'placed' | 'cancelled' | 'fulfilled'"""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM customer_history WHERE tenant_id = %s AND customer_id = %s",
                (tenant_id, customer_id),
            )
            row = cur.fetchone()

            total = row["total_orders"] if row else 0
            cancelled = row["cancelled_orders"] if row else 0
            fulfilled = row["fulfilled_orders"] if row else 0

            if outcome == "placed":
                total += 1
            elif outcome == "cancelled":
                cancelled += 1
            elif outcome == "fulfilled":
                fulfilled += 1

            now = _now()
            cur.execute("""
                INSERT INTO customer_history
                    (tenant_id, customer_id, total_orders, cancelled_orders, fulfilled_orders,
                     last_order_id, last_outcome, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, customer_id) DO UPDATE SET
                    total_orders = EXCLUDED.total_orders,
                    cancelled_orders = EXCLUDED.cancelled_orders,
                    fulfilled_orders = EXCLUDED.fulfilled_orders,
                    last_order_id = EXCLUDED.last_order_id,
                    last_outcome = EXCLUDED.last_outcome,
                    last_updated = EXCLUDED.last_updated
            """, (tenant_id, customer_id, total, cancelled, fulfilled, order_id, outcome, now))
        conn.commit()
        return compute_risk({
            "total_orders": total, "cancelled_orders": cancelled, "fulfilled_orders": fulfilled,
            "last_order_id": order_id, "last_outcome": outcome, "last_updated": now,
        })
    finally:
        conn.close()


def get_customer_history(tenant_id: str, customer_id: str) -> dict:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM customer_history WHERE tenant_id = %s AND customer_id = %s",
                (tenant_id, customer_id),
            )
            row = cur.fetchone()
        if not row:
            return {
                "total_orders": 0, "cancelled_orders": 0, "fulfilled_orders": 0,
                "cancellation_rate": 0.0, "is_serial_canceller": False,
            }
        return compute_risk(dict(row))
    finally:
        conn.close()


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
