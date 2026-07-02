"""
Renders a conversation transcript as a PDF, attached to the staff lead email.

Uses fpdf2 (already a project dependency, pulled in for the knowledge-base
export) rather than adding reportlab as a second PDF library for the same job.
"""
from fpdf import FPDF

from knowledge_base import RESTAURANT_NAME

PAGE_WIDTH = 210 - 20  # A4 minus 10mm margins each side

CUSTOMER_COLOR = (17, 24, 39)      # dark gray/near-black
ASSISTANT_COLOR = (232, 115, 74)   # brand accent

# The core "Helvetica" font only supports Latin-1. Transcript content is
# arbitrary customer/model text (em dashes, curly quotes, emoji, accented
# names), so map common punctuation to ASCII and replace anything else that
# still doesn't fit, rather than letting PDF generation crash and silently
# drop the staff notification.
_UNICODE_REPLACEMENTS = {
    "—": "-", "–": "-",       # em/en dash
    "‘": "'", "’": "'",       # curly single quotes
    "“": '"', "”": '"',       # curly double quotes
    "…": "...",                    # ellipsis
}


def _sanitize_text(text: str) -> str:
    for char, replacement in _UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def build_transcript_pdf(transcript: list[dict], phone_number: str) -> bytearray:
    """Returns the PDF file content as bytes, ready to attach - not written to disk."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(10, 10, 10)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(PAGE_WIDTH, 9, _sanitize_text(f"{RESTAURANT_NAME} - Conversation Transcript"))
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(PAGE_WIDTH, 6, _sanitize_text(f"Customer: {phone_number}"))
    pdf.ln(10)
    pdf.set_text_color(0, 0, 0)

    for message in transcript:
        speaker = "Customer" if message["role"] == "user" else "Assistant"
        color = CUSTOMER_COLOR if message["role"] == "user" else ASSISTANT_COLOR

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*color)
        pdf.set_x(10)
        pdf.cell(PAGE_WIDTH, 6, speaker)
        pdf.ln(6)

        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 30)
        pdf.set_x(10)
        pdf.multi_cell(PAGE_WIDTH, 5, _sanitize_text(message["content"]))
        pdf.ln(3)

    return pdf.output()
