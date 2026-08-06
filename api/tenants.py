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
            # ALTER ... IF NOT EXISTS so this is safe to run against a table
            # that was created before trial_ends_at existed (Render/Neon
            # already has live tenant rows from before this field existed).
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_ends_at TEXT")
            # subscription_status: trial | active | expired | cancelled.
            # 'trial' + a past trial_ends_at is what actually gates API access
            # now (see require_api_key in app.py) — 'active' means a human
            # (admin, until real billing exists) has confirmed this tenant is
            # a paying/approved account and should never be gated by the
            # trial clock. Existing rows default to 'trial', which means any
            # tenant whose 7-day trial already lapsed before this column
            # existed will be gated on next deploy unless marked active.
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'trial'")
            # Optional Slack/Discord/generic incoming-webhook URL — if set,
            # a block-level fraud alert is POSTed here in addition to (not
            # instead of) the existing email alert. NULL = disabled.
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS webhook_url TEXT")
            # Second key, sk_test_..., that authenticates exactly like the
            # live key but is flagged as test mode (see require_api_key in
            # app.py): usage isn't counted toward billing/usage counters and
            # it never triggers merchant-facing block alerts. Lets a
            # merchant integrate and test without polluting real stats.
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS api_key_test TEXT")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_api_key_test ON tenants (api_key_test)")
            # Tracks which trial-ending reminder emails have already gone out
            # for this tenant, as a comma-separated list of day-thresholds
            # (e.g. "3" or "3,1") — prevents the daily reminder job from
            # sending the same "3 days left" email twice if it runs more
            # than once, or re-sending after the tenant's trial_ends_at
            # changes for an unrelated reason.
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_reminders_sent TEXT NOT NULL DEFAULT ''")
            cur.execute("SELECT id FROM tenants WHERE api_key_test IS NULL")
            missing_test_key = [r[0] for r in cur.fetchall()]
            for tid in missing_test_key:
                cur.execute("UPDATE tenants SET api_key_test = %s WHERE id = %s", (_generate_test_key(), tid))
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


def _generate_test_key() -> str:
    return "sk_test_" + secrets.token_urlsafe(24)


def seed_demo_tenant():
    """Dashboard'un kutudan çıktığı gibi çalışması için sabit bir demo
    tenant + demo API anahtarı oluşturur (yoksa)."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE id = %s", ("demo",))
            row = cur.fetchone()
            if row:
                tenant = _row_to_tenant(row)
                # Self-heal: rows created before subscription_status existed
                # default to 'trial', but the demo account must never be
                # gated by the trial clock.
                if tenant.get("subscription_status") != "active":
                    cur.execute("UPDATE tenants SET subscription_status = 'active' WHERE id = 'demo'")
                    conn.commit()
                    tenant["subscription_status"] = "active"
                return tenant

            demo = {
                "id": "demo",
                "name": "MeraFraud Demo Store",
                "api_key": "sk_demo_merafraud_dashboard",
                "api_key_test": "sk_test_demo_merafraud_dashboard",
                "thresholds": {"block": 0.75, "review": 0.35},
                "created_at": _now(),
                "usage": {"total_calls": 0, "blocked": 0, "reviewed": 0, "approved": 0},
                "trial_ends_at": None,  # demo account, never expires
                "subscription_status": "active",
            }
            cur.execute("""
                INSERT INTO tenants (id, name, email, password_hash, reset_token,
                    reset_token_expires, api_key, api_key_test, thresholds, created_at, usage, trial_ends_at, subscription_status)
                VALUES (%s, %s, NULL, NULL, NULL, NULL, %s, %s, %s, %s, %s, NULL, %s)
            """, (demo["id"], demo["name"], demo["api_key"], demo["api_key_test"],
                  json.dumps(demo["thresholds"]), demo["created_at"], json.dumps(demo["usage"]),
                  demo["subscription_status"]))
        conn.commit()
        return demo
    finally:
        conn.close()


def create_tenant(name: str, thresholds: dict | None = None, email: str | None = None,
                   password: str | None = None, trial_days: int = 7) -> dict:
    """trial_days defaults to 7 to match the "7-day free trial" already
    advertised on the pricing page — public self-serve signup should never
    let the caller override this (see /api/tenants in app.py). For a longer
    pilot trial, create the tenant normally and then call set_trial_end()
    as an admin action."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            tenant_id = secrets.token_hex(6)
            trial_ends_at = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat() if trial_days else None
            tenant = {
                "id": tenant_id,
                "name": name,
                "email": email,
                "password_hash": generate_password_hash(password) if password else None,
                "reset_token": None,
                "reset_token_expires": None,
                "api_key": _generate_key(),
                "api_key_test": _generate_test_key(),
                "thresholds": thresholds or {"block": 0.75, "review": 0.35},
                "created_at": _now(),
                "usage": {"total_calls": 0, "blocked": 0, "reviewed": 0, "approved": 0},
                "trial_ends_at": trial_ends_at,
                "subscription_status": "trial",
            }
            cur.execute("""
                INSERT INTO tenants (id, name, email, password_hash, reset_token,
                    reset_token_expires, api_key, api_key_test, thresholds, created_at, usage, trial_ends_at, subscription_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant["id"], tenant["name"], tenant["email"], tenant["password_hash"],
                  tenant["reset_token"], tenant["reset_token_expires"], tenant["api_key"], tenant["api_key_test"],
                  json.dumps(tenant["thresholds"]), tenant["created_at"], json.dumps(tenant["usage"]),
                  tenant["trial_ends_at"], tenant["subscription_status"]))
        conn.commit()
        return tenant
    finally:
        conn.close()


def set_subscription_status(tenant_id: str, status: str) -> dict | None:
    """Admin action: manually mark a tenant's subscription status. This is
    the stand-in for real billing until a payment gateway is wired up —
    e.g. after confirming a bank transfer, an admin calls this with
    'active' so the tenant is never gated by the trial-expiry check in
    app.py's require_api_key. 'cancelled'/'expired' immediately gate access
    the same way a lapsed trial does."""
    if status not in ("trial", "active", "expired", "cancelled"):
        raise ValueError("status must be one of: trial, active, expired, cancelled")
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM tenants WHERE id = %s", (tenant_id,))
            if not cur.fetchone():
                return None
            cur.execute("UPDATE tenants SET subscription_status = %s WHERE id = %s", (status, tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            updated = cur.fetchone()
        conn.commit()
        return _row_to_tenant(updated)
    finally:
        conn.close()


def set_trial_end(tenant_id: str, days_from_now: int) -> dict | None:
    """Admin action: (re)set a tenant's trial end date, counted from today.
    Used to give a specific merchant (e.g. a pilot customer) a longer free
    period than the standard 7 days — pass e.g. 30 for "first month free"."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            trial_ends_at = (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat()
            cur.execute("UPDATE tenants SET trial_ends_at = %s WHERE id = %s",
                        (trial_ends_at, tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        conn.commit()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


TRIAL_REMINDER_DAY_THRESHOLDS = (3, 1)


def get_tenants_needing_trial_reminder() -> list[dict]:
    """Called once a day by a scheduled job (see the trial-reminders GitHub
    Action). Returns tenants still on 'trial' whose trial_ends_at falls
    within one of TRIAL_REMINDER_DAY_THRESHOLDS days from now, and who
    haven't already been sent that specific reminder — each returned row
    is tagged with '_reminder_day' so the caller knows which one to send.
    Tenants without an email on file are skipped; there's nowhere to send
    the reminder."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM tenants
                WHERE subscription_status = 'trial'
                  AND trial_ends_at IS NOT NULL
                  AND email IS NOT NULL
                  AND email != ''
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        tenant = _row_to_tenant(row)
        try:
            ends_at = datetime.fromisoformat(tenant["trial_ends_at"])
        except (TypeError, ValueError):
            continue
        already_sent = set((tenant.get("trial_reminders_sent") or "").split(",")) - {""}
        days_left = (ends_at - now).total_seconds() / 86400
        if days_left <= 0:
            continue  # already expired — the subscription gate handles this, not a reminder
        for threshold in TRIAL_REMINDER_DAY_THRESHOLDS:
            if str(threshold) in already_sent:
                continue
            # Fire once days_left drops to/below the threshold (covers the
            # job running slightly late or the trial length changing).
            if days_left <= threshold:
                tenant["_reminder_day"] = threshold
                due.append(tenant)
                break  # one reminder per tenant per run, even if multiple thresholds are overdue
    return due


def mark_trial_reminder_sent(tenant_id: str, day_threshold: int) -> None:
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT trial_reminders_sent FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            if not row:
                return
            sent = set((row["trial_reminders_sent"] or "").split(",")) - {""}
            sent.add(str(day_threshold))
            cur.execute("UPDATE tenants SET trial_reminders_sent = %s WHERE id = %s",
                        (",".join(sorted(sent)), tenant_id))
        conn.commit()
    finally:
        conn.close()


def get_tenant_by_key(api_key: str) -> dict | None:
    """Matches either the live key OR the test key. The returned dict carries
    an internal '_matched_key_mode' ('live' or 'test') so the caller (see
    require_api_key in app.py) knows which one was used — public_view()
    always strips this before it reaches an API response."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE api_key = %s OR api_key_test = %s", (api_key, api_key))
            row = cur.fetchone()
        if not row:
            return None
        tenant = _row_to_tenant(row)
        tenant["_matched_key_mode"] = "test" if tenant.get("api_key_test") == api_key else "live"
        return tenant
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


def update_webhook(tenant_id: str, webhook_url: str | None) -> dict | None:
    """Sets (or clears, if webhook_url is falsy) the tenant's Slack/Discord/
    generic incoming-webhook URL for block-level fraud alerts."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM tenants WHERE id = %s", (tenant_id,))
            if not cur.fetchone():
                return None
            cur.execute("UPDATE tenants SET webhook_url = %s WHERE id = %s",
                        (webhook_url or None, tenant_id))
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
    """Issues brand new live AND test keys, immediately invalidating both old
    ones — used when a key may have been leaked, or the merchant just wants
    to rotate. Requires the merchant to already be authenticated with the
    OLD key (or logged in via email+password) to call this."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE tenants SET api_key = %s, api_key_test = %s WHERE id = %s",
                        (_generate_key(), _generate_test_key(), tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        conn.commit()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def public_view(tenant: dict, reveal_api_key: bool = True) -> dict:
    """Strips fields that must NEVER leave the server in an API response —
    password_hash/reset_token are secrets, and _matched_key_mode is an
    internal detail set by get_tenant_by_key() for require_api_key's use,
    not client-facing data. Call this on every tenant dict right before
    jsonify()."""
    safe = {k: v for k, v in tenant.items()
            if k not in ("password_hash", "reset_token", "reset_token_expires", "_matched_key_mode")}
    if not reveal_api_key:
        if safe.get("api_key"):
            safe["api_key"] = safe["api_key"][:11] + "…" + safe["api_key"][-4:]
        if safe.get("api_key_test"):
            safe["api_key_test"] = safe["api_key_test"][:11] + "…" + safe["api_key_test"][-4:]
    return safe
