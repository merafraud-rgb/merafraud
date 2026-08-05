import uuid


def test_signup_rate_limit_kicks_in_after_five_per_hour(client):
    """POST /api/tenants (public, unauthenticated self-serve signup) is
    limited to 5/hour/IP — the 6th call in the same window from the same
    client should get 429, not create a 6th tenant."""
    for i in range(5):
        resp = client.post("/api/tenants", json={"name": f"Rate Limit Test {uuid.uuid4().hex[:6]}"})
        assert resp.status_code == 201, f"call {i} unexpectedly failed: {resp.get_json()}"

    resp = client.post("/api/tenants", json={"name": f"Rate Limit Test {uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 429


def test_support_ticket_rate_limit_kicks_in_after_five_per_hour(client):
    payload = {"name": "Test User", "email": "test@example.com", "message": "hello"}
    for i in range(5):
        resp = client.post("/api/support/ticket", json=payload)
        # 503 is expected in CI (RESEND_API_KEY isn't configured there) —
        # what matters is that none of the first 5 are rate-limited yet.
        assert resp.status_code != 429, f"call {i} was rate-limited too early"

    resp = client.post("/api/support/ticket", json=payload)
    assert resp.status_code == 429
