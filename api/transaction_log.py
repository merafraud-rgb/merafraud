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
        conn.commit()
        _DB_INITIALIZED = True


def log_transaction(tenant_id: str, row: dict, risk_score: float, risk_level: str, customer_id: str | None):
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO transaction_logs
                    (tenant_id, scored_at, customer_id, transaction_amount, risk_score, risk_level)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (tenant_id, _now(), customer_id or "", row.get("transaction_amount"),
                  round(risk_score, 4), risk_level))
        conn.commit()
    finally:
        conn.close()


def get_transactions(tenant_id: str) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT scored_at, customer_id, transaction_amount, risk_score, risk_level
                FROM transaction_logs WHERE tenant_id = %s ORDER BY scored_at
            """, (tenant_id,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def to_csv(tenant_id: str) -> str:
    rows = get_transactions(tenant_id)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["scored_at", "customer_id", "transaction_amount", "risk_score", "risk_level"])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
