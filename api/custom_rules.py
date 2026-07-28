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

Data storage: PostgreSQL (DATABASE_URL) — same database tenants.py uses.
Previously this was a local JSON file, which meant every rule a merchant
configured was wiped on Render's free tier whenever the service redeployed
or woke from sleep. Moved here for the same reason tenants.py was moved.
"""

import os
import secrets
import threading

import psycopg2
import psycopg2.extras

_lock = threading.Lock()
_DB_INITIALIZED = False
_init_lock = threading.Lock()

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


def _get_conn():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it in your .env (local) or in "
            "Render's Environment tab (production) — see .env.example."
        )
    conn = psycopg2.connect(database_url)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _init_lock:
        if _DB_INITIALIZED:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS custom_rules (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    value DOUBLE PRECISION NOT NULL
                )
            """)
            cur.execute("ALTER TABLE custom_rules ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'review'")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_custom_rules_tenant ON custom_rules (tenant_id)")
        conn.commit()
        _DB_INITIALIZED = True


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
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            rule_id = f"rule_{secrets.token_hex(5)}_{field}"
            cur.execute("""
                INSERT INTO custom_rules (id, tenant_id, field, operator, value, action)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (rule_id, tenant_id, field, operator, float(value), action))
        conn.commit()
        return {"id": rule_id, "field": field, "operator": operator, "value": float(value), "action": action}
    finally:
        conn.close()


def list_rules(tenant_id: str) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, field, operator, value, action FROM custom_rules WHERE tenant_id = %s ORDER BY id",
                (tenant_id,),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_rule(tenant_id: str, rule_id: str) -> bool:
    conn = _get_conn()
    try:
        with _lock, conn.cursor() as cur:
            cur.execute("DELETE FROM custom_rules WHERE tenant_id = %s AND id = %s", (tenant_id, rule_id))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


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
