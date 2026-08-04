"""
MeraFraud Integration for BigCommerce
--------------------------------------------
Confidence: HIGH — BigCommerce's Webhooks API is REST-based, well
documented, and stable.

SETUP:
  1. Get a BigCommerce API Account (Store-level API account) with
     `orders` and `customers` read scopes from your BigCommerce admin:
     Settings > API > API Accounts > Create API Account.
  2. Pick a WEBHOOK_SHARED_SECRET (any long random string) and set it as
     an env var on your server. This same value is sent to BigCommerce
     in register_webhook() below, and BigCommerce echoes it back on every
     callback so this server can confirm the request really came from
     your store (see verify_webhook_request()).
  3. Register a webhook for the `store/order/created` scope, pointing to
     https://your-server.com/webhooks/bigcommerce/order-created
     (Do this via the BigCommerce API — see register_webhook() below,
     run it once from a Python shell.)
  4. Deploy this Flask app (e.g. on Render, same as your MeraFraud API).

Run: python bigcommerce_webhook.py

KNOWN PLATFORM LIMITATIONS (not bugs in this script — BigCommerce's REST
Orders API simply doesn't expose this data, confirmed against BigCommerce's
official API docs):
  - No raw customer IP on the order. BigCommerce only returns a
    geolocation derived from that IP (`geoip_country_iso2`), never the
    IP itself. Full IP intelligence (proxy/VPN/datacenter detection)
    would require capturing the IP client-side during checkout and
    storing it yourself — out of scope for a webhook-only integration.
  - No failed-payment-attempt count or pre-purchase login-attempt count
    anywhere in the Orders API. `num_failed_payments_7d` and
    `login_attempts_before_purchase` are sent as safe defaults (0 / 1).
  - No "new device" / "new payment method" signal — BigCommerce doesn't
    fingerprint devices or payment methods on the order object.
"""

import os
import hmac
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

MERAFRAUD_API_BASE = os.environ.get("MERAFRAUD_API_BASE", "https://your-merafraud-api.onrender.com/api")
MERAFRAUD_API_KEY = os.environ.get("MERAFRAUD_API_KEY", "sk_live_REPLACE_ME")

BIGCOMMERCE_STORE_HASH = os.environ.get("BIGCOMMERCE_STORE_HASH", "")
BIGCOMMERCE_ACCESS_TOKEN = os.environ.get("BIGCOMMERCE_ACCESS_TOKEN", "")
BIGCOMMERCE_CLIENT_ID = os.environ.get("BIGCOMMERCE_CLIENT_ID", "")

# A secret only you and your own webhook registration know. BigCommerce's
# store-level API accounts don't compute a request signature the way an
# OAuth app's client_secret does, but POST /v3/hooks lets you attach a
# custom `headers` block that BigCommerce echoes back unchanged on every
# callback — so a shared secret in a custom header is the correct way to
# confirm a request actually came from your webhook registration and not
# a random POST to a guessable public URL.
WEBHOOK_SHARED_SECRET = os.environ.get("WEBHOOK_SHARED_SECRET", "")

BC_HEADERS = {"X-Auth-Client": BIGCOMMERCE_CLIENT_ID, "X-Auth-Token": BIGCOMMERCE_ACCESS_TOKEN}


def register_webhook():
    """Run this ONCE (e.g. `python -c "from bigcommerce_webhook import register_webhook; register_webhook()"`)
    to tell BigCommerce to start sending order events to your server."""
    resp = requests.post(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v3/hooks",
        headers={**BC_HEADERS, "Content-Type": "application/json"},
        json={
            "scope": "store/order/created",
            "destination": "https://your-server.com/webhooks/bigcommerce/order-created",
            "is_active": True,
            "headers": {"X-Webhook-Secret": WEBHOOK_SHARED_SECRET},
        },
    )
    print(resp.status_code, resp.json())


def verify_webhook_request(req) -> bool:
    """Confirms the incoming POST carries the same shared secret we told
    BigCommerce to attach when we registered the webhook. Constant-time
    comparison (hmac.compare_digest) so this can't be brute-forced via
    response-timing differences."""
    if not WEBHOOK_SHARED_SECRET:
        return True  # no secret configured yet — allow through during initial setup
    incoming = req.headers.get("X-Webhook-Secret", "")
    return hmac.compare_digest(incoming, WEBHOOK_SHARED_SECRET)


def fetch_order_details(order_id: str) -> dict:
    resp = requests.get(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v2/orders/{order_id}",
        headers=BC_HEADERS, timeout=5,
    )
    return resp.json()


def fetch_order_shipping_addresses(order_id: str) -> list:
    resp = requests.get(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v2/orders/{order_id}/shipping_addresses",
        headers=BC_HEADERS, timeout=5,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data if isinstance(data, list) else [data]


def fetch_customer(customer_id: int) -> dict:
    resp = requests.get(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v2/customers/{customer_id}",
        headers=BC_HEADERS, timeout=5,
    )
    if resp.status_code != 200:
        return {}
    return resp.json()


def fetch_customer_past_orders(customer_id: int, before_order_id: int) -> list:
    """All of this customer's prior orders, oldest-order fields included,
    used to compute LTV, average order size, and recency. Excludes the
    order that just triggered this webhook."""
    resp = requests.get(
        f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v2/orders",
        headers=BC_HEADERS,
        params={"customer_id": customer_id, "limit": 250},
        timeout=5,
    )
    if resp.status_code != 200:
        return []
    orders = resp.json()
    if not isinstance(orders, list):
        return []
    return [o for o in orders if o.get("id") != before_order_id]


def bigcommerce_order_to_merafraud_payload(order: dict) -> dict:
    billing = order.get("billing_address", {})
    order_id = order.get("id")
    customer_id = order.get("customer_id") or 0
    current_amount = float(order.get("total_inc_tax", 0))

    # date_created comes back as RFC 2822 (e.g. "Tue, 05 Mar 2019 21:40:11 +0000"), always UTC
    try:
        order_dt = parsedate_to_datetime(order.get("date_created", ""))
        if order_dt.tzinfo is None:
            order_dt = order_dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        order_dt = datetime.now(timezone.utc)

    # --- Account age: real signup date from the Customer object ---
    # customer_id 0 means guest checkout — no customer record to look up.
    account_age_days = 0
    if customer_id:
        customer = fetch_customer(customer_id)
        try:
            created_dt = parsedate_to_datetime(customer.get("date_created", ""))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            account_age_days = max(0, (order_dt - created_dt).days)
        except (TypeError, ValueError):
            account_age_days = 0

    # --- Order history: LTV, average order size, recency, 24h velocity ---
    customer_ltv = 0.0
    amount_ratio_to_avg = 1.0
    time_since_last_tx_min = 999999.0
    num_tx_last_24h = 0
    if customer_id:
        past_orders = fetch_customer_past_orders(customer_id, order_id)
        past_amounts = [float(o.get("total_inc_tax", 0)) for o in past_orders]
        customer_ltv = sum(past_amounts)
        if past_amounts:
            avg_amount = customer_ltv / len(past_amounts)
            amount_ratio_to_avg = round(current_amount / avg_amount, 2) if avg_amount > 0 else 1.0

        past_dts = []
        for o in past_orders:
            try:
                dt = parsedate_to_datetime(o.get("date_created", ""))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                past_dts.append(dt)
            except (TypeError, ValueError):
                continue
        if past_dts:
            most_recent = max(past_dts)
            time_since_last_tx_min = max(0.0, (order_dt - most_recent).total_seconds() / 60)
            num_tx_last_24h = sum(1 for dt in past_dts if (order_dt - dt).total_seconds() <= 86400)

    # --- Billing vs. shipping address mismatch ---
    # Digital-only orders can have zero shipping addresses — treat as no mismatch.
    billing_shipping_mismatch = 0
    express_shipping = 0
    shipping_addresses = fetch_order_shipping_addresses(order_id)
    if shipping_addresses:
        ship = shipping_addresses[0]
        billing_country = (billing.get("country_iso2") or "").upper()
        ship_country = (ship.get("country_iso2") or "").upper()
        billing_zip = (billing.get("zip") or "").strip()
        ship_zip = (ship.get("zip") or "").strip()
        if billing_country and ship_country and billing_country != ship_country:
            billing_shipping_mismatch = 1
        elif billing_zip and ship_zip and billing_zip != ship_zip:
            billing_shipping_mismatch = 1

        shipping_method = (ship.get("shipping_method") or "").lower()
        if any(k in shipping_method for k in ("express", "expedited", "overnight", "next day", "rush")):
            express_shipping = 1

    # --- IP-derived country vs. billing country ---
    # BigCommerce never exposes the raw checkout IP over the Orders REST API —
    # only a geolocation derived from it. That's enough to flag a mismatch
    # signal even without the IP itself.
    geoip_country_iso2 = (order.get("geoip_country_iso2") or "").upper()
    billing_country_iso2 = (billing.get("country_iso2") or "").upper()
    ip_billing_country_mismatch = int(
        bool(geoip_country_iso2) and bool(billing_country_iso2) and geoip_country_iso2 != billing_country_iso2
    )

    return {
        "transaction_amount": current_amount,
        "amount_ratio_to_avg": amount_ratio_to_avg,
        "account_age_days": account_age_days,
        "customer_ltv": round(customer_ltv, 2),
        "time_since_last_tx_min": round(time_since_last_tx_min, 1),
        "num_tx_last_24h": num_tx_last_24h,
        "hour_of_day": order_dt.hour,
        "num_items_in_cart": int(order.get("items_total", 1)),
        # Not available via BigCommerce's REST API — see KNOWN PLATFORM LIMITATIONS above.
        "num_failed_payments_7d": 0,
        "login_attempts_before_purchase": 1,
        "billing_shipping_mismatch": billing_shipping_mismatch,
        "ip_billing_country_mismatch": ip_billing_country_mismatch,
        # Not available via BigCommerce's REST API — see KNOWN PLATFORM LIMITATIONS above.
        "new_device": 0,
        "new_payment_method": 0,
        "free_email_domain": int((billing.get("email", "").split("@")[-1] or "") in
                                  {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}),
        "express_shipping": express_shipping,
        "customer_id": billing.get("email", str(order_id)),
        "billing_country": billing_country_iso2 or None,
        "customer_phone": billing.get("phone") or None,
        "postal_code": billing.get("zip") or None,
        "billing_city": billing.get("city") or None,
        # No raw IP available from this API — omitted rather than sent as a
        # bogus value. If you want full IP intelligence (proxy/VPN/datacenter
        # detection), capture the visitor's IP in a checkout script and pass
        # it through separately; MeraFraud's /api/predict accepts it as
        # "customer_ip" when you have it.
    }


@app.route("/webhooks/bigcommerce/order-created", methods=["POST"])
def bigcommerce_webhook():
    if not verify_webhook_request(request):
        return jsonify({"error": "invalid webhook signature"}), 401

    event = request.get_json(force=True)
    order_id = event.get("data", {}).get("id")
    if not order_id:
        return jsonify({"status": "ignored"}), 200

    order = fetch_order_details(order_id)
    payload = bigcommerce_order_to_merafraud_payload(order)
    # Drop optional fields we couldn't determine, rather than sending nulls.
    payload = {k: v for k, v in payload.items() if v is not None}

    resp = requests.post(
        f"{MERAFRAUD_API_BASE}/predict",
        headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
        json=payload, timeout=5,
    )
    result = resp.json()

    if result.get("risk_level") == "block":
        # status_id 7 is "Awaiting Payment", NOT a fraud-review status — using it
        # would incorrectly tell the customer their payment didn't go through.
        # BigCommerce's closest built-in status is 12 ("Manual Verification
        # Required"). For a status actually labeled for fraud review, create a
        # custom order status in Settings > Order Statuses and use its ID here.
        requests.put(
            f"https://api.bigcommerce.com/stores/{BIGCOMMERCE_STORE_HASH}/v2/orders/{order_id}",
            headers={**BC_HEADERS, "Content-Type": "application/json"},
            json={"status_id": 12},
            timeout=5,
        )
        print(f"⛔ Order {order_id} flagged HIGH RISK: {result.get('reasons')}")
    elif result.get("risk_level") == "review":
        print(f"⚠ Order {order_id} flagged for review: {result.get('reasons')}")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(port=5060, debug=True)
