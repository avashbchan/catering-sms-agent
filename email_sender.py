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
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Optional

from config import config
from knowledge_base import RESTAURANT_NAME, estimate_order_value
from order_extraction import OrderSummary
from transcript_pdf import build_transcript_pdf

logger = logging.getLogger(__name__)

BRAND_NAME = "Caterable"
BRAND_ACCENT = "#E8734A"


def _format_event_datetime(value: Optional[str]) -> str:
    """Formats an ISO datetime for human scanning; passes through anything
    that isn't ISO-parseable (e.g. the extraction left the customer's
    original wording as-is because it couldn't confidently resolve a date)."""
    if not value:
        return "Not provided"
    try:
        dt = datetime.fromisoformat(value)
        formatted = dt.strftime("%a, %b %d, %Y at %I:%M %p")
        return formatted.replace(" 0", " ")  # strip leading zero from hour
    except ValueError:
        return value


def _build_summary_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:6px 12px;color:#666;font-size:13px;white-space:nowrap;'
        f'vertical-align:top;">{escape(label)}</td>'
        f'<td style="padding:6px 12px;color:#111;font-size:14px;">{value}</td>'
        f'</tr>'
    )


def _items_table_from_summary(order_summary: OrderSummary) -> str:
    if not order_summary.items:
        return '<p style="font-size:13px;color:#666;">No items itemized.</p>'

    missing_price_note = ""
    if any(item.unit_price is None or item.line_total is None for item in order_summary.items):
        missing_price_note = (
            '<p style="color:#b45309;font-size:12px;margin:0 0 6px;">'
            "⚠ Some prices need staff confirmation.</p>"
        )

    header = (
        f'<tr style="text-align:left;border-bottom:2px solid {BRAND_ACCENT};">'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Item</th>'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Qty</th>'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Unit Price</th>'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Line Total</th>'
        '</tr>'
    )
    rows = "".join(
        '<tr style="border-bottom:1px solid #f0f0f0;">'
        f'<td style="padding:4px 8px;font-size:13px;">{escape(item.name)}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">{item.quantity}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">'
        f'{f"${item.unit_price:,.2f}" if item.unit_price is not None else "—"}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">'
        f'{f"${item.line_total:,.2f}" if item.line_total is not None else "—"}</td>'
        '</tr>'
        for item in order_summary.items
    )
    total_row = ""
    if order_summary.order_total is not None:
        total_row = (
            '<tr>'
            '<td colspan="3" style="padding:6px 8px;font-size:13px;font-weight:bold;text-align:right;">Order Total</td>'
            f'<td style="padding:6px 8px;font-size:14px;font-weight:bold;color:{BRAND_ACCENT};">${order_summary.order_total:,.2f}</td>'
            '</tr>'
        )
    table = f'<table style="width:100%;border-collapse:collapse;">{header}{rows}{total_row}</table>'
    return missing_price_note + table


def _open_questions_callout(order_summary: Optional[OrderSummary]) -> str:
    if not order_summary or not order_summary.open_questions:
        return ""
    items = "".join(f"<li>{escape(q)}</li>" for q in order_summary.open_questions)
    return (
        '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;'
        'padding:10px 14px;margin:16px 0;">'
        '<p style="margin:0 0 4px;font-size:13px;font-weight:bold;color:#92400e;">'
        "⚠ Needs staff follow-up</p>"
        f'<ul style="margin:0;padding-left:18px;font-size:13px;color:#92400e;">{items}</ul>'
        "</div>"
    )


def _build_html_email(lead: dict, order_summary: Optional[OrderSummary] = None) -> str:
    """
    `lead` is the dict of arguments the model passed to submit_catering_lead
    during the live conversation - used as a fallback if `order_summary`
    (the re-derived, schema-guaranteed extraction from the full transcript)
    isn't available.

    The full conversation transcript is no longer inlined here - it's
    attached as a PDF instead (see transcript_pdf.py / send_lead_email).
    """
    if order_summary is not None:
        customer_name = order_summary.customer_name or lead.get("customer_name", "Unknown")
        event_datetime = _format_event_datetime(order_summary.event_datetime)
        guest_count = order_summary.guest_count
        dietary_notes = order_summary.dietary_notes or "None noted"
        budget = order_summary.budget or "Not provided"
        items_section = _items_table_from_summary(order_summary)

        is_delivery = order_summary.delivery_or_pickup.lower() == "delivery"
        if is_delivery and order_summary.delivery_address:
            delivery_value = f"Delivery — {order_summary.delivery_address}"
        elif order_summary.delivery_or_pickup and order_summary.delivery_or_pickup != "unspecified":
            delivery_value = order_summary.delivery_or_pickup.capitalize()
        else:
            delivery_value = "Not provided"
    else:
        customer_name = lead.get("customer_name", "Unknown")
        guest_count = lead.get("guest_count")
        selected_items = lead.get("selected_items") or []
        estimated_total, matched_items, unmatched_items = estimate_order_value(
            selected_items, guest_count
        )
        items_list_html = "".join(
            f'<li style="margin-bottom:4px;">{escape(str(i))}</li>' for i in selected_items
        ) or "<li>Not specified</li>"
        estimate_note = ""
        if unmatched_items:
            estimate_note = (
                '<p style="color:#b45309;font-size:12px;margin:4px 0 0;">'
                f"Note: could not price {len(unmatched_items)} item(s) against the menu "
                f"({escape(', '.join(str(u) for u in unmatched_items))}) - estimate may be incomplete."
                "</p>"
            )
        total_line = ""
        if matched_items:
            total_line = (
                f'<p style="font-size:13px;font-weight:bold;margin:8px 0 0;">'
                f'Order Total (estimated): <span style="color:{BRAND_ACCENT};">${estimated_total:,.2f}</span></p>'
            )
        items_section = (
            f'<ul style="margin:0;padding-left:20px;font-size:14px;">{items_list_html}</ul>{estimate_note}{total_line}'
        )
        event_datetime = _format_event_datetime(lead.get("event_datetime"))
        dietary_notes = lead.get("dietary_notes") or "None noted"
        budget = lead.get("budget") or "Not provided"
        delivery_value = lead.get("delivery_address") or "Not provided"

    summary_rows = "".join(
        [
            _build_summary_row("Customer name", escape(str(customer_name))),
            _build_summary_row("Event date/time", escape(str(event_datetime))),
            _build_summary_row("Guest count", escape(str(guest_count or "Not provided"))),
            _build_summary_row("Delivery / pickup", escape(str(delivery_value))),
            _build_summary_row("Dietary / allergy notes", escape(str(dietary_notes))),
            _build_summary_row("Budget", escape(str(budget))),
        ]
    )

    open_questions_html = _open_questions_callout(order_summary)
    customer_name_escaped = escape(str(customer_name))

    return f"""\
<html>
<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f4f4f5;padding:24px;">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="padding:20px 24px;border-bottom:3px solid {BRAND_ACCENT};">
      <div style="font-size:20px;font-weight:bold;color:{BRAND_ACCENT};">{BRAND_NAME}</div>
      <div style="font-size:13px;color:#666;margin-top:4px;">New catering lead — {customer_name_escaped}, {escape(str(event_datetime))}</div>
    </div>

    <div style="padding:20px 24px;">
      <table style="width:100%;border-collapse:collapse;">
        {summary_rows}
      </table>

      {open_questions_html}

      <h3 style="font-size:14px;margin:20px 0 8px;color:{BRAND_ACCENT};">Itemized Order</h3>
      {items_section}
    </div>

    <div style="padding:12px 24px;border-top:1px solid #eee;">
      <p style="font-size:11px;color:#999;margin:0;">Sent by {BRAND_NAME} · AI catering assistant for {escape(RESTAURANT_NAME)}</p>
    </div>
  </div>
</body>
</html>
"""


def _send_via_smtp(subject: str, html_body: str, pdf_bytes: bytes, pdf_filename: str) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.STAFF_EMAIL

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(html_body, "html"))
    msg.attach(body)

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=pdf_filename)
    msg.attach(attachment)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        if config.SMTP_USER and config.SMTP_PASSWORD:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_FROM, [config.STAFF_EMAIL], msg.as_string())


def _send_via_sendgrid(subject: str, html_body: str, pdf_bytes: bytes, pdf_filename: str) -> None:
    # Imported lazily so `sendgrid` is only required if it's actually used.
    import base64

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Attachment, Disposition, FileContent, FileName, FileType, Mail

    message = Mail(
        from_email=config.SMTP_FROM or config.STAFF_EMAIL,
        to_emails=config.STAFF_EMAIL,
        subject=subject,
        html_content=html_body,
    )
    message.attachment = Attachment(
        FileContent(base64.b64encode(pdf_bytes).decode()),
        FileName(pdf_filename),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    sg = SendGridAPIClient(config.SENDGRID_API_KEY)
    response = sg.send(message)
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid returned status {response.status_code}")


def send_lead_email(lead: dict, transcript: list[dict], order_summary: Optional[OrderSummary] = None) -> bool:
    """
    Sends the staff notification email, with the full conversation transcript
    attached as a PDF. Returns True on success, False on failure (never
    raises) so callers can fall back to a polite customer SMS.

    `order_summary`, if provided, is the schema-guaranteed re-extraction from
    the full transcript (see order_extraction.py) and takes priority over
    `lead` for the summary fields and itemized order - `lead` (the live
    tool-call snapshot) is only used as a fallback when extraction failed.
    """
    customer_name = (order_summary.customer_name if order_summary else None) or lead.get("customer_name", "Unknown customer")
    raw_event_datetime = (order_summary.event_datetime if order_summary else None) or lead.get("event_datetime")
    subject = f"Catering Lead: {customer_name} — {_format_event_datetime(raw_event_datetime) if raw_event_datetime else 'date TBD'}"
    phone_number = lead.get("phone", "unknown")
    pdf_filename = f"transcript_{phone_number}.pdf".replace(" ", "")

    try:
        html_body = _build_html_email(lead, order_summary)
        pdf_bytes = bytes(build_transcript_pdf(transcript, phone_number))
        if config.SENDGRID_API_KEY:
            _send_via_sendgrid(subject, html_body, pdf_bytes, pdf_filename)
        else:
            _send_via_smtp(subject, html_body, pdf_bytes, pdf_filename)
        logger.info("Lead email sent for customer=%s", customer_name)
        return True
    except Exception:
        logger.exception("Failed to send lead email for customer=%s", customer_name)
        return False
