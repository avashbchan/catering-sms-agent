"""
Structured order-summary extraction.

Runs a second, dedicated Azure OpenAI call right before the staff lead email
is sent. Unlike the live in-conversation `submit_catering_lead` tool call
(which captures a snapshot of what the model believed at that moment), this
pass re-reads the *entire* transcript and produces a schema-guaranteed
summary - so if the customer corrected themselves mid-conversation (wrong
date, changed their order, etc.), the email reflects the final, corrected
state rather than whatever was true when the tool was first called.

Uses the OpenAI SDK's structured-outputs support (`.parse()` with a Pydantic
model) so the result is always schema-valid, instead of asking the model for
JSON text and hoping it parses.
"""
import json
import logging
from datetime import date
from typing import Optional

from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from config import config
from llm import get_client

logger = logging.getLogger(__name__)


class OrderItem(BaseModel):
    name: str
    quantity: int
    unit_price: Optional[float] = None  # null if price couldn't be determined from the KB
    line_total: Optional[float] = None


class OrderSummary(BaseModel):
    customer_name: Optional[str] = None
    event_datetime: Optional[str] = None
    guest_count: Optional[int] = None
    delivery_or_pickup: str = "unspecified"  # "delivery", "pickup", or "unspecified"
    delivery_address: Optional[str] = None
    dietary_notes: Optional[str] = None
    budget: Optional[str] = None
    items: list[OrderItem] = []
    order_total: Optional[float] = None
    open_questions: list[str] = []  # anything unresolved/ambiguous, for staff to confirm


_EXTRACTION_SYSTEM_PROMPT = """You extract a structured catering order summary from a conversation \
transcript between a customer and an AI catering assistant. Use the \
restaurant's menu/pricing below to fill in unit_price and line_total for \
each item. If the customer corrected an earlier detail (e.g. a wrong date \
or a changed item), use the corrected/final value, not the earlier mistaken \
one. If a field was never provided, leave it null rather than guessing. \
List anything ambiguous or unresolved in open_questions so staff can follow up.

Today's actual date is {today} ({today_weekday}). When the customer gives a \
relative date ("next Saturday", "this Friday", "in two weeks"), resolve it \
against this real date - never guess a year or use a training-data default. \
If you cannot confidently resolve a date, keep the customer's original \
wording in event_datetime as-is and add a note to open_questions instead of \
guessing.

MENU / PRICING:
{knowledge_base}"""


def _transcript_to_text(transcript: list[dict]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in transcript)


def extract_order_summary(transcript: list[dict], knowledge_base_text: str) -> Optional[OrderSummary]:
    """
    Re-derives a clean OrderSummary from the full conversation transcript.
    Returns None if extraction fails for any reason (model error, API not
    supporting structured outputs, etc.) - callers should fall back to
    whatever the live tool call captured rather than blocking the lead email.
    """
    client = get_client()
    today = date.today()
    system_prompt = _EXTRACTION_SYSTEM_PROMPT.format(
        today=today.isoformat(),
        today_weekday=today.strftime("%A"),
        knowledge_base=knowledge_base_text,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Transcript:\n" + _transcript_to_text(transcript)},
    ]

    try:
        completion = client.chat.completions.parse(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            response_format=OrderSummary,
        )
        return completion.choices[0].message.parsed
    except BadRequestError:
        # The deployment/API version in use may predate structured-outputs
        # support. Fall back to plain JSON mode and validate manually.
        logger.warning("Structured outputs not supported by this deployment, falling back to JSON mode")
        return _extract_via_json_mode(client, messages)
    except Exception:
        logger.exception("Order summary extraction failed")
        return None


def _extract_via_json_mode(client, messages: list[dict]) -> Optional[OrderSummary]:
    schema_hint = json.dumps(OrderSummary.model_json_schema())
    json_messages = messages + [
        {
            "role": "system",
            "content": f"Respond with ONLY a JSON object matching this schema:\n{schema_hint}",
        }
    ]
    try:
        completion = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=json_messages,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content
        return OrderSummary.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError):
        logger.exception("JSON-mode fallback extraction produced invalid data")
        return None
    except Exception:
        logger.exception("JSON-mode fallback extraction failed")
        return None
