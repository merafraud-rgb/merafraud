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


def send_email(to_email: str, subject: str, html_body: str, text_body: str | None = None,
               reply_to: str | None = None) -> bool:
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
    if reply_to:
        payload["reply_to"] = [reply_to]

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


def send_support_ticket_email(name: str, from_email: str, store_name: str, issue_type: str, message: str) -> bool:
    """Forwards a website support-ticket submission to the team inbox, with
    reply_to set to the customer's own address so replying to the email in
    any normal mail client goes straight back to them — no ticketing system
    needed yet for a team this size."""
    inbox = os.environ.get("SUPPORT_INBOX_EMAIL", "hello@merafraud.com")
    subject = f"[Support] {issue_type or 'General'} — {store_name or name}"
    html = f"""
    <div style="font-family:sans-serif; max-width:520px; margin:0 auto;">
      <h2 style="color:#1c1044;">New support ticket</h2>
      <p><b>From:</b> {name} &lt;{from_email}&gt;</p>
      <p><b>Store:</b> {store_name or '—'}</p>
      <p><b>Issue type:</b> {issue_type or '—'}</p>
      <p><b>Message:</b></p>
      <div style="background:#f4f4f8; padding:14px; border-radius:8px; white-space:pre-wrap;">{message}</div>
      <p style="color:#888; font-size:12px; margin-top:24px;">Reply directly to this email to respond to {name}.</p>
    </div>
    """
    text = f"From: {name} <{from_email}>\nStore: {store_name or '-'}\nIssue: {issue_type or '-'}\n\n{message}"
    return send_email(inbox, subject, html, text, reply_to=from_email)


def send_ticket_confirmation_email(to_email: str, name: str, lang: str = "tr") -> bool:
    """Sent back to the customer right after they submit a support ticket, so
    they know it actually went through instead of wondering whether the form
    worked and re-submitting. Reply-to is the team inbox so if they hit
    reply here (instead of waiting), it still reaches us. Doesn't promise a
    specific SLA number here beyond the general targets already published on
    the support page, so this stays accurate even if those targets change."""
    if lang == "en":
        subject = "We received your support ticket"
        html = f"""
        <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
          <h2 style="color:#1c1044;">We've got your message</h2>
          <p>Hi {name},</p>
          <p>Thanks for reaching out — your support ticket has been received, and a real person on our team will reply directly to this email address.</p>
          <p>Typical reply times: within 1 business day (Growth plan) or within 4 business hours (Scale plan). Starter plan tickets are handled as capacity allows.</p>
          <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud</p>
        </div>
        """
        text = f"Hi {name}, your support ticket has been received. A real person on our team will reply directly to this email address."
    else:
        subject = "Destek talebiniz alındı"
        html = f"""
        <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
          <h2 style="color:#1c1044;">Mesajınızı aldık</h2>
          <p>Merhaba {name},</p>
          <p>Bize ulaştığınız için teşekkürler — destek talebiniz alındı, ekibimizden gerçek biri bu e-posta adresine doğrudan yanıt verecek.</p>
          <p>Ortalama yanıt süreleri: Growth planında 1 iş günü içinde, Scale planında 4 iş saati içinde. Starter plan talepleri kapasiteye göre değerlendirilir.</p>
          <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud</p>
        </div>
        """
        text = f"Merhaba {name}, destek talebiniz alındı. Ekibimizden gerçek biri bu e-posta adresine doğrudan yanıt verecek."
    return send_email(to_email, subject, html, text, reply_to="hello@merafraud.com")


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


def send_webhook_alert(webhook_url: str, store_name: str, risk_score: float,
                        reasons: list[str], order_amount: float | None) -> bool:
    """POSTs a block-level fraud alert to a merchant-configured Slack/Discord/
    generic incoming-webhook URL, in addition to (not instead of) the email
    alert. Sends both 'text' (Slack's field) and 'content' (Discord's field)
    in the same payload so it works out of the box with either, plus the
    raw structured fields for a custom receiver."""
    if not webhook_url:
        return False

    pct = round(risk_score * 100)
    amount_line = f" (€{order_amount})" if order_amount else ""
    reasons_line = "; ".join(reasons[:3]) if reasons else "no single dominant reason"
    message = f"⚠ MeraFraud blocked a high-risk order for {store_name} — {pct}% risk{amount_line}. Reasons: {reasons_line}"

    payload = {
        "text": message,       # Slack incoming webhooks
        "content": message,    # Discord webhooks
        "store_name": store_name,
        "risk_score": round(risk_score, 4),
        "reasons": reasons,
        "order_amount": order_amount,
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=5)
        if resp.status_code >= 400:
            print(f"[webhook] {webhook_url} responded {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"[webhook] Failed to POST to {webhook_url}: {e}")
        return False


def send_trial_reminder_email(to_email: str, store_name: str, days_left: int, lang: str = "tr") -> bool:
    """Sent 3 days and 1 day before a tenant's free trial ends (see
    tenants.get_tenants_needing_trial_reminder / the daily trial-reminders
    GitHub Action). Without this, a merchant's API key silently starts
    returning 402 the moment the trial clock runs out, with no warning —
    this gives them a heads-up and a clear next step (contact us to go
    active) before that happens."""
    settings_url = "https://merafraud.com/dashboard/settings.html"
    if lang == "en":
        day_word = "day" if days_left == 1 else "days"
        subject = f"Your MeraFraud trial ends in {days_left} {day_word}"
        html = f"""
        <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
          <h2 style="color:#1c1044;">Your trial ends in {days_left} {day_word}</h2>
          <p>Hi {store_name},</p>
          <p>Your MeraFraud free trial ends in <b>{days_left} {day_word}</b>. After that, your API key will stop scoring transactions until your account is switched to active.</p>
          <p>To keep it running without interruption, reply to this email or reach us at <a href="mailto:hello@merafraud.com">hello@merafraud.com</a> and we'll get you set up.</p>
          <p><a href="{settings_url}">Check your account status</a></p>
          <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud</p>
        </div>
        """
        text = f"Hi {store_name}, your MeraFraud free trial ends in {days_left} {day_word}. Reply to this email or contact hello@merafraud.com to stay active without interruption."
    else:
        day_word = "gün"
        subject = f"MeraFraud deneme süreniz {days_left} {day_word} sonra bitiyor"
        html = f"""
        <div style="font-family:sans-serif; max-width:480px; margin:0 auto;">
          <h2 style="color:#1c1044;">Deneme süreniz {days_left} {day_word} sonra bitiyor</h2>
          <p>Merhaba {store_name},</p>
          <p>MeraFraud ücretsiz deneme süreniz <b>{days_left} {day_word}</b> sonra bitiyor. Bu tarihten sonra hesabınız aktif hale getirilmeden API anahtarınız işlem puanlamayı durduracak.</p>
          <p>Kesintisiz devam etmek için bu e-postayı yanıtlayabilir veya <a href="mailto:hello@merafraud.com">hello@merafraud.com</a> adresinden bize ulaşabilirsiniz.</p>
          <p><a href="{settings_url}">Hesap durumunuzu kontrol edin</a></p>
          <p style="color:#888; font-size:12px; margin-top:32px;">— MeraFraud</p>
        </div>
        """
        text = f"Merhaba {store_name}, MeraFraud ücretsiz deneme süreniz {days_left} gün sonra bitiyor. Kesintisiz devam etmek için hello@merafraud.com adresinden bize ulaşın."
    return send_email(to_email, subject, html, text, reply_to="hello@merafraud.com")


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
