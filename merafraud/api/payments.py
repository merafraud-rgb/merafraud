"""
MeraFraud - iyzico Payment Integration
------------------------------------------
Turkey-based payment provider (Stripe doesn't support Turkey-registered
businesses — see chat history). Uses iyzico's REST API directly via
`requests` (no extra SDK install needed — `iyzipay` the official Python
SDK isn't required, this talks to their HTTP API directly).

To activate: fill in IYZICO_API_KEY / IYZICO_SECRET_KEY in .env (get
these from https://merchant.iyzipay.com → Settings → API, use the
SANDBOX keys first to test with fake cards before going live).

Docs: https://docs.iyzico.com/en/products/checkout-form

⚠️ NOT YET LIVE-TESTED: this code is written against iyzico's documented
API contract, but has not been run against a real iyzico account (no
sandbox credentials were available while building this). Before
accepting real payments, test thoroughly with iyzico's sandbox test
cards first.
"""

import os
import json
import random
import string
import hashlib
import hmac
import base64
import requests

IYZICO_BASE_URL = os.environ.get("IYZICO_BASE_URL", "https://sandbox-api.iyzipay.com")


def is_configured() -> bool:
    return bool(os.environ.get("IYZICO_API_KEY") and os.environ.get("IYZICO_SECRET_KEY"))


def _random_string(length=8):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def _generate_auth_header(request_body: dict, uri_path: str) -> dict:
    """iyzico's HMAC-SHA256 based auth scheme (IYZWSv2)."""
    api_key = os.environ["IYZICO_API_KEY"]
    secret_key = os.environ["IYZICO_SECRET_KEY"]
    random_key = _random_string(8) + str(random.randint(100000, 999999))

    body_json = json.dumps(request_body, separators=(",", ":"))
    data_to_sign = random_key + uri_path + body_json
    signature = hmac.new(secret_key.encode(), data_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_params = f"apiKey:{api_key}&randomKey:{random_key}&signature:{signature}"
    authorization = "IYZWSv2 " + base64.b64encode(auth_params.encode()).decode()

    return {
        "Authorization": authorization,
        "x-iyzi-rnd": random_key,
        "Content-Type": "application/json",
    }


def create_checkout_form(plan_name: str, price: float, currency: str, buyer_email: str,
                          callback_url: str) -> dict:
    """Creates an iyzico hosted checkout session. Returns a dict with
    either `checkout_form_content` (HTML/JS to embed) or an `error`.

    If iyzico isn't configured, returns a demo-mode response instead of
    calling the real API — lets the checkout page still be testable."""
    if not is_configured():
        return {
            "demo_mode": True,
            "message": "iyzico not configured — see .env.example. This is a simulated response.",
        }

    uri_path = "/payment/iyzipos/checkoutform/initialize/auth/ecom"
    conversation_id = _random_string(12)

    body = {
        "locale": "tr",
        "conversationId": conversation_id,
        "price": str(price),
        "paidPrice": str(price),
        "currency": currency,
        "basketId": conversation_id,
        "paymentGroup": "SUBSCRIPTION",
        "callbackUrl": callback_url,
        "buyer": {
            "id": buyer_email,
            "name": "MeraFraud",
            "surname": "Customer",
            "email": buyer_email,
            "identityNumber": "11111111111",  # placeholder — collect real TCKN/VKN in production
            "registrationAddress": "N/A",
            "ip": "127.0.0.1",
            "city": "Istanbul",
            "country": "Turkey",
        },
        "shippingAddress": {
            "contactName": "MeraFraud Customer", "city": "Istanbul", "country": "Turkey",
            "address": "N/A",
        },
        "billingAddress": {
            "contactName": "MeraFraud Customer", "city": "Istanbul", "country": "Turkey",
            "address": "N/A",
        },
        "basketItems": [{
            "id": plan_name, "name": f"MeraFraud {plan_name} Plan",
            "category1": "SaaS Subscription", "itemType": "VIRTUAL", "price": str(price),
        }],
    }

    headers = _generate_auth_header(body, uri_path)

    try:
        resp = requests.post(IYZICO_BASE_URL + uri_path, json=body, headers=headers, timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}
