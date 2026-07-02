"""
Restaurant knowledge base — MENU, ALLERGENS, AND CATERING POLICIES.

*** THIS IS THE FILE A NON-DEVELOPER EDITS TO UPDATE THE MENU. ***
No code changes are needed elsewhere: everything below is injected directly
into the AI assistant's instructions on every message, so edits here take
effect immediately the next time someone texts in.

How to edit:
  - To add/remove/change a menu item, edit the MENU list below. Each item is
    a Python dictionary — copy an existing one and change the values.
  - `allergens` should list anything from ALLERGEN tags that the dish
    CONTAINS. Leave it as an empty list [] only if the dish truly has none
    of the tracked allergens.
  - `dietary` should list which DIETARY tags the dish qualifies for.
  - `price_per_person` is in whole US dollars.
  - To change catering policies (minimums, lead time, delivery), edit the
    CATERING_POLICY dictionary further down.
  - Save the file — no restart needed if using a dev server with reload;
    otherwise restart the app for changes to take effect.

This is placeholder/sample data — replace it with your restaurant's real
menu and policies before going live.
"""

from config import config

RESTAURANT_NAME = config.RESTAURANT_NAME

# Allergens we track. Keep tags short and consistent — they're shown to
# customers verbatim.
ALLERGEN_TAGS = ["nuts", "peanuts", "dairy", "gluten", "shellfish", "soy", "egg"]

# Dietary categories we track.
DIETARY_TAGS = ["vegetarian", "vegan", "gluten-free"]

MENU = [
    {
        "name": "Herb Roasted Chicken Platter",
        "category": "Entrees",
        "description": "Bone-in chicken thighs roasted with rosemary and lemon, served with pan jus.",
        "price_per_person": 14,
        "allergens": [],
        "dietary": ["gluten-free"],
    },
    {
        "name": "Grilled Salmon with Chimichurri",
        "category": "Entrees",
        "description": "Wild-caught salmon filet, grilled and topped with a bright herb chimichurri.",
        "price_per_person": 18,
        "allergens": [],
        "dietary": ["gluten-free"],
    },
    {
        "name": "Braised Short Rib",
        "category": "Entrees",
        "description": "Red-wine braised short rib, slow-cooked until fork tender.",
        "price_per_person": 22,
        "allergens": ["soy"],
        "dietary": ["gluten-free"],
    },
    {
        "name": "Roasted Vegetable & Chickpea Bowl",
        "category": "Entrees",
        "description": "Seasonal roasted vegetables, chickpeas, and tahini dressing over grains.",
        "price_per_person": 13,
        "allergens": ["soy"],
        "dietary": ["vegetarian", "vegan", "gluten-free"],
    },
    {
        "name": "Baked Ziti",
        "category": "Entrees",
        "description": "House marinara, mozzarella, and ricotta baked with ziti pasta.",
        "price_per_person": 12,
        "allergens": ["dairy", "gluten"],
        "dietary": ["vegetarian"],
    },
    {
        "name": "Caesar Salad",
        "category": "Sides & Salads",
        "description": "Romaine, parmesan, garlic croutons, house Caesar dressing.",
        "price_per_person": 6,
        "allergens": ["dairy", "gluten", "egg"],
        "dietary": ["vegetarian"],
    },
    {
        "name": "Garden Salad",
        "category": "Sides & Salads",
        "description": "Mixed greens, tomato, cucumber, red onion, balsamic vinaigrette.",
        "price_per_person": 5,
        "allergens": [],
        "dietary": ["vegetarian", "vegan", "gluten-free"],
    },
    {
        "name": "Garlic Mashed Potatoes",
        "category": "Sides & Salads",
        "description": "Yukon gold potatoes, roasted garlic, butter, cream.",
        "price_per_person": 5,
        "allergens": ["dairy"],
        "dietary": ["vegetarian", "gluten-free"],
    },
    {
        "name": "Thai Peanut Noodle Salad",
        "category": "Sides & Salads",
        "description": "Chilled noodles tossed in a peanut-lime dressing with scallion and cilantro.",
        "price_per_person": 7,
        "allergens": ["peanuts", "soy", "gluten"],
        "dietary": ["vegetarian", "vegan"],
    },
    {
        "name": "Assorted Dinner Rolls",
        "category": "Sides & Salads",
        "description": "Warm rolls with butter.",
        "price_per_person": 3,
        "allergens": ["dairy", "gluten"],
        "dietary": ["vegetarian"],
    },
    {
        "name": "Chocolate Chunk Cookies",
        "category": "Desserts",
        "description": "House-baked cookies with dark chocolate chunks. Baked in a kitchen that also processes tree nuts.",
        "price_per_person": 4,
        "allergens": ["dairy", "gluten", "egg", "nuts"],
        "dietary": ["vegetarian"],
    },
    {
        "name": "Seasonal Fruit Tart",
        "category": "Desserts",
        "description": "Buttery tart shell, pastry cream, seasonal fresh fruit.",
        "price_per_person": 6,
        "allergens": ["dairy", "gluten", "egg"],
        "dietary": ["vegetarian"],
    },
]

CATERING_POLICY = {
    "order_minimum_guests": 10,
    "order_minimum_dollars": 150,
    "lead_time_hours": 48,
    "delivery_radius_miles": 15,
    "delivery_fee": "Delivery fee is calculated by distance and quoted by staff when the order is confirmed.",
    "service_area_note": "We currently deliver within 15 miles of the restaurant. Orders outside that radius may still be available for pickup.",
    "deposit_note": "A deposit may be required for large events; staff will confirm during follow-up.",
    "kitchen_note": (
        "Our kitchen is NOT a dedicated allergen-free facility (this includes nuts, "
        "gluten, and dairy). Cross-contact is possible even when a dish's listed "
        "allergens don't include a particular allergen."
    ),
}


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
                f"  - {item['name']} — ${item['price_per_person']}/person. "
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


def get_knowledge_base_text() -> str:
    """Render the full menu + policy as plain text for injection into the system prompt."""
    return (
        f"=== {RESTAURANT_NAME} — CATERING MENU ===\n"
        f"{_format_menu_for_prompt()}\n\n"
        f"=== CATERING POLICIES ===\n"
        f"{_format_policy_for_prompt()}\n\n"
        f"=== TRACKED ALLERGEN TAGS ===\n{', '.join(ALLERGEN_TAGS)}\n"
        f"=== TRACKED DIETARY TAGS ===\n{', '.join(DIETARY_TAGS)}"
    )


def find_menu_item(name: str):
    """Case-insensitive lookup of a menu item by (partial) name, used for price estimation."""
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
    Best-effort estimate of order value from KB prices.
    `selected_items` is a list of item name strings (as captured by the LLM).
    Returns (estimated_total, matched_items, unmatched_items).
    """
    if not guest_count or guest_count <= 0:
        guest_count = 1

    matched_items = []
    unmatched_items = []
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
