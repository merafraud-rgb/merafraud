"""
MeraFraud — Shopify Webhook Integration Example
-----------------------------------------------------
Shopify doesn't let a third party (like MeraFraud) sit directly inside its
checkout. The realistic integration pattern is:

  1. Shopify fires a webhook to YOUR OWN small server whenever an order is
     created ("orders/create") or cancelled ("orders/cancelled").
  2. Your server (this script) receives that webhook, calls MeraFraud's
     /api/predict with the order's details, and gets back a decision.
  3. Because Shopify has usually already captured payment by the time
     "orders/create" fires, a "block" decision here typically means:
     auto-refund + cancel the order, or hold it and tag it for manual
     review inside Shopify admin (via the Shopify Admin API) — rather
     than preventing checkout outright. (Preventing checkout in real time
     requires a Shopify Plus "Checkout UI extension" / Shopify Functions,
     which is a deeper, Plus-tier integration.)

Setup steps (do this in your Shopify Partner/Admin dashboard):
  1. Create a Shopify app (or use "Admin API access token" for a custom app
     on your own store) with `read_orders` scope.
  2. Register a webhook for `orders/create` and `orders/cancelled` pointing
     to https://your-server.com/webhooks/shopify/orders
  3. Set SHOPIFY_WEBHOOK_SECRET below to the secret Shopify gives you, so
     you can verify webhook authenticity (Shopify signs every webhook).

This file is a template — you will need to fill in your own Shopify
Admin API calls for the "flag for manual review" / "cancel order" actions,
since those require your Shopify access token, not MeraFraud's.
"""

import os
import hmac
import hashlib
import base64
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

MERAFRAUD_API_BASE = os.environ.get("MERAFRAUD_API_BASE", "http://localhost:5000/api")
MERAFRAUD_API_KEY = os.environ.get("MERAFRAUD_API_KEY", "sk_demo_merafraud_dashboard")
SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "replace-with-real-secret")


def verify_shopify_webhook(raw_body: bytes, hmac_header: str) -> bool:
    """Confirms this webhook really came from Shopify, not an impostor."""
    digest = hmac.new(SHOPIFY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed_hmac, hmac_header or "")


def shopify_order_to_merafraud_payload(order: dict) -> dict:
    """Maps Shopify's order JSON shape onto MeraFraud's expected fields.
    Shopify gives you a lot of this directly; a few fields (account age,
    lifetime value, failed payments) typically need a lookup against your
    own customer database, since Shopify's webhook payload doesn't include
    full customer history."""
    customer = order.get("customer") or {}
    billing = order.get("billing_address") or {}
    shipping = order.get("shipping_address") or {}

    return {
        "transaction_amount": float(order.get("total_price", 0)),
        "amount_ratio_to_avg": 1.2,          # look this up from your own customer stats
        "account_age_days": 180,             # look this up: customer["created_at"] vs now
        "customer_ltv": float(customer.get("total_spent", 0)),
        "time_since_last_tx_min": 120,        # look this up from your order history
        "num_tx_last_24h": customer.get("orders_count", 0),
        "hour_of_day": int(order["created_at"][11:13]) if order.get("created_at") else 12,
        "num_items_in_cart": sum(li.get("quantity", 1) for li in order.get("line_items", [])),
        "num_failed_payments_7d": 0,          # look this up if you track failed charges
        "login_attempts_before_purchase": 1,  # Shopify doesn't expose this directly
        "billing_shipping_mismatch": int(billing.get("address1") != shipping.get("address1")),
        "ip_billing_country_mismatch": int(order.get("browser_ip", "") != "" and
                                            billing.get("country_code") != order.get("client_details", {}).get("country_code", billing.get("country_code"))),
        "new_device": 0,                       # Shopify doesn't expose device fingerprinting here
        "new_payment_method": 0,
        "free_email_domain": int((customer.get("email") or "").split("@")[-1] in
                                  {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}),
        "express_shipping": int("express" in (order.get("shipping_lines", [{}])[0].get("title", "").lower())),
        "customer_id": customer.get("email", order.get("id")),
    }


@app.route("/webhooks/shopify/orders", methods=["POST"])
def shopify_orders_webhook():
    raw_body = request.get_data()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")

    if not verify_shopify_webhook(raw_body, hmac_header):
        return jsonify({"error": "invalid webhook signature"}), 401

    topic = request.headers.get("X-Shopify-Topic", "")
    order = request.get_json(force=True)

    if topic == "orders/create":
        payload = shopify_order_to_merafraud_payload(order)
        resp = requests.post(
            f"{MERAFRAUD_API_BASE}/predict",
            headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=3,
        )
        result = resp.json()

        # Tell MeraFraud this customer placed an order (for serial-canceller tracking)
        requests.post(
            f"{MERAFRAUD_API_BASE}/orders/outcome",
            headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
            json={"customer_id": payload["customer_id"], "outcome": "placed", "order_id": str(order["id"])},
            timeout=3,
        )

        if result.get("risk_level") == "block":
            # TODO: call the Shopify Admin API here to cancel/refund the
            # order, or add an "MERAFRAUD-BLOCK" tag for manual review.
            print(f"⛔ Order {order['id']} flagged as high risk: {result['reasons']}")
        elif result.get("risk_level") == "review":
            # TODO: tag the order in Shopify admin for manual review
            print(f"⚠ Order {order['id']} flagged for review: {result['reasons']}")

    elif topic == "orders/cancelled":
        customer_email = (order.get("customer") or {}).get("email", order.get("id"))
        requests.post(
            f"{MERAFRAUD_API_BASE}/orders/outcome",
            headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
            json={"customer_id": customer_email, "outcome": "cancelled", "order_id": str(order["id"])},
            timeout=3,
        )

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    # In production this runs behind HTTPS on your own domain, e.g. via
    # Render/Railway — Shopify requires webhook URLs to be HTTPS.
    app.run(port=5050, debug=True)
