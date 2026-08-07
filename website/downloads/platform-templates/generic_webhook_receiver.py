"""
MeraFraud — Generic Webhook Receiver
--------------------------------------------
Confidence: LOW-to-MEDIUM depending on platform (see platform-templates/README.md)

For platforms where I don't have confident, up-to-date knowledge of the
exact API/webhook field names (Ticimax, İkas, T-Soft, PlatinMarket) or
where the ecosystem is broad enough that a single template can't cover
every setup (Ecwid, Shopware, Sylius, AbanteCart, ThirtyBees, CS-Cart),
this file gives you a WORKING webhook server with a single function to
customize: `map_platform_fields()`.

HOW TO USE THIS:
  1. Find your platform's way to configure a webhook / callback URL for
     "order created" events (every platform above supports SOME form of
     this — check their admin panel or developer docs for the exact term:
     "Webhook", "Callback URL", "Bildirim Adresi", "API entegrasyonu").
  2. Point it at: https://your-server.com/webhooks/generic/order-created
  3. Send your platform ONE real test order, and print/log the raw JSON
     it sends (this file already logs it — see the console output).
  4. Update `map_platform_fields()` below to match your platform's actual
     field names once you see the real payload shape.
  5. (Optional but recommended) If your platform lets you attach a custom
     header or a secret query param to its webhook config, set
     WEBHOOK_SHARED_SECRET below and configure the same value on the
     platform side — see verify_webhook_request().

This is intentionally the "figure it out with real data" template —
which is safer than guessing wrong field names for a platform I can't
test against.
"""

import os
import hmac
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

MERAFRAUD_API_BASE = os.environ.get("MERAFRAUD_API_BASE", "https://your-merafraud-api.onrender.com/api")
MERAFRAUD_API_KEY = os.environ.get("MERAFRAUD_API_KEY", "sk_live_REPLACE_ME")

# Optional. If your platform can send a custom header or query param with
# every webhook call, set this and check for it below — cheap protection
# against random POSTs to a guessable public URL. Leave blank to skip.
WEBHOOK_SHARED_SECRET = os.environ.get("WEBHOOK_SHARED_SECRET", "")

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}

# Common field names platforms use for an order's creation timestamp —
# tried in order, first match wins. This can't know YOUR platform's exact
# field name, but trying common conventions beats a hardcoded fake hour.
_COMMON_DATE_KEYS = ("date_created", "created_at", "order_date", "date_add", "date_added", "orderDate")
# A few common date formats seen across platforms (ISO 8601 variants +
# RFC 2822, which BigCommerce/older PHP platforms tend to use).
_COMMON_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _guess_order_datetime(raw_order: dict):
    """Best-effort: look for a recognizable order-timestamp field under a
    few common names/formats. Returns None if nothing matches — callers
    should fall back to the current time rather than guess."""
    for key in _COMMON_DATE_KEYS:
        value = raw_order.get(key)
        if not value or not isinstance(value, str):
            continue
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass
        for fmt in _COMMON_DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def verify_webhook_request(req) -> bool:
    if not WEBHOOK_SHARED_SECRET:
        return True  # not configured — nothing to check
    incoming = req.headers.get("X-Webhook-Secret") or req.args.get("secret") or ""
    return hmac.compare_digest(incoming, WEBHOOK_SHARED_SECRET)


def map_platform_fields(raw_order: dict) -> dict:
    """
    ⚠️ CUSTOMIZE THIS FUNCTION for your specific platform.

    `raw_order` is whatever JSON your platform's webhook actually sends —
    log it first (see the /webhooks/generic/order-created route below) to
    see its real shape, then adjust the .get() paths accordingly.
    """
    customer = raw_order.get("customer", {}) if isinstance(raw_order.get("customer"), dict) else {}
    email = customer.get("email") or raw_order.get("email", "")
    email_domain = email.split("@")[-1].lower() if "@" in email else ""

    # Best-effort — try a few common shapes; adjust once you've logged your
    # platform's real payload (see the print() in the route below). Feeding
    # these through lets MeraFraud's customer-lookup panel in Settings show
    # WHO and roughly WHERE an order came from, not just an opaque ID.
    billing = raw_order.get("billing_address", {}) if isinstance(raw_order.get("billing_address"), dict) else {}
    customer_name = (
        customer.get("name")
        or billing.get("name")
        or " ".join(filter(None, [customer.get("first_name") or billing.get("first_name"),
                                   customer.get("last_name") or billing.get("last_name")])).strip()
        or None
    )
    billing_country = billing.get("country") or billing.get("country_code") or raw_order.get("country") or None
    billing_city = billing.get("city") or raw_order.get("city") or None

    order_dt = _guess_order_datetime(raw_order)
    hour_of_day = order_dt.hour if order_dt else datetime.now(timezone.utc).hour

    return {
        "transaction_amount": float(raw_order.get("total") or raw_order.get("order_total") or 0),
        # These four genuinely require querying YOUR platform's customer/order
        # history — there's no universal field for them in a webhook payload,
        # and no safe way to guess them generically. Once you've customized
        # the fields above and confirmed the integration works, come back and
        # wire these to your platform's own order-history API/database if you
        # want the model to use real values instead of these neutral defaults.
        "amount_ratio_to_avg": 1.0,
        "account_age_days": 0,
        "customer_ltv": 0,
        "time_since_last_tx_min": 999999,
        "num_tx_last_24h": 0,
        "hour_of_day": hour_of_day,
        "num_items_in_cart": int(raw_order.get("item_count") or len(raw_order.get("items", [])) or 1),
        "num_failed_payments_7d": 0,
        "login_attempts_before_purchase": 1,
        "billing_shipping_mismatch": 0,   # TODO: compare billing vs shipping address fields once you see the real payload
        "ip_billing_country_mismatch": 0,  # send customer_ip + billing_country below and MeraFraud computes this itself
        "new_device": 0,
        "new_payment_method": 0,
        "free_email_domain": int(email_domain in FREE_EMAIL_DOMAINS),
        "express_shipping": 0,
        "customer_id": email or str(raw_order.get("id", "unknown")),
        "customer_name": customer_name,
        "customer_ip": raw_order.get("ip_address", ""),
        "billing_country": billing_country,
        "billing_city": billing_city,
    }


def report_order_outcome(customer_id, outcome, order_id=None, customer_name=None, billing_country=None, billing_city=None):
    """POSTs to /api/orders/outcome so MeraFraud's per-customer history
    (customer_history.py's total/cancelled/fulfilled counts — what powers
    the 'serial canceller' flag and the customer-lookup panel in Settings)
    actually gets real data. Called with outcome='placed' below whenever an
    order comes in; call it again with 'cancelled' or 'fulfilled' once you
    wire up your platform's order-status-changed webhook (see
    /webhooks/generic/order-status-changed below — customize
    map_platform_outcome() the same way you customized map_platform_fields()).
    Fails silently: fraud scoring already happened via /predict, so a
    history-reporting hiccup shouldn't be treated as a request failure."""
    try:
        requests.post(
            f"{MERAFRAUD_API_BASE}/orders/outcome",
            headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
            json={k: v for k, v in {
                "customer_id": customer_id, "outcome": outcome, "order_id": order_id,
                "customer_name": customer_name, "billing_country": billing_country, "billing_city": billing_city,
            }.items() if v is not None},
            timeout=5,
        )
    except requests.RequestException as e:
        print(f"MeraFraud order-outcome report failed: {e}")


def map_platform_outcome(raw_event: dict):
    """⚠️ CUSTOMIZE THIS FUNCTION once you know your platform's order-status-
    change payload shape (log it first, same as map_platform_fields() above).
    Return one of 'cancelled' / 'fulfilled', or None to ignore this event
    (e.g. a status change that isn't a final cancellation or fulfillment)."""
    status = str(raw_event.get("status") or raw_event.get("order_status") or "").lower()
    if status in ("cancelled", "canceled", "refunded", "returned"):
        return "cancelled"
    if status in ("fulfilled", "completed", "shipped", "delivered"):
        return "fulfilled"
    return None


@app.route("/webhooks/generic/order-status-changed", methods=["POST"])
def generic_order_status_webhook():
    """Point your platform's order-status-change webhook here (separate from
    /webhooks/generic/order-created above, which only fires once at
    creation). See map_platform_outcome() — customize it first using a
    logged real payload, same workflow as map_platform_fields()."""
    if not verify_webhook_request(request):
        return jsonify({"error": "invalid webhook signature"}), 401

    raw_event = request.get_json(force=True, silent=True) or {}
    print("=== RAW STATUS-CHANGE PAYLOAD (use this to customize map_platform_outcome) ===")
    print(raw_event)

    outcome = map_platform_outcome(raw_event)
    if not outcome:
        return jsonify({"status": "ignored"}), 200

    fields = map_platform_fields(raw_event)
    report_order_outcome(fields.get("customer_id"), outcome, str(raw_event.get("id", "")) or None,
                          fields.get("customer_name"), fields.get("billing_country"), fields.get("billing_city"))
    return jsonify({"status": "ok", "outcome": outcome}), 200


@app.route("/webhooks/generic/order-created", methods=["POST"])
def generic_order_webhook():
    if not verify_webhook_request(request):
        return jsonify({"error": "invalid webhook signature"}), 401

    raw_order = request.get_json(force=True, silent=True) or {}

    # Log the raw payload so you can see your platform's real field names —
    # remove this print() once map_platform_fields() is customized and confirmed working.
    print("=== RAW WEBHOOK PAYLOAD (use this to customize map_platform_fields) ===")
    print(raw_order)

    payload = map_platform_fields(raw_order)

    try:
        resp = requests.post(
            f"{MERAFRAUD_API_BASE}/predict",
            headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=5,
        )
        result = resp.json()
    except Exception as e:
        print(f"MeraFraud call failed: {e}")
        return jsonify({"status": "error_but_ok"}), 200  # fail open, don't break the platform's webhook retry logic

    report_order_outcome(payload.get("customer_id"), "placed", str(raw_order.get("id", "")) or None,
                          payload.get("customer_name"), payload.get("billing_country"), payload.get("billing_city"))

    level = result.get("risk_level")
    if level == "block":
        print(f"⛔ Order flagged HIGH RISK: {result.get('reasons')}")
        # TODO: call your platform's API to hold/cancel the order, or send
        # yourself an alert (Slack, email) — MeraFraud can't do this last
        # mile for you since every platform's "hold an order" API differs.
    elif level == "review":
        print(f"⚠ Order flagged for review: {result.get('reasons')}")

    return jsonify({"status": "ok", "merafraud_result": result}), 200


if __name__ == "__main__":
    app.run(port=5070, debug=True)
