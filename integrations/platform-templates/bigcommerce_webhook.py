"""
MeraFraud Integration for BigCommerce
--------------------------------------------
Confidence: HIGH — BigCommerce's Webhooks API is REST-based, well
documented, and stable.

SETUP:
  1. Get a BigCommerce API Account (Store-level API account) with
     `orders` read scope from your BigCommerce admin: Settings > API >
     API Accounts > Create API Account.
  2. Register a webhook for the `store/order/created` scope, pointing to
     https://your-server.com/webhooks/bigcommerce/order-created
     (Do this via the BigCommerce API — see register_webhook() below,
     run it once from a Python shell.)
  3. Deploy this Flask app (e.g. on Render, same as your MeraFraud API).

Run: python bigcommerce_webhook.py
"""

import os
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

MERAFRAUD_API_BASE = os.environ.get("MERAFRAUD_API_BASE", "https://your-merafraud-api.onrender.com/api")
MERAFRAUD_API_KEY = os.environ.get("MERAFRAUD_API_KEY", "sk_live_REPLACE_ME")

BIGCOMMERCE_STORE_HASH = os.environ.get("BIGCOMMERCE_STORE_HASH", "")
BIGCOMMERCE_ACCESS_TOKEN = os.environ.get("BIGCOMMERCE_ACCESS_TOKEN", "")
BIGCOMMERCE_CLIENT_ID = os.environ.get("BIGCOMMERCE_CLIENT_ID", "")


def register_webhook():
    """Run this ONCE (e.g. `python -c "from bigcommerce_webhook import register_webhook; register_webhook()"`)
    to tell BigCommerce to start sending order events to your server."""
    resp = requests.post(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v3/hooks",
        headers={
            "X-Auth-Client": BIGCOMMERCE_CLIENT_ID,
            "X-Auth-Token": BIGCOMMERCE_ACCESS_TOKEN,
            "Content-Type": "application/json",
        },
        json={
            "scope": "store/order/created",
            "destination": "https://your-server.com/webhooks/bigcommerce/order-created",
            "is_active": True,
        },
    )
    print(resp.status_code, resp.json())


def fetch_order_details(order_id: str) -> dict:
    resp = requests.get(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v2/orders/{order_id}",
        headers={"X-Auth-Client": BIGCOMMERCE_CLIENT_ID, "X-Auth-Token": BIGCOMMERCE_ACCESS_TOKEN},
        timeout=5,
    )
    return resp.json()


def bigcommerce_order_to_merafraud_payload(order: dict) -> dict:
    billing = order.get("billing_address", {})
    return {
        "transaction_amount": float(order.get("total_inc_tax", 0)),
        "amount_ratio_to_avg": 1.2,      # TODO: compute from customer's order history via /v2/customers
        "account_age_days": 180,          # TODO: look up customer creation date
        "customer_ltv": 0,                # TODO: sum of customer's past orders
        "time_since_last_tx_min": 999,
        "num_tx_last_24h": 0,
        "hour_of_day": 12,                 # TODO: parse order['date_created']
        "num_items_in_cart": int(order.get("items_total", 1)),
        "num_failed_payments_7d": 0,
        "login_attempts_before_purchase": 1,
        "billing_shipping_mismatch": 0,   # TODO: compare billing_address vs shipping_addresses[0]
        "ip_billing_country_mismatch": 0,
        "new_device": 0,
        "new_payment_method": 0,
        "free_email_domain": int((billing.get("email", "").split("@")[-1] or "") in
                                  {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}),
        "express_shipping": 0,
        "customer_id": billing.get("email", str(order.get("id"))),
        "customer_ip": order.get("geoip_country", ""),  # BigCommerce doesn't expose raw IP by default
    }


@app.route("/webhooks/bigcommerce/order-created", methods=["POST"])
def bigcommerce_webhook():
    event = request.get_json(force=True)
    order_id = event.get("data", {}).get("id")
    if not order_id:
        return jsonify({"status": "ignored"}), 200

    order = fetch_order_details(order_id)
    payload = bigcommerce_order_to_merafraud_payload(order)

    resp = requests.post(
        f"{MERAFRAUD_API_BASE}/predict",
        headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
        json=payload, timeout=5,
    )
    result = resp.json()

    if result.get("risk_level") == "block":
        # TODO: call BigCommerce API to update order status to "Awaiting Fraud Review" (status_id 7)
        print(f"⛔ Order {order_id} flagged HIGH RISK: {result.get('reasons')}")
    elif result.get("risk_level") == "review":
        print(f"⚠ Order {order_id} flagged for review: {result.get('reasons')}")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(port=5060, debug=True)
