"""
MeraFraud - IP Intelligence
--------------------------------
Independently verifies a transaction's IP address instead of trusting a
client-supplied "ip_country_mismatch" boolean. Uses ip-api.com's free tier
(no API key needed, ~45 requests/minute limit — fine for an MVP; upgrade to
a paid geolocation API before high volume).

Returns country code, and — critically — whether the IP is a known
proxy/VPN or a datacenter/hosting IP, both strong fraud signals that
FraudLabsPro-style tools use and MeraFraud didn't have before.

Fails safe: if the lookup is slow, rate-limited, or unreachable, returns
None and the caller falls back to whatever the client supplied (or skips
the check) — a network hiccup should never break scoring.
"""

import requests

IP_API_URL = "http://ip-api.com/json/{ip}"
IP_API_FIELDS = "status,countryCode,proxy,hosting,query"
TIMEOUT_SECONDS = 2  # fail fast — never let this slow down checkout


def lookup_ip(ip_address: str) -> dict | None:
    if not ip_address or ip_address in ("127.0.0.1", "localhost", "::1"):
        return None  # local/test addresses aren't real signals

    try:
        resp = requests.get(
            IP_API_URL.format(ip=ip_address),
            params={"fields": IP_API_FIELDS},
            timeout=TIMEOUT_SECONDS,
        )
        data = resp.json()
        if data.get("status") != "success":
            return None
        return {
            "country_code": data.get("countryCode"),
            "is_proxy_or_vpn": bool(data.get("proxy")),
            "is_datacenter": bool(data.get("hosting")),
        }
    except Exception as e:
        print(f"[ip_intelligence] Lookup failed for {ip_address}: {e}")
        return None


def apply_ip_risk_adjustment(base_score: float, ip_info: dict | None, billing_country: str | None) -> tuple[float, list[str]]:
    """Blends real IP intelligence into the ML score. Returns (adjusted_score, extra_reasons)."""
    if not ip_info:
        return base_score, []

    score = base_score
    reasons = []

    if ip_info["is_proxy_or_vpn"]:
        score = min(0.99, score + 0.20)
        reasons.append("Customer IP is a known proxy/VPN")

    if ip_info["is_datacenter"]:
        score = min(0.99, score + 0.15)
        reasons.append("Customer IP belongs to a datacenter (not a residential/mobile connection)")

    if billing_country and ip_info["country_code"] and ip_info["country_code"] != billing_country.upper():
        score = min(0.99, score + 0.10)
        reasons.append(f"IP location ({ip_info['country_code']}) doesn't match billing country ({billing_country.upper()})")

    return score, reasons
