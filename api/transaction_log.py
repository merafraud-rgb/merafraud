"""
MeraFraud - Transaction Log & Report Export
-----------------------------------------------
Keeps a log of scored transactions per tenant, so merchants can export
their history as a CSV report (Settings page → "Export Report"), and so
there's a growing, real dataset to eventually retrain the fraud model on
(see model/train_model.py and README's "Model yeniden eğitimi" section).

This now lives in PostgreSQL (same DATABASE_URL as tenants.py) instead of
a JSON file — on Render's free tier, a JSON file on disk gets wiped on
every redeploy / sleep-wake cycle, which would have silently thrown away
exactly the real-world data this log exists to eventually train on.
"""

import os
import csv
import io
import threading
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

_lock = threading.Lock()

_DB_INITIALIZED = False
_init_lock = threading.Lock()


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
                CREATE TABLE IF NOT EXISTS transaction_logs (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    scored_at TEXT NOT NULL,
                    customer_id TEXT,
                    transaction_amount DOUBLE PRECISION,
                    risk_score DOUBLE PRECISION,
                    risk_level TEXT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_transaction_logs_tenant
                ON transaction_logs (tenant_id, scored_at)
            """)
            # Optional customer identity/location, carried over from the
            # /api/predict payload (see app.py) — lets a merchant's exported
            # CSV report and the customer-lookup panel in Settings show WHO
            # and roughly WHERE a flagged transaction came from, not just an
            # opaque customer_id. Never required; a merchant that doesn't
            # send these fields just gets NULLs here, same as before.
            cur.execute("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS customer_name TEXT")
            cur.execute("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS billing_country TEXT")
            cur.execute("ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS billing_city TEXT")
        conn.commit()
        _DB_INITIALIZED = True


def log_transaction(tenant_id: str, row: dict, risk_score: float, risk_level: str, customer_id: str | None,
                     customer_name: str | None = None, billing_country: str | None = None,
                     billing_city: str | None = None):
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO transaction_logs
                    (tenant_id, scored_at, customer_id, transaction_amount, risk_score, risk_level,
                     customer_name, billing_country, billing_city)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, _now(), customer_id or "", row.get("transaction_amount"),
                  round(risk_score, 4), risk_level, customer_name, billing_country, billing_city))
        conn.commit()
    finally:
        conn.close()


def get_transactions(tenant_id: str) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT scored_at, customer_id, customer_name, billing_country, billing_city,
                       transaction_amount, risk_score, risk_level
                FROM transaction_logs WHERE tenant_id = %s ORDER BY scored_at
            """, (tenant_id,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_weekly_summary(tenant_id: str) -> dict:
    """Aggregates this tenant's last 7 days of scored transactions for the
    weekly digest email (see email_service.send_weekly_digest_email /
    POST /api/internal/send-weekly-digest in app.py). 'amount_protected' is
    the sum of transaction_amount across blocked transactions only — a
    simple, defensible "money kept out of a fraudster's hands" estimate,
    not a claim about actual chargebacks avoided."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT risk_level, COUNT(*) AS n, COALESCE(SUM(transaction_amount), 0) AS amount
                FROM transaction_logs
                WHERE tenant_id = %s AND scored_at::timestamptz >= (NOW() - INTERVAL '7 days')
                GROUP BY risk_level
            """, (tenant_id,))
            rows = cur.fetchall()
    finally:
        conn.close()

    counts = {"approve": 0, "review": 0, "block": 0}
    amount_protected = 0.0
    for r in rows:
        level = r["risk_level"]
        if level in counts:
            counts[level] = r["n"]
        if level == "block":
            amount_protected = float(r["amount"] or 0)

    total = counts["approve"] + counts["review"] + counts["block"]
    return {
        "total": total,
        "approved": counts["approve"],
        "reviewed": counts["review"],
        "blocked": counts["block"],
        "amount_protected": round(amount_protected, 2),
    }


def to_csv(tenant_id: str) -> str:
    rows = get_transactions(tenant_id)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "scored_at", "customer_id", "customer_name", "billing_country", "billing_city",
        "transaction_amount", "risk_score", "risk_level",
    ])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
