"""
Pytest configuration — repo root.
------------------------------------
Puts api/ on sys.path (mirroring how the app is actually run — `python
api/app.py`, which makes api/ the script directory and lets app.py's sibling
imports like `import tenants` resolve) and exposes a `client` fixture that
every test module uses to talk to the Flask app in-process, no server
needed.

Requires DATABASE_URL to point at a real (test) Postgres instance — see
.github/workflows/tests.yml, which spins one up as a service container.
ADMIN_API_KEY should also be set so admin-gated tests have a known value to
send back.
"""

import os
import sys
import uuid

import pytest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.join(REPO_ROOT, "api")
sys.path.insert(0, API_DIR)

# Must be set before `import app`, since app.py reads ADMIN_API_KEY at
# module import time (as a default arg to require_admin_key's closure).
os.environ.setdefault("ADMIN_API_KEY", "ci_test_admin_key")


@pytest.fixture(scope="session")
def flask_app():
    import app as merafraud_app  # noqa: local import — api/ must be on sys.path first
    merafraud_app.app.config["TESTING"] = True
    return merafraud_app.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def admin_headers():
    return {"X-Admin-Key": os.environ.get("ADMIN_API_KEY", "ci_test_admin_key")}


@pytest.fixture()
def demo_headers():
    return {"X-API-Key": "sk_demo_merafraud_dashboard"}


@pytest.fixture()
def valid_transaction_payload():
    """A complete, plausible-looking low-risk transaction — every field
    /api/predict's FEATURE_COLUMNS requires, per api/app.py's validate_transaction()."""
    return {
        "transaction_amount": 45.90,
        "amount_ratio_to_avg": 1.1,
        "account_age_days": 240,
        "customer_ltv": 310.0,
        "time_since_last_tx_min": 4200,
        "num_tx_last_24h": 1,
        "hour_of_day": 14,
        "num_items_in_cart": 3,
        "num_failed_payments_7d": 0,
        "login_attempts_before_purchase": 1,
        "billing_shipping_mismatch": 0,
        "ip_billing_country_mismatch": 0,
        "new_device": 0,
        "new_payment_method": 0,
        "free_email_domain": 0,
        "express_shipping": 0,
    }


@pytest.fixture()
def new_tenant(client):
    """Creates a fresh, isolated tenant via the real public signup endpoint
    for tests that need their own API key (so they don't share/pollute the
    shared demo tenant's usage counters or custom rules)."""
    resp = client.post("/api/tenants", json={
        "name": f"Test Store {uuid.uuid4().hex[:8]}",
        "email": f"test-{uuid.uuid4().hex[:8]}@example.com",
    })
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()
