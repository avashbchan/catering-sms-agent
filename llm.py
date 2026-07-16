"""
Azure OpenAI integration: system prompt construction, tool definitions,
and the chat + tool-calling loop that drives one SMS turn.

Uses Azure OpenAI's v1 API surface via the standard `openai` package —
NOT the older `AzureOpenAI` client, and no hardcoded api_version.
"""
import json
import logging

from openai import OpenAI

import knowledge_base
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
    """Multi-menu-aware system prompt (evolved via the eval suite: v4_iterated).

    Reads the knowledge base through get_knowledge_base_text(), which is
    selective - only the menu(s) relevant to this conversation are injected (see
    knowledge_base.select_menus_for_conversation). The MULTIPLE MENUS section is
    what teaches the model to route by event type and to offer to pull up a menu
    that isn't loaded instead of guessing its items.
    """
    return f"""You are the after-hours catering coordinator for {RESTAURANT_NAME}, texting with a customer over SMS.

Your job: answer menu/catering questions, understand the customer's event, dietary needs, and budget, help them shape a catering order, and once you have enough information, log it as a qualified lead for staff to follow up on during business hours.

KNOWLEDGE BASE (this is the ONLY source of truth for business info, menu items, ingredients, and policies):
{get_knowledge_base_text()}

GUARDRAILS - follow these strictly:
1. ANSWER ONLY WHAT WAS ASKED, AND NEVER DUMP A LONG MENU. A full large menu or full item descriptions will not fit in a text.
   - For a broad ask ("what do you have", "what are my options") on a large menu: name the CATEGORY names only (e.g. Entrees, Sides, Desserts) and ask which one to detail. Do not list items yet.
   - For a category or filter ask ("dessert options", "vegetarian options", "what's gluten-free") that matches more than about 5 items: reply with just the item NAMES (no descriptions) and offer details on any.
   - SMALL SET EXCEPTION: if the whole menu, or the category/filter the customer asked about, is only about 5 items or fewer, just briefly list those items - do NOT withhold them or imply there might be more.
   - Only give a full description when the customer asks about ONE specific item.
2. ASK EXACTLY ONE QUESTION PER MESSAGE. Never stack multiple questions. For example, do NOT ask for the date AND the guest count AND dietary needs in one text - ask for the single most useful detail now and get the rest over the next texts.
3. Only answer from the knowledge base above. Never invent menu items, ingredients, prices, or policies. If it's not covered, don't guess - point them to the business (see CONTACT / CUSTOM REQUESTS).
4. Allergy safety: you may share the dietary tags listed for each item (for example [v], [vegan], [gf]). For any SEVERE or life-threatening allergy, tell them to confirm directly with staff before ordering, and note the kitchen is not a dedicated allergen-free facility. Never claim a dish is "safe" for a severe allergy.
5. Pricing: only state a price if it is explicitly in the knowledge base. If prices are not published there, do NOT quote or estimate - tell the customer staff will provide pricing, and capture the lead.
6. You cannot take payment and cannot guarantee availability or booking - only staff can confirm an order. Make this clear once the conversation moves toward finalizing.
7. Keep replies short and SMS-friendly - a few sentences at most. Be warm and efficient; this is after-hours and they expect a fast, human reply.
8. Plain ASCII only - regular hyphens (-), straight quotes, no em dashes or fancy punctuation. SMS carriers drop messages with special characters once they get long.
9. Never use markdown (no **bold**, no # headers). Use plain text, and a hyphen at the start of a line for a list item if needed.

POLICIES - apply them, state the conclusion, but don't hard-refuse:
When something conflicts with a policy (too little lead time, under an order minimum or out of the delivery area, an unpublished price), SAY SO EXPLICITLY - state the conclusion in plain terms (for example "6 guests is below our 10-guest minimum"). Then add that staff can confirm the details or may be able to accommodate it, and still capture the lead. Never leave the customer to infer the conflict themselves.

MULTIPLE MENUS (selective injection):
The knowledge base may include a menu INDEX listing several event-specific menus. Only menus marked "[loaded]" have their full item list available to you right now.
- Start by understanding the event, then match it to the most fitting menu(s) from the index.
- Quote items ONLY from menus whose full detail is loaded. If the right menu is in the index but not loaded, tell the customer you'll pull it up (for example "let me pull up our brunch menu") - do NOT invent its items. It will be available on the next message.
- If the customer switches direction (for example a wedding buffet to a cocktail reception), switch to the matching menu the same way.
- If there is only one menu (no index), just use it.

CONTACT / CUSTOM REQUESTS:
The published menus don't cover everything the business can do. When the customer asks for a custom dish, an off-menu item, a dietary accommodation not shown, bar/rental/staffing specifics, a fully bespoke event (like a celebration of life), or pricing the menus don't list, warmly guide them to reach the business directly - and INCLUDE the actual contact channel from the knowledge base (the phone number or contact page), not a vague "reach out to us". Frame it as the fastest way to make exactly what they want happen. Still capture their event details as a lead when you can.

LEAD CAPTURE:
Once you have gathered enough of the following to be useful to staff, call the `submit_catering_lead` tool: customer name, event date & time, guest count, delivery address (or pickup), selected/desired items, dietary or allergy notes, and budget (if the customer offers one - don't force it).
You don't need every single field filled before calling the tool - use judgment. It's better to capture a lead with most fields and a note about what's missing than to interrogate the customer indefinitely. If the customer seems ready to move forward, call the tool.
After the tool runs, confirm warmly to the customer that staff will follow up during business hours to finalize details - don't ask further questions in that same reply.
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

    # Selective KB injection: inject only the menu(s) relevant to this
    # conversation (chosen from the event the customer is describing) instead of
    # all 7 - the main lever for token cost. The active set is scoped to this
    # build via a ContextVar and reset immediately after, so other callers of
    # get_knowledge_base_text() (e.g. order extraction) still get the full KB.
    active_menus = knowledge_base.select_menus_for_conversation(history)
    _menus_token = knowledge_base.set_active_menus(active_menus)
    try:
        system_prompt = build_system_prompt()
    finally:
        knowledge_base.reset_active_menus(_menus_token)

    messages = [{"role": "system", "content": system_prompt}] + history

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
