"""
Render a conversation transcript to a PDF, in-process.

PDF LIBRARY CHOICE: fpdf2 (pure Python, no system dependencies). Picked over
weasyprint/xhtml2pdf (which need Cairo/Pango or lxml system libraries that
aren't guaranteed in the gunicorn/Linux deployment) and over reportlab (heavier
API for what is a simple labeled transcript). fpdf2 also runs cleanly on the
Windows dev box. This replaces embedding the whole transcript inline in the
lead email; there was never an external/Azure PDF service to remove.

Output is a clean, labeled back-and-forth: each message shows the sender
(Customer / Assistant) and its timestamp, followed by the message text.
"""
import logging
from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from business_info import BUSINESS

logger = logging.getLogger(__name__)

# Core PDF fonts (Helvetica) are Latin-1 only. SMS content is ASCII by design
# (see the "Plain ASCII only" guardrail), but sanitize defensively so an
# unexpected character can never crash PDF generation and block the lead email.
_ENCODING = "latin-1"


def _safe(text: str) -> str:
    return str(text).encode(_ENCODING, "replace").decode(_ENCODING)


def _format_timestamp(created_at: str) -> str:
    """ISO-8601 UTC (as stored by storage.add_message) -> 'Jul 24, 2026 2:32 PM UTC'.

    The 12-hour clock is built by hand rather than with strftime("%-I") because
    the no-leading-zero directive isn't portable (%-I on Linux vs %#I on Windows).
    """
    try:
        dt = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return str(created_at or "")
    hour_12 = dt.hour % 12 or 12
    return f"{dt.strftime('%b %d, %Y')} {hour_12}:{dt.strftime('%M %p')} UTC"


def build_transcript_pdf(
    transcript: list[dict],
    customer_name: str | None = None,
    phone: str | None = None,
) -> bytes:
    """Render `transcript` (list of {"role", "content", "created_at"}) to PDF bytes.

    `role` is "user" (shown as "Customer") or "assistant" (shown as
    "Assistant"). Missing timestamps are tolerated (rendered blank).
    """
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=18, top=18, right=18)
    pdf.add_page()

    def line(text: str, height: float) -> None:
        # Full-width line that always returns the cursor to the left margin.
        # fpdf2's multi_cell otherwise leaves x at the right edge (new_x
        # defaults to RIGHT), which starves the next multi_cell of width.
        pdf.multi_cell(0, height, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # --- Header ---
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(17, 24, 39)
    line("Conversation Transcript", 8)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(107, 114, 128)
    line(BUSINESS.name, 5)

    meta_bits = []
    if customer_name:
        meta_bits.append(f"Customer: {customer_name}")
    if phone:
        meta_bits.append(f"Phone: {phone}")
    if meta_bits:
        line("  |  ".join(meta_bits), 5)

    if transcript:
        first = _format_timestamp(transcript[0].get("created_at", ""))
        last = _format_timestamp(transcript[-1].get("created_at", ""))
        window = first if first == last else f"{first}  ->  {last}"
        if window:
            line(f"Window: {window}", 5)
    else:
        line("(no messages in transcript)", 5)

    pdf.ln(3)
    pdf.set_draw_color(229, 229, 229)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    # --- Messages ---
    for msg in transcript:
        is_customer = msg.get("role") == "user"
        sender = "Customer" if is_customer else "Assistant"
        timestamp = _format_timestamp(msg.get("created_at", ""))

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(30, 64, 175) if is_customer else pdf.set_text_color(17, 24, 39)
        label = f"{sender}   {timestamp}" if timestamp else sender
        line(label, 5)

        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(17, 24, 39)
        line(msg.get("content", ""), 5.5)
        pdf.ln(2.5)

    out = pdf.output()  # fpdf2 returns a bytearray when no destination is given
    return bytes(out)
