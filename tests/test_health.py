def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["model_loaded"] is True
    assert "roc_auc" in data


def test_config_public(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert "whatsapp_number" in resp.get_json()


def test_feature_importance_is_public(client):
    resp = client.get("/api/feature-importance")
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), dict)
