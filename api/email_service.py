"""
MeraFraud - Email Sending
-------------------------------
Sends real emails through Resend's HTTP API, otherwise silently no-ops and
the caller falls back to "demo mode" (e.g. showing a reset token on screen
instead of emailing it).

IMPORTANT: this used to send over raw SMTP (port 587). Render's free web
services block all outbound traffic to SMTP ports (25, 465, 587) as of
September 2025 to fight spam abuse — see
https://render.com/changelog/free-web-services-will-no-longer-allow-outbound-traffic-to-smtp-ports
That's why signup/welcome emails were silently never arriving even though
SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD were all correctly set: every
connection attempt to smtp.resend.com:587 was blocked at the network level
before it ever reached Resend, with no exception raised to log. Switched
here to Resend's REST API, which goes over plain HTTPS (port 443) — not
blocked on the free tier.

Set RESEND_API_KEY (and optionally SMTP_FROM_EMAIL / SMTP_FROM_NAME) in
your .env (local) or Render's Environment tab (production) to enable this.
The RESEND_API_KEY is the same API key already created in the Resend
dashboard (previously used as SMTP_PASSWORD) — just add it under this new
name too; the old SMTP_* vars can stay, they're just unused now.
"""

import os
import requests

RESEND_API_URL = "https://api.resend.com/emails"


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def send_email(to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """Returns True if the email was sent, False if email isn't configured
    (caller should fall back to demo behavior) or sending failed."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        return False

    from_email = os.environ.get("SMTP_FROM_EMAIL", "hello@merafraud.com")
    from_name = os.environ.get("SMTP_FROM_NAME", "MeraFraud")

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    if text_body:
        payload["text"] = text_body

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code >= 400:
            print(f"[email] Failed to send to {to_email}: {resp.status_code} {resp.text}")
            return False
        return True
    except requests.RequestException as e:
        print(f"[email] Failed to send to {to_email}: {e}")
        return False


def send_password_reset_email(to_email: str, reset_token: str, store_name: str) -> bool:
    subject = "Reset your MeraFraud password"
    html = f"""
    <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
      <h2 style="color:#1c1044;">Reset your password</h2>
      <p>Hi {store_name},</p>
      <p>Use this code to reset your MeraFraud password. It expires in 60 minutes.</p>
      <div style="background:#f4f4f8; padding:16px; border-radius:8px; font-family:monospace;
                  font-size:18px; text-align:center; letter-spacing:1px; margin:20px 0;">
        {reset_token}
      </div>
      <p>If you didn't request this, you can safely ignore this email.</p>
      <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud</p>
    </div>
    """
    text = f"Reset your MeraFraud password with this code: {reset_token} (expires in 60 minutes)"
    return send_email(to_email, subject, html, text)


def send_fraud_alert_email(to_email: str, store_name: str, risk_score: float, reasons: list[str], order_amount: float | None) -> bool:
    """Notifies a merchant immediately when a transaction is auto-blocked —
    so they're not relying on manually checking the dashboard."""
    subject = f"⚠ MeraFraud blocked a high-risk order ({round(risk_score*100)}% risk)"
    reasons_html = "".join(f"<li>{r}</li>" for r in reasons)
    amount_line = f"<p>Transaction amount: <b>€{order_amount}</b></p>" if order_amount else ""
    html = f"""
    <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
      <h2 style="color:#1c1044;">High-risk order blocked</h2>
      <p>Hi {store_name}, MeraFraud just blocked a transaction with a risk score of <b>{round(risk_score*100)}%</b>.</p>
      {amount_line}
      <p><b>Reasons:</b></p>
      <ul>{reasons_html}</ul>
      <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud · You can adjust when you get alerted from your dashboard Settings.</p>
    </div>
    """
    return send_email(to_email, subject, html)


def send_welcome_email(to_email: str, store_name: str) -> bool:
    """Sent right after a merchant signs up with an email address. This was
    previously missing its own function signature — its body had been
    accidentally left as unreachable code after send_fraud_alert_email's
    return statement, which meant every real-email signup crashed with
    AttributeError: module 'email_service' has no attribute
    'send_welcome_email'. Fixed here."""
    subject = "Welcome to MeraFraud"
    html = f"""
    <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
      <h2 style="color:#1c1044;">Welcome, {store_name}! 🎉</h2>
      <p>Your MeraFraud account is ready. Your API key was shown on screen at signup —
      if you saved it, you're all set to start scoring transactions.</p>
      <p>Lost your key? You can recover it anytime by logging in at your dashboard's
      Settings page with this email and your password.</p>
      <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud</p>
    </div>
    """
    return send_email(to_email, subject, html)
