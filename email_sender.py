"""
Sends the qualified-lead notification email to staff.

Supports two delivery methods, chosen automatically based on what's
configured (see config.validate_for_startup):
  - SendGrid (if SENDGRID_API_KEY is set) — sent via SendGrid's HTTP API.
  - SMTP (if SMTP_HOST is set) — sent via smtplib, no extra dependency.

Either path builds the same HTML email so the "which one is configured"
choice is invisible to the rest of the app.
"""
import base64
import logging
import re
import smtplib
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

    header = (
        '<tr style="text-align:left;border-bottom:1px solid #e5e5e5;">'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Item</th>'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Qty</th>'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Unit price</th>'
        '<th style="padding:4px 8px;font-size:12px;color:#666;">Line total</th>'
        '</tr>'
    )
    rows = "".join(
        '<tr style="border-bottom:1px solid #f0f0f0;">'
        f'<td style="padding:4px 8px;font-size:13px;">{escape(item.name)}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">{item.quantity}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">'
        f'{f"${item.unit_price:,.2f}" if item.unit_price is not None else "-"}</td>'
        f'<td style="padding:4px 8px;font-size:13px;">'
        f'{f"${item.line_total:,.2f}" if item.line_total is not None else "-"}</td>'
        '</tr>'
        for item in order_summary.items
    )
    total_row = ""
    if order_summary.order_total is not None:
        total_row = (
            '<tr>'
            '<td colspan="3" style="padding:6px 8px;font-size:13px;font-weight:bold;text-align:right;">Total</td>'
            f'<td style="padding:6px 8px;font-size:13px;font-weight:bold;">${order_summary.order_total:,.2f}</td>'
            '</tr>'
        )
    return f'<table style="width:100%;border-collapse:collapse;">{header}{rows}{total_row}</table>'


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


def _build_html_email(lead: dict, transcript: list[dict], order_summary: Optional[OrderSummary] = None) -> str:
    """
    `lead` is the dict of arguments the model passed to submit_catering_lead
    during the live conversation - used as a fallback if `order_summary`
    (the re-derived, schema-guaranteed extraction from the full transcript)
    isn't available.
    `transcript` is the full conversation history: list of {"role", "content"}.
    """
    if order_summary is not None:
        event_datetime = order_summary.event_datetime or "Not provided"
        guest_count = order_summary.guest_count
        delivery_address = order_summary.delivery_address or order_summary.delivery_or_pickup or "Not provided"
        dietary_notes = order_summary.dietary_notes or "None noted"
        budget = order_summary.budget or "Not provided"
        order_value_label = f"${order_summary.order_total:,.2f}" if order_summary.order_total is not None else "Unable to estimate"
        items_section = _items_table_from_summary(order_summary)
        customer_name = escape(str(order_summary.customer_name or lead.get("customer_name", "Unknown")))
    else:
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
        items_section = f'<ul style="margin:0;padding-left:20px;font-size:14px;">{items_list_html}</ul>{estimate_note}'
        event_datetime = lead.get("event_datetime", "Not provided")
        delivery_address = lead.get("delivery_address", "Not provided")
        dietary_notes = lead.get("dietary_notes") or "None noted"
        budget = lead.get("budget") or "Not provided"
        order_value_label = f"${estimated_total:,.2f}" if matched_items else "Unable to estimate"
        customer_name = escape(str(lead.get("customer_name", "Unknown")))

    summary_rows = "".join(
        [
            _build_summary_row("Event date/time", escape(str(event_datetime))),
            _build_summary_row("Guest count", escape(str(guest_count or "Not provided"))),
            _build_summary_row("Delivery address", escape(str(delivery_address))),
            _build_summary_row("Phone", escape(str(lead.get("phone", "Not provided")))),
            _build_summary_row("Dietary / allergy notes", escape(str(dietary_notes))),
            _build_summary_row("Budget", escape(str(budget))),
            _build_summary_row("Estimated order value", order_value_label),
        ]
    )

    open_questions_html = _open_questions_callout(order_summary)

    # The full transcript (scoped to this lead - see storage.
    # get_transcript_since_last_lead) rides along as a PDF attachment, so the
    # body just points at it instead of dumping the whole back-and-forth inline.
    message_count = len(transcript)
    transcript_note = (
        f"Full conversation transcript ({message_count} message"
        f"{'' if message_count == 1 else 's'}) is attached as a PDF."
    )

    # Body order: the transcript pointer sits up top; the lead summary (details,
    # follow-up callout, itemized order) sits at the BOTTOM of the body.
    return f"""\
<html>
<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f4f4f5;padding:24px;">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e5e5e5;">
    <div style="background:#111827;color:#fff;padding:16px 24px;">
      <h2 style="margin:0;font-size:18px;">New Catering Lead — {customer_name}</h2>
      <p style="margin:4px 0 0;font-size:13px;color:#d1d5db;">{escape(RESTAURANT_NAME)} — follow up during business hours</p>
    </div>

    <div style="padding:20px 24px;">
      <h3 style="font-size:14px;margin:0 0 8px;">Conversation transcript</h3>
      <p style="margin:0;font-size:13px;color:#374151;background:#f9fafb;border-radius:6px;padding:12px 16px;">
        📎 {escape(transcript_note)}
      </p>

      <h3 style="font-size:14px;margin:24px 0 8px;border-top:1px solid #e5e5e5;padding-top:16px;">
        Lead summary
      </h3>
      <table style="width:100%;border-collapse:collapse;">
        {summary_rows}
      </table>

      {open_questions_html}

      <h3 style="font-size:14px;margin:20px 0 8px;">Itemized order</h3>
      {items_section}
    </div>
  </div>
</body>
</html>
"""


def _send_via_smtp(subject: str, html_body: str, attachments: Optional[list[tuple[str, bytes]]] = None) -> None:
    # "mixed" (not "alternative") because we're combining an HTML body with a
    # separate file attachment, which are different parts, not alternative
    # renderings of the same content.
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.STAFF_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    for filename, data in attachments or []:
        part = MIMEApplication(data, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        if config.SMTP_USER and config.SMTP_PASSWORD:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_FROM, [config.STAFF_EMAIL], msg.as_string())


def _send_via_sendgrid(subject: str, html_body: str, attachments: Optional[list[tuple[str, bytes]]] = None) -> None:
    # Imported lazily so `sendgrid` is only required if it's actually used.
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Attachment,
        Disposition,
        FileContent,
        FileName,
        FileType,
        Mail,
    )

    message = Mail(
        from_email=config.SMTP_FROM or config.STAFF_EMAIL,
        to_emails=config.STAFF_EMAIL,
        subject=subject,
        html_content=html_body,
    )
    for filename, data in attachments or []:
        # Assigning message.attachment repeatedly appends (SendGrid's setter
        # accumulates), so this supports one or many attachments.
        message.attachment = Attachment(
            FileContent(base64.b64encode(data).decode()),
            FileName(filename),
            FileType("application/pdf"),
            Disposition("attachment"),
        )
    sg = SendGridAPIClient(config.SENDGRID_API_KEY)
    response = sg.send(message)
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid returned status {response.status_code}")


def send_lead_email(lead: dict, transcript: list[dict], order_summary: Optional[OrderSummary] = None) -> bool:
    """
    Sends the staff notification email. Returns True on success, False on
    failure (never raises) so callers can fall back to a polite customer SMS.

    `order_summary`, if provided, is the schema-guaranteed re-extraction from
    the full transcript (see order_extraction.py) and takes priority over
    `lead` for the summary fields and itemized order - `lead` (the live
    tool-call snapshot) is only used as a fallback when extraction failed.
    """
    customer_name = (order_summary.customer_name if order_summary else None) or lead.get("customer_name", "Unknown customer")
    event_datetime = (order_summary.event_datetime if order_summary else None) or lead.get("event_datetime", "date TBD")
    subject = f"Catering Lead: {customer_name} — {event_datetime}"

    attachments = _build_transcript_attachment(transcript, customer_name, lead.get("phone"))

    try:
        html_body = _build_html_email(lead, transcript, order_summary)
        if config.SENDGRID_API_KEY:
            _send_via_sendgrid(subject, html_body, attachments)
        else:
            _send_via_smtp(subject, html_body, attachments)
        logger.info("Lead email sent for customer=%s", customer_name)
        return True
    except Exception:
        logger.exception("Failed to send lead email for customer=%s", customer_name)
        return False


def _build_transcript_attachment(
    transcript: list[dict], customer_name: str, phone: Optional[str]
) -> list[tuple[str, bytes]]:
    """Render the transcript to a PDF attachment. On any failure, log and return
    no attachment rather than blocking the lead email - the summary is still in
    the body, so a broken PDF must never cost staff the notification itself."""
    try:
        pdf_bytes = build_transcript_pdf(transcript, customer_name=customer_name, phone=phone)
    except Exception:
        logger.exception("Transcript PDF generation failed; sending lead email without it")
        return []

    slug = re.sub(r"[^a-z0-9]+", "-", str(customer_name or "customer").lower()).strip("-") or "customer"
    return [(f"transcript-{slug}.pdf", pdf_bytes)]
