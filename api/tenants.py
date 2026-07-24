"""
MeraFraud - Multi-Tenant Yönetimi
------------------------------------
Her KOBİ müşterisi (tenant) kendi API anahtarına, kendi risk eşiklerine ve
kendi kullanım istatistiklerine sahiptir. Tenant verisi artık PostgreSQL'de
tutulur (DATABASE_URL ortam değişkeninden bağlanılır) — böylece Render'ın
free tier'ında her redeploy/uyku-sonrası-uyanmada dosyanın sıfırlanması
sorunu ortadan kalkar. Dışa açılan fonksiyonların (interface) hepsi
eskisiyle (tenants.json sürümüyle) birebir aynı, yani api/app.py içinde
hiçbir değişiklik gerekmedi.

Bir tenant şu bilgileri taşır:
  - id, name, api_key (sk_live_...)
  - thresholds: {block, review}  -> merchant'a özel risk toleransı
  - created_at
  - usage: {total_calls, blocked, reviewed, approved}  -> gerçek kullanım sayaçları
"""

import os
import json
import secrets
import threading
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg2
import psycopg2.extras

_lock = threading.Lock()
RESET_TOKEN_TTL_MINUTES = 60

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
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT,
                    password_hash TEXT,
                    reset_token TEXT,
                    reset_token_expires TEXT,
                    api_key TEXT UNIQUE NOT NULL,
                    thresholds JSONB NOT NULL,
                    created_at TEXT NOT NULL,
                    usage JSONB NOT NULL
                )
            """)
        conn.commit()
        _DB_INITIALIZED = True


def _row_to_tenant(row) -> dict:
    """Rows come back with jsonb columns already parsed into dict/list by
    psycopg2, but we defensively json.loads() in case a driver ever hands
    us a raw string instead."""
    tenant = dict(row)
    for key in ("thresholds", "usage"):
        if isinstance(tenant.get(key), str):
            tenant[key] = json.loads(tenant[key])
    return tenant


def _generate_key() -> str:
    return "sk_live_" + secrets.token_urlsafe(24)


def seed_demo_tenant():
    """Dashboard'un kutudan çıktığı gibi çalışması için sabit bir demo
    tenant + demo API anahtarı oluşturur (yoksa)."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE id = %s", ("demo",))
            row = cur.fetchone()
            if row:
                return _row_to_tenant(row)

            demo = {
                "id": "demo",
                "name": "MeraFraud Demo Store",
                "api_key": "sk_demo_merafraud_dashboard",
                "thresholds": {"block": 0.75, "review": 0.35},
                "created_at": _now(),
                "usage": {"total_calls": 0, "blocked": 0, "reviewed": 0, "approved": 0},
            }
            cur.execute("""
                INSERT INTO tenants (id, name, email, password_hash, reset_token,
                    reset_token_expires, api_key, thresholds, created_at, usage)
                VALUES (%s, %s, NULL, NULL, NULL, NULL, %s, %s, %s, %s)
            """, (demo["id"], demo["name"], demo["api_key"],
                  json.dumps(demo["thresholds"]), demo["created_at"], json.dumps(demo["usage"])))
        conn.commit()
        return demo
    finally:
        conn.close()


def create_tenant(name: str, thresholds: dict | None = None, email: str | None = None, password: str | None = None) -> dict:
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            tenant_id = secrets.token_hex(6)
            tenant = {
                "id": tenant_id,
                "name": name,
                "email": email,
                "password_hash": generate_password_hash(password) if password else None,
                "reset_token": None,
                "reset_token_expires": None,
                "api_key": _generate_key(),
                "thresholds": thresholds or {"block": 0.75, "review": 0.35},
                "created_at": _now(),
                "usage": {"total_calls": 0, "blocked": 0, "reviewed": 0, "approved": 0},
            }
            cur.execute("""
                INSERT INTO tenants (id, name, email, password_hash, reset_token,
                    reset_token_expires, api_key, thresholds, created_at, usage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant["id"], tenant["name"], tenant["email"], tenant["password_hash"],
                  tenant["reset_token"], tenant["reset_token_expires"], tenant["api_key"],
                  json.dumps(tenant["thresholds"]), tenant["created_at"], json.dumps(tenant["usage"])))
        conn.commit()
        return tenant
    finally:
        conn.close()


def get_tenant_by_key(api_key: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE api_key = %s", (api_key,))
            row = cur.fetchone()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def list_tenants() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants ORDER BY created_at")
            rows = cur.fetchall()
        return [public_view(_row_to_tenant(r), reveal_api_key=False) for r in rows]
    finally:
        conn.close()


def record_usage(tenant_id: str, level: str):
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT usage FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            if not row:
                return
            usage = row["usage"] if isinstance(row["usage"], dict) else json.loads(row["usage"])
            usage["total_calls"] += 1
            key = {"block": "blocked", "review": "reviewed", "approve": "approved"}.get(level)
            if key:
                usage[key] += 1
            cur.execute("UPDATE tenants SET usage = %s WHERE id = %s", (json.dumps(usage), tenant_id))
        conn.commit()
    finally:
        conn.close()


def update_thresholds(tenant_id: str, thresholds: dict) -> dict | None:
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM tenants WHERE id = %s", (tenant_id,))
            if not cur.fetchone():
                return None
            cur.execute("UPDATE tenants SET thresholds = %s WHERE id = %s",
                        (json.dumps(thresholds), tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            updated = cur.fetchone()
        conn.commit()
        return _row_to_tenant(updated)
    finally:
        conn.close()


def get_tenant_by_email(email: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE lower(email) = lower(%s)", (email,))
            row = cur.fetchone()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def login(email: str, password: str) -> dict | None:
    """Returns the full tenant record (including the real api_key — this is
    how a merchant recovers a lost key: log in with email + password)."""
    tenant = get_tenant_by_email(email)
    if not tenant or not tenant.get("password_hash"):
        return None
    if not check_password_hash(tenant["password_hash"], password):
        return None
    return tenant


def set_password(tenant_id: str, password: str) -> dict | None:
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE tenants SET password_hash = %s WHERE id = %s",
                        (generate_password_hash(password), tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        conn.commit()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def request_password_reset(email: str) -> str | None:
    """Generates a reset token for the account with this email.
    Returns the raw token (caller decides how to deliver it — in this MVP
    there's no real email sending configured, so the API returns it
    directly, clearly marked as a demo-only shortcut)."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM tenants WHERE lower(email) = lower(%s)", (email,))
            row = cur.fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(24)
            expires = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat()
            cur.execute("UPDATE tenants SET reset_token = %s, reset_token_expires = %s WHERE id = %s",
                        (token, expires, row["id"]))
        conn.commit()
        return token
    finally:
        conn.close()


def reset_password_with_token(token: str, new_password: str) -> dict | None:
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE reset_token = %s", (token,))
            row = cur.fetchone()
            if not row:
                return None
            expires = row.get("reset_token_expires")
            if not expires or datetime.fromisoformat(expires) < datetime.now(timezone.utc):
                return None  # token expired
            cur.execute("""
                UPDATE tenants SET password_hash = %s, reset_token = NULL, reset_token_expires = NULL
                WHERE id = %s
            """, (generate_password_hash(new_password), row["id"]))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (row["id"],))
            updated = cur.fetchone()
        conn.commit()
        return _row_to_tenant(updated)
    finally:
        conn.close()


def regenerate_api_key(tenant_id: str) -> dict | None:
    """Issues a brand new API key and immediately invalidates the old one —
    used when a key may have been leaked, or the merchant just wants to
    rotate it. Requires the merchant to already be authenticated with the
    OLD key (or logged in via email+password) to call this."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE tenants SET api_key = %s WHERE id = %s",
                        (_generate_key(), tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        conn.commit()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def public_view(tenant: dict, reveal_api_key: bool = True) -> dict:
    """Strips fields that must NEVER leave the server in an API response —
    password_hash and reset_token are secrets, not client-facing data.
    Call this on every tenant dict right before jsonify()."""
    safe = {k: v for k, v in tenant.items()
            if k not in ("password_hash", "reset_token", "reset_token_expires")}
    if not reveal_api_key and "api_key" in safe:
        safe["api_key"] = safe["api_key"][:11] + "…" + safe["api_key"][-4:]
    return safe
