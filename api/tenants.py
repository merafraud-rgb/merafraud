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
            # Team accounts: additional people who can sign in to ONE tenant's
            # dashboard under their own email/password, without sharing the
            # owner's login. They authenticate as themselves but act through
            # the same tenant's api_key underneath (see login() below) — no
            # separate per-request session system exists yet, so role
            # enforcement (admin vs viewer) is done in the dashboard UI, not
            # re-checked on every API call. Good enough for a small-team MVP;
            # tightening that is a deliberate later step, not an oversight.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_members (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    email TEXT NOT NULL,
                    password_hash TEXT,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    status TEXT NOT NULL DEFAULT 'invited',
                    invite_token TEXT,
                    invite_token_expires TEXT,
                    invited_by TEXT,
                    created_at TEXT NOT NULL,
                    accepted_at TEXT
                )
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_email ON team_members (lower(email))")

            # Admin audit log — every action taken from the internal admin
            # panel (extend trial, change subscription status, etc.) is
            # recorded here. ADMIN_API_KEY is a single shared secret (see
            # app.py), so this can't attribute an action to a specific
            # person — only "the admin panel did X to tenant Y at time Z" —
            # but a timestamped trail beats none at all.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id SERIAL PRIMARY KEY,
                    action TEXT NOT NULL,
                    tenant_id TEXT,
                    detail TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Referral program: every tenant gets a short unique code at
            # creation. A signup that arrives with ?ref=<code> (see POST
            # /api/tenants in app.py) is linked back via referred_by, and
            # both sides get a trial extension (see apply_referral_bonus).
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS referral_code TEXT")
            cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS referred_by TEXT")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_referral_code ON tenants (referral_code)")

            cur.execute("SELECT id FROM tenants WHERE api_key_test IS NULL")
            missing_test_key = [r[0] for r in cur.fetchall()]
            for tid in missing_test_key:
                cur.execute("UPDATE tenants SET api_key_test = %s WHERE id = %s", (_generate_test_key(), tid))

            cur.execute("SELECT id FROM tenants WHERE referral_code IS NULL")
            missing_ref_code = [r[0] for r in cur.fetchall()]
            for tid in missing_ref_code:
                cur.execute("UPDATE tenants SET referral_code = %s WHERE id = %s", (_generate_referral_code(), tid))
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


def _generate_referral_code() -> str:
    return secrets.token_hex(4).upper()


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
                "referral_code": _generate_referral_code(),
            }
            cur.execute("""
                INSERT INTO tenants (id, name, email, password_hash, reset_token,
                    reset_token_expires, api_key, api_key_test, thresholds, created_at, usage, trial_ends_at, subscription_status, referral_code)
                VALUES (%s, %s, NULL, NULL, NULL, NULL, %s, %s, %s, %s, %s, NULL, %s, %s)
            """, (demo["id"], demo["name"], demo["api_key"], demo["api_key_test"],
                  json.dumps(demo["thresholds"]), demo["created_at"], json.dumps(demo["usage"]),
                  demo["subscription_status"], demo["referral_code"]))
        conn.commit()
        return demo
    finally:
        conn.close()


def create_tenant(name: str, thresholds: dict | None = None, email: str | None = None,
                   password: str | None = None, trial_days: int = 7,
                   referred_by: str | None = None) -> dict:
    """trial_days defaults to 7 to match the "7-day free trial" already
    advertised on the pricing page — public self-serve signup should never
    let the caller override this (see /api/tenants in app.py). For a longer
    pilot trial, create the tenant normally and then call set_trial_end()
    as an admin action.

    referred_by is the referring tenant's id (resolved from a ?ref=<code>
    query param in app.py via get_tenant_by_referral_code) — just recorded
    here; the actual trial-extension reward is applied separately by
    apply_referral_bonus() once the row exists."""
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
                "referral_code": _generate_referral_code(),
                "referred_by": referred_by,
            }
            cur.execute("""
                INSERT INTO tenants (id, name, email, password_hash, reset_token,
                    reset_token_expires, api_key, api_key_test, thresholds, created_at, usage, trial_ends_at, subscription_status,
                    referral_code, referred_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant["id"], tenant["name"], tenant["email"], tenant["password_hash"],
                  tenant["reset_token"], tenant["reset_token_expires"], tenant["api_key"], tenant["api_key_test"],
                  json.dumps(tenant["thresholds"]), tenant["created_at"], json.dumps(tenant["usage"]),
                  tenant["trial_ends_at"], tenant["subscription_status"],
                  tenant["referral_code"], tenant["referred_by"]))
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


def log_admin_action(action: str, tenant_id: str | None, detail: str = "") -> None:
    """Appends one row to admin_audit_log. Called from app.py right after an
    admin-key-gated action (extend trial, change subscription status)
    succeeds — never called on a failed/rejected attempt, since those never
    reach the point of having anything to log."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO admin_audit_log (action, tenant_id, detail, created_at)
                VALUES (%s, %s, %s, %s)
            """, (action, tenant_id, detail, _now()))
        conn.commit()
    finally:
        conn.close()


def get_admin_audit_log(limit: int = 200) -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, action, tenant_id, detail, created_at
                FROM admin_audit_log ORDER BY id DESC LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
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


def get_tenant_by_id(tenant_id: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        return _row_to_tenant(row) if row else None
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


REFERRAL_BONUS_DAYS = 30


def get_tenant_by_referral_code(code: str) -> dict | None:
    code = (code or "").strip()
    if not code:
        return None
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE upper(referral_code) = upper(%s)", (code,))
            row = cur.fetchone()
        return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def apply_referral_bonus(referrer_id: str, referred_id: str, bonus_days: int = REFERRAL_BONUS_DAYS) -> None:
    """Extends both sides' trial by bonus_days once a referred signup
    completes. Only meaningful for tenants still on the 'trial' clock — an
    'active' tenant is never gated by trial_ends_at (see
    _check_subscription_gate in app.py), so extending it there would be an
    invisible no-op; those are skipped rather than writing a number nobody
    will ever see enforced. Extends from whichever is later — today, or the
    tenant's current trial end — so a referral never wastes days someone
    already has left."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for tid in (referrer_id, referred_id):
                cur.execute("SELECT trial_ends_at, subscription_status FROM tenants WHERE id = %s", (tid,))
                row = cur.fetchone()
                if not row or row["subscription_status"] != "trial":
                    continue
                base = datetime.now(timezone.utc)
                if row["trial_ends_at"]:
                    try:
                        current_end = datetime.fromisoformat(row["trial_ends_at"])
                        if current_end.tzinfo is None:
                            current_end = current_end.replace(tzinfo=timezone.utc)
                        if current_end > base:
                            base = current_end
                    except ValueError:
                        pass
                new_end = (base + timedelta(days=bonus_days)).isoformat()
                cur.execute("UPDATE tenants SET trial_ends_at = %s WHERE id = %s", (new_end, tid))
        conn.commit()
    finally:
        conn.close()


def get_referral_stats(tenant_id: str) -> dict:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT referral_code FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            code = row["referral_code"] if row else None
            cur.execute("SELECT COUNT(*) AS n FROM tenants WHERE referred_by = %s", (tenant_id,))
            count = cur.fetchone()["n"]
        return {"referral_code": code, "referral_count": count}
    finally:
        conn.close()


def login(email: str, password: str) -> dict | None:
    """Returns the full tenant record (including the real api_key — this is
    how a merchant recovers a lost key: log in with email + password).

    If the email belongs to the tenant owner, this is unchanged from before.
    If it belongs to an invited team member instead, we still return the
    SAME parent tenant record — same api_key, same dashboard data — but with
    an extra '_member' key describing who actually logged in and at what
    role, so app.py/the frontend can show "signed in as X (Admin/Viewer)"
    and hide owner-only controls without needing a whole second data model."""
    tenant = get_tenant_by_email(email)
    if tenant and tenant.get("password_hash") and check_password_hash(tenant["password_hash"], password):
        return tenant

    member = get_team_member_by_email(email)
    if not member or not member.get("password_hash"):
        return None
    if not check_password_hash(member["password_hash"], password):
        return None

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE id = %s", (member["tenant_id"],))
            tenant_row = cur.fetchone()
        if not tenant_row:
            return None
        tenant = _row_to_tenant(tenant_row)
        tenant["_member"] = {"id": member["id"], "email": member["email"], "role": member["role"]}
        return tenant
    finally:
        conn.close()


TEAM_INVITE_TTL_MINUTES = 60 * 24 * 7  # a week to accept before the link expires
TEAM_ROLES = ("admin", "viewer")


def _member_email_taken(email: str) -> bool:
    """An email can only belong to one identity across all of MeraFraud —
    either a tenant owner or a team member, never both — so login-by-email
    always resolves to exactly one account."""
    if get_tenant_by_email(email):
        return True
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM team_members WHERE lower(email) = lower(%s)", (email,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def create_team_invite(tenant_id: str, email: str, role: str, invited_by: str | None = None) -> dict:
    """Owner invites a teammate by email. This only creates a pending row
    with a signup token — the teammate isn't a real login until they open
    the invite link and set a password (see accept_team_invite)."""
    email = (email or "").strip()
    if not email:
        raise ValueError("email is required")
    if role not in TEAM_ROLES:
        raise ValueError(f"role must be one of: {', '.join(TEAM_ROLES)}")
    if _member_email_taken(email):
        raise ValueError("That email is already in use on MeraFraud (as an account owner or another team's member).")

    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            member_id = secrets.token_hex(8)
            token = secrets.token_urlsafe(24)
            expires = (datetime.now(timezone.utc) + timedelta(minutes=TEAM_INVITE_TTL_MINUTES)).isoformat()
            cur.execute("""
                INSERT INTO team_members (id, tenant_id, email, role, status, invite_token,
                    invite_token_expires, invited_by, created_at)
                VALUES (%s, %s, %s, %s, 'invited', %s, %s, %s, %s)
            """, (member_id, tenant_id, email, role, token, expires, invited_by, _now()))
        conn.commit()
        return {
            "id": member_id, "tenant_id": tenant_id, "email": email, "role": role,
            "status": "invited", "invite_token": token, "invite_token_expires": expires,
        }
    finally:
        conn.close()


def get_team_members(tenant_id: str) -> list[dict]:
    """Never includes password_hash or invite_token — this is meant to be
    shown directly in the dashboard's Team panel."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, tenant_id, email, role, status, invited_by, created_at, accepted_at
                FROM team_members WHERE tenant_id = %s ORDER BY created_at ASC
            """, (tenant_id,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_team_member_by_email(email: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM team_members WHERE lower(email) = lower(%s) AND status = 'active'", (email,))
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_invite_by_token(token: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM team_members WHERE invite_token = %s", (token,))
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def accept_team_invite(token: str, password: str) -> dict | None:
    """Returns {'member': {...}, 'tenant': {...}} on success, None if the
    token is invalid, expired, or already used."""
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM team_members WHERE invite_token = %s", (token,))
            row = cur.fetchone()
            if not row or row["status"] != "invited":
                return None
            expires = row.get("invite_token_expires")
            if not expires or datetime.fromisoformat(expires) < datetime.now(timezone.utc):
                return None
            cur.execute("""
                UPDATE team_members SET password_hash = %s, status = 'active',
                    invite_token = NULL, invite_token_expires = NULL, accepted_at = %s
                WHERE id = %s
            """, (generate_password_hash(password), _now(), row["id"]))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (row["tenant_id"],))
            tenant_row = cur.fetchone()
        conn.commit()
        if not tenant_row:
            return None
        member = dict(row)
        member["status"] = "active"
        member.pop("password_hash", None)
        member.pop("invite_token", None)
        return {"member": member, "tenant": _row_to_tenant(tenant_row)}
    finally:
        conn.close()


def remove_team_member(tenant_id: str, member_id: str) -> bool:
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            cur.execute("DELETE FROM team_members WHERE id = %s AND tenant_id = %s", (member_id, tenant_id))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


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


def change_email(tenant_id: str, new_email: str, current_password: str) -> dict:
    """Owner-only self-service email change. A team member's own password
    won't match the owner's password_hash checked here, so this naturally
    can't be used by an invited teammate to change the account's registered
    (owner) email — only the owner's own login credential works. Raises
    ValueError with a user-facing message on any failure, which app.py
    returns directly as the API error."""
    new_email = (new_email or "").strip()
    if not new_email or "@" not in new_email:
        raise ValueError("Please provide a valid email address.")
    conn = _get_conn()
    try:
        with _lock, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            if not row or not row.get("password_hash"):
                raise ValueError("This account has no password set yet — set one first, then change your email.")
            if not check_password_hash(row["password_hash"], current_password):
                raise ValueError("Current password is incorrect.")
            cur.execute("SELECT 1 FROM tenants WHERE lower(email) = lower(%s) AND id != %s", (new_email, tenant_id))
            if cur.fetchone():
                raise ValueError("That email is already in use by another MeraFraud account.")
            cur.execute("SELECT 1 FROM team_members WHERE lower(email) = lower(%s)", (new_email,))
            if cur.fetchone():
                raise ValueError("That email is already in use by another MeraFraud account.")
            cur.execute("UPDATE tenants SET email = %s WHERE id = %s", (new_email, tenant_id))
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            updated = cur.fetchone()
        conn.commit()
        return _row_to_tenant(updated)
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
