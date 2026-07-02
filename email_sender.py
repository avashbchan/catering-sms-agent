"""
Sends the qualified-lead notification email to staff.

Supports two delivery methods, chosen automatically based on what's
configured (see config.validate_for_startup):
  - SendGrid (if SENDGRID_API_KEY is set) — sent via SendGrid's HTTP API.
  - SMTP (if SMTP_HOST is set) — sent via smtplib, no extra dependency.

Either path builds the same HTML email so the "which one is configured"
choice is invisible to the rest of the app.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from config import config
from knowledge_base import RESTAURANT_NAME, estimate_order_value

logger = logging.getLogger(__name__)


def _build_summary_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:6px 12px;color:#666;font-size:13px;white-space:nowrap;'
        f'vertical-align:top;">{escape(label)}</td>'
        f'<td style="padding:6px 12px;color:#111;font-size:14px;">{value}</td>'
        f'</tr>'
    )


def _build_html_email(lead: dict, transcript: list[dict]) -> str:
    """
    `lead` is the dict of arguments the model passed to submit_catering_lead.
    `transcript` is the full conversation history: list of {"role", "content"}.
    """
    guest_count = lead.get("guest_count")
    selected_items = lead.get("selected_items") or []
    estimated_total, matched_items, unmatched_items = estimate_order_value(
        selected_items, guest_count
    )

    items_html = "".join(
        f'<li style="margin-bottom:4px;">{escape(str(i))}</li>' for i in selected_items
    ) or "<li>Not specified</li>"

    estimate_note = ""
    if unmatched_items:
        estimate_note = (
            '<p style="color:#b45309;font-size:12px;margin:4px 0 0;">'
            f"Note: could not price {len(unmatched_items)} item(s) against the menu "
            f"({escape(', '.join(str(u) for u in unmatched_items))}) — estimate may be incomplete."
            "</p>"
        )

    summary_rows = "".join(
        [
            _build_summary_row("Event date/time", escape(str(lead.get("event_datetime", "Not provided")))),
            _build_summary_row("Guest count", escape(str(guest_count or "Not provided"))),
            _build_summary_row("Delivery address", escape(str(lead.get("delivery_address", "Not provided")))),
            _build_summary_row("Phone", escape(str(lead.get("phone", "Not provided")))),
            _build_summary_row(
                "Dietary / allergy notes",
                escape(str(lead.get("dietary_notes") or "None noted")),
            ),
            _build_summary_row("Budget", escape(str(lead.get("budget") or "Not provided"))),
            _build_summary_row(
                "Estimated order value",
                f"${estimated_total:,.2f}" if matched_items else "Unable to estimate",
            ),
        ]
    )

    transcript_html = "".join(
        f'<p style="margin:4px 0;font-size:13px;">'
        f'<strong>{"Customer" if m["role"] == "user" else "Assistant"}:</strong> '
        f'{escape(m["content"])}</p>'
        for m in transcript
    )

    customer_name = escape(str(lead.get("customer_name", "Unknown")))

    return f"""\
<html>
<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f4f4f5;padding:24px;">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#111827;color:#fff;padding:16px 24px;">
      <h2 style="margin:0;font-size:18px;">New Catering Lead — {customer_name}</h2>
      <p style="margin:4px 0 0;font-size:13px;color:#d1d5db;">{escape(RESTAURANT_NAME)} — follow up during business hours</p>
    </div>

    <div style="padding:20px 24px;">
      <table style="width:100%;border-collapse:collapse;">
        {summary_rows}
      </table>

      <h3 style="font-size:14px;margin:20px 0 8px;">Recommended / requested items</h3>
      <ul style="margin:0;padding-left:20px;font-size:14px;">
        {items_html}
      </ul>
      {estimate_note}

      <h3 style="font-size:14px;margin:24px 0 8px;border-top:1px solid #e5e5e5;padding-top:16px;">
        Full conversation transcript
      </h3>
      <div style="background:#f9fafb;border-radius:6px;padding:12px 16px;">
        {transcript_html}
      </div>
    </div>
  </div>
</body>
</html>
"""


def _send_via_smtp(subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.STAFF_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        if config.SMTP_USER and config.SMTP_PASSWORD:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_FROM, [config.STAFF_EMAIL], msg.as_string())


def _send_via_sendgrid(subject: str, html_body: str) -> None:
    # Imported lazily so `sendgrid` is only required if it's actually used.
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=config.SMTP_FROM or config.STAFF_EMAIL,
        to_emails=config.STAFF_EMAIL,
        subject=subject,
        html_content=html_body,
    )
    sg = SendGridAPIClient(config.SENDGRID_API_KEY)
    response = sg.send(message)
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid returned status {response.status_code}")


def send_lead_email(lead: dict, transcript: list[dict]) -> bool:
    """
    Sends the staff notification email. Returns True on success, False on
    failure (never raises) so callers can fall back to a polite customer SMS.
    """
    customer_name = lead.get("customer_name", "Unknown customer")
    event_datetime = lead.get("event_datetime", "date TBD")
    subject = f"Catering Lead: {customer_name} — {event_datetime}"

    try:
        html_body = _build_html_email(lead, transcript)
        if config.SENDGRID_API_KEY:
            _send_via_sendgrid(subject, html_body)
        else:
            _send_via_smtp(subject, html_body)
        logger.info("Lead email sent for customer=%s", customer_name)
        return True
    except Exception:
        logger.exception("Failed to send lead email for customer=%s", customer_name)
        return False
