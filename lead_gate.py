"""
Lead-submission guard (the code-level safety net for the confirmation flow).

Before a catering lead is actually written to the DB / emailed to staff, two
independent checks must both pass - regardless of what the model decided to do
via the submit_catering_lead tool call:

  1. missing_required_fields(): a DETERMINISTIC check that all five required
     fields are present, computed from the same OrderSummary that
     order_extraction already produces (no re-implementation of extraction).
  2. customer_ready_to_submit(): an LLM JUDGMENT check that the customer's most
     recent message actually indicates they have no more questions / need no
     further help (so leads only fire after a real confirmation exchange, not
     on fixed keywords).

If either fails, the lead must NOT be submitted; the caller feeds the matching
guidance string back to the model so the conversation continues naturally
(asking for the missing detail, or restating + asking if more help is needed).

This lives next to the live conversation flow but is deliberately separate from
it: the prompt gets the model to follow the confirm-then-submit flow, and this
module enforces it even if the model jumps the gun.
"""
import logging
from typing import Optional

from config import config
from llm import get_client
from order_extraction import OrderSummary

logger = logging.getLogger(__name__)

# The five fields a lead must have before it can be submitted - same set as the
# submit_catering_lead tool's required args and the OrderSummary core fields.
REQUIRED_FIELDS = (
    "customer_name",
    "event_datetime",
    "guest_count",
    "delivery_address",
    "selected_items",
)

# Human-friendly labels for the guidance the model gets when something's missing.
_FIELD_LABELS = {
    "customer_name": "the customer's name",
    "event_datetime": "the event date and time",
    "guest_count": "the guest count",
    "delivery_address": "the delivery address (or whether they'll pick up)",
    "selected_items": "which menu items they want",
}

NEEDS_CONFIRMATION_GUIDANCE = (
    "Do NOT tell the customer a lead was submitted. Before it can be, briefly "
    "restate the order details you have back to them - their name, the event "
    "date and time, guest count, delivery or pickup, and the items - and ask if "
    "there is anything else they need help with. Only once they confirm they are "
    "all set should the lead be submitted."
)


def missing_required_fields(order_summary: Optional[OrderSummary]) -> list[str]:
    """Return the subset of REQUIRED_FIELDS that are missing/empty in
    `order_summary`. Empty list means all five are present.

    A None summary (extraction failed, so completeness can't be verified) is
    treated as everything-missing, so the lead is blocked rather than sent on
    unverified data.
    """
    if order_summary is None:
        return list(REQUIRED_FIELDS)

    missing: list[str] = []
    if not (order_summary.customer_name or "").strip():
        missing.append("customer_name")
    if not (order_summary.event_datetime or "").strip():
        missing.append("event_datetime")
    if order_summary.guest_count is None or order_summary.guest_count <= 0:
        missing.append("guest_count")
    # A concrete delivery address, OR an explicit pickup, both count as answered.
    has_delivery = bool((order_summary.delivery_address or "").strip()) or (
        order_summary.delivery_or_pickup == "pickup"
    )
    if not has_delivery:
        missing.append("delivery_address")
    if not order_summary.items:
        missing.append("selected_items")
    return missing


def missing_fields_guidance(missing: list[str]) -> str:
    """Guidance fed back to the model when required fields are missing."""
    needed = ", ".join(_FIELD_LABELS.get(f, f) for f in missing)
    return (
        "Do NOT tell the customer a lead was submitted. It cannot be yet because "
        f"you still need: {needed}. Ask the customer for the most useful missing "
        "detail now - one natural question, not a checklist."
    )


_READY_SYSTEM_PROMPT = (
    "You are a strict classifier in a catering-order chat. Decide whether the "
    "customer, in their MOST RECENT message, has indicated they have no more "
    "questions and need no further help - i.e. they are ready to wrap up.\n"
    "Answer YES if the latest customer message signals they are done or satisfied "
    "(e.g. 'nope that's it', 'all good', 'no more questions', \"that's everything, "
    "thanks\", 'sounds good, go ahead').\n"
    "Answer NO if they are still asking something, changing or adding details, or "
    "have not clearly indicated they are finished.\n"
    "Reply with exactly one word: YES or NO."
)


def customer_ready_to_submit(history: list[dict]) -> bool:
    """LLM judgment: does the customer's most recent message indicate they need
    no further help? Reuses the app's OpenAI client. Returns False (blocking the
    lead) on any uncertainty or error, so a lead never fires without a clear
    confirmation.
    """
    if not history:
        return False

    convo = "\n".join(
        f"{'Customer' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
        for m in history
    )
    user_msg = (
        f"Conversation so far:\n{convo}\n\n"
        "Does the customer's LAST message indicate they have no more questions "
        "and need no further help? Answer YES or NO."
    )

    try:
        completion = get_client().chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _READY_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        answer = (completion.choices[0].message.content or "").strip().upper()
        return answer.startswith("Y")
    except Exception:
        logger.exception("customer_ready_to_submit classification failed; blocking lead to be safe")
        return False
