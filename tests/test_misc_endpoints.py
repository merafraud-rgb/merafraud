def test_webhook_url_can_be_set_and_cleared(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}

    resp = client.put("/api/tenants/webhook", json={"webhook_url": "https://hooks.slack.com/services/test"},
                       headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["webhook_url"] == "https://hooks.slack.com/services/test"

    resp = client.put("/api/tenants/webhook", json={"webhook_url": None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["webhook_url"] is None


def test_webhook_url_must_be_http_or_https(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.put("/api/tenants/webhook", json={"webhook_url": "javascript:alert(1)"}, headers=headers)
    assert resp.status_code == 400


def test_network_stats_is_public(client):
    resp = client.get("/api/network/stats")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "total_reported_ips" in body


def test_blacklist_report_requires_at_least_one_identifier(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/blacklist/report", json={}, headers=headers)
    assert resp.status_code == 400


def test_blacklist_report_accepts_an_identifier(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/blacklist/report", json={"email": "known-fraudster@example.com"}, headers=headers)
    assert resp.status_code == 200


def test_order_outcome_requires_customer_and_outcome(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/orders/outcome", json={"customer_id": "x@example.com"}, headers=headers)
    assert resp.status_code == 400


def test_order_outcome_rejects_invalid_outcome_value(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/orders/outcome",
                        json={"customer_id": "x@example.com", "outcome": "teleported"},
                        headers=headers)
    assert resp.status_code == 400


def test_order_outcome_records_successfully(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/orders/outcome",
                        json={"customer_id": "x@example.com", "outcome": "cancelled"},
                        headers=headers)
    assert resp.status_code == 200


def test_admin_tenant_list_masks_api_keys(client, admin_headers, new_tenant):
    resp = client.get("/api/tenants", headers=admin_headers)
    assert resp.status_code == 200
    tenants = resp.get_json()
    match = next(t for t in tenants if t["id"] == new_tenant["id"])
    assert match["api_key"] != new_tenant["api_key"]
    assert match["api_key"].endswith(new_tenant["api_key"][-4:])
    assert "…" in match["api_key"] or "..." in match["api_key"]


def test_admin_tenant_list_requires_admin_key(client):
    resp = client.get("/api/tenants")
    assert resp.status_code == 401


def test_regenerate_key_rotates_both_live_and_test(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}
    resp = client.post("/api/tenants/regenerate-key", headers=headers)
    assert resp.status_code == 200
    rotated = resp.get_json()
    assert rotated["api_key"] != new_tenant["api_key"]
    assert rotated["api_key_test"] != new_tenant["api_key_test"]

    # the OLD live key must no longer work
    resp = client.get("/api/tenants/me", headers={"X-API-Key": new_tenant["api_key"]})
    assert resp.status_code == 403
