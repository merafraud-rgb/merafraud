"""
MeraFraud API
----------------
A lightweight Flask service that wraps the trained fraud-detection model
and exposes it as a SaaS-style REST API that any e-commerce backend
(Shopify, WooCommerce, custom checkout, etc.) could call at the moment
of checkout.

Endpoints
  GET  /api/health            -> service status + model info
  POST /api/predict           -> score a single transaction
  POST /api/predict/batch     -> score a list of transactions (CSV upload flow)
  GET  /api/stats             -> aggregate demo stats for the dashboard
  GET  /api/feature-importance -> global model explainability
  POST /api/support/ticket    -> forward the website support form to the team inbox

Run:
  pip install -r requirements.txt
  python api/app.py
  -> serves on http://localhost:5000
"""

import json
import time
import random
import os
from pathlib import Path
from functools import wraps
from collections import defaultdict
from threading import Lock
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()  # reads .env if present — see .env.example for what goes here

import joblib
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, g, Response

import tenants as tenant_store
import customer_history
import transaction_log
import email_service
import payments
import ip_intelligence
import disposable_email
import custom_rules
import phone_validation
import shared_intelligence
import bin_lookup
import postal_lookup

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "model" / "merafraud_model.pkl"
META_PATH = BASE_DIR / "model" / "model_meta.json"

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    # Allow the static dashboard (opened from file:// or a different port)
    # to call this API during local development / demo.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def cors_preflight(_any):
    return "", 204

model = joblib.load(MODEL_PATH)
with open(META_PATH) as f:
    META = json.load(f)

FEATURE_COLUMNS = META["feature_columns"]

# Demo tenant + demo API key are auto-created on first run, so the
# dashboard keeps working out of the box.
DEMO_TENANT = tenant_store.seed_demo_tenant()


def require_api_key(f):
    """Every /api/predict* call requires a valid X-API-Key header.
    The resolved tenant is stashed on Flask's `g` object (g.tenant) so
    the endpoint can use that merchant's own thresholds/usage counters."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return jsonify({
                "error": "Missing X-API-Key header. Every request must carry an API key.",
                "hint": "Demo key: sk_demo_merafraud_dashboard",
            }), 401
        tenant = tenant_store.get_tenant_by_key(api_key)
        if not tenant:
            return jsonify({"error": "Invalid API key"}), 403

        blocked = _check_subscription_gate(tenant)
        if blocked:
            return blocked

        # sk_test_... keys authenticate identically to sk_live_... keys but
        # are flagged here so predict()/predict_batch() can mark the
        # response and skip billing-relevant usage counters + merchant
        # alerts for test traffic (see g.api_mode below).
        g.api_mode = tenant.pop("_matched_key_mode", "live")
        g.tenant = tenant
        return f(*args, **kwargs)
    return wrapper


def _check_subscription_gate(tenant: dict):
    """Returns a (jsonify(...), 402) tuple if this tenant's access should be
    blocked, or None if the request may proceed. This is the actual
    enforcement behind trial_ends_at / subscription_status — without this,
    a tenant's key kept working forever after their trial lapsed, with
    trial_ends_at tracked in the database but never checked anywhere."""
    status = tenant.get("subscription_status", "trial")
    upgrade_url = "https://merafraud.com/website/pricing.html"

    if status == "active":
        return None

    if status in ("expired", "cancelled"):
        return jsonify({
            "error": "This account's subscription is not active.",
            "reason": status,
            "upgrade_url": upgrade_url,
        }), 402

    # status == "trial"
    trial_ends_at = tenant.get("trial_ends_at")
    if not trial_ends_at:
        return None  # trial with no end date (e.g. legacy rows) — treat as unrestricted
    try:
        ends = datetime.fromisoformat(trial_ends_at)
    except ValueError:
        return None
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= ends:
        # Flip the stored status so /api/tenants/me and the admin panel show
        # "expired" instead of a trial that quietly stopped working.
        tenant_store.set_subscription_status(tenant["id"], "expired")
        return jsonify({
            "error": "Your free trial has ended.",
            "reason": "trial_expired",
            "upgrade_url": upgrade_url,
        }), 402
    return None


# --- Lightweight rate limiting for public, unauthenticated endpoints ---
# Signup (POST /api/tenants) and the support-ticket form are the only two
# routes anyone on the internet can call with no API key, so they're the
# only realistic spam/abuse vectors. This is an in-memory sliding window,
# not Redis-backed -- fine here because the service runs as a single
# gunicorn worker (WEB_CONCURRENCY=1, see logs). It resets on every
# restart/redeploy, which is an acceptable tradeoff: abuse is bursty, not
# something that needs to be remembered for weeks.
_rate_limit_buckets = defaultdict(list)
_rate_limit_lock = Lock()


def check_rate_limit(bucket_name, max_requests=5, window_seconds=3600):
    """Returns None if the request is allowed, or a Flask response tuple
    (jsonify(...), 429) if the caller has exceeded max_requests within
    window_seconds. Keyed by client IP -- reads X-Forwarded-For first
    since Render sits in front of the app as a proxy."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    now = time.time()
    with _rate_limit_lock:
        key = (bucket_name, ip)
        bucket = _rate_limit_buckets[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= max_requests:
            return jsonify({"error": "Too many requests. Please try again later."}), 429
        bucket.append(now)
    return None


ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "changeme_admin_key")


def require_admin_key(f):
    """Locks down tenant-management endpoints (currently just GET
    /api/tenants, the full customer list) so random visitors can't see
    every merchant on the platform. Set ADMIN_API_KEY in your .env /
    Render environment to a real secret before going live."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin_key = request.headers.get("X-Admin-Key")
        if not admin_key or admin_key != ADMIN_API_KEY:
            return jsonify({"error": "Missing or invalid X-Admin-Key header"}), 401
        return f(*args, **kwargs)
    return wrapper


def risk_level(score: float, thresholds: dict) -> str:
    if score >= thresholds["block"]:
        return "block"
    if score >= thresholds["review"]:
        return "review"
    return "approve"


def top_reasons(row: dict, n: int = 3):
    """Very lightweight, fast explanation: rank the transaction's own
    feature values by the model's *global* feature importance, then
    surface which of the high-importance features look anomalous.
    This is not SHAP-level rigor, but it's dependency-light and gives
    analysts a legible reason on every single call.
    """
    importances = META["feature_importances"]
    reasons = []
    HUMAN_LABELS = {
        "time_since_last_tx_min": ("Time since last transaction is very short", lambda v: v < 15),
        "customer_ltv": ("Low customer lifetime value", lambda v: v < 40),
        "amount_ratio_to_avg": ("Amount is far above the customer's average", lambda v: v > 2.5),
        "num_failed_payments_7d": ("Failed payment attempts in the last 7 days", lambda v: v >= 2),
        "num_tx_last_24h": ("High transaction frequency in 24 hours", lambda v: v >= 4),
        "account_age_days": ("Very new account", lambda v: v < 30),
        "login_attempts_before_purchase": ("Many login attempts before purchase", lambda v: v >= 4),
        "billing_shipping_mismatch": ("Billing/shipping address mismatch", lambda v: v == 1),
        "ip_billing_country_mismatch": ("IP country doesn't match billing country", lambda v: v == 1),
        "new_device": ("Unknown/new device", lambda v: v == 1),
        "new_payment_method": ("New payment method", lambda v: v == 1),
        "free_email_domain": ("Free email provider", lambda v: v == 1),
        "express_shipping": ("Express/rush shipping selected", lambda v: v == 1),
        "num_items_in_cart": ("Very few items in cart", lambda v: v <= 1),
    }
    ranked_features = list(importances.keys())
    for feat in ranked_features:
        if feat not in HUMAN_LABELS or feat not in row:
            continue
        label, flagged = HUMAN_LABELS[feat]
        if flagged(row[feat]):
            reasons.append(label)
        if len(reasons) >= n:
            break
    if not reasons:
        reasons = ["No strong anomaly detected; score reflects overall pattern"]
    return reasons


def validate_transaction(payload: dict):
    missing = [c for c in FEATURE_COLUMNS if c not in payload]
    if missing:
        return None, f"Missing fields: {', '.join(missing)}"
    try:
        row = {c: float(payload[c]) for c in FEATURE_COLUMNS}
    except (TypeError, ValueError) as e:
        return None, f"Invalid numeric value: {e}"
    return row, None


@app.route("/api/config", methods=["GET"])
def get_public_config():
    """Public, non-secret config the frontend needs — reads from the same
    single .env file as everything else, so there's one place to edit
    instead of hunting through multiple HTML/JS files."""
    return jsonify({
        "whatsapp_number": os.environ.get("WHATSAPP_NUMBER", "905374575844"),
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "MeraFraud API",
        "model_loaded": model is not None,
        "roc_auc": META["metrics"]["roc_auc"],
        "training_rows": META["training_rows"],
    })


@app.route("/api/predict", methods=["POST"])
@require_api_key
def predict():
    payload = request.get_json(force=True, silent=True) or {}
    row, err = validate_transaction(payload)
    if err:
        return jsonify({"error": err}), 400

    thresholds = g.tenant["thresholds"]
    X = pd.DataFrame([row])[FEATURE_COLUMNS]
    score = float(model.predict_proba(X)[0, 1])
    reasons = top_reasons(row)

    customer_id = payload.get("customer_id")
    customer_ip = payload.get("customer_ip")
    billing_country = payload.get("billing_country")
    customer_phone = payload.get("customer_phone")
    card_bin = payload.get("card_bin")
    postal_code = payload.get("postal_code")
    billing_city = payload.get("billing_city")

    # 1) Customer order/cancellation history (existing)
    customer_risk = None
    if customer_id:
        customer_risk = customer_history.get_customer_history(g.tenant["id"], str(customer_id))
        score, extra = customer_history.apply_customer_risk_adjustment(score, customer_risk)
        reasons = extra + reasons

    # 2) Real IP geolocation + proxy/VPN/datacenter detection
    if customer_ip:
        ip_info = ip_intelligence.lookup_ip(customer_ip)
        score, extra = ip_intelligence.apply_ip_risk_adjustment(score, ip_info, billing_country)
        reasons = extra + reasons

    # 3) Disposable/throwaway email detection (stronger signal than "free email")
    if customer_id and "@" in str(customer_id):
        if disposable_email.is_disposable(str(customer_id)):
            score = min(0.99, score + 0.25)
            reasons = ["Customer used a disposable/throwaway email address"] + reasons

    # 4) Phone number format validation
    if customer_phone:
        phone_check = phone_validation.check_phone(customer_phone)
        score, extra = phone_validation.apply_phone_risk_adjustment(score, phone_check)
        reasons = extra + reasons

    # 5) Shared merchant network — flagged by OTHER stores too
    network_check = shared_intelligence.check_identifier(
        ip=customer_ip, email=str(customer_id) if customer_id else None
    )
    score, extra = shared_intelligence.apply_network_risk_adjustment(score, network_check)
    reasons = extra + reasons

    # 6) Card BIN issuer-country vs. billing-country mismatch
    if card_bin:
        bin_info = bin_lookup.lookup_bin(card_bin)
        score, extra = bin_lookup.apply_bin_risk_adjustment(score, bin_info, billing_country)
        reasons = extra + reasons

    # 7) Billing postal/ZIP code doesn't exist, or doesn't match claimed city
    if postal_code and billing_country:
        postal_check = postal_lookup.lookup_postal_code(postal_code, billing_country)
        score, extra = postal_lookup.apply_postal_risk_adjustment(score, postal_check, billing_city)
        reasons = extra + reasons

    level = risk_level(score, thresholds)

    # 8) Custom per-merchant rules — can only escalate, never override down
    level, rule_reasons = custom_rules.evaluate_rules(g.tenant["id"], row, level)
    reasons = rule_reasons + reasons

    # Test-mode calls (sk_test_... key) score exactly like live calls, but
    # don't count toward billing/usage counters and never page the merchant
    # — otherwise every integration test would inflate real usage stats and
    # fire real block alerts for fake data.
    if g.api_mode == "live":
        tenant_store.record_usage(g.tenant["id"], level)
        transaction_log.log_transaction(g.tenant["id"], row, score, level, customer_id)

        if level == "block" and g.tenant.get("email"):
            email_service.send_fraud_alert_email(
                g.tenant["email"], g.tenant["name"], score, reasons[:3], row.get("transaction_amount")
            )
        if level == "block" and g.tenant.get("webhook_url"):
            email_service.send_webhook_alert(
                g.tenant["webhook_url"], g.tenant["name"], score, reasons[:3], row.get("transaction_amount")
            )

    response = {
        "risk_score": round(score, 4),
        "risk_level": level,          # approve | review | block
        "reasons": reasons,
        "thresholds": thresholds,
        "tenant": g.tenant["name"],
        "mode": g.api_mode,           # "live" or "test"
        "scored_at": time.time(),
    }
    if customer_risk is not None:
        response["customer_history"] = {
            "total_orders": customer_risk["total_orders"],
            "cancelled_orders": customer_risk["cancelled_orders"],
            "cancellation_rate": customer_risk["cancellation_rate"],
            "is_serial_canceller": customer_risk["is_serial_canceller"],
        }
    return jsonify(response)


@app.route("/api/predict/batch", methods=["POST"])
@require_api_key
def predict_batch():
    payload = request.get_json(force=True, silent=True) or {}
    transactions = payload.get("transactions", [])
    if not isinstance(transactions, list) or not transactions:
        return jsonify({"error": "'transactions' must be a non-empty list"}), 400

    thresholds = g.tenant["thresholds"]
    rows, errors = [], []
    for i, tx in enumerate(transactions):
        row, err = validate_transaction(tx)
        if err:
            errors.append({"index": i, "error": err})
            rows.append({c: 0 for c in FEATURE_COLUMNS})
        else:
            rows.append(row)

    X = pd.DataFrame(rows)[FEATURE_COLUMNS]
    scores = model.predict_proba(X)[:, 1]

    results = []
    for i, (row, score) in enumerate(zip(rows, scores)):
        level = risk_level(float(score), thresholds)
        if g.api_mode == "live":
            tenant_store.record_usage(g.tenant["id"], level)
        results.append({
            "index": i,
            "risk_score": round(float(score), 4),
            "risk_level": level,
            "reasons": top_reasons(row),
        })

    return jsonify({"results": results, "errors": errors, "count": len(results), "mode": g.api_mode})


@app.route("/api/feature-importance", methods=["GET"])
def feature_importance():
    return jsonify(META["feature_importances"])


@app.route("/api/stats", methods=["GET"])
@require_api_key
def stats():
    """This tenant's REAL usage counters (grow as /predict is called).
    If no calls have been made yet, realistic sample numbers fill the
    demo so it doesn't look empty — real traffic adds on top of that."""
    usage = g.tenant["usage"]
    has_real_traffic = usage["total_calls"] > 0

    baseline_total = 0 if has_real_traffic else random.Random(g.tenant["id"]).randint(18000, 24000)
    baseline_blocked = 0 if has_real_traffic else int(baseline_total * 0.028)
    avg_amount_saved = 245

    total_tx = baseline_total + usage["total_calls"]
    blocked = baseline_blocked + usage["blocked"]
    reviewed = int(baseline_total * 0.06) + usage["reviewed"]

    return jsonify({
        "tenant": g.tenant["name"],
        "total_transactions_30d": total_tx,
        "blocked_30d": blocked,
        "flagged_for_review_30d": reviewed,
        "estimated_loss_prevented_eur": round(blocked * avg_amount_saved, 2),
        "model_roc_auc": META["metrics"]["roc_auc"],
        "avg_response_time_ms": random.randint(18, 45),
        "live_calls_this_session": usage["total_calls"],
    })


# ---------------------------------------------------------------------------
# Tenant management — for adding a new SME to the platform.
# NOTE: In a real product these endpoints should sit behind admin auth
# (left open here for demo/CLI use).
# ---------------------------------------------------------------------------

RISK_PROFILES = {
    "standard": {"block": 0.75, "review": 0.35},
    "strict":   {"block": 0.55, "review": 0.20},   # blocks/reviews more aggressively — fewer fraud slip-throughs, more false positives
    "lenient":  {"block": 0.90, "review": 0.55},   # only stops the most obvious fraud — fewer false positives, more risk tolerated
}


@app.route("/api/checkout/initialize", methods=["POST"])
def checkout_initialize():
    payload = request.get_json(force=True, silent=True) or {}
    plan = payload.get("plan", "growth")
    email = payload.get("email", "")
    prices = {"starter": 19, "growth": 49, "scale": 199}
    price = prices.get(plan, 49)

    if not email:
        return jsonify({"error": "'email' is required"}), 400

    result = payments.create_checkout_form(
        plan_name=plan, price=price, currency="EUR", buyer_email=email,
        callback_url=payload.get("callback_url", "https://merafraud.com/checkout/callback"),
    )
    return jsonify(result)


@app.route("/api/tenants", methods=["GET", "POST"])
def tenants_collection():
    """POST is the public self-service signup flow (used by signup.html —
    stays open, no key needed). GET is the full tenant list — an admin-only
    view, so it's gated behind X-Admin-Key. Both share one route/function
    to avoid any ambiguity in how GET vs POST get matched on this path."""
    if request.method == "GET":
        admin_key = request.headers.get("X-Admin-Key")
        if not admin_key or admin_key != ADMIN_API_KEY:
            return jsonify({"error": "Missing or invalid X-Admin-Key header"}), 401
        return jsonify(tenant_store.list_tenants())

    limited = check_rate_limit("signup", max_requests=5, window_seconds=3600)
    if limited:
        return limited

    payload = request.get_json(force=True, silent=True) or {}
    name = payload.get("name", "").strip()
    if not name:
        return jsonify({"error": "'name' field is required"}), 400
    email = (payload.get("email") or "").strip() or None
    password = payload.get("password") or None

    thresholds = payload.get("thresholds")
    if not thresholds:
        profile = payload.get("risk_profile", "standard")
        thresholds = RISK_PROFILES.get(profile, RISK_PROFILES["standard"])

    # trial_days is intentionally NOT read from the request body here — this
    # is the public, unauthenticated self-serve signup route, and letting a
    # client pick their own trial length would let anyone grant themselves
    # an unlimited free account. create_tenant() defaults to the standard
    # 7-day trial. Longer trials (pilot customers) are set separately via
    # the admin-only /api/tenants/<id>/trial route below.
    tenant = tenant_store.create_tenant(name, thresholds, email, password)
    if email:
        email_service.send_welcome_email(email, name)  # no-op if SMTP isn't configured
    return jsonify(tenant_store.public_view(tenant)), 201


@app.route("/api/support/ticket", methods=["POST"])
def support_ticket():
    """Public, unauthenticated endpoint behind the website's support-ticket
    form. Forwards the submission to the team inbox via Resend instead of
    just faking a ticket ID client-side. Returns 503 (not an error the user
    caused) if RESEND_API_KEY isn't configured, so the frontend can show a
    clear "try WhatsApp instead" message rather than a fake success."""
    limited = check_rate_limit("support_ticket", max_requests=5, window_seconds=3600)
    if limited:
        return limited

    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    message = (payload.get("message") or "").strip()
    store_name = (payload.get("store_name") or "").strip()
    issue_type = (payload.get("issue_type") or "").strip()
    lang = (payload.get("lang") or "tr").strip()

    if not name or not email or not message:
        return jsonify({"error": "'name', 'email', and 'message' fields are required"}), 400

    if not email_service.is_configured():
        return jsonify({"error": "Email delivery isn't configured on this server yet."}), 503

    sent = email_service.send_support_ticket_email(name, email, store_name, issue_type, message)
    if not sent:
        return jsonify({"error": "Could not send the ticket right now. Please try WhatsApp instead."}), 502

    # Best-effort confirmation back to the customer -- if this leg fails
    # (e.g. their address bounces) the ticket itself is already safely in
    # the team inbox above, so we don't fail the whole request over it.
    email_service.send_ticket_confirmation_email(email, name, lang)

    return jsonify({"status": "sent"}), 201


@app.route("/api/tenants/<tenant_id>/trial", methods=["PUT"])
def set_tenant_trial(tenant_id):
    """Admin-only: (re)set how many days from today a specific tenant's free
    trial runs. Use this for pilot merchants who get more than the standard
    7 days (e.g. {"days": 30} for a first-month-free pilot deal)."""
    admin_key = request.headers.get("X-Admin-Key")
    if not admin_key or admin_key != ADMIN_API_KEY:
        return jsonify({"error": "Missing or invalid X-Admin-Key header"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    days = payload.get("days")
    if not isinstance(days, int) or days <= 0:
        return jsonify({"error": "'days' must be a positive integer"}), 400

    updated = tenant_store.set_trial_end(tenant_id, days)
    if not updated:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant_store.public_view(updated))


@app.route("/api/tenants/<tenant_id>/subscription", methods=["PUT"])
def set_tenant_subscription(tenant_id):
    """Admin-only: manually mark a tenant active/cancelled/expired/trial.
    This is the stand-in for real billing enforcement until a payment
    gateway is live — e.g. confirm a bank transfer, then PUT status=active
    so that tenant is never gated by the trial-expiry check."""
    admin_key = request.headers.get("X-Admin-Key")
    if not admin_key or admin_key != ADMIN_API_KEY:
        return jsonify({"error": "Missing or invalid X-Admin-Key header"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    status = payload.get("status")
    if status not in ("trial", "active", "expired", "cancelled"):
        return jsonify({"error": "'status' must be one of: trial, active, expired, cancelled"}), 400

    updated = tenant_store.set_subscription_status(tenant_id, status)
    if not updated:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant_store.public_view(updated))


# ---------------------------------------------------------------------------
# Account recovery — login with email+password (this doubles as API key
# recovery, since login returns the real key), forgot/reset password, and
# API key regeneration for merchants who lost or leaked their key.
# ---------------------------------------------------------------------------

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    payload = request.get_json(force=True, silent=True) or {}
    email = payload.get("email", "").strip()
    password = payload.get("password", "")
    if not email or not password:
        return jsonify({"error": "'email' and 'password' are required"}), 400

    tenant = tenant_store.login(email, password)
    if not tenant:
        return jsonify({"error": "Invalid email or password"}), 401
    return jsonify(tenant_store.public_view(tenant))


@app.route("/api/auth/set-password", methods=["POST"])
@require_api_key
def auth_set_password():
    """For accounts created before a password existed, or to change one —
    requires being authenticated with the current API key."""
    payload = request.get_json(force=True, silent=True) or {}
    password = payload.get("password", "")
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    updated = tenant_store.set_password(g.tenant["id"], password)
    return jsonify({"status": "ok"})


@app.route("/api/auth/forgot-password", methods=["POST"])
def auth_forgot_password():
    payload = request.get_json(force=True, silent=True) or {}
    email = payload.get("email", "").strip()
    if not email:
        return jsonify({"error": "'email' is required"}), 400

    token = tenant_store.request_password_reset(email)
    if not token:
        # Don't reveal whether the email exists, in a real product — but
        # for this MVP demo we're direct so it's easier to test.
        return jsonify({"error": "No account found with that email"}), 404

    tenant = tenant_store.get_tenant_by_email(email)
    sent = email_service.send_password_reset_email(email, token, tenant["name"] if tenant else "there")

    if sent:
        # Real email went out — don't also leak the token in the API response.
        return jsonify({
            "status": "ok",
            "message": f"A reset code was emailed to {email}.",
        })

    # SMTP isn't configured (see .env.example) — fall back to demo mode so
    # the flow is still testable without a real mail server.
    return jsonify({
        "status": "ok",
        "reset_token": token,
        "note": "DEMO MODE: SMTP not configured (see .env.example), so this token is shown here instead of emailed.",
        "expires_in_minutes": tenant_store.RESET_TOKEN_TTL_MINUTES,
    })


@app.route("/api/auth/reset-password", methods=["POST"])
def auth_reset_password():
    payload = request.get_json(force=True, silent=True) or {}
    token = payload.get("token", "")
    new_password = payload.get("new_password", "")
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    tenant = tenant_store.reset_password_with_token(token, new_password)
    if not tenant:
        return jsonify({"error": "Invalid or expired reset token"}), 400
    return jsonify(tenant_store.public_view(tenant))


@app.route("/api/tenants/regenerate-key", methods=["POST"])
@require_api_key
def regenerate_api_key():
    """Rotates the caller's API key. The OLD key stops working immediately
    — the caller must switch to the new one everywhere it's used."""
    updated = tenant_store.regenerate_api_key(g.tenant["id"])
    return jsonify(tenant_store.public_view(updated))


# ---------------------------------------------------------------------------
# Order outcome tracking — lets the merchant tell MeraFraud what actually
# happened to an order, so future predictions for that same customer can
# account for a pattern of serial ordering/cancelling.
# ---------------------------------------------------------------------------

@app.route("/api/orders/outcome", methods=["POST"])
@require_api_key
def report_order_outcome():
    payload = request.get_json(force=True, silent=True) or {}
    customer_id = payload.get("customer_id")
    outcome = payload.get("outcome")
    order_id = payload.get("order_id")

    if not customer_id or not outcome:
        return jsonify({"error": "'customer_id' and 'outcome' fields are required"}), 400
    if outcome not in ("placed", "cancelled", "fulfilled"):
        return jsonify({"error": "'outcome' must be one of: placed, cancelled, fulfilled"}), 400

    updated = customer_history.record_order_outcome(g.tenant["id"], str(customer_id), outcome, order_id)
    return jsonify(updated)


@app.route("/api/customers/<customer_id>/history", methods=["GET"])
@require_api_key
def get_customer_history_endpoint(customer_id):
    history = customer_history.get_customer_history(g.tenant["id"], str(customer_id))
    return jsonify(history)


# ---------------------------------------------------------------------------
# Custom rule engine — merchant-defined IF-THEN rules on top of the ML model
# ---------------------------------------------------------------------------

@app.route("/api/rules", methods=["GET"])
@require_api_key
def list_custom_rules():
    return jsonify(custom_rules.list_rules(g.tenant["id"]))


@app.route("/api/rules", methods=["POST"])
@require_api_key
def create_custom_rule():
    payload = request.get_json(force=True, silent=True) or {}
    field = payload.get("field")
    operator = payload.get("operator")
    value = payload.get("value")
    action = payload.get("action")

    error = custom_rules.validate_rule(field, operator, value, action)
    if error:
        return jsonify({"error": error}), 400

    rule = custom_rules.add_rule(g.tenant["id"], field, operator, value, action)
    return jsonify(rule), 201


@app.route("/api/rules/<rule_id>", methods=["DELETE"])
@require_api_key
def remove_custom_rule(rule_id):
    deleted = custom_rules.delete_rule(g.tenant["id"], rule_id)
    if not deleted:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Shared merchant fraud network — report confirmed fraud, benefit from
# reports made by other tenants. See shared_intelligence.py for details.
# ---------------------------------------------------------------------------

@app.route("/api/blacklist/report", methods=["POST"])
@require_api_key
def report_to_shared_network():
    payload = request.get_json(force=True, silent=True) or {}
    ip = payload.get("ip")
    email = payload.get("email")
    device_id = payload.get("device_id")

    if not any([ip, email, device_id]):
        return jsonify({"error": "Provide at least one of: ip, email, device_id"}), 400

    result = shared_intelligence.report_fraud(g.tenant["id"], ip=ip, email=email, device_id=device_id)
    return jsonify(result)


@app.route("/api/network/stats", methods=["GET"])
def network_stats():
    """Public — shows the shared network's size without exposing any data."""
    return jsonify(shared_intelligence.network_stats())


@app.route("/api/reports/transactions.csv", methods=["GET"])
@require_api_key
def export_transactions_csv():
    csv_data = transaction_log.to_csv(g.tenant["id"])
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=merafraud_transactions_{g.tenant['id']}.csv"},
    )


@app.route("/api/tenants/me", methods=["GET"])
@require_api_key
def get_my_account():
    """Returns the calling tenant's own account info — used by the Settings
    page so a merchant can see their store name, current thresholds, and
    usage without needing a separate login/session system."""
    return jsonify(tenant_store.public_view(g.tenant, reveal_api_key=True))


@app.route("/api/tenants/thresholds", methods=["PUT"])
@require_api_key
def update_my_thresholds():
    payload = request.get_json(force=True, silent=True) or {}
    block = payload.get("block")
    review = payload.get("review")
    if block is None or review is None:
        return jsonify({"error": "'block' and 'review' fields are required"}), 400
    if not (0 <= review < block <= 1):
        return jsonify({"error": "must satisfy 0 <= review < block <= 1"}), 400
    updated = tenant_store.update_thresholds(g.tenant["id"], {"block": block, "review": review})
    return jsonify(updated)


@app.route("/api/tenants/webhook", methods=["PUT"])
@require_api_key
def update_my_webhook():
    """Sets or clears the calling tenant's Slack/Discord/generic incoming-
    webhook URL. Send {"webhook_url": null} (or omit it) to disable."""
    payload = request.get_json(force=True, silent=True) or {}
    webhook_url = payload.get("webhook_url")
    if webhook_url and not (webhook_url.startswith("http://") or webhook_url.startswith("https://")):
        return jsonify({"error": "'webhook_url' must be a valid http(s) URL, or null/empty to disable"}), 400
    updated = tenant_store.update_webhook(g.tenant["id"], webhook_url)
    return jsonify(tenant_store.public_view(updated))


# ---------------------------------------------------------------------------
# Geo activity — powers the dashboard's world map. In production this would
# be derived from each transaction's real IP-geolocation / billing country,
# stored per tenant. For this demo we generate a plausible, stable-per-hour
# spread of recent flagged transactions across common e-commerce markets.
# ---------------------------------------------------------------------------

CITIES = [
    {"city": "Istanbul", "country": "Türkiye", "lat": 41.01, "lng": 28.98},
    {"city": "Berlin", "country": "Germany", "lat": 52.52, "lng": 13.40},
    {"city": "Paris", "country": "France", "lat": 48.85, "lng": 2.35},
    {"city": "Madrid", "country": "Spain", "lat": 40.42, "lng": -3.70},
    {"city": "Rome", "country": "Italy", "lat": 41.90, "lng": 12.50},
    {"city": "Amsterdam", "country": "Netherlands", "lat": 52.37, "lng": 4.90},
    {"city": "Warsaw", "country": "Poland", "lat": 52.23, "lng": 21.01},
    {"city": "London", "country": "United Kingdom", "lat": 51.51, "lng": -0.13},
    {"city": "Lagos", "country": "Nigeria", "lat": 6.52, "lng": 3.38},
    {"city": "Jakarta", "country": "Indonesia", "lat": -6.21, "lng": 106.85},
    {"city": "São Paulo", "country": "Brazil", "lat": -23.55, "lng": -46.63},
    {"city": "Moscow", "country": "Russia", "lat": 55.76, "lng": 37.62},
    {"city": "Hanoi", "country": "Vietnam", "lat": 21.03, "lng": 105.85},
    {"city": "Bucharest", "country": "Romania", "lat": 44.43, "lng": 26.10},
    {"city": "Athens", "country": "Greece", "lat": 37.98, "lng": 23.73},
    {"city": "Vienna", "country": "Austria", "lat": 48.21, "lng": 16.37},
]


@app.route("/api/geo-activity", methods=["GET"])
@require_api_key
def geo_activity():
    seed_bucket = int(time.time()) // 900  # refresh every 15 minutes, feels "live" without being random noise on every reload
    rng = random.Random(f"{g.tenant['id']}-{seed_bucket}")
    n_points = rng.randint(14, 22)
    points = []
    for _ in range(n_points):
        origin = rng.choice(CITIES)
        score = rng.betavariate(1.6, 4)  # skewed toward lower scores, occasional spikes
        level = risk_level(score, g.tenant["thresholds"])
        points.append({
            "city": origin["city"],
            "country": origin["country"],
            "lat": origin["lat"] + rng.uniform(-0.4, 0.4),
            "lng": origin["lng"] + rng.uniform(-0.4, 0.4),
            "risk_score": round(score, 3),
            "risk_level": level,
            "minutes_ago": rng.randint(1, 340),
        })
    points.sort(key=lambda p: p["minutes_ago"])
    return jsonify({"points": points})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
