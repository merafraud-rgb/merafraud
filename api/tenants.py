"""
MeraFraud - Multi-Tenant Yönetimi
------------------------------------
Her KOBİ müşterisi (tenant) kendi API anahtarına, kendi risk eşiklerine ve
kendi kullanım istatistiklerine sahiptir. Bu MVP'de tenant verisi basit bir
JSON dosyasında tutulur (tenants.json) — gerçek üretimde bu bir veritabanı
(PostgreSQL) olmalıdır, ancak arayüz (interface) aynı kalacağı için ileride
sadece bu dosyanın içini değiştirmek yeterli olur.

Bir tenant şu bilgileri taşır:
  - id, name, api_key (sk_live_...)
  - thresholds: {block, review}  -> merchant'a özel risk toleransı
  - created_at
  - usage: {total_calls, blocked, reviewed, approved}  -> gerçek kullanım sayaçları
"""

import json
import secrets
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

TENANTS_PATH = Path(__file__).resolve().parent.parent / "data" / "tenants.json"
_lock = threading.Lock()
RESET_TOKEN_TTL_MINUTES = 60


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load():
    if not TENANTS_PATH.exists():
        return {"tenants": {}}
    with open(TENANTS_PATH) as f:
        return json.load(f)


def _save(data):
    TENANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TENANTS_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _generate_key() -> str:
    return "sk_live_" + secrets.token_urlsafe(24)


def seed_demo_tenant():
    """Dashboard'un kutudan çıktığı gibi çalışması için sabit bir demo
    tenant + demo API anahtarı oluşturur (yoksa)."""
    data = _load()
    if "demo" not in data["tenants"]:
        data["tenants"]["demo"] = {
            "id": "demo",
            "name": "MeraFraud Demo Store",
            "api_key": "sk_demo_merafraud_dashboard",
            "thresholds": {"block": 0.75, "review": 0.35},
            "created_at": _now(),
            "usage": {"total_calls": 0, "blocked": 0, "reviewed": 0, "approved": 0},
        }
        _save(data)
    return data["tenants"]["demo"]


def create_tenant(name: str, thresholds: dict | None = None, email: str | None = None, password: str | None = None) -> dict:
    with _lock:
        data = _load()
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
        data["tenants"][tenant_id] = tenant
        _save(data)
        return tenant


def get_tenant_by_key(api_key: str) -> dict | None:
    data = _load()
    for tenant in data["tenants"].values():
        if tenant["api_key"] == api_key:
            return tenant
    return None


def list_tenants() -> list:
    data = _load()
    return [public_view(t, reveal_api_key=False) for t in data["tenants"].values()]


def record_usage(tenant_id: str, level: str):
    with _lock:
        data = _load()
        if tenant_id not in data["tenants"]:
            return
        usage = data["tenants"][tenant_id]["usage"]
        usage["total_calls"] += 1
        key = {"block": "blocked", "review": "reviewed", "approve": "approved"}.get(level)
        if key:
            usage[key] += 1
        _save(data)


def update_thresholds(tenant_id: str, thresholds: dict) -> dict | None:
    with _lock:
        data = _load()
        if tenant_id not in data["tenants"]:
            return None
        data["tenants"][tenant_id]["thresholds"] = thresholds
        _save(data)
        return data["tenants"][tenant_id]


def get_tenant_by_email(email: str) -> dict | None:
    data = _load()
    for tenant in data["tenants"].values():
        if tenant.get("email") and tenant["email"].lower() == email.lower():
            return tenant
    return None


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
    with _lock:
        data = _load()
        if tenant_id not in data["tenants"]:
            return None
        data["tenants"][tenant_id]["password_hash"] = generate_password_hash(password)
        _save(data)
        return data["tenants"][tenant_id]


def request_password_reset(email: str) -> str | None:
    """Generates a reset token for the account with this email.
    Returns the raw token (caller decides how to deliver it — in this MVP
    there's no real email sending configured, so the API returns it
    directly, clearly marked as a demo-only shortcut)."""
    with _lock:
        data = _load()
        tenant_id = None
        for tid, t in data["tenants"].items():
            if t.get("email") and t["email"].lower() == email.lower():
                tenant_id = tid
                break
        if not tenant_id:
            return None

        token = secrets.token_urlsafe(24)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat()
        data["tenants"][tenant_id]["reset_token"] = token
        data["tenants"][tenant_id]["reset_token_expires"] = expires
        _save(data)
        return token


def reset_password_with_token(token: str, new_password: str) -> dict | None:
    with _lock:
        data = _load()
        for tid, t in data["tenants"].items():
            if t.get("reset_token") == token:
                expires = t.get("reset_token_expires")
                if not expires or datetime.fromisoformat(expires) < datetime.now(timezone.utc):
                    return None  # token expired
                t["password_hash"] = generate_password_hash(new_password)
                t["reset_token"] = None
                t["reset_token_expires"] = None
                _save(data)
                return t
        return None


def regenerate_api_key(tenant_id: str) -> dict | None:
    """Issues a brand new API key and immediately invalidates the old one —
    used when a key may have been leaked, or the merchant just wants to
    rotate it. Requires the merchant to already be authenticated with the
    OLD key (or logged in via email+password) to call this."""
    with _lock:
        data = _load()
        if tenant_id not in data["tenants"]:
            return None
        data["tenants"][tenant_id]["api_key"] = _generate_key()
        _save(data)
        return data["tenants"][tenant_id]


def public_view(tenant: dict, reveal_api_key: bool = True) -> dict:
    """Strips fields that must NEVER leave the server in an API response —
    password_hash and reset_token are secrets, not client-facing data.
    Call this on every tenant dict right before jsonify()."""
    safe = {k: v for k, v in tenant.items()
            if k not in ("password_hash", "reset_token", "reset_token_expires")}
    if not reveal_api_key and "api_key" in safe:
        safe["api_key"] = safe["api_key"][:11] + "…" + safe["api_key"][-4:]
    return safe
