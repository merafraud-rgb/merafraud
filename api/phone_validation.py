"""
MeraFraud - Phone Validation (basic)
------------------------------------------
This is intentionally lightweight: format/pattern validation only, no
carrier lookup, no VOIP/disposable-number detection. Real carrier-level
validation (is this a VOIP line? a burner number?) requires a paid API
like Twilio Lookup or Numverify — not included here since it needs an
account + API key, same pattern as iyzico/SMTP in .env.example. If you
add one later, plug it into `check_phone()` below.

What this DOES catch: obviously fake/malformed numbers (too short, too
long, non-numeric junk) — a real fraud pattern where bots fill checkout
forms with garbage phone numbers.
"""

import re

# Very loose international format: optional +, 7-15 digits, optional spaces/dashes
PHONE_PATTERN = re.compile(r"^\+?[\d\s\-\(\)]{7,20}$")


def check_phone(phone: str) -> dict:
    """Returns {'valid_format': bool, 'digit_count': int}. Does not call
    any external service — pure format validation."""
    if not phone or not phone.strip():
        return {"valid_format": False, "digit_count": 0}

    phone = phone.strip()
    digits_only = re.sub(r"\D", "", phone)

    valid = bool(PHONE_PATTERN.match(phone)) and 7 <= len(digits_only) <= 15
    return {"valid_format": valid, "digit_count": len(digits_only)}


def apply_phone_risk_adjustment(base_score: float, phone_check: dict | None) -> tuple[float, list[str]]:
    if not phone_check or phone_check["digit_count"] == 0:
        return base_score, []  # no phone provided — not itself suspicious, many checkouts don't require it

    if not phone_check["valid_format"]:
        return min(0.99, base_score + 0.08), ["Phone number format looks invalid"]

    return base_score, []
