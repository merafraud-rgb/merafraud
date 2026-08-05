def test_fresh_trial_tenant_can_call_predict(client, new_tenant, valid_transaction_payload):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/predict", json=valid_transaction_payload, headers=headers)
    assert resp.status_code == 200


def test_admin_can_mark_tenant_active(client, admin_headers, new_tenant):
    resp = client.put(f"/api/tenants/{new_tenant['id']}/subscription",
                       json={"status": "active"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()["subscription_status"] == "active"


def test_subscription_endpoint_requires_admin_key(client, new_tenant):
    resp = client.put(f"/api/tenants/{new_tenant['id']}/subscription", json={"status": "active"})
    assert resp.status_code == 401


def test_subscription_endpoint_rejects_bad_status(client, admin_headers, new_tenant):
    resp = client.put(f"/api/tenants/{new_tenant['id']}/subscription",
                       json={"status": "not_a_real_status"}, headers=admin_headers)
    assert resp.status_code == 400


def test_expired_tenant_is_blocked_from_predict(client, admin_headers, new_tenant, valid_transaction_payload):
    """This is the core fix behind the whole subscription-gate feature:
    trial_ends_at used to be tracked but never enforced, so a lapsed trial
    kept working forever. Marking a tenant 'expired' must now actually
    block /api/predict with a 402, not silently keep scoring."""
    client.put(f"/api/tenants/{new_tenant['id']}/subscription",
               json={"status": "expired"}, headers=admin_headers)

    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/predict", json=valid_transaction_payload, headers=headers)
    assert resp.status_code == 402
    assert resp.get_json()["reason"] == "expired"


def test_cancelled_tenant_is_blocked_from_predict(client, admin_headers, new_tenant, valid_transaction_payload):
    client.put(f"/api/tenants/{new_tenant['id']}/subscription",
               json={"status": "cancelled"}, headers=admin_headers)

    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/predict", json=valid_transaction_payload, headers=headers)
    assert resp.status_code == 402
    assert resp.get_json()["reason"] == "cancelled"


def test_active_tenant_is_never_gated_regardless_of_trial_end(client, admin_headers, new_tenant, valid_transaction_payload):
    client.put(f"/api/tenants/{new_tenant['id']}/subscription",
               json={"status": "active"}, headers=admin_headers)

    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/predict", json=valid_transaction_payload, headers=headers)
    assert resp.status_code == 200


def test_demo_tenant_is_always_active(client, demo_headers, valid_transaction_payload):
    """The demo tenant backs the public dashboard demo and must never be
    gated, even though it's the same 'trial' machinery under the hood."""
    resp = client.get("/api/tenants/me", headers=demo_headers)
    assert resp.get_json()["subscription_status"] == "active"

    resp = client.post("/api/predict", json=valid_transaction_payload, headers=demo_headers)
    assert resp.status_code == 200
