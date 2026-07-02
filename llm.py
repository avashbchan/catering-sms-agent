"""
Azure OpenAI integration: system prompt construction, tool definitions,
and the chat + tool-calling loop that drives one SMS turn.

Uses Azure OpenAI's v1 API surface via the standard `openai` package —
NOT the older `AzureOpenAI` client, and no hardcoded api_version.
"""
import json
import logging

from openai import OpenAI

from config import config
from knowledge_base import get_knowledge_base_text, RESTAURANT_NAME

logger = logging.getLogger(__name__)

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.AZURE_OPENAI_API_KEY,
            base_url=f"{config.AZURE_OPENAI_ENDPOINT}/openai/v1",
        )
    return _client


def build_system_prompt() -> str:
    return f"""You are the after-hours catering coordinator for {RESTAURANT_NAME}, texting with a customer over SMS.

Your job: answer menu/catering questions, understand the customer's dietary needs and budget, help them shape a catering order, and once you have enough information, log it as a qualified lead for staff to follow up on during business hours.

KNOWLEDGE BASE (this is the ONLY source of truth for menu items, prices, ingredients, and policies):
{get_knowledge_base_text()}

GUARDRAILS — follow these strictly:
1. Only answer from the knowledge base above. Never invent menu items, prices, ingredients, or policies. If the customer asks about something not covered here, say you're not sure and that you'll flag it for staff to confirm.
2. Allergy safety: you may share the allergen tags listed for each item. For any SEVERE or life-threatening allergy the customer mentions, tell them to confirm directly with staff before ordering, and mention the kitchen is not a dedicated allergen-free facility. Do not offer reassurance beyond what the listed tags say — never claim a dish is "safe" for a severe allergy.
3. You cannot take payment and cannot guarantee availability or booking — only staff can confirm an order. Make this clear once the conversation moves toward finalizing an order.
4. Keep replies short and SMS-friendly (a few sentences at most). Ask ONE question at a time — don't stack multiple questions in one message.
5. Be warm and efficient. This is after-hours, so the customer expects a fast, helpful reply, not a phone tree.

LEAD CAPTURE:
Once you have gathered enough of the following to be useful to staff, call the `submit_catering_lead` tool: customer name, event date & time, guest count, delivery address, selected/desired items, dietary or allergy notes, and budget (if the customer offers one — don't force it if they don't want to share).
You don't need every single field filled before calling the tool — use judgment. It's better to capture a lead with most fields and a note about what's missing than to interrogate the customer indefinitely. If the customer seems ready to move forward (e.g. they've given you the core details and are waiting to hear next steps), call the tool.
After the tool runs, confirm warmly to the customer that staff will follow up during business hours to finalize details — don't ask further questions in that same reply.
Only call the tool once per conversation unless the customer explicitly wants to substantially change their order after already submitting."""


SUBMIT_CATERING_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_catering_lead",
        "description": (
            "Submit a qualified catering lead to restaurant staff for follow-up. "
            "Call this once you've gathered enough details about the customer's "
            "catering request to be useful to staff."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer's name"},
                "event_datetime": {
                    "type": "string",
                    "description": "Event date and time as the customer described it, e.g. 'Saturday June 14 at 6pm'",
                },
                "guest_count": {
                    "type": "integer",
                    "description": "Number of guests the order should serve",
                },
                "delivery_address": {
                    "type": "string",
                    "description": "Delivery address, or 'pickup' if the customer wants to pick up",
                },
                "selected_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Menu item names the customer wants, matching names from the knowledge base where possible",
                },
                "dietary_notes": {
                    "type": "string",
                    "description": "Any dietary restrictions or allergy notes the customer mentioned",
                },
                "budget": {
                    "type": "string",
                    "description": "Customer's stated budget, if any, e.g. '$500' or 'around $20/person'",
                },
            },
            "required": [
                "customer_name",
                "event_datetime",
                "guest_count",
                "delivery_address",
                "selected_items",
            ],
        },
    },
}


def get_assistant_reply(history: list[dict], phone_number: str, on_lead_submitted):
    """
    Runs one turn of the conversation against the model, handling a single
    round of tool calling if the model decides to submit a lead.

    `history` is a list of {"role": "user"|"assistant", "content": str},
    already trimmed to the recent window, ending with the newest user message.

    `on_lead_submitted(lead_args: dict) -> bool` is called if the model
    invokes submit_catering_lead; it should perform the actual side effect
    (sending the staff email) and return whether it succeeded. The result is
    fed back to the model so its final reply can reflect success/failure.

    Returns the final assistant text reply to send back over SMS.
    """
    client = get_client()
    messages = [{"role": "system", "content": build_system_prompt()}] + history

    response = client.chat.completions.create(
        model=config.AZURE_OPENAI_DEPLOYMENT,
        messages=messages,
        tools=[SUBMIT_CATERING_LEAD_TOOL],
    )
    choice = response.choices[0]

    if choice.message.tool_calls:
        # Append the assistant's tool-call message, then one tool result per
        # call, then ask the model for its final natural-language reply.
        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            if tool_call.function.name == "submit_catering_lead":
                try:
                    lead_args = json.loads(tool_call.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    logger.exception("Model returned malformed tool arguments")
                    lead_args = {}
                lead_args.setdefault("phone", phone_number)

                success = on_lead_submitted(lead_args)
                tool_result = (
                    "Lead successfully sent to staff."
                    if success
                    else "Lead could not be sent to staff due to a technical issue — "
                    "let the customer know staff will still be notified and to expect a follow-up call."
                )
            else:
                tool_result = f"Unknown tool: {tool_call.function.name}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

        follow_up = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            tools=[SUBMIT_CATERING_LEAD_TOOL],
        )
        return follow_up.choices[0].message.content or (
            "Thanks! Our staff will follow up with you during business hours."
        )

    return choice.message.content or "Sorry, could you rephrase that?"
