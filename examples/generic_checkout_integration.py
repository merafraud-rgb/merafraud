"""
MeraFraud — Generic Checkout Integration Example
-----------------------------------------------------
This is what runs on THE MERCHANT'S OWN SERVER — not on MeraFraud's
servers, and never in the customer's browser. The end customer buying
something never sees this code or the API key.

Where does the API key live?
  - As an environment variable on the merchant's server (MERAFRAUD_API_KEY)
  - NEVER hardcoded in source code, NEVER sent to the browser/frontend
  - Loaded once when the server starts, reused for every request

This example uses Flask, but the same pattern applies to any backend
language: Node/Express, PHP/Laravel, Django, Ruby on Rails, etc. — the
concept is identical, only the syntax changes.

Run this file directly to see it work against a local MeraFraud API
(python examples/generic_checkout_integration.py), assuming
`python api/app.py` is already running in another terminal.
"""

import os
import requests  # pip install requests

# ── 1. Configuration ─────────────────────────────────────────────────────
# In production this comes from an environment variable / secrets manager,
# set once on the server — e.g. in a .env file or your hosting provider's
# "Environment Variables" settings (Render, Vercel, etc.)
MERAFRAUD_API_BASE = os.environ.get("MERAFRAUD_API_BASE", "http://localhost:5000/api")
MERAFRAUD_API_KEY = os.environ.get("MERAFRAUD_API_KEY", "sk_demo_merafraud_dashboard")


# ── 2. Call this at the moment of checkout, BEFORE finalizing the order ──
def score_transaction(order: dict) -> dict:
    """
    `order` is whatever your own checkout already knows about this purchase.
    You map your own fields onto MeraFraud's expected fields here.
    """
    payload = {
        # Required signals for the ML model
        "transaction_amount": order["total_amount"],
        "amount_ratio_to_avg": order["total_amount"] / max(order["customer_avg_order_value"], 1),
        "account_age_days": order["customer_account_age_days"],
        "customer_ltv": order["customer_lifetime_value"],
        "time_since_last_tx_min": order["minutes_since_last_order"],
        "num_tx_last_24h": order["customer_orders_last_24h"],
        "hour_of_day": order["order_hour"],
        "num_items_in_cart": order["item_count"],
        "num_failed_payments_7d": order["customer_failed_payments_7d"],
        "login_attempts_before_purchase": order["login_attempts_this_session"],
        "billing_shipping_mismatch": int(order["billing_address"] != order["shipping_address"]),
        "ip_billing_country_mismatch": int(order["ip_country"] != order["billing_country"]),
        "new_device": int(order["is_new_device"]),
        "new_payment_method": int(order["is_new_payment_method"]),
        "free_email_domain": int(order["customer_email"].split("@")[-1] in
                                  {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}),
        "express_shipping": int(order["shipping_method"] == "express"),

        # Optional: identify the customer to factor in their order/cancel
        # history (see report_order_outcome below) — use email, phone,
        # or your own internal customer ID, whatever you have consistently.
        "customer_id": order["customer_email"],
    }

    response = requests.post(
        f"{MERAFRAUD_API_BASE}/predict",
        headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=3,  # fail fast — don't let a slow fraud check block checkout for long
    )
    response.raise_for_status()
    return response.json()


# ── 3. Act on the decision ────────────────────────────────────────────────
def handle_checkout(order: dict):
    result = score_transaction(order)
    decision = result["risk_level"]  # "approve" | "review" | "block"

    if decision == "approve":
        finalize_order(order)  # proceed exactly as normal
    elif decision == "review":
        finalize_order(order, flagged_for_review=True)  # let it through, but flag for staff
        notify_ops_team(order, result)
    elif decision == "block":
        reject_order(order, reason="Flagged as high risk")
        # Optionally: ask the customer for extra verification instead of a
        # hard rejection (e.g. SMS/email confirmation) before giving up.

    return result


# ── 4. Report what actually happened, so MeraFraud learns this customer's
#      pattern over time (see the "serial canceller" feature) ─────────────
def report_order_placed(customer_email: str, order_id: str):
    requests.post(
        f"{MERAFRAUD_API_BASE}/orders/outcome",
        headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
        json={"customer_id": customer_email, "outcome": "placed", "order_id": order_id},
        timeout=3,
    )


def report_order_cancelled(customer_email: str, order_id: str):
    requests.post(
        f"{MERAFRAUD_API_BASE}/orders/outcome",
        headers={"X-API-Key": MERAFRAUD_API_KEY, "Content-Type": "application/json"},
        json={"customer_id": customer_email, "outcome": "cancelled", "order_id": order_id},
        timeout=3,
    )


# ── Stubs standing in for your actual store logic ──────────────────────────
def finalize_order(order, flagged_for_review=False):
    print(f"✅ Order finalized. Flagged for review: {flagged_for_review}")

def reject_order(order, reason):
    print(f"⛔ Order rejected: {reason}")

def notify_ops_team(order, result):
    print(f"📣 Ops team notified — reasons: {result['reasons']}")


# ── Demo run ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample_order = {
        "total_amount": 45.0, "customer_avg_order_value": 40.0,
        "customer_account_age_days": 500, "customer_lifetime_value": 220,
        "minutes_since_last_order": 300, "customer_orders_last_24h": 1,
        "order_hour": 14, "item_count": 3, "customer_failed_payments_7d": 0,
        "login_attempts_this_session": 1,
        "billing_address": "123 Main St", "shipping_address": "123 Main St",
        "ip_country": "TR", "billing_country": "TR",
        "is_new_device": False, "is_new_payment_method": False,
        "customer_email": "loyal.customer@example.com",
        "shipping_method": "standard",
    }

    report_order_placed(sample_order["customer_email"], "ORD-1001")
    result = handle_checkout(sample_order)
    print("\nMeraFraud response:", result)
