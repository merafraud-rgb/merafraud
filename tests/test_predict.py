def test_predict_requires_api_key(client, valid_transaction_payload):
    resp = client.post("/api/predict", json=valid_transaction_payload)
    assert resp.status_code == 401


def test_predict_rejects_invalid_key(client, valid_transaction_payload):
    resp = client.post("/api/predict", json=valid_transaction_payload,
                        headers={"X-API-Key": "sk_live_totally_made_up"})
    assert resp.status_code == 403


def test_predict_rejects_missing_fields(client, demo_headers):
    resp = client.post("/api/predict", json={"transaction_amount": 50}, headers=demo_headers)
    assert resp.status_code == 400
    assert "Missing fields" in resp.get_json()["error"]


def test_predict_valid_transaction_scores_successfully(client, demo_headers, valid_transaction_payload):
    resp = client.post("/api/predict", json=valid_transaction_payload, headers=demo_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert 0.0 <= data["risk_score"] <= 1.0
    assert data["risk_level"] in ("approve", "review", "block")
    assert isinstance(data["reasons"], list) and len(data["reasons"]) > 0
    assert data["mode"] == "live"


def test_predict_test_key_is_flagged_and_does_not_bill(client, new_tenant, valid_transaction_payload):
    """The whole point of the sk_test_ key: it scores identically to the
    live key, but the response says mode=test and the tenant's real usage
    counters (used for billing) don't move."""
    live_headers = {"X-API-Key": new_tenant["api_key"]}
    test_headers = {"X-API-Key": new_tenant["api_key_test"]}

    before = client.get("/api/tenants/me", headers=live_headers).get_json()

    resp = client.post("/api/predict", json=valid_transaction_payload, headers=test_headers)
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "test"

    after = client.get("/api/tenants/me", headers=live_headers).get_json()
    assert after["usage"]["total_calls"] == before["usage"]["total_calls"]


def test_predict_live_key_does_bill(client, new_tenant, valid_transaction_payload):
    live_headers = {"X-API-Key": new_tenant["api_key"]}
    before = client.get("/api/tenants/me", headers=live_headers).get_json()

    resp = client.post("/api/predict", json=valid_transaction_payload, headers=live_headers)
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "live"

    after = client.get("/api/tenants/me", headers=live_headers).get_json()
    assert after["usage"]["total_calls"] == before["usage"]["total_calls"] + 1


def test_predict_batch_requires_nonempty_list(client, demo_headers):
    resp = client.post("/api/predict/batch", json={"transactions": []}, headers=demo_headers)
    assert resp.status_code == 400


def test_predict_batch_scores_multiple(client, demo_headers, valid_transaction_payload):
    resp = client.post("/api/predict/batch",
                        json={"transactions": [valid_transaction_payload, valid_transaction_payload]},
                        headers=demo_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 2
    assert len(data["results"]) == 2
