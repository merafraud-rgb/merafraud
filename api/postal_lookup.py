"""
MeraFraud - Postal/ZIP Code Validation
------------------------------------------
Checks whether a submitted postal/ZIP code actually exists in the claimed
billing country — and, when a city is also provided, whether it plausibly
matches. A postal code that doesn't resolve at all, or resolves to a
wildly different place than the claimed city, is a classic sign of a
fabricated billing address (common in card-testing and reshipping fraud).

Uses Zippopotam.us — free, no API key required. Fails safe: a network
error or timeout returns None (no signal added, never blocks the
request) — distinct from a successful lookup that comes back "not
found", which IS a real signal.
"""

import requests

ZIPPOPOTAM_URL = "https://api.zippopotam.us/{country}/{postal_code}"
TIMEOUT_SECONDS = 2.0


def lookup_postal_code(postal_code: str, country_code: str) -> dict | None:
    """country_code: ISO 3166-1 alpha-2 (e.g. 'TR', 'DE', 'US').
    Returns {"valid": bool, "places": [...]} on a completed lookup, or
    None if the lookup itself couldn't be completed (network/timeout) —
    callers should treat None as "unknown, don't penalize"."""
    if not postal_code or not country_code:
        return None
    code = str(postal_code).strip().replace(" ", "")
    country = str(country_code).strip().lower()
    try:
        resp = requests.get(
            ZIPPOPOTAM_URL.format(country=country, postal_code=code),
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            return {"valid": False, "places": []}
        if resp.status_code != 200:
            return None  # unexpected error — fail safe, no signal
        data = resp.json()
        places = [p.get("place name") for p in data.get("places", []) if p.get("place name")]
        return {"valid": True, "country": data.get("country"), "places": places}
    except (requests.RequestException, ValueError):
        return None  # network/timeout — fail safe, no signal


def apply_postal_risk_adjustment(base_score: float, postal_check: dict | None, claimed_city: str | None) -> tuple[float, list[str]]:
    """postal_check is None both when we never looked (no postal_code
    given) and when the lookup itself failed — either way, no penalty.
    Only an actual "not found" or city mismatch counts as a signal."""
    if postal_check is None:
        return base_score, []

    if not postal_check.get("valid"):
        reason = "Billing postal code does not exist in the claimed country"
        adjusted = min(0.99, base_score + 0.20)
        return adjusted, [reason]

    if claimed_city and postal_check.get("places"):
        claimed = str(claimed_city).strip().lower()
        matches = any(claimed in p.lower() or p.lower() in claimed for p in postal_check["places"])
        if not matches:
            reason = f"Billing postal code doesn't match claimed city '{claimed_city}'"
            adjusted = min(0.99, base_score + 0.12)
            return adjusted, [reason]

    return base_score, []
