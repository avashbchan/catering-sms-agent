"""
Talk of the Town knowledge base - MULTI-MENU, SELECTIVE-INJECTION READY.

*** THIS (plus the markdown in kb_data/) IS WHAT A NON-DEVELOPER EDITS TO
    UPDATE THE MENUS. ***

The business has several event-specific menus (barbecue, brunch, corporate,
hors d'oeuvres, seated dinner, buffet, celebration of life). The always-true
business info (policies, contact, dietary legend, custom/off-menu redirect)
lives in kb_data/business.md; each menu's items live in kb_data/menus/<key>.md.
To edit a menu, edit that markdown file. To add a menu, drop a new
kb_data/menus/<key>.md and add an entry to MENUS below.

Two rendering modes:
  - Multi-menu (production default): `get_knowledge_base_text()` returns the
    business info + a menu index + the full detail of the requested menus.
    `render_kb(active_menus)` lets you inject only the relevant menu(s) to cut
    token cost (see "Selective injection" note at the bottom).
  - Legacy simple-menu: if a flat `MENU` list is set (this is how the eval
    fixtures inject small synthetic menus with allergen tags), the renderer
    falls back to the old single-menu format. Production leaves MENU empty.
"""
import contextvars
import os
from functools import lru_cache

# Business identity. Edit here (or the business.md contact block) to rebrand.
RESTAURANT_NAME = "Talk of the Town Catering & Special Events"

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_KB_DATA = os.path.join(_THIS_DIR, "kb_data")
_MENUS_DIR = os.path.join(_KB_DATA, "menus")
_BUSINESS_PATH = os.path.join(_KB_DATA, "business.md")


# ======================================================================
# Multi-menu knowledge base
# ======================================================================

# The always-injected menu INDEX. key -> metadata. `key` must match
# kb_data/menus/<key>.md. `event_types` help a router (or the model) match a
# customer's event to a menu; `blurb` is the one-liner shown in the index so the
# model knows a menu exists even when its detail isn't loaded.
MENUS = {
    "bbq": {
        "name": "Barbecue",
        "event_types": ["cookout", "picnic", "company picnic", "casual", "bbq", "backyard"],
        "blurb": "Casual BBQ packages (pulled pork, smoked chicken, brisket, ribs), burgers and hot dogs, low country boil, sides.",
    },
    "brunch": {
        "name": "Brunch",
        "event_types": ["brunch", "morning", "shower", "baby shower", "bridal shower", "breakfast reception"],
        "blurb": "Brunch entrees, frittatas and quiche, action and carving stations, finger sandwiches, breads.",
    },
    "corporate": {
        "name": "Corporate",
        "event_types": ["corporate", "office", "business", "meeting", "office lunch", "breakfast meeting", "conference"],
        "blurb": "Business breakfasts, boxed sandwiches and wraps, executive hot and cold luncheons, sides.",
    },
    "hors_doeuvres": {
        "name": "Hors d'oeuvres & Enhancements",
        "event_types": ["cocktail", "reception", "cocktail hour", "appetizers", "enhancements", "happy hour", "mixer"],
        "blurb": "Passed and displayed appetizers, dips and displays, action and carving stations, dessert stations.",
    },
    "seated_dinner": {
        "name": "Seated Served Dinner",
        "event_types": ["wedding", "gala", "formal dinner", "plated dinner", "rehearsal dinner", "anniversary"],
        "blurb": "Formal plated dinners: plated appetizers, soups, salads, chicken/beef/seafood/pork/lamb/vegetarian entrees.",
    },
    "buffet": {
        "name": "Buffet",
        "event_types": ["wedding", "reception", "buffet", "large gathering", "party", "graduation", "celebration"],
        "blurb": "Buffet salads, entrees, vegetables, starches, and pasta sides for larger self-serve events.",
    },
    "celebration_of_life": {
        "name": "Celebration of Life",
        "event_types": ["memorial", "funeral", "celebration of life", "repast", "wake"],
        "blurb": "Fully customized memorial catering and staffing; built directly with our events team.",
    },
}


def menu_keys():
    return list(MENUS)


@lru_cache(maxsize=None)
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _normalize_active(active_menus) -> list:
    """Coerce a caller-provided active set into validated, de-duplicated known
    menu keys (order preserved)."""
    if not active_menus:
        return []
    if isinstance(active_menus, str):
        active_menus = [active_menus]
    seen = []
    for key in active_menus:
        k = str(key).strip()
        if k in MENUS and k not in seen:
            seen.append(k)
    return seen


def _menu_index(active: list) -> str:
    lines = [
        "=== AVAILABLE MENUS (event-specific) ===",
        "This business has several event-specific menus. Only the menu(s) marked "
        "[loaded] below have their full item list available right now. For any "
        "other menu, tell the customer you can pull it up - do NOT guess its "
        "items.",
    ]
    for key, meta in MENUS.items():
        state = "loaded" if key in active else "not loaded"
        events = ", ".join(meta["event_types"][:4])
        lines.append(f"- {meta['name']} [{state}] - {meta['blurb']} (good for: {events})")
    return "\n".join(lines)


def render_kb(active_menus=None) -> str:
    """Render the injected KB text for a given active menu set.

    active_menus: a menu key, a list of keys, or None. Unknown keys are ignored.
    With no active menus, only business info + the menu index are injected
    (routing mode) - useful once a menu router is wired up. `get_knowledge_base_text`
    below defaults to loading ALL menus so the agent works without a router.
    """
    active = _normalize_active(active_menus)
    parts = [_read(_BUSINESS_PATH), "", _menu_index(active)]
    if active:
        parts.append("")
        parts.append("=== LOADED MENU DETAIL ===")
        for key in active:
            parts.append("")
            parts.append(f"## {MENUS[key]['name'].upper()} MENU")
            parts.append(_read(os.path.join(_MENUS_DIR, f"{key}.md")))
    else:
        parts.append("")
        parts.append(
            "No specific menu is loaded yet. Ask what kind of event the customer "
            "is planning, match it to one of the menus above, and load that menu."
        )
    return "\n".join(parts)


# ======================================================================
# Selective injection: pick which menu(s) to inject from the conversation
#
# This is the production port of what the eval suite did with an explicit
# `active_menus` per test: choose the relevant menu(s) from the customer's
# event so only those are injected, instead of paying for all 7 every message.
# ======================================================================

# The active menu set for the current conversation turn. A ContextVar (not a
# plain global) so concurrent requests/threads don't clobber each other.
# Value None => inject ALL menus (the safe default, e.g. for order extraction);
# a list => inject exactly those; [] => index only (routing mode).
_active_menus_var = contextvars.ContextVar("active_menus", default=None)


def set_active_menus(keys):
    """Set the active menu set for the current context. Returns a token to pass
    to reset_active_menus() in a finally block."""
    return _active_menus_var.set(keys)


def reset_active_menus(token):
    _active_menus_var.reset(token)


# Trigger substrings per menu: the manifest event_types plus a few obvious menu
# names/synonyms. Matched case-insensitively against the conversation text.
def _triggers(key, meta):
    extra = {
        "bbq": ["barbecue", "bbq", "smoked", "ribs", "pulled pork"],
        "brunch": ["brunch", "breakfast"],
        "corporate": ["corporate", "office", "work lunch", "company lunch"],
        "hors_doeuvres": ["hors", "appetizer", "hors d'oeuvres", "passed", "canape", "small bites"],
        "seated_dinner": ["seated", "plated", "sit down", "sit-down", "formal dinner"],
        "buffet": ["buffet"],
        "celebration_of_life": ["celebration of life", "memorial", "funeral", "repast", "wake"],
    }.get(key, [])
    return [t.lower() for t in (list(meta["event_types"]) + [meta["name"].lower()] + extra)]


def select_menus_for_conversation(messages, max_menus=3):
    """Return the menu keys relevant to a conversation, newest mention first.

    `messages` is the {"role","content"} history (any subset). Scans newest to
    oldest so a customer who switches events (corporate -> wedding) gets the new
    menu(s) first. Returns [] when nothing matches, so the agent stays in
    routing mode (business + index only) and asks what kind of event it is.
    Capped at `max_menus` to bound token cost.
    """
    selected = []
    for msg in reversed(list(messages or [])):
        text = str((msg or {}).get("content", "")).lower()
        if not text:
            continue
        for key, meta in MENUS.items():
            if key in selected:
                continue
            if any(trigger in text for trigger in _triggers(key, meta)):
                selected.append(key)
                if len(selected) >= max_menus:
                    return selected
    return selected


# ======================================================================
# Legacy simple-menu interface (kept for backward compatibility)
#
# Production leaves MENU empty, so get_knowledge_base_text() uses the multi-menu
# renderer above. The eval suite's synthetic fixtures (small allergen-tagged
# test menus) patch MENU/CATERING_POLICY/... at runtime; when MENU is non-empty
# the renderer falls back to this single-menu format, and estimate_order_value /
# find_menu_item operate on it.
# ======================================================================

ALLERGEN_TAGS = ["nuts", "peanuts", "dairy", "gluten", "shellfish", "soy", "egg"]
DIETARY_TAGS = ["vegetarian", "vegan", "gluten-free"]
MENU = []          # empty in production -> multi-menu mode
CATERING_POLICY = {}   # populated only in legacy/eval mode


def _format_menu_for_prompt() -> str:
    lines = []
    by_category = {}
    for item in MENU:
        by_category.setdefault(item["category"], []).append(item)
    for category, items in by_category.items():
        lines.append(f"\n{category}:")
        for item in items:
            allergen_str = ", ".join(item["allergens"]) if item["allergens"] else "none listed"
            dietary_str = ", ".join(item["dietary"]) if item["dietary"] else "none"
            lines.append(
                f"  - {item['name']} - ${item['price_per_person']}/person. "
                f"{item['description']} "
                f"[Allergens: {allergen_str}] [Dietary: {dietary_str}]"
            )
    return "\n".join(lines)


def _format_policy_for_prompt() -> str:
    p = CATERING_POLICY
    return (
        f"  - Order minimum: {p['order_minimum_guests']} guests or ${p['order_minimum_dollars']}, whichever is greater.\n"
        f"  - Lead time: at least {p['lead_time_hours']} hours' notice is required.\n"
        f"  - {p['service_area_note']}\n"
        f"  - {p['delivery_fee']}\n"
        f"  - {p['deposit_note']}\n"
        f"  - {p['kitchen_note']}"
    )


def _render_legacy() -> str:
    return (
        f"=== {RESTAURANT_NAME} - CATERING MENU ===\n"
        f"{_format_menu_for_prompt()}\n\n"
        f"=== CATERING POLICIES ===\n"
        f"{_format_policy_for_prompt()}\n\n"
        f"=== TRACKED ALLERGEN TAGS ===\n{', '.join(ALLERGEN_TAGS)}\n"
        f"=== TRACKED DIETARY TAGS ===\n{', '.join(DIETARY_TAGS)}"
    )


def get_knowledge_base_text() -> str:
    """Render the KB as plain text for injection into the system prompt.

    - Legacy simple-menu mode (a flat MENU is set): the old single-menu format.
    - Multi-menu mode (production default, MENU empty): business info + index +
      the menu detail for the ACTIVE set (see set_active_menus /
      select_menus_for_conversation). When no active set is in context (e.g. a
      direct call for order extraction), all menus are injected so nothing is
      missing.
    """
    if MENU:
        return _render_legacy()
    active = _active_menus_var.get()
    keys = menu_keys() if active is None else active
    return render_kb(keys)


def find_menu_item(name: str):
    """Case-insensitive lookup of a menu item by (partial) name (legacy MENU)."""
    name_lower = name.lower().strip()
    for item in MENU:
        if item["name"].lower() == name_lower:
            return item
    for item in MENU:
        if name_lower in item["name"].lower() or item["name"].lower() in name_lower:
            return item
    return None


def estimate_order_value(selected_items, guest_count):
    """
    Best-effort estimate of order value from KB prices (legacy MENU).
    Returns (estimated_total, matched_items, unmatched_items).

    Talk of the Town's menus do not publish prices, so in production this
    returns 0 with everything unmatched - staff quote pricing. It stays
    functional for the priced eval fixtures and any future priced menu.
    """
    if not guest_count or guest_count <= 0:
        guest_count = 1
    matched_items, unmatched_items = [], []
    per_person_total = 0.0
    for raw_name in selected_items or []:
        item = find_menu_item(raw_name)
        if item:
            matched_items.append(item)
            per_person_total += item["price_per_person"]
        else:
            unmatched_items.append(raw_name)
    estimated_total = round(per_person_total * guest_count, 2)
    return estimated_total, matched_items, unmatched_items


# ----------------------------------------------------------------------
# Selective injection (token savings) - how to enable it later
#
# Today get_knowledge_base_text() injects ALL menus every message (~13k tokens
# for this business) so the agent works with no extra wiring. Most of that is
# unnecessary once you know the event type. To inject only the relevant menu(s):
#   1. Pick the active set (e.g. from the event type via MENUS[*]['event_types'],
#      or let the model request a menu through a `load_menu` tool).
#   2. Build the system prompt from render_kb(active_menus) instead of
#      get_knowledge_base_text().
# Injecting business + index + one menu is ~3k tokens vs ~13k for all menus
# (about a 77% cut). The v2+/v3/v4 prompt variants are already written to route
# by event type and to offer to "pull up" a menu that isn't loaded.
# ----------------------------------------------------------------------
