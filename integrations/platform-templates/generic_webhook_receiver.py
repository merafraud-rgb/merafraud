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

This is intentionally the "figure it out with real data" template —
which is safer than guessing wrong field names for a platform I can't
test against.
"""

import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

MERAFRAUD_API_BASE = os.environ.get("MERAFRAUD_API_BASE", "https://your-merafraud-api.onrender.com/api")
MERAFRAUD_API_KEY = os.environ.get("MERAFRAUD_API_KEY", "sk_live_REPLACE_ME")

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}


def map_platform_fields(raw_order: dict) -> dict:
    """
    ⚠️ CUSTOMIZE THIS FUNCTION for your specific platform.

    `raw_order` is whatever JSON your platform's webhook actually sends —
    log it first (see the /webhooks/generic/order-created route below) to
    see its real shape, then adjust the .get() paths accordingly.
    """
    email = raw_order.get("customer", {}).get("email") or raw_order.get("email", "")
    email_domain = email.split("@")[-1].lower() if "@" in email else ""

    return {
        "transaction_amount": float(raw_order.get("total") or raw_order.get("order_total") or 0),
        "amount_ratio_to_avg": 1.2,      # TODO: platform-specific — usually not in the webhook payload itself
        "account_age_days": 180,          # TODO
        "customer_ltv": 0,                 # TODO
        "time_since_last_tx_min": 999,
        "num_tx_last_24h": 0,
        "hour_of_day": 12,                 # TODO: parse a real order timestamp field if present
        "num_items_in_cart": int(raw_order.get("item_count") or len(raw_order.get("items", [])) or 1),
        "num_failed_payments_7d": 0,
        "login_attempts_before_purchase": 1,
        "billing_shipping_mismatch": 0,   # TODO: compare billing vs shipping address fields
        "ip_billing_country_mismatch": 0,
        "new_device": 0,
        "new_payment_method": 0,
        "free_email_domain": int(email_domain in FREE_EMAIL_DOMAINS),
        "express_shipping": 0,
        "customer_id": email or str(raw_order.get("id", "unknown")),
        "customer_ip": raw_order.get("ip_address", ""),
    }


@app.route("/webhooks/generic/order-created", methods=["POST"])
def generic_order_webhook():
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
