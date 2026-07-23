"""
MeraFraud - Custom Rule Engine
------------------------------------
FraudLabsPro-style "custom validation rules": merchants can define their
own simple IF-THEN rules on top of the ML model, e.g.:
    "IF transaction_amount > 500 AND account_age_days < 7 THEN block"

Rules are evaluated AFTER the ML score + customer-history + IP-intelligence
adjustments. If any rule matches, its action is compared against the
already-computed decision — whichever is MORE severe wins (block > review
> approve). This means custom rules can only make a transaction look
riskier, never override a genuine high-risk score down to "approve" —
that's a deliberate safety choice.
"""

import json
import threading
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "custom_rules.json"
_lock = threading.Lock()

ALLOWED_FIELDS = {
    "transaction_amount", "amount_ratio_to_avg", "account_age_days", "customer_ltv",
    "time_since_last_tx_min", "num_tx_last_24h", "hour_of_day", "num_items_in_cart",
    "num_failed_payments_7d", "login_attempts_before_purchase", "billing_shipping_mismatch",
    "ip_billing_country_mismatch", "new_device", "new_payment_method", "free_email_domain",
    "express_shipping",
}
ALLOWED_OPERATORS = {">", "<", ">=", "<=", "==", "!="}
ALLOWED_ACTIONS = {"review", "block"}  # rules can only escalate, never auto-approve
SEVERITY = {"approve": 0, "review": 1, "block": 2}


def _load():
    if not RULES_PATH.exists():
        return {}
    with open(RULES_PATH) as f:
        return json.load(f)


def _save(data):
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RULES_PATH, "w") as f:
        json.dump(data, f, indent=2)


def validate_rule(field: str, operator: str, value, action: str) -> str | None:
    """Returns an error message, or None if the rule is valid."""
    if field not in ALLOWED_FIELDS:
        return f"'field' must be one of: {', '.join(sorted(ALLOWED_FIELDS))}"
    if operator not in ALLOWED_OPERATORS:
        return f"'operator' must be one of: {', '.join(sorted(ALLOWED_OPERATORS))}"
    if action not in ALLOWED_ACTIONS:
        return "'action' must be 'review' or 'block' (rules can only escalate risk, not approve)"
    try:
        float(value)
    except (TypeError, ValueError):
        return "'value' must be numeric"
    return None


def add_rule(tenant_id: str, field: str, operator: str, value: float, action: str) -> dict:
    with _lock:
        data = _load()
        data.setdefault(tenant_id, [])
        rule = {
            "id": f"rule_{len(data[tenant_id]) + 1}_{field}",
            "field": field, "operator": operator, "value": float(value), "action": action,
        }
        data[tenant_id].append(rule)
        _save(data)
        return rule


def list_rules(tenant_id: str) -> list:
    return _load().get(tenant_id, [])


def delete_rule(tenant_id: str, rule_id: str) -> bool:
    with _lock:
        data = _load()
        rules = data.get(tenant_id, [])
        new_rules = [r for r in rules if r["id"] != rule_id]
        if len(new_rules) == len(rules):
            return False  # nothing removed
        data[tenant_id] = new_rules
        _save(data)
        return True


def _compare(actual, operator, target):
    if operator == ">": return actual > target
    if operator == "<": return actual < target
    if operator == ">=": return actual >= target
    if operator == "<=": return actual <= target
    if operator == "==": return actual == target
    if operator == "!=": return actual != target
    return False


def evaluate_rules(tenant_id: str, row: dict, current_level: str) -> tuple[str, list[str]]:
    """Checks all of the tenant's custom rules against this transaction.
    Returns (final_level, reasons) — final_level is only ever equal to or
    MORE severe than current_level, never less."""
    rules = list_rules(tenant_id)
    if not rules:
        return current_level, []

    final_level = current_level
    reasons = []
    for rule in rules:
        field_value = row.get(rule["field"])
        if field_value is None:
            continue
        if _compare(field_value, rule["operator"], rule["value"]):
            reasons.append(f"Custom rule matched: {rule['field']} {rule['operator']} {rule['value']}")
            if SEVERITY[rule["action"]] > SEVERITY[final_level]:
                final_level = rule["action"]

    return final_level, reasons
