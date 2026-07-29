"""
MeraFraud - Card BIN (Issuer) Lookup
--------------------------------------
The first 6-8 digits of a card number (the BIN/IIN) identify which bank
issued the card and in which country. A classic fraud signal: if the card
was issued by a bank in one country but the order's billing address or IP
puts the customer somewhere completely different, that's worth flagging —
legitimate cross-border purchases happen, but a mismatch on top of other
risk factors compounds.

We only ever accept/send the BIN (first 6-8 digits), never the full card
number, never the CVV or expiry — this is public issuer-range metadata,
not PCI cardholder data.

Uses binlist.net — free, no API key required (subject to their fair-use
rate limiting). Fails safe: any error, timeout, or unrecognized BIN just
means no BIN-based signal is added, never blocks the request.
"""

import requests

BINLIST_URL = "https://lookup.binlist.net/{bin}"
TIMEOUT_SECONDS = 2.0


def lookup_bin(card_bin: str) -> dict | None:
    """card_bin: first 6-8 digits of the card number (never send the full
    PAN). Returns issuer info, or None if unrecognized/unreachable."""
    digits = "".join(c for c in str(card_bin) if c.isdigit())[:8]
    if len(digits) < 6:
        return None
    try:
        resp = requests.get(
            BINLIST_URL.format(bin=digits),
            headers={"Accept-Version": "3"},
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        country = data.get("country") or {}
        bank = data.get("bank") or {}
        return {
            "scheme": data.get("scheme"),
            "type": data.get("type"),
            "brand": data.get("brand"),
            "bank": bank.get("name"),
            "country_code": country.get("alpha2"),
            "country_name": country.get("name"),
        }
    except (requests.RequestException, ValueError):
        return None


def apply_bin_risk_adjustment(base_score: float, bin_info: dict | None, billing_country: str | None) -> tuple[float, list[str]]:
    """Flags a mismatch between the card's issuing country and the order's
    billing country. Never penalizes when we simply don't know (lookup
    failed/unrecognized) or there's nothing to compare against."""
    if not bin_info or not bin_info.get("country_code") or not billing_country:
        return base_score, []

    issuer_country = bin_info["country_code"].strip().upper()
    order_country = str(billing_country).strip().upper()
    if issuer_country == order_country:
        return base_score, []

    reason = f"Card issued in {bin_info.get('country_name') or issuer_country}, but billing country is {order_country}"
    adjusted = min(0.99, base_score + 0.15)
    return adjusted, [reason]
