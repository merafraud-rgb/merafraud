"""
MeraFraud - Email Sending
-------------------------------
Sends real emails via SMTP if configured (see .env.example), otherwise
silently no-ops and the caller falls back to "demo mode" (e.g. showing a
reset token on screen instead of emailing it).

Works with ANY SMTP provider — Gmail, SendGrid, Postmark, Amazon SES,
Resend, your own mail server — since it uses the standard SMTP protocol,
not a provider-specific SDK. Set the SMTP_* variables in .env to enable it.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USERNAME"))


def send_email(to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """Returns True if the email was sent, False if email isn't configured
    (caller should fall back to demo behavior) or sending failed."""
    if not is_configured():
        return False

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", 587))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ.get("SMTP_PASSWORD", "")
    from_email = os.environ.get("SMTP_FROM_EMAIL", username)
    from_name = os.environ.get("SMTP_FROM_NAME", "MeraFraud")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
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
