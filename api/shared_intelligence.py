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

Data storage: PostgreSQL (DATABASE_URL) — same database tenants.py uses.
Previously this was a local JSON file, which meant the entire shared
blacklist was wiped on Render's free tier whenever the service redeployed
or woke from sleep — defeating the whole point of a network that's
supposed to accumulate over time. Moved here for the same reason
tenants.py was moved.
"""

import os
import hashlib
import json
import threading
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

_lock = threading.Lock()
_DB_INITIALIZED = False
_init_lock = threading.Lock()

# How many DIFFERENT tenants must report the same identifier before it's
# trusted as a network-wide signal (prevents one bad-faith report, or a
# single merchant's grudge, from blacklisting an innocent customer).
MIN_INDEPENDENT_REPORTS = 2
NETWORK_SCORE_BOOST = 0.30


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash_identifier(value: str) -> str:
    """Store a hash, not the raw value — so even this table doesn't hold
    plaintext emails/IPs at rest."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:24]


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
                CREATE TABLE IF NOT EXISTS shared_blacklist (
                    category TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    reporting_tenants JSONB NOT NULL,
                    first_reported TEXT NOT NULL,
                    last_reported TEXT NOT NULL,
                    PRIMARY KEY (category, key_hash)
                )
            """)
            # Anonymous, append-only event log — powers the "live feed" shown
            # in the dashboard. Deliberately stores NOTHING that could
            # identify which merchant reported what: no tenant_id, no
            # identifier value or hash, just a category and a timestamp.
            # This is intentionally separate from shared_blacklist (which
            # tracks current state per identifier) so the feed can show
            # every individual report as it happens, including repeat
            # reports of the same identifier by different merchants.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS network_events (
                    id SERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_network_events_id
                ON network_events (id DESC)
            """)
        conn.commit()
        _DB_INITIALIZED = True


def report_fraud(tenant_id: str, ip: str | None = None, email: str | None = None, device_id: str | None = None) -> dict:
    """A tenant confirms an identifier was involved in fraud. Safe to call
    multiple times — reports are deduplicated per tenant per identifier."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            reported = []
            now = _now()

            for category, raw_value in (("ips", ip), ("emails", email), ("devices", device_id)):
                if not raw_value:
                    continue
                key_hash = _hash_identifier(raw_value)

                cur.execute(
                    "SELECT reporting_tenants FROM shared_blacklist WHERE category = %s AND key_hash = %s",
                    (category, key_hash),
                )
                row = cur.fetchone()
                reporting_tenants = row["reporting_tenants"] if row else []
                if isinstance(reporting_tenants, str):
                    reporting_tenants = json.loads(reporting_tenants)

                if tenant_id not in reporting_tenants:
                    reporting_tenants.append(tenant_id)

                if row:
                    cur.execute("""
                        UPDATE shared_blacklist SET reporting_tenants = %s, last_reported = %s
                        WHERE category = %s AND key_hash = %s
                    """, (json.dumps(reporting_tenants), now, category, key_hash))
                else:
                    cur.execute("""
                        INSERT INTO shared_blacklist (category, key_hash, reporting_tenants, first_reported, last_reported)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (category, key_hash, json.dumps(reporting_tenants), now, now))

                reported.append({"category": category, "report_count": len(reporting_tenants)})

                # Log an anonymous event for the live feed — one row per
                # category reported in this call, no tenant/identifier info.
                cur.execute(
                    "INSERT INTO network_events (category, created_at) VALUES (%s, %s)",
                    (category, now),
                )

            # Keep the event log from growing forever — the feed only ever
            # shows the most recent ~50, so trim well above that.
            cur.execute("""
                DELETE FROM network_events WHERE id NOT IN (
                    SELECT id FROM network_events ORDER BY id DESC LIMIT 1000
                )
            """)

        conn.commit()
        return {"status": "ok", "reported": reported}
    finally:
        conn.close()


def recent_events(limit: int = 30) -> list[dict]:
    """Anonymous, real-time-ish feed of fraud reports across the whole
    network. Only ever returns a category and a timestamp — never which
    merchant reported it or what the identifier was."""
    limit = max(1, min(limit, 100))
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT category, created_at FROM network_events ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        return [{"category": r["category"], "created_at": r["created_at"]} for r in rows]
    finally:
        conn.close()


def check_identifier(ip: str | None = None, email: str | None = None, device_id: str | None = None) -> dict:
    """Checks whether any submitted identifier is flagged by the network.
    Returns which ones matched and how many independent tenants reported them."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            matches = []
            for category, raw_value in (("ips", ip), ("emails", email), ("devices", device_id)):
                if not raw_value:
                    continue
                key_hash = _hash_identifier(raw_value)
                cur.execute(
                    "SELECT reporting_tenants FROM shared_blacklist WHERE category = %s AND key_hash = %s",
                    (category, key_hash),
                )
                row = cur.fetchone()
                if not row:
                    continue
                reporting_tenants = row["reporting_tenants"]
                if isinstance(reporting_tenants, str):
                    reporting_tenants = json.loads(reporting_tenants)
                if len(reporting_tenants) >= MIN_INDEPENDENT_REPORTS:
                    matches.append({"category": category, "report_count": len(reporting_tenants)})

        return {"is_flagged": len(matches) > 0, "matches": matches}
    finally:
        conn.close()


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
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            counts = {}
            for category in ("ips", "emails", "devices"):
                cur.execute("SELECT COUNT(*) FROM shared_blacklist WHERE category = %s", (category,))
                counts[category] = cur.fetchone()[0]
        return {
            "total_reported_ips": counts["ips"],
            "total_reported_emails": counts["emails"],
            "total_reported_devices": counts["devices"],
        }
    finally:
        conn.close()
