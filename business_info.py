"""
Structured business contact info, mirrored from kb_data/business.md.

--------------------------------------------------------------------------
SOURCE OF TRUTH: kb_data/business.md (the "## Contact" block).
The knowledge-base / retrieval pipeline still reads business.md directly and
is unchanged. This module is a small, MANUALLY MAINTAINED mirror of just the
structured contact fields, so the rest of the codebase can import typed values
(BUSINESS.phone, BUSINESS.website, ...) without parsing markdown.

If you edit the business name, phone, website, or address here, ALSO update
kb_data/business.md (and vice-versa) so they don't silently drift. The
non-fatal `verify_against_business_md()` check below logs a warning at startup
if they diverge.
--------------------------------------------------------------------------
"""
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUSINESS_MD_PATH = os.path.join(_THIS_DIR, "kb_data", "business.md")


@dataclass(frozen=True)
class BusinessInfo:
    """The business's public contact details.

    Note: business.md publishes no customer-facing email — the only contact
    channels are the phone number and the website contact page — so there is
    intentionally no `email` field here. (STAFF_EMAIL in config is an internal
    staff notification address, not a customer contact.)
    """

    name: str
    phone: str
    website: str
    address: str

    @property
    def redirect_line(self) -> str:
        """One-liner for redirecting customers with custom/off-menu/special
        requests to the business directly. Mirrors the phrasing in the
        "Custom, off-menu, and special requests" section of business.md."""
        return f"Call {self.phone} or use the contact page at {self.website}."


# Keep these values in sync with the "## Contact" block of kb_data/business.md.
BUSINESS = BusinessInfo(
    name="Talk of the Town Catering & Special Events",
    phone="770.594.1567",
    website="talkofthetownatlanta.com",
    address="2469 Canton Rd, Marietta, GA, 30066",
)


def verify_against_business_md() -> list[str]:
    """Sanity-check that the mirrored values still appear verbatim in
    business.md. Returns a list of human-readable drift warnings (empty when
    everything matches). Never raises — a missing/unreadable business.md just
    yields a single warning so this can be used as a soft startup guard without
    risking the app failing to boot.
    """
    try:
        with open(_BUSINESS_MD_PATH, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        return [f"Could not read {_BUSINESS_MD_PATH} to verify business info: {exc}"]

    warnings: list[str] = []
    for field, value in (
        ("name", BUSINESS.name),
        ("phone", BUSINESS.phone),
        ("website", BUSINESS.website),
        ("address", BUSINESS.address),
    ):
        if value not in raw:
            warnings.append(
                f"business_info.BUSINESS.{field} ({value!r}) no longer appears in "
                f"kb_data/business.md - the Python mirror and the knowledge base "
                f"may have drifted. Update one to match the other."
            )
    return warnings
