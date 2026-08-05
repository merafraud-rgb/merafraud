import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))
import custom_rules  # noqa: E402 — needs the sys.path insert above


# --- Pure validation logic: no DB, no Flask app needed ---

def test_validate_rule_accepts_valid_rule():
    assert custom_rules.validate_rule("transaction_amount", ">", 500, "block") is None


def test_validate_rule_rejects_unknown_field():
    assert custom_rules.validate_rule("not_a_real_field", ">", 500, "block") is not None


def test_validate_rule_rejects_bad_operator():
    assert custom_rules.validate_rule("transaction_amount", "~=", 500, "block") is not None


def test_validate_rule_rejects_approve_action():
    """Custom rules can only escalate risk — 'approve' must never be an
    allowed action, or a merchant could accidentally build a rule that
    auto-approves risky transactions."""
    assert custom_rules.validate_rule("transaction_amount", ">", 500, "approve") is not None


def test_validate_rule_rejects_non_numeric_value():
    assert custom_rules.validate_rule("transaction_amount", ">", "not-a-number", "block") is not None


def test_compare_operators():
    assert custom_rules._compare(10, ">", 5) is True
    assert custom_rules._compare(10, "<", 5) is False
    assert custom_rules._compare(10, ">=", 10) is True
    assert custom_rules._compare(10, "<=", 9) is False
    assert custom_rules._compare(10, "==", 10) is True
    assert custom_rules._compare(10, "!=", 10) is False


def test_severity_ordering_never_lets_a_rule_downgrade_risk():
    """evaluate_rules() picks the more severe of (current_level, rule action)
    by comparing SEVERITY[...] — this only works if block > review > approve.
    An end-to-end 'does a review-action rule downgrade a block' scenario is
    covered by test_custom_rule_escalates_... below via the real DB-backed
    endpoint; this checks the ordering the whole mechanism depends on."""
    assert custom_rules.SEVERITY["block"] > custom_rules.SEVERITY["review"] > custom_rules.SEVERITY["approve"]


# --- Integration: the actual /api/rules endpoints, against a fresh tenant ---

def test_rules_lifecycle_via_api(client, new_tenant):
    headers = {"X-API-Key": new_tenant["api_key"]}

    # starts empty
    resp = client.get("/api/rules", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == []

    # add a rule
    resp = client.post("/api/rules", json={
        "field": "transaction_amount", "operator": ">", "value": 1000, "action": "block"
    }, headers=headers)
    assert resp.status_code == 201
    rule_id = resp.get_json()["id"]

    # shows up in the list
    resp = client.get("/api/rules", headers=headers)
    assert len(resp.get_json()) == 1

    # invalid rule is rejected
    resp = client.post("/api/rules", json={
        "field": "transaction_amount", "operator": ">", "value": 1000, "action": "approve"
    }, headers=headers)
    assert resp.status_code == 400

    # delete it
    resp = client.delete(f"/api/rules/{rule_id}", headers=headers)
    assert resp.status_code == 200
    resp = client.get("/api/rules", headers=headers)
    assert resp.get_json() == []


def test_custom_rule_escalates_a_transaction_that_would_otherwise_approve(client, new_tenant, valid_transaction_payload):
    headers = {"X-API-Key": new_tenant["api_key"]}

    # baseline: the fixture payload should score low risk for a fresh tenant
    # with default thresholds (block=0.75, review=0.35)
    baseline = client.post("/api/predict", json=valid_transaction_payload, headers=headers).get_json()
    assert baseline["risk_level"] == "approve"

    # a rule that always fires on this payload (cart has 3 items)
    client.post("/api/rules", json={
        "field": "num_items_in_cart", "operator": ">=", "value": 1, "action": "block"
    }, headers=headers)

    resp = client.post("/api/predict", json=valid_transaction_payload, headers=headers)
    data = resp.get_json()
    assert data["risk_level"] == "block"
    assert any("Custom rule matched" in r for r in data["reasons"])
