"""Chat Service - Conversational AI agent with tool execution."""
from typing import Dict, Any, Optional, List
import logging
import uuid
import json
import re
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core import database as db_module
from app.models.product import Product
from app.models.category import Category
from app.models.brand import Brand
from app.models.vendor import Vendor
from app.schemas.chat import ChatResponse, ActionButton, ChatCard

from app.services.product_service import ProductService
from app.services.ai_service import ChatAIService

logger = logging.getLogger(__name__)

# Tool definitions for function calling

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search for building material products by name, category, price range, or brand",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Search term for product name"},
                    "category": {"type": "string", "description": "Exact category name to filter by (e.g. 'Roofing Systems', 'Cement')"},
                    "min_price": {"type": "number", "description": "Minimum price filter"},
                    "max_price": {"type": "number", "description": "Maximum price filter"},
                    "brand": {"type": "string", "description": "Brand name to filter by"},
                    "page": {"type": "integer", "description": "Page number", "default": 1},
                    "page_size": {"type": "integer", "description": "Results per page", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Get detailed information about a specific product by ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The UUID of the product"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_categories",
            "description": "Search product categories by name or division",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Category name or keyword to search"},
                    "division": {"type": "string", "description": "Filter by division (e.g. Structure, Finishes, MEP)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_suppliers",
            "description": "Find verified suppliers/vendors by business name or location",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Business name or keyword to search"},
                    "location": {"type": "string", "description": "City or state to filter by"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_brands",
            "description": "Search for brands by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Brand name to search"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_alternatives",
            "description": "Find alternative products in the same category when a product is not found",
            "parameters": {
                "type": "object",
                "properties": {
                    "category_name": {"type": "string", "description": "Category name to find alternatives in"},
                    "product_name": {"type": "string", "description": "Original product name for context"},
                    "max_price": {"type": "number", "description": "Maximum price for alternatives"},
                },
                "required": ["category_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_availability",
            "description": "Check stock availability for a product",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The UUID of the product"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_price_history",
            "description": "Get current pricing and discount info for a product",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The UUID of the product"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_vendor_request",
            "description": "Create a sourcing request for a product not currently in our catalog",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "Name of the product being requested"},
                    "quantity": {"type": "integer", "description": "Quantity needed"},
                    "specifications": {"type": "string", "description": "Any specifications or requirements"},
                },
                "required": ["product_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_vendor_request",
            "description": "Check the status of a previously created vendor sourcing request",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "The UUID of the vendor request"},
                },
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_prices",
            "description": "Compare verified DB offers for a material in a city, including total procurement cost",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Material description"},
                    "quantity": {"type": "number", "description": "Quantity needed", "default": 1.0},
                    "city": {"type": "string", "description": "City (e.g. Abuja, Lagos)", "default": "Abuja"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_range",
            "description": "Get verified min/max price range for a material in a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Material description"},
                    "city": {"type": "string", "description": "City", "default": "Abuja"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": "Get price history and trend for a material from verified records",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Material description"},
                    "city": {"type": "string", "description": "City", "default": "Abuja"},
                    "limit": {"type": "integer", "description": "Max history points", "default": 12},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyse_quotation",
            "description": "Analyse a supplier quotation text against verified DB market rates",
            "parameters": {
                "type": "object",
                "properties": {
                    "quote_text": {"type": "string", "description": "The quotation text"},
                    "supplier_name": {"type": "string", "description": "Supplier name"},
                },
                "required": ["quote_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_savings",
            "description": "Compare two suppliers' quotes against verified DB market rates",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of {description, quantity, quoted_rate}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quantity": {"type": "number"},
                                "quoted_rate": {"type": "number"},
                            },
                            "required": ["description"],
                        },
                    },
                    "supplier_a": {"type": "object", "description": "Supplier A rates keyed by description"},
                    "supplier_b": {"type": "object", "description": "Supplier B rates keyed by description"},
                },
                "required": ["items", "supplier_a", "supplier_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_procurement_score",
            "description": "Get an explainable procurement score (0-100) for a list of quoted items",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of {description, quoted_rate}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quoted_rate": {"type": "number"},
                            },
                            "required": ["description"],
                        },
                    },
                    "city": {"type": "string", "description": "City", "default": "Abuja"},
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_supplier_quotes",
            "description": "Compare two suppliers' itemized quotes for the same basket of materials and recommend the cheaper option with line-level deltas",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of {description, quantity}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quantity": {"type": "number"},
                            },
                            "required": ["description"],
                        },
                    },
                    "supplier_a": {"type": "object", "description": "Supplier A rates keyed by description"},
                    "supplier_b": {"type": "object", "description": "Supplier B rates keyed by description"},
                    "supplier_a_name": {"type": "string", "description": "Display name for supplier A", "default": "Supplier A"},
                    "supplier_b_name": {"type": "string", "description": "Display name for supplier B", "default": "Supplier B"},
                },
                "required": ["items", "supplier_a", "supplier_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products_by_quantity",
            "description": "Find products that meet a required quantity in stock",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Product search term"},
                    "quantity": {"type": "number", "description": "Required quantity"},
                    "city": {"type": "string", "description": "City", "default": "Abuja"},
                },
                "required": ["search", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_boq",
            "description": "Get the BOQs for a project to understand required materials",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project ID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_remaining_materials",
            "description": "Get what materials are still needed (remaining) for a project",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project ID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_procurement_recommendation",
            "description": "Get a 'what should I buy now' recommendation for a project",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project ID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_supplier_for_products",
            "description": "Find verified suppliers that carry a set of products in a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "products": {
                        "type": "array",
                        "description": "List of product names",
                        "items": {"type": "string"},
                    },
                    "city": {"type": "string", "description": "City", "default": "Abuja"},
                },
                "required": ["products"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": "Add a product to the user's cart",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product UUID"},
                    "quantity": {"type": "integer", "description": "Quantity", "default": 1},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Place an order (requires explicit user confirmation; will ask before executing)",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of {description, quantity, rate}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quantity": {"type": "number"},
                                "rate": {"type": "number"},
                            },
                            "required": ["description"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Get the status of an order",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order ID"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cheapest_price",
            "description": (
                "Find the cheapest catalog listing(s) for a specific product. Returns the "
                "unit rate, the marketer (supplier business name), the market/location, the "
                "minimum order quantity and the MOQ-inclusive procurement total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Product name to price (e.g. 'Dangote cement')"},
                    "location": {"type": "string", "description": "City or state to prefer", "default": ""},
                    "limit": {"type": "integer", "description": "How many cheapest offers to return", "default": 3},
                },
                "required": ["search"],
            },
        },
    },
]

# ── Reply formatting ─────────────────────────────────────────────────────────
# The chat bubble renders plain text (no markdown), so model-emitted markdown
# markers would otherwise be shown as literal characters.

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_UNDERSCORE_BOLD = re.compile(r"__(.+?)__", re.S)
_MD_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])")
_MD_BULLET = re.compile(r"^[ \t]*\*[ \t]+", re.M)
_MD_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.M)
_MD_CODE = re.compile(r"`+")
_MD_BLANK_LINES = re.compile(r"\n{3,}")


def _strip_markdown(text: str) -> str:
    """Flatten markdown markers so replies read cleanly as plain text.

    Handles **bold**, __bold__, *italic*, leading "* " bullets, "#" headings and
    backticks. Deliberately leaves mid-line single asterisks (multiplication),
    dashes and numbered lists untouched.
    """
    if not text:
        return text
    out = _MD_BOLD.sub(r"\1", text)
    out = _MD_UNDERSCORE_BOLD.sub(r"\1", out)
    out = _MD_BULLET.sub("• ", out)
    out = _MD_ITALIC.sub(r"\1", out)
    out = _MD_HEADING.sub("", out)
    out = _MD_CODE.sub("", out)
    out = _MD_BLANK_LINES.sub("\n\n", out)
    return out.strip()


# Completion claims that must never reach the shopper without a backing tool
# result. The model occasionally asserts "10 ... have been added to your cart"
# without calling add_to_cart — which also bypasses the minimum-order guard.
_CART_CLAIM_MARKERS = (
    "added to your cart",
    "added to cart",
    "added to the cart",
    "have been added",
    "has been added",
    "added it to your cart",
    # Guest/device-cart confirmation wording — the app's primary add phrasing.
    "saved to your cart",
    "saved to cart",
    "been saved to",
    "now in your cart",
    "in your cart now",
    "removed from your cart",
    "order has been placed",
    "order was placed",
    "placed your order",
    "your order is confirmed",
)
# Denials that neutralise a completion phrase, e.g. "nothing has been saved to
# your cart yet" or "I couldn't add the cement". Without these an honest refusal
# containing "saved to your cart" would be mistaken for a false claim — and the
# corrective nudge would then push the model to add something it should refuse.
_CART_DENIAL_MARKERS = (
    "not been added",
    "not been saved",
    "not added",
    "not saved",
    "nothing has",
    "haven't",
    "have not",
    "hasn't",
    "has not",
    "wasn't",
    "was not",
    "weren't",
    "were not",
    "couldn't",
    "could not",
    "cannot add",
    "can't add",
    "unable to add",
)
# Tools whose result authorises a cart/order claim.
_CART_TOOLS = ("add_to_cart", "create_order")


def _claims_cart_change(text: str) -> bool:
    """True when a reply asserts a cart/order change was already completed."""
    lower = (text or "").lower()
    if any(marker in lower for marker in _CART_DENIAL_MARKERS):
        return False
    return any(marker in lower for marker in _CART_CLAIM_MARKERS)


def _add_result_landed(result: dict) -> bool:
    """True when an add/cart write actually took effect (signed-in or staged guest).

    `success` false with `staged` true is a real guest add; `success` false with
    neither is a refusal (MOQ, unknown product, not signed in, ...).
    """
    result = result or {}
    return bool(result.get("staged")) or result.get("success") is True


def _has_cart_tool_result(tool_results: List[dict]) -> bool:
    """True when this turn actually ran a cart/order tool (success or refusal)."""
    return any((tr.get("tool") in _CART_TOOLS) for tr in tool_results or [])


# Explicit "<number> <order unit>" clauses — each one is a separate item in a cart
# request ("21 bags of 12mm rods ... and 10 bags of cement"). Deliberately
# conservative in BOTH directions:
#   - prose without a quantity ("add cement to my cart") counts as 0;
#   - dimension/spec tokens are excluded (no bare `m`/`cm`/`mm`/`kg`), so "12mm
#     rods" and "50kg bag" don't masquerade as a second item.
_QUANTITY_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:bag|bags|piece|pieces|pcs|length|lengths|len|unit|units|"
    r"roll|rolls|sheet|sheets|tonne|tonnes|ton|tons|bundle|bundles|pack|packs|"
    r"box|boxes|litre|litres|liter|liters|tin|tins|drum|drums|set|sets|"
    r"metre|metres|meter|meters)\b",
    re.I,
)


def _requested_add_target_count(text: str) -> int:
    """How many separate quantity-bearing items a message asks to add.

    Used only to spot a half-fulfilled multi-item request: if the shopper listed
    more items than the tools actually handled, the agent gets one corrective
    round instead of silently adding just one of them.
    """
    return len(_QUANTITY_UNIT_RE.findall(text or ""))



# System prompt

SYSTEM_PROMPT = """You are Burncost AI — a Nigerian construction project manager with 15+ years experience in building materials procurement across Lagos, Abuja, and Port Harcourt. You work for Burncost, a Nigerian construction materials marketplace.

BREVITY (HIGHEST PRIORITY — overrides all other guidance):
- Replies must be plain text with no markdown. Never use asterisks (* or **),
  underscores for emphasis, backticks, or "#" headings.
- Never start a line with "* ". For lists use plain numbered lines ("1.", "2.")
  or dashes ("-"), one item per line.
- Reply in AT MOST ~120 words: one short paragraph, or 2-4 short lines.
- Answer exactly the question asked. Do not broaden scope, do not repeat earlier advice, do not restate the question back.
- No long intros or conclusions, no repeated caveats, no generic fillers.
- Give the core recommendation first, then one line on cost/options if relevant, then offer one specific next step or search.
- Always use verified/DB prices. Never state an unverified price (see NON-NEGOTIABLE PRICE RULE below).

YOUR CAPABILITIES:
- Search for products, categories, suppliers, and brands
- Provide product details, pricing, and availability
- Recommend alternative products with reasons they fit
- Create vendor sourcing requests for items not currently in stock
- Help users compare prices across suppliers
- Give professional construction advice on building projects, materials, and methods

OUTPUT MANAGEMENT:
- Every response must fit within the maximum output token limit.
- Before generating a response, estimate its length and adjust it to fit within the available output budget.
- Never end a response mid-sentence, mid-list, mid-table, or mid-explanation.
- If a complete answer would exceed the output budget:
  - Prioritize answering the user's primary question fully.
  - Compress examples, repetition, and secondary details.
  - Summarize supporting information while preserving all important facts.
  - End with: "I can provide more details if needed."
- Be concise without sacrificing accuracy or usefulness.
- Never intentionally truncate a response.

RESPONSE RULES:
GENERAL RESPONSE POLICY:
- Unless the user explicitly asks for a detailed explanation, provide the shortest complete answer that satisfies the request.
- Expand only when requested or when additional detail is necessary for accuracy.
- Prefer concise, information-dense responses over lengthy explanations.
1. Be direct, professional, and concise. Fully answer the user's primary request using the fewest words necessary while preserving all important information.
2. NEVER say a product is "out of stock", "not available", or "we don't have it". Instead:
   a. First, search for alternatives in the same category and explain why they also fit the user's needs
   b. If nothing suitable exists, use create_vendor_request to start sourcing
   c. Then say: "I've notified our vendors about this. You'll get quotations once available."
   d. Only use this fallback after a search_products call returns zero results; if results exist, present them with price, brand, and unit.
3. When showing products, ALWAYS include: name, price, brand, why it's a good fit, plus the MARKETER (the supplier's business name, e.g. "Raintech Constructions") and the MARKET (the supplier's market/location, e.g. "Dei-Dei market, Abuja"). Use the marketer and market returned by the tools; if a field is blank, say so rather than inventing one.
3b. MINIMUM ORDER RULE: if the user asks for a quantity below a product's minimum order quantity (MOQ), say clearly that you can't add that count and state the exact minimum, e.g. "Can't add 2 lengths — the minimum order is 10 lengths." Never add a sub-minimum quantity.
3c. CHEAPEST PRICE: when the user asks for the cheapest price of a specific product, call get_cheapest_price. The unit rate is the headline; the MOQ-inclusive total (minimum order + shipping) is separate and must never be presented as the price of one item.
3c-bis. CATEGORY FILTERS: a search result that reports "relaxed_category_from" means the exact category the shopper named had no matches, so you broadened to related categories. Say so plainly (e.g. "nothing under Roofing Systems — here is what I found nearby") instead of presenting the results as if they were in that category.
3d. CART CONFIRMATION: you MUST call add_to_cart before saying anything was added to the cart, and you must never say the cart changed without a confirming tool result. Only state that an item is in the shopper's cart when the tool result says success true, or `staged` true for a guest/device cart. For a staged add, say it is saved to their cart on this device and that they can proceed to checkout — signing in merges it into their account. If the tool refuses the quantity because it is below the minimum order quantity, tell the shopper that exact minimum; never pretend the add succeeded. Never claim an order has been placed; checkout always requires an account.
3e. MULTI-ITEM CART RULE: when the shopper asks for more than one item, you must handle EVERY item — call search_products as needed and then add_to_cart once per item. Never add only some of them, and never stay silent about an item the shopper asked for: for each item say whether it was added or, if it was refused, why (e.g. below the minimum order). If an item is ambiguous (several matching products or brands), ask the shopper which one they mean instead of dropping it. Use the product's real unit of measure (reinforcement rods are sold per length, cement per bag); if the shopper used the wrong unit, add the quantity they asked for and mention the correct unit.
4. After presenting a product, naturally nudge toward action: "Want me to add this to your cart?" or "I can help you place an order."
5. Use 90% proper English. Only use Pidgin once per conversation at the persuasion moment — e.g. "Oga, this one na the best price for 12mm iron rods for this week."
6. Prices are in Nigerian Naira (₦).
7. If the user asks to create a BOQ, generate a quote, or place an order without an account, tell them to sign up and include a signup action.
8. NON-NEGOTIABLE PRICE RULE: Never state a current market price unless it came from a tool result (compare_prices, get_price_range, get_price_history, or a verified DB price). Never invent or estimate a price from your own knowledge. If no verified price was returned, say exactly: "BurnCost does not currently have a verified price for this item in this location. I've notified our vendors — you'll get a quotation once available."
9. APPROVAL RULE: Never execute an order or place a purchase without the user's explicit, unambiguous confirmation. Always show the full itemized total and ask "Shall I proceed with this order?" before executing. You may only execute after the user confirms.
10. DOMAIN GUARD: You are exclusively a construction and building-materials assistant. If the user asks about anything unrelated to construction, building materials, pricing, or project advice, do not engage. Respond with a polite redirect: "I specialize in construction and building materials procurement. I can help you with materials, pricing, BOQs, suppliers, and project advice — how can I assist with your project?"
11. CROSS-LOCATION RULE: If a tool returns offers for a different state/city than the user's (other_location is true), you may quote that verified price, but you MUST name its location explicitly (e.g. "verified FCT/Abuja rate of ₦11,500 per sheet") and make clear it is not a local price. Never present another location's price as the user's local price.
12. TOOL USAGE RULE: Pass a plain product name to search_products and get_cheapest_price (e.g. "cement", "stone-coated roofing sheet"). Do NOT pass brand + grade + material combinations ("Dangote OPC cement") — pass the material and, if the user named a brand, that brand alone. Do not invent category names — an incorrect category returns zero results. If a search returns nothing, retry with a simpler product name before using the "no verified price" message.
13. BROADENED-SEARCH RULE: when a tool result carries `relaxed_from`, the exact item was not found and the results are the closest alternatives. Say so plainly, then present the alternatives — for each one name the product, its price, the MARKETER (supplier) and the MARKET/location. Never say "we don't have it" or "no price available" while alternatives exist, and always name the alternative brand you are offering instead (e.g. "We don't stock Lafarge, but BUA and Dangote cement are available").

GREETING RULES:
- Greetings ("hi", "hello", "good morning") get one short friendly line that also
  offers help, e.g. "Hello! How can I help with your project today?". Thanks and
  pleasantries get a one-line acknowledgement.
- Never answer a greeting with the DOMAIN GUARD redirect — the guard is only for
  genuinely off-topic requests (news, entertainment, unrelated advice).
- Greet the user only on the first message of a conversation.
- Use a simple greeting such as "Hello!" or "Welcome!".
- Do not use time-based greetings (e.g., "Good morning", "Good afternoon", or "Good evening").
- Keep the greeting to one short line, then immediately address the user's request.
- Do not greet again unless the user starts a new conversation.

CONVERSATION FOCUS:
- Keep all conversations centered on construction, building materials, pricing, and project advice.
- If the user asks about non-construction topics, politely steer the conversation back to construction.
- Do not engage in general chit-chat, entertainment, or topics unrelated to building/construction.

CONSTRUCTION ADVICE GUIDELINES:
- IMPORTANT: Follow the BREVITY rule above. Lead with the single most relevant recommendation, one line on cost, then offer to search. Do not write a full multi-topic essay.
- When asked for advice on construction topics (roofing, foundation, flooring, plumbing, electrical, finishes, etc.):
  1. Give professional, practical advice based on Nigerian building standards and practices
  2. Reference specific materials, their suitability for the Nigerian climate, and cost implications
  3. Consider local factors: weather (rainy/dry season, harmattan), soil type, location (Lagos/Abuja/PH), and budget
  4. Always include a cost estimate range or mention which materials offer best value
  5. End with a specific next step or offer to search for relevant materials
- Example: If asked about roofing, advise on roofing sheet types (stone-coated, aluminum, long-span), insulation needs, typical roof pitch, and material trade-offs
- Example: If asked about foundation, advise on soil test importance, strip footing vs raft foundation, concrete mix ratios, and reinforcement requirements
- These examples illustrate expected style ONLY — never answer an example unless the user actually asks about it."""


GUEST_SYSTEM_PROMPT = SYSTEM_PROMPT + """

GUEST USER RULES:
- You have a limited conversation allowance of 50000 tokens.
- If the user asks to create a BOQ, place an order, or access any feature that requires an account, tell them to sign up.
- When the user approaches the token limit, suggest they create a free account to continue using the assistant."""


class ToolExecutor:
    """Executes tool calls using existing services and direct DB queries."""

    def __init__(self, db: AsyncSession, pg_db: Optional[AsyncSession] = None, user_id: Optional[str] = None):
        self.db = db
        self.pg_db = pg_db
        self.user_id = user_id
        self.product_service = ProductService(db)
        # Brand tokens from the DB, resolved once per ToolExecutor instance.
        self._brand_tokens: Optional[set] = None

    # Map colloquial user terms -> catalog naming so "iron rods" finds products
    # stored as "Reinforcement Rod" / "rebar". Used only as a fallback when the
    # literal ILIKE search returns nothing.
    _MATERIAL_SYNONYMS = {
        "iron rod": ["reinforcement rod", "reinforcement bar", "rebar", "tmt bar", "steel bar"],
        "steel bar": ["reinforcement bar", "rebar", "high yield rebar"],
        "rebar": ["reinforcement rod", "high yield rebar", "tmt bar"],
    }

    # Well-known Nigerian material brands the catalogue may not stock yet. A query
    # naming one of these must still resolve to its MATERIAL ("Lafarge cement" ->
    # "cement" -> whichever cement brands we do carry) instead of returning nothing.
    _COMMON_BRANDS = {
        "dangote", "bua", "lafarge", "sokoto", "unicem", "ashaka", "ccnn",
        "mfamosing", "elephant", "purechem",
    }

    # Spec/grade words that carry no material identity. Dropping them is what lets
    # "Dangote OPC cement" reach the catalogue row "Dangote Cement 50kg".
    _BRAND_SPEC_NOISE = {
        "opc", "ppc", "pcc", "grade", "class", "type", "brand", "quality",
        "premium", "standard", "heavy", "duty", "new", "original", "genuine",
    }

    @staticmethod
    def _synonym_terms(search: str) -> List[str]:
        """Return catalog-compatible alternative search terms for a colloquial query."""
        if not search:
            return []
        key = search.lower().strip()
        for canon, alts in ToolExecutor._MATERIAL_SYNONYMS.items():
            if canon in key:
                return alts
        return []

    @staticmethod
    def _query_variants(search: str, brand_tokens: Optional[set] = None) -> List[str]:
        """Ordered, progressively broader retries for a free-text material query.

        Used only when the literal search finds nothing, so precision is never
        sacrificed for a query that already works. A brand + spec + material
        phrase fails the catalogue's AND-of-all-tokens match (the row is
        "Dangote Cement 50kg", which has no "opc" anywhere), so the retries drop
        tokens from the least to the most material-like:

            "Dangote OPC cement" -> ["Dangote cement", "cement", "Dangote"]

        The material-only attempt is what surfaces every stocked brand for that
        material, which is the "or any one that matches" behaviour.
        """
        raw = (search or "").strip()
        if not raw:
            return []
        tokens = [t for t in re.split(r"[\s,/\-]+", raw.lower()) if t]
        if len(tokens) < 2:
            return []

        known_brands = set(brand_tokens or set()) | ToolExecutor._COMMON_BRANDS
        brands = [t for t in tokens if t in known_brands]
        noise = [t for t in tokens if t in ToolExecutor._BRAND_SPEC_NOISE and t not in known_brands]
        material = [t for t in tokens if t not in brands and t not in noise]
        # Length-descending: the longest token is the most likely material noun.
        material.sort(key=len, reverse=True)

        candidates: List[str] = []

        def _add(*parts: str) -> None:
            phrase = " ".join(p for p in parts if p).strip()
            if phrase and phrase.lower() != raw.lower() and phrase not in candidates:
                candidates.append(phrase)

        # Brand + material (keeps the brand the user asked for).
        for b in brands:
            _add(b, *material)
        # Material alone -> every stocked brand for that material.
        if material:
            _add(*material)
        # Longest single tokens, then the brand on its own.
        for t in material:
            _add(t)
        for b in brands:
            _add(b)

        return candidates

    async def _known_brand_tokens(self) -> set:
        """Lowercased tokens from the live Brand table (cached per instance)."""
        if self._brand_tokens is not None:
            return self._brand_tokens
        tokens: set = set()
        try:
            result = await self.db.execute(select(Brand.name))
            for (name,) in result.all():
                for tok in re.split(r"[\s,/\-]+", (name or "").lower()):
                    if len(tok) >= 3:
                        tokens.add(tok)
        except Exception as e:
            logger.warning(f"_known_brand_tokens failed: {e}")
        self._brand_tokens = tokens
        return tokens

    @staticmethod
    def _retry_terms(search: str, brand_tokens: Optional[set] = None) -> List[str]:
        """All fallback search terms for a query that returned nothing."""
        terms = ToolExecutor._query_variants(search, brand_tokens)
        for alt in ToolExecutor._synonym_terms(search):
            if alt not in terms:
                terms.append(alt)
        return terms

    async def _vendor_meta(self, vendor_id) -> dict:
        """Resolve a vendor's marketer name and market/location for display.

        "Marketer" is the supplier's business name (how Nigerian buyers refer to
        the seller); "market" is the supplier's primary address (street + LGA,
        with any market landmark) falling back to city, state. Blank strings are
        returned when unknown so callers never fabricate a supplier or market.
        """
        meta = {"marketer": "", "market": ""}
        if not vendor_id:
            return meta
        try:
            result = await self.db.execute(select(Vendor).where(Vendor.id == vendor_id))
            vendor = result.scalar_one_or_none()
            if not vendor:
                return meta
            meta["marketer"] = vendor.business_name or ""
            addr = None
            try:
                from app.models.vendor_address import VendorAddress
                addr_result = await self.db.execute(
                    select(VendorAddress)
                    .where(VendorAddress.vendor_id == vendor.id, VendorAddress.is_primary == True)
                    .limit(1)
                )
                addr = addr_result.scalar_one_or_none()
            except Exception:
                addr = None
            if addr:
                market = ", ".join([p for p in [addr.address_line1, addr.lga or addr.city] if p])
                if addr.landmark:
                    market = f"{market} ({addr.landmark})" if market else addr.landmark
                meta["market"] = market or ", ".join([p for p in [addr.city, addr.state] if p])
            else:
                meta["market"] = ", ".join([p for p in [vendor.city, vendor.state] if p])
        except Exception as e:
            logger.warning(f"_vendor_meta failed: {e}")
        return meta

    async def execute(self, tool_name: str, args: dict) -> dict:
        method = getattr(self, f"_{tool_name}", None)
        if not method:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return await method(**args)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"error": str(e)}

    async def _search_products(
        self,
        search: Optional[str] = None,
        category: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        brand: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        from app.schemas.product import ProductFilter
        filters = ProductFilter(
            search=search,
            category=category,
            min_price=min_price,
            max_price=max_price,
        )
        if brand:
            result = await self.db.execute(
                select(Brand).where(Brand.name.ilike(f"%{brand}%"))
            )
            b = result.scalar_one_or_none()
            if b:
                filters.brand_id = b.id

        result = await self.product_service.list_products(
            filters=filters, page=page, page_size=page_size
        )
        products = result.get("products", [])
        matched_query = filters.search
        relaxed_from = None

        # Literal search first (precision); only broaden when it finds nothing.
        # A brand + spec + material phrase ("Dangote OPC cement") fails the
        # catalogue's AND-of-all-tokens match, and a colloquial term ("iron rods")
        # never appears verbatim — both need a retry before we claim "not found".
        if not products and filters.search:
            for alt in self._retry_terms(filters.search, await self._known_brand_tokens()):
                alt_filters = filters.model_copy(update={"search": alt})
                alt_result = await self.product_service.list_products(
                    filters=alt_filters, page=page, page_size=page_size
                )
                if alt_result.get("products"):
                    result = alt_result
                    products = result.get("products", [])
                    matched_query = alt
                    relaxed_from = filters.search
                    break

        # The assistant may pass a colloquial category ("roofing sheets") that no
        # single category name matches. The marketplace filter is an AND of the
        # tokens so it can never show the wrong category, so relax to OR here —
        # only after the strict attempt returned nothing — and report it.
        relaxed_category_from = None
        if not products and category:
            relaxed_result = await self.product_service.list_products(
                filters=filters, page=page, page_size=page_size,
                relaxed_category=True,
            )
            if relaxed_result.get("products"):
                result = relaxed_result
                products = result.get("products", [])
                relaxed_category_from = category

        # Serialize for JSON
        serialized = []
        for p in products:
            meta = await self._vendor_meta(p.get("vendor_id"))
            serialized.append({
                "id": str(p.get("id", "")),
                "name": p.get("name", ""),
                "base_price": float(p.get("base_price", 0)),
                "discount_price": float(p.get("discount_price", 0)) if p.get("discount_price") else None,
                "category": p.get("category", ""),
                "brand_name": p.get("brand_name", ""),
                "quantity": p.get("quantity", 0),
                "unit_of_measure": p.get("unit_of_measure", "piece"),
                "minimum_order_quantity": p.get("minimum_order_quantity") or 1,
                # Marketer (supplier business name) + market/location so the
                # assistant can always name who is selling and where.
                "vendor_name": meta["marketer"],
                "vendor_market": meta["market"],
                "status": p.get("status", ""),
                "rating": float(p.get("rating", 0)),
            })
        return {
            "products": serialized,
            "total": result.get("total", 0),
            "page": result.get("page", page),
            "matched_query": matched_query,
            "relaxed_from": relaxed_from,
            "relaxed_category_from": relaxed_category_from,
        }

    async def _get_product(self, product_id: str) -> dict:
        from uuid import UUID
        product = await self.product_service.get_product_by_id(UUID(product_id))
        if not product:
            return {"error": "Product not found"}
        meta = await self._vendor_meta(product.get("vendor_id"))
        return {
            "id": str(product.get("id", "")),
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "base_price": float(product.get("base_price", 0)),
            "discount_price": float(product.get("discount_price", 0)) if product.get("discount_price") else None,
            "category": product.get("category", ""),
            "category_division": product.get("category_division", ""),
            "brand_name": product.get("brand_name", ""),
            "quantity": product.get("quantity", 0),
            "unit_of_measure": product.get("unit_of_measure", "piece"),
            "minimum_order_quantity": product.get("minimum_order_quantity") or 1,
            "shipping_fee": float(product.get("shipping_fee") or 0),
            "vendor_name": meta["marketer"],
            "vendor_market": meta["market"],
            "status": product.get("status", ""),
            "rating": float(product.get("rating", 0)),
            "sku": product.get("sku", ""),
        }

    async def _get_cheapest_price(self, search: str, location: Optional[str] = None, limit: int = 3) -> dict:
        """Cheapest catalog listing(s) for a product, with provenance.

        Backed by the same product catalogue the marketplace uses (not the
        material_rates table) so every offer carries a product id the chat card
        can link to, plus the marketer (supplier business name), the market
        location and the supplier's minimum order quantity / shipping — making
        the unit rate and the MOQ-inclusive procurement total unambiguous.
        """
        from app.schemas.product import ProductFilter
        page_size = max(limit * 4, 20)
        filters = ProductFilter(search=search, sort_by="price", sort_order="asc")
        result = await self.product_service.list_products(filters=filters, page=1, page_size=page_size)
        products = result.get("products", [])
        matched_query = search

        # Literal first; only broaden when it finds nothing (e.g. the catalogue row
        # "Dangote Cement 50kg" can never match "Dangote OPC cement" verbatim).
        if not products:
            for alt in self._retry_terms(search, await self._known_brand_tokens()):
                alt_result = await self.product_service.list_products(
                    filters=filters.model_copy(update={"search": alt}), page=1, page_size=page_size
                )
                if alt_result.get("products"):
                    products = alt_result.get("products", [])
                    matched_query = alt
                    break
        relaxed_from = search if matched_query != search else None

        offers = []
        for p in products:
            base = float(p.get("base_price", 0) or 0)
            discount = float(p.get("discount_price", 0)) if p.get("discount_price") else None
            # Effective price is what the buyer actually pays.
            rate = discount if discount and discount > 0 else base
            if rate <= 0:
                continue
            meta = await self._vendor_meta(p.get("vendor_id"))
            moq = int(p.get("minimum_order_quantity") or 1)
            shipping = float(p.get("shipping_fee") or 0)
            billable = max(1, moq)
            offers.append({
                "product_id": str(p.get("id", "")),
                "product_name": p.get("name", ""),
                "rate": round(rate, 2),
                "base_price": round(base, 2),
                "discount_price": round(discount, 2) if discount else None,
                "unit": p.get("unit_of_measure") or "piece",
                "brand_name": p.get("brand_name", ""),
                "marketer": meta["marketer"],
                "market": meta["market"],
                "minimum_order_quantity": moq,
                "quantity": 1,
                "billable_quantity": billable,
                "shipping_fee": shipping,
                "total_procurement_cost": round(billable * rate + shipping, 2),
                "stock": int(p.get("quantity") or 0),
                "city": location or "",
                "price_source": "catalogue",
                "verified": bool(p.get("status") == "active"),
            })

        # The catalogue sorts on base_price; re-rank on the effective price.
        offers.sort(key=lambda o: (o["rate"], o["total_procurement_cost"]))
        offers = offers[:max(1, limit)]
        cheapest = offers[0] if offers else None
        if cheapest:
            explanation = (
                f"Cheapest catalog listing for '{matched_query}': {cheapest['product_name']} at "
                f"{cheapest['rate']} per {cheapest['unit']}"
                + (f" from {cheapest['marketer']}" if cheapest["marketer"] else "")
                + (f" at {cheapest['market']}" if cheapest["market"] else "")
                + f". Minimum order {cheapest['minimum_order_quantity']} {cheapest['unit']}(s)."
            )
            if relaxed_from:
                explanation = (
                    f"No exact catalogue match for '{relaxed_from}' — showing closest available "
                    f"options. " + explanation
                )
        else:
            explanation = f"No catalog listing found for '{search}'."
        return {
            "search": search,
            "offers": offers,
            "cheapest": cheapest,
            "cheapest_product_id": cheapest["product_id"] if cheapest else None,
            "total": len(offers),
            "matched_query": matched_query,
            "relaxed_from": relaxed_from,
            "explanation": explanation,
        }

    async def _search_categories(
        self,
        search: Optional[str] = None,
        division: Optional[str] = None,
    ) -> dict:
        query = select(Category).where(Category.is_active == True)
        if search:
            query = query.where(Category.name.ilike(f"%{search}%"))
        if division:
            query = query.where(Category.division == division)
        query = query.order_by(Category.name)
        result = await self.db.execute(query)
        categories = result.scalars().all()
        return {
            "categories": [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "division": c.division,
                    "material_type": c.material_type,
                    "default_unit": c.default_unit,
                }
                for c in categories
            ]
        }

    async def _search_suppliers(
        self,
        search: Optional[str] = None,
        location: Optional[str] = None,
    ) -> dict:
        query = select(Vendor).where(Vendor.verification_status == "verified")
        if search:
            query = query.where(Vendor.business_name.ilike(f"%{search}%"))
        if location:
            query = query.where(
                (Vendor.city.ilike(f"%{location}%")) | (Vendor.state.ilike(f"%{location}%"))
            )
        query = query.order_by(Vendor.business_name).limit(20)
        result = await self.db.execute(query)
        suppliers = result.scalars().all()
        return {
            "suppliers": [
                {
                    "id": str(s.id),
                    "business_name": s.business_name,
                    "city": s.city,
                    "state": s.state,
                    "rating": float(s.rating) if s.rating else None,
                    "verification_status": s.verification_status,
                }
                for s in suppliers
            ]
        }

    async def _search_brands(self, search: Optional[str] = None) -> dict:
        query = select(Brand).where(Brand.is_active == True)
        if search:
            query = query.where(Brand.name.ilike(f"%{search}%"))
        query = query.order_by(Brand.name).limit(20)
        result = await self.db.execute(query)
        brands = result.scalars().all()
        return {
            "brands": [
                {"id": str(b.id), "name": b.name, "description": b.description}
                for b in brands
            ]
        }

    async def _recommend_alternatives(
        self,
        category_name: str,
        product_name: Optional[str] = None,
        max_price: Optional[float] = None,
    ) -> dict:
        # Find category by name
        result = await self.db.execute(
            select(Category).where(Category.name.ilike(f"%{category_name}%"))
        )
        category = result.scalar_one_or_none()
        if not category:
            return {"error": f"Category '{category_name}' not found"}

        from app.schemas.product import ProductFilter
        filters = ProductFilter(category_id=category.id)
        if max_price:
            filters.max_price = max_price

        result = await self.product_service.list_products(
            filters=filters, page=1, page_size=10
        )
        products = result.get("products", [])
        serialized = []
        for p in products:
            serialized.append({
                "id": str(p.get("id", "")),
                "name": p.get("name", ""),
                "base_price": float(p.get("base_price", 0)),
                "brand_name": p.get("brand_name", ""),
                "quantity": p.get("quantity", 0),
                "unit_of_measure": p.get("unit_of_measure", "piece"),
            })
        return {
            "category": category.name,
            "alternatives": serialized,
            "total": result.get("total", 0),
        }

    async def _get_product_availability(self, product_id: str) -> dict:
        from uuid import UUID
        product = await self.product_service.get_product_by_id(UUID(product_id))
        if not product:
            return {"error": "Product not found"}
        qty = product.get("quantity", 0)
        threshold = product.get("low_stock_threshold", 10)
        return {
            "product_id": product_id,
            "name": product.get("name", ""),
            "quantity": qty,
            "in_stock": qty > 0,
            "low_stock": 0 < qty <= threshold,
            "out_of_stock": qty == 0,
            "allow_backorder": product.get("allow_backorder", False),
        }

    async def _get_product_price_history(self, product_id: str) -> dict:
        from uuid import UUID
        product = await self.product_service.get_product_by_id(UUID(product_id))
        if not product:
            return {"error": "Product not found"}
        base = float(product.get("base_price", 0))
        discount = product.get("discount_price")
        discount_val = float(discount) if discount else None
        return {
            "product_id": product_id,
            "name": product.get("name", ""),
            "current_price": discount_val or base,
            "base_price": base,
            "has_discount": discount_val is not None,
            "discount_percentage": float(product.get("discount_percentage", 0)) if product.get("discount_percentage") else None,
            "unit": product.get("unit_of_measure", "piece"),
        }

    async def _create_vendor_request(
        self,
        product_name: str,
        quantity: Optional[int] = 1,
        specifications: Optional[str] = None,
    ) -> dict:
        request_id = str(uuid.uuid4())
        doc = {
            "_id": request_id,
            "product_name": product_name,
            "quantity": quantity or 1,
            "specifications": specifications or "",
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }
        await db_module.mongodb["vendor_requests"].insert_one(doc)
        logger.info(f"Vendor request created: {request_id} for {product_name}")
        return {
            "request_id": request_id,
            "product_name": product_name,
            "quantity": quantity or 1,
            "status": "pending",
            "message": "Our procurement team is sourcing this item from verified vendors. You'll receive quotations once available.",
        }

    async def _track_vendor_request(self, request_id: str) -> dict:
        doc = await db_module.mongodb["vendor_requests"].find_one({"_id": request_id})
        if not doc:
            return {"error": "Vendor request not found"}
        return {
            "request_id": request_id,
            "product_name": doc.get("product_name", ""),
            "quantity": doc.get("quantity", 1),
            "status": doc.get("status", "pending"),
            "quotations": doc.get("quotations", []),
            "created_at": doc.get("created_at", ""),
        }

    # ── Phase 4/5/6 business tools (DB-verified) ──────────────────────────

    async def _compare_prices(self, description: str, quantity: float = 1.0, city: str = "Abuja") -> dict:
        from app.services.procurement_intelligence_service import ProcurementIntelligenceService
        svc = ProcurementIntelligenceService(self.pg_db)
        return await svc.compare_prices(description, quantity, city)

    async def _get_price_range(self, description: str, city: str = "Abuja") -> dict:
        from app.services.procurement_intelligence_service import ProcurementIntelligenceService
        svc = ProcurementIntelligenceService(self.pg_db)
        return await svc.get_price_range(description, city)

    async def _get_price_history(self, description: str, city: str = "Abuja", limit: int = 12) -> dict:
        from app.services.procurement_intelligence_service import ProcurementIntelligenceService
        svc = ProcurementIntelligenceService(self.pg_db)
        return await svc.get_price_history(description, city, limit)

    async def _analyse_quotation(self, quote_text: str, supplier_name: Optional[str] = None) -> dict:
        from app.services.boq_generator import BOQGenerator
        boq_gen = BOQGenerator(pg_db=self.pg_db)
        parsed = await boq_gen.verify_quote_text(quote_text, self.user_id or "")
        items = [
            {"description": i.get("description", ""), "quantity": i.get("quantity", 0),
             "unit": i.get("unit"), "quoted_rate": i.get("quoted_rate", 0)}
            for i in parsed.get("items", [])
        ]
        from app.services.procurement_intelligence_service import ProcurementIntelligenceService
        svc = ProcurementIntelligenceService(self.pg_db)
        return await svc.analyse_quotation(
            quoted_items=items, supplier_name=supplier_name, user_id=self.user_id, city="Abuja"
        )

    async def _calculate_savings(self, items: list, supplier_a: dict, supplier_b: dict) -> dict:
        from app.services.procurement_intelligence_service import ProcurementIntelligenceService
        svc = ProcurementIntelligenceService(self.pg_db)
        return await svc.calculate_savings(items, supplier_a, supplier_b)

    async def _get_procurement_score(self, items: list, city: str = "Abuja") -> dict:
        from app.services.procurement_intelligence_service import ProcurementIntelligenceService
        svc = ProcurementIntelligenceService(self.pg_db)
        return await svc.get_procurement_score(items, city)

    async def _compare_supplier_quotes(
        self,
        items: list,
        supplier_a: dict,
        supplier_b: dict,
        supplier_a_name: str = "Supplier A",
        supplier_b_name: str = "Supplier B",
    ) -> dict:
        """Compare two suppliers' itemized quotes for a shared basket.

        Uses only the quoted rates provided by the two suppliers (never AI-
        invented) and reports line-level deltas plus the cheaper option.
        """
        total_a = 0.0
        total_b = 0.0
        line_deltas = []
        for line in items or []:
            description = line.get("description", "")
            quantity = float(line.get("quantity", 0))
            rate_a = float(supplier_a.get(description, 0) or 0)
            rate_b = float(supplier_b.get(description, 0) or 0)
            total_a += quantity * rate_a
            total_b += quantity * rate_b
            line_deltas.append({
                "description": description,
                "quantity": quantity,
                "unit": line.get("unit"),
                "rate_a": rate_a,
                "rate_b": rate_b,
                "line_delta": round(quantity * (rate_a - rate_b), 2),
                "cheaper": "a" if rate_a <= rate_b else "b",
            })

        return {
            "supplier_a_name": supplier_a_name,
            "supplier_b_name": supplier_b_name,
            "supplier_a_total": round(total_a, 2),
            "supplier_b_total": round(total_b, 2),
            "delta": round(total_a - total_b, 2),
            "recommended_supplier": "a" if total_a <= total_b else "b",
            "recommended_supplier_name": supplier_a_name if total_a <= total_b else supplier_b_name,
            "line_items": line_deltas,
            "note": "Comparison uses only the quoted rates you provided for each supplier.",
        }

    async def _search_products_by_quantity(self, search: str, quantity: float, city: str = "Abuja") -> dict:
        result = await self._search_products(search=search, page_size=25)
        products = result.get("products", [])
        filtered = [p for p in products if (p.get("quantity") or 0) >= quantity]
        return {"products": filtered, "total": len(filtered), "required_quantity": quantity}

    async def _get_project_boq(self, project_id: str) -> dict:
        try:
            from bson import ObjectId
            cursor = db_module.mongodb["boqs"].find({"projectId": ObjectId(project_id)})
            boqs = await cursor.to_list(length=20)
            return {"project_id": project_id, "boqs": [str(b.get("_id")) for b in boqs], "count": len(boqs)}
        except Exception as e:
            logger.warning(f"_get_project_boq failed: {e}")
            return {"error": str(e)}

    async def _get_remaining_materials(self, project_id: str) -> dict:
        from app.services.project_memory_service import ProjectMemoryService
        svc = ProjectMemoryService(mongo_db=db_module.mongodb, pg_db=self.pg_db)
        return await svc.get_project_materials(project_id)

    async def _get_procurement_recommendation(self, project_id: str) -> dict:
        from app.services.project_memory_service import ProjectMemoryService
        svc = ProjectMemoryService(mongo_db=db_module.mongodb, pg_db=self.pg_db)
        result = await svc.get_project_materials(project_id)
        pending = [m for m in result.get("materials", []) if m.get("status") != "complete"]
        return {
            "project_id": project_id,
            "message": f"{len(pending)} material(s) still needed. Buy in priority order.",
            "recommended_buy": pending[:10],
        }

    async def _find_supplier_for_products(self, products: list, city: str = "Abuja") -> dict:
        suppliers = await self._search_suppliers(location=city)
        return {"city": city, "suppliers": suppliers.get("suppliers", []), "for_products": products}

    async def _add_to_cart(self, product_id: str, quantity: int = 1) -> dict:
        from uuid import UUID
        try:
            product = await self.product_service.get_product_by_id(UUID(product_id))
        except Exception:
            product = None
        if not product:
            return {"success": False, "error": "Product not found.", "product_id": product_id}

        name = product.get("name") or "This product"
        unit = product.get("unit_of_measure") or "piece"
        moq = int(product.get("minimum_order_quantity") or 1)
        requested = int(quantity or 1)

        # Below the supplier's minimum: refuse and tell the user the minimum.
        if requested < moq:
            return {
                "success": False,
                "error": (
                    f"Can't add {requested} {unit}(s) of {name} — the minimum order "
                    f"quantity is {moq} {unit}(s)."
                ),
                "product_id": product_id,
                "product_name": name,
                "requested_quantity": requested,
                "minimum_order_quantity": moq,
                "unit": unit,
            }

        # Anonymous shoppers keep the item in the on-device cart (cartStore) which
        # is merged into their account after sign-in/sign-up. The server cannot
        # write that cart, so this is STAGED: the client performs the write when
        # it receives this payload (see Chat.tsx). `staged` marks it so the action
        # button is emitted from the tool result rather than from prose matching.
        if not self.user_id:
            return {
                "success": False,
                "guest": True,
                "staged": True,
                "product_id": product_id,
                "product_name": name,
                "quantity": requested,
                "minimum_order_quantity": moq,
                "unit": unit,
                "price": float(product.get("discount_price") or product.get("base_price") or 0),
                "message": (
                    f"{requested} {unit}(s) of {name} saved to your cart on this device. "
                    "Create an account (or sign in) at checkout to complete the order."
                ),
            }

        from app.models.cart import CartItem
        price = float(product.get("discount_price") or product.get("base_price") or 0)
        existing = await self.db.execute(
            select(CartItem).where(CartItem.user_id == self.user_id, CartItem.product_id == product_id)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.quantity += requested
            await self.db.commit()
        else:
            self.db.add(CartItem(user_id=self.user_id, product_id=product_id, quantity=requested, price_at_addition=price))
            await self.db.commit()
        return {
            "success": True,
            "product_id": product_id,
            "product_name": name,
            "quantity": requested,
            "minimum_order_quantity": moq,
            "unit": unit,
            "price": price,
        }

    async def _create_order(self, items: list) -> dict:
        # Order execution requires explicit user confirmation (handled in prompt +
        # handle_message flow). This tool only stages the request.
        return {
            "required": "confirmation",
            "message": "Please confirm you want to place this order. I will not execute without your explicit approval.",
            "items": items,
        }

    async def _get_order_status(self, order_id: str) -> dict:
        if not self.pg_db:
            return {"error": "No database connection"}
        from sqlalchemy import text
        try:
            result = await self.pg_db.execute(
                text("SELECT order_number, status, total_amount, payment_status FROM orders WHERE id = :oid"),
                {"oid": order_id},
            )
            row = result.fetchone()
            if not row:
                return {"error": "Order not found"}
            return {"order_id": order_id, "order_number": str(row[0]), "status": str(row[1]),
                    "total_amount": float(row[2]), "payment_status": str(row[3])}
        except Exception as e:
            logger.warning(f"_get_order_status failed: {e}")
            return {"error": str(e)}


class ChatService:
    """Orchestrates conversation with AI, tool execution, and persistence."""

    def __init__(self, db: AsyncSession, is_authenticated: bool = False, pg_db: Optional[AsyncSession] = None, user_id: Optional[str] = None):
        self.db = db
        self.is_authenticated = is_authenticated
        self.ai_service = ChatAIService()
        self.tool_executor = ToolExecutor(db, pg_db=pg_db, user_id=user_id)
        self.max_guest_tokens = 50000
        self.user_location: Optional[str] = None

    async def handle_message(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_location: Optional[str] = None,
    ) -> ChatResponse:
        # Store location for consistent use across turns
        if user_location:
            self.user_location = user_location

        # Load or create conversation
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        history = await self._load_history(conversation_id)

        # Check guest token limit
        if not self.is_authenticated:
            total_tokens = sum(
                m.get("usage", {}).get("total_tokens", 0) for m in history.get("metadata", [])
            )
            if total_tokens >= self.max_guest_tokens:
                return ChatResponse(
                    reply=(
                        "You've reached the free conversation limit. "
                        "Create a free account to continue using Burncost AI assistant "
                        "with unlimited access to product search, price comparisons, and more!"
                    ),
                    conversation_id=conversation_id,
                    action="signup_required",
                )

        # Build messages array with location context
        system_prompt = GUEST_SYSTEM_PROMPT if not self.is_authenticated else SYSTEM_PROMPT
        if self.user_location:
            system_prompt = (
                f"USER LOCATION: {self.user_location}\n\n"
                f"IMPORTANT: The user is located in {self.user_location}. "
                f"Always reference this location for pricing, availability, and advice. "
                f"Never switch to a different location unless the user explicitly says they're elsewhere.\n\n"
                f"{system_prompt}"
            )
        messages = [{"role": "system", "content": system_prompt}]

        # Add history
        for msg in history.get("messages", []):
            messages.append(msg)

        # Add current user message
        messages.append({"role": "user", "content": message})

        # Check for BOQ/order intent before calling AI
        lower_msg = message.lower()
        boq_keywords = ["create boq", "generate boq", "make a boq", "build a boq", "boq generation"]
        order_keywords = ["place order", "buy now", "purchase", "order now", "checkout"]

        is_boq_request = any(kw in lower_msg for kw in boq_keywords)
        is_order_request = any(kw in lower_msg for kw in order_keywords)

        if (is_boq_request or is_order_request) and not self.is_authenticated:
            return ChatResponse(
                reply=(
                    "I'd love to help with that! Please create a free account to access "
                    "BOQ generation, order placement, and all premium features."
                ),
                conversation_id=conversation_id,
                action="auth_required",
            )

        # Multi-turn function calling loop
        # Multi-item cart flows legitimately need several turns (search + add per
        # item) and the cart guards below each consume one corrective turn, so the
        # budget must comfortably exceed a single search/add pair.
        max_turns = 8
        tool_results_list = []
        cart_claim_retry = 0
        cart_fail_retry = 0
        partial_add_retry = 0
        for turn in range(max_turns):
            try:
                response = await self.ai_service.chat_completion(
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                )

            except Exception as e:
                logger.error(f"AI service error (turn {turn}): {e}")
                # Return a helpful fallback instead of a generic error
                return ChatResponse(
                    reply=(
                        "I'm sorry, I'm having trouble processing that right now. "
                        "Could you try rephrasing your question? I can help with:\n"
                        "• Material prices and product searches\n"
                        "• Construction advice and project guidance\n"
                        "• Comparing suppliers and finding alternatives"
                    ),
                    conversation_id=conversation_id,
                )

            choice = response.choices[0] if response.choices else None
            if not choice:
                return ChatResponse(
                    reply="I'm sorry, I couldn't generate a response. Please try again.",
                    conversation_id=conversation_id,
                )

            msg = choice.message

            # Track token usage
            token_usage = {
                "total_tokens": response.usage.total_tokens if response.usage else 0,
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            }

            # If no tool calls, return the text response with actions
            if not msg.tool_calls:
                reply_text = _strip_markdown(msg.content or "")
                if not reply_text:
                    # Gemini can return an empty candidate; never persist or surface
                    # a blank assistant turn (it also poisons future history).
                    reply_text = (
                        "I've gathered the details for you. What would you like to do next?"
                        if tool_results_list
                        else "I'm sorry, I didn't catch that. Could you rephrase your question?"
                    )
                # A cart/order claim must be backed by the matching tool result —
                # otherwise the model has "confirmed" a change it never made and
                # silently bypassed the minimum-order guard. Give it one chance to
                # call the tool, then refuse to forward the unbacked claim.
                if _claims_cart_change(reply_text) and not _has_cart_tool_result(tool_results_list):
                    if cart_claim_retry < 1:
                        cart_claim_retry += 1
                        logger.warning(
                            "Assistant claimed a cart change with no tool result; forcing a retry"
                        )
                        messages.append({"role": "assistant", "content": reply_text})
                        messages.append({
                            "role": "system",
                            "content": (
                                "You have not called any tool, so nothing was added to or removed "
                                "from the cart and no order exists. Call add_to_cart now with the "
                                "product_id and the exact quantity the shopper asked for. If the "
                                "quantity is below the product's minimum order quantity the tool "
                                "will refuse it — then tell the shopper that minimum. Never say "
                                "the cart changed until a tool result confirms it."
                            ),
                        })
                        continue

                    logger.warning("Cart claim still unbacked after retry; replacing the reply")
                    reply_text = (
                        "I haven't changed your cart yet — I won't confirm something I haven't "
                        "done. Tell me the item and the quantity you want (for example \"add 20 "
                        "lengths of 12mm reinforcement rod\") and I'll add it and confirm."
                    )

                # An add that did NOT take effect must never be reported as done:
                # a refused add_to_cart (below MOQ, unknown product, ...) that the
                # reply turns into "saved to your cart" is the same lie as never
                # calling the tool at all.
                failed_cart_writes = [
                    tr for tr in tool_results_list
                    if tr.get("tool") in _CART_TOOLS
                    and not _add_result_landed(tr.get("result") or {})
                ]
                if (
                    cart_fail_retry < 1
                    and failed_cart_writes
                    and _claims_cart_change(reply_text)
                ):
                    cart_fail_retry += 1
                    reasons = "; ".join(
                        str((tr.get("result") or {}).get("error")
                            or (tr.get("result") or {}).get("message")
                            or "the tool refused it")
                        for tr in failed_cart_writes
                    )
                    logger.warning(
                        "Reply claims a cart change but %s cart write(s) failed; forcing a retry",
                        len(failed_cart_writes),
                    )
                    messages.append({"role": "assistant", "content": reply_text})
                    messages.append({
                        "role": "system",
                        "content": (
                            f"Your {len(failed_cart_writes)} cart call(s) did NOT take effect "
                            f"({reasons}). That item is not in the cart — tell the shopper it was "
                            "not added and why. If the reason is a minimum order quantity, state "
                            "that exact minimum. Never claim an item was added unless the tool "
                            "result reports success true or staged true."
                        ),
                    })
                    continue

                # A multi-item request must not be half-fulfilled. If the shopper
                # listed more quantity-bearing items than the cart tools actually
                # handled, the agent gets one corrective round rather than silently
                # adding only the first item and never mentioning the rest.
                add_results = [
                    tr for tr in tool_results_list if tr.get("tool") == "add_to_cart"
                ]
                target_count = _requested_add_target_count(message)
                if (
                    partial_add_retry < 1
                    and add_results
                    and target_count > len(add_results)
                ):
                    partial_add_retry += 1
                    logger.warning(
                        "Cart request listed %s item(s) but only %s add_to_cart call(s) ran; "
                        "forcing a corrective round",
                        target_count,
                        len(add_results),
                    )
                    messages.append({"role": "assistant", "content": reply_text})
                    messages.append({
                        "role": "system",
                        "content": (
                            f"The shopper listed {target_count} items to add, but only "
                            f"{len(add_results)} were handled. Call add_to_cart again for EVERY "
                            "remaining item (do not re-add the ones already confirmed) — "
                            "search_products first if you need its product_id. Use the product's "
                            "real unit of measure and the quantity the shopper asked for; for any "
                            "item whose quantity is below its minimum order quantity, do NOT add "
                            "it and state that minimum instead. If an item is ambiguous (several "
                            "matching products), ask the shopper which one they mean rather than "
                            "dropping it. Then confirm each item that was added and each item that "
                            "was not — never stay silent about an item the shopper asked for."
                        ),
                    })
                    continue

                assistant_msg = {"role": "assistant", "content": reply_text}
                messages.append(assistant_msg)
                await self._save_history(conversation_id, messages, user_id, token_usage)
                actions = self._build_actions(reply_text, tool_results_list, message)
                cards = self._build_cards(tool_results_list)
                return ChatResponse(
                    reply=reply_text,
                    conversation_id=conversation_id,
                    actions=actions,
                    cards=cards,
                    has_tool_results=len(tool_results_list) > 0,
                )

            # Process tool calls. Gemini requires the model turn that carries the
            # function_call to precede its function_response — the inverted order
            # made Gemini return EMPTY text and poisoned the stored history.
            assistant_msg = {"role": "assistant", "content": msg.content, "tool_calls": []}
            for tc in msg.tool_calls:
                assistant_msg["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                })
            messages.append(assistant_msg)

            for tc in msg.tool_calls:
                # Execute tool
                args = json.loads(tc.function.arguments)
                logger.info(f"Executing tool: {tc.function.name} with args: {args}")
                result = await self.tool_executor.execute(tc.function.name, args)
                tool_results_list.append({"tool": tc.function.name, "result": result})

                # Observability: log every tool turn (supports Phase 10 as well).
                await self._log_agent_turn(
                    tool_name=tc.function.name,
                    tool_args=args,
                    result=result,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "tool_name": tc.function.name,
                    "content": json.dumps(result),
                })

        # If we exit the loop without a final text response
        return ChatResponse(
            reply="I've processed your request. Is there anything else I can help you with?",
            conversation_id=conversation_id,
            has_tool_results=len(tool_results_list) > 0,
        )

    async def _load_history(self, conversation_id: str) -> dict:
        try:
            doc = await db_module.mongodb["chat_conversations"].find_one({"_id": conversation_id})
            if doc:
                return doc
        except Exception as e:
            logger.warning(f"Failed to load chat history: {e}")
        return {"messages": [], "metadata": []}

    async def _log_agent_turn(
        self,
        *,
        tool_name: str,
        tool_args: dict,
        result: dict,
        conversation_id: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Best-effort observability: persist one ai_agent_logs row per tool turn.

        Captures intent, tool, args, result, price_source, quantity_source and
        confidence so every AI/tool interaction is auditable (Phase 10 too).
        Never raises — logging failure must not break the chat flow.
        """
        try:
            from app.services.ai_agent_log_service import AIAgentLogService
            is_error = "error" in result
            svc = AIAgentLogService(self.db)
            await svc.log_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                intent=self._infer_intent(tool_name),
                tool_name=tool_name,
                tool_args=tool_args,
                execution_status="error" if is_error else "success",
                result_summary=self._summarize_result(result),
                execution_result=result,
                price_source=result.get("source") or result.get("price_source"),
                quantity_source=result.get("quantity_source"),
                confidence=int((result.get("confidence") or 0) * 100) if isinstance(result.get("confidence"), (int, float)) else None,
                fallback_used="ai_estimate" if result.get("insufficient_data") or result.get("insufficient_history") else None,
                estimated_items=result.get("estimated_count") or 0,
            )
        except Exception as e:
            logger.warning(f"Failed to log agent turn ({tool_name}): {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass

    @staticmethod
    def _infer_intent(tool_name: str) -> str:
        """Map a tool name to a coarse intent label."""
        if tool_name in ("compare_prices", "get_price_range", "get_price_history"):
            return "price_query"
        if tool_name == "analyse_quotation":
            return "quotation_analysis"
        if tool_name in ("calculate_savings", "compare_supplier_quotes"):
            return "supplier_optimisation"
        if tool_name == "get_procurement_score":
            return "procurement_intelligence"
        if tool_name in ("get_remaining_materials", "get_procurement_recommendation", "get_project_boq"):
            return "project_memory"
        return "assist"

    @staticmethod
    def _summarize_result(result: dict) -> str:
        """Small human-readable summary of a tool result for the log."""
        if not isinstance(result, dict):
            return ""
        parts = []
        if result.get("offers"):
            parts.append(f"{len(result['offers'])} offer(s)")
        if result.get("overall_status"):
            parts.append(f"status={result['overall_status']}")
        if "score" in result:
            parts.append(f"score={result['score']}")
        if result.get("supplier_a_total") is not None and result.get("supplier_b_total") is not None:
            parts.append(f"A={result['supplier_a_total']} B={result['supplier_b_total']}")
        if result.get("message"):
            parts.append(str(result["message"])[:120])
        if result.get("error"):
            parts.append(f"error: {str(result['error'])[:120]}")
        return "; ".join(parts)[:1000]

    async def _save_history(
        self,
        conversation_id: str,
        messages: list,
        user_id: Optional[str] = None,
        token_usage: Optional[dict] = None,
    ) -> None:
        try:
            # Strip system prompt from stored messages
            store_messages = [m for m in messages if m.get("role") != "system"]
            await db_module.mongodb["chat_conversations"].update_one(
                {"_id": conversation_id},
                {
                    "$set": {
                        "messages": store_messages,
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    "$setOnInsert": {
                        "created_at": datetime.utcnow().isoformat(),
                        "user_id": user_id,
                    },
                    "$push": {
                        "metadata": {
                            "timestamp": datetime.utcnow().isoformat(),
                            "usage": token_usage or {},
                        }
                    },
                },
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Failed to save chat history: {e}")

    def _build_cards(self, tool_results: List[dict]) -> Optional[List[ChatCard]]:
        """Build structured cards from tool results (Phase 7)."""
        cards: List[ChatCard] = []
        for tr in tool_results:
            tool = tr.get("tool", "")
            result = tr.get("result", {})
            if not result:
                continue

            if tool == "get_cheapest_price":
                # Catalog-backed cheapest listing(s): each offer carries the
                # product id (for click-through to the detail page), the marketer
                # (supplier) and the market/location.
                cards.append(ChatCard(
                    type="cheapest_price",
                    title="Cheapest Price",
                    data={"search": result.get("search"),
                          "matched_query": result.get("matched_query"),
                          "relaxed_from": result.get("relaxed_from"),
                          "offers": result.get("offers"),
                          "cheapest": result.get("cheapest"),
                          "cheapest_product_id": result.get("cheapest_product_id"),
                          "total": result.get("total"),
                          "explanation": result.get("explanation")},
                ))
            elif tool in ("compare_prices", "get_price_range", "get_price_history"):
                cards.append(ChatCard(
                    type="price_comparison",
                    title="Price Intelligence",
                    data={"tool": tool, "description": result.get("description"),
                          "city": result.get("city"),
                          "source": result.get("source"),
                          "verified": result.get("verified", False),
                          "insufficient_data": result.get("insufficient_data", False),
                          "offers": result.get("offers"),
                          "range": result.get("range"),
                          "history": result.get("history"),
                          "best_price": result.get("best_price"),
                          "trend": result.get("trend"),
                          "explanation": result.get("explanation")},
                ))
                # Also emit a Price Passport card surfacing per-item provenance
                # (price_source, verified, confidence, last_verified_at) so users
                # can see exactly where each figure came from.
                provenance = []
                for offer in result.get("offers") or []:
                    provenance.append({
                        "item": offer.get("product_name") or result.get("description"),
                        "product_id": offer.get("product_id"),
                        "rate": offer.get("rate"),
                        "unit": offer.get("unit"),
                        "city": offer.get("city"),
                        "marketer": offer.get("marketer"),
                        "market": offer.get("market"),
                        "minimum_order_quantity": offer.get("minimum_order_quantity"),
                        "price_source": offer.get("price_source") or result.get("source"),
                        "verified": offer.get("verified", result.get("verified", False)),
                        "confidence": offer.get("confidence"),
                        "last_verified_at": offer.get("last_verified_at"),
                    })
                if provenance:
                    cards.append(ChatCard(
                        type="price_passport",
                        title="Price Passport",
                        data={"items": provenance,
                              "note": "Provenance is traced to verified DB records. Estimates are always flagged."},
                    ))
            elif tool == "analyse_quotation":
                cards.append(ChatCard(
                    type="quotation_analysis",
                    title="Quotation Analysis",
                    data={"overall_status": result.get("overall_status"),
                          "total_quoted": result.get("total_quoted"),
                          "total_market": result.get("total_market"),
                          "total_overcharge": result.get("total_overcharge"),
                          "inflated_count": result.get("inflated_count"),
                          "fair_count": result.get("fair_count"),
                          "unverified_count": result.get("unverified_count"),
                          "demand_alerts_created": result.get("demand_alerts_created"),
                          "items": result.get("items"),
                          "explanation": result.get("explanation")},
                ))
            elif tool == "compare_supplier_quotes":
                cards.append(ChatCard(
                    type="supplier",
                    title="Supplier Quote Comparison",
                    data={"supplier_a_name": result.get("supplier_a_name"),
                          "supplier_b_name": result.get("supplier_b_name"),
                          "supplier_a_total": result.get("supplier_a_total"),
                          "supplier_b_total": result.get("supplier_b_total"),
                          "delta": result.get("delta"),
                          "recommended_supplier": result.get("recommended_supplier"),
                          "recommended_supplier_name": result.get("recommended_supplier_name"),
                          "line_items": result.get("line_items"),
                          "note": result.get("note")},
                ))
            elif tool == "calculate_savings":
                cards.append(ChatCard(
                    type="savings",
                    title="Savings Comparison",
                    data={"baseline_market_total": result.get("baseline_market_total"),
                          "supplier_a_total": result.get("supplier_a_total"),
                          "supplier_b_total": result.get("supplier_b_total"),
                          "savings_vs_market_a": result.get("savings_vs_market_a"),
                          "savings_vs_market_b": result.get("savings_vs_market_b"),
                          "recommended_supplier": result.get("recommended_supplier"),
                          "note": result.get("note")},
                ))
            elif tool == "get_procurement_score":
                cards.append(ChatCard(
                    type="procurement_plan",
                    title="Procurement Score",
                    data={"score": result.get("score"), "grade": result.get("grade"),
                          "breakdown": result.get("breakdown"), "explanation": result.get("explanation")},
                ))
            elif tool in ("get_remaining_materials", "get_procurement_recommendation"):
                cards.append(ChatCard(
                    type="project_memory",
                    title="Project Materials",
                    data={"message": result.get("message"),
                          "materials": result.get("materials"),
                          "remaining_materials_count": result.get("remaining_materials_count"),
                          "recommended_buy": result.get("recommended_buy")},
                ))
            elif tool == "search_suppliers" or tool == "find_supplier_for_products":
                cards.append(ChatCard(
                    type="supplier",
                    title="Verified Suppliers",
                    data={"suppliers": result.get("suppliers"), "for_products": result.get("for_products")},
                ))
            elif tool == "create_order":
                cards.append(ChatCard(
                    type="order_confirmation",
                    title="Order Confirmation Required",
                    data={"required": result.get("required"),
                          "message": result.get("message"), "items": result.get("items")},
                ))

        return cards if cards else None

    @staticmethod
    def _product_candidates(tool_results: List[dict]) -> List[dict]:
        """Collect addable products (id, name, quantity, moq) from tool results.

        Quantity stays 1 for catalogue listings — a product's `quantity` field is
        stock, not the buyer's requested count. Only an explicit add_to_cart
        result carries the count the user actually asked for.
        """
        candidates: List[dict] = []
        seen = set()

        def add(pid, name, qty, moq):
            if not pid or str(pid) in seen:
                return
            seen.add(str(pid))
            candidates.append({
                "id": str(pid),
                "name": name,
                "quantity": int(qty or 1),
                "minimum_order_quantity": moq,
            })

        for tr in tool_results or []:
            result = tr.get("result", {}) or {}
            tool = tr.get("tool", "")
            if tool == "add_to_cart" and result.get("product_id"):
                add(result["product_id"], result.get("product_name"),
                    result.get("quantity"), result.get("minimum_order_quantity"))
                continue
            if tool == "get_product" and result.get("id"):
                add(result["id"], result.get("name"), 1, result.get("minimum_order_quantity"))
                continue
            for key in ("products", "alternatives", "offers"):
                for p in result.get(key) or []:
                    add(p.get("id") or p.get("product_id"),
                        p.get("name") or p.get("product_name"), 1,
                        p.get("minimum_order_quantity"))
        return candidates

    def _build_actions(
        self,
        reply: str,
        tool_results: List[dict],
        user_message: str,
    ) -> Optional[List[ActionButton]]:
        """Generate action buttons based on AI response and tool results.

        Cart and checkout work for both audiences: signed-in shoppers use the
        server cart / dashboard, anonymous shoppers use the on-device cart and
        guest checkout (which only prompts for an account at the final step). An
        account is required solely for BOQ creation / vendor sourcing.
        """
        actions: List[ActionButton] = []
        lower_reply = reply.lower()
        lower_user = user_message.lower()

        candidates = self._product_candidates(tool_results)
        ai_wants_cart = any(kw in lower_reply for kw in [
            "add to cart", "add this to your cart", "add to your cart",
        ])
        user_wants_cart = any(kw in lower_user for kw in ["add to cart", "add it", "add this", "buy"])
        cart_intent = (ai_wants_cart or user_wants_cart) and bool(candidates)

        # The model may already have executed the add (signed-in carts are written
        # server-side), so never offer a second add — offer checkout instead.
        already_added = any(
            tr.get("tool") == "add_to_cart" and (tr.get("result") or {}).get("success") is True
            for tr in tool_results
        )

        # Guest adds are STAGED: the server cannot write the device cart, so the
        # client must. That write is mandatory and must never depend on prose
        # keyword matching ("alright add the 20" matches none of them), so it is
        # driven straight from the tool result. One action PER item: a multi-item
        # request stages several lines in one turn, and `next(...)` used to keep
        # only the first — silently dropping every other item the model added.
        staged_adds: List[dict] = []
        for tr in tool_results:
            res = tr.get("result") or {}
            if tr.get("tool") == "add_to_cart" and res.get("staged") and res.get("product_id"):
                # De-duplicate by product: a repeated add in one turn must not
                # write the same line (and quantity) twice.
                staged_adds = [
                    s for s in staged_adds if str(s.get("product_id")) != str(res["product_id"])
                ]
                staged_adds.append(res)

        purchase_intent = any(kw in lower_user for kw in [
            "add it", "add this", "add to cart", "buy", "place order",
            "order it", "checkout", "create boq", "generate boq",
            "proceed with the order",
        ])
        ai_suggests_action = any(kw in lower_reply for kw in [
            "add to cart", "add this to your cart", "place an order", "place order", "checkout",
        ])
        checkout_intent = any(kw in lower_user for kw in ["checkout", "place order", "proceed with the order"])

        # 1. Add to Cart — carries the requested quantity + minimum so the client
        #    can refuse a sub-minimum add and quote the exact minimum. Also emitted
        #    whenever guest adds are staged (see `staged_adds` above).
        if (cart_intent or staged_adds) and not already_added:
            if staged_adds:
                for s in staged_adds:
                    actions.append(ActionButton(
                        label="Add to Cart",
                        type="add_to_cart",
                        data={
                            "product_ids": [s.get("product_id")],
                            "product_id": s.get("product_id"),
                            "product_name": s.get("product_name"),
                            "quantity": s.get("quantity", 1),
                            "minimum_order_quantity": s.get("minimum_order_quantity"),
                            "unit": s.get("unit"),
                            "price": s.get("price"),
                            "staged": True,
                        },
                    ))
            else:
                primary = candidates[0]
                action_data = {
                    "product_ids": [c["id"] for c in candidates],
                    "product_id": primary["id"],
                    "product_name": primary.get("name"),
                    "quantity": primary.get("quantity", 1),
                    "minimum_order_quantity": primary.get("minimum_order_quantity"),
                }
                actions.append(ActionButton(
                    label="Add to Cart",
                    type="add_to_cart",
                    data=action_data,
                ))

        # 2. Checkout — anonymous shoppers land on the guest cart.
        confirmed = any(kw in lower_user for kw in ["yes", "add it", "go ahead", "sure"]) or checkout_intent
        mentions_cart_flow = any(kw in lower_reply for kw in ["cart", "order", "checkout"])
        if (confirmed or ai_suggests_action) and (mentions_cart_flow or checkout_intent):
            if not any(a.type == "checkout" for a in actions):
                actions.append(ActionButton(label="Proceed to Checkout", type="checkout", data={}))

        # Already added server-side — offer the cart instead of another add.
        if already_added and not any(a.type == "checkout" for a in actions):
            actions.append(ActionButton(label="View Cart", type="checkout", data={}))

        # 3. Anonymous: an account is only needed to create a BOQ / source items.
        if not self.is_authenticated:
            needs_account = (
                (purchase_intent and not cart_intent and not checkout_intent)
                or (ai_suggests_action and not any(a.type in ("add_to_cart", "checkout") for a in actions))
            )
            if needs_account:
                actions.append(ActionButton(
                    label="Create Free Account",
                    type="signup",
                    data={"redirect": "/auth/choose-role"},
                ))

        # 4. View results — a product-level link when we have one, else the
        #    marketplace catalogue.
        if tool_results:
            has_results = bool(candidates) or any(
                (tr.get("result", {}) or {}).get("products")
                or (tr.get("result", {}) or {}).get("alternatives")
                or (tr.get("result", {}) or {}).get("offers")
                or (tr.get("result", {}) or {}).get("suppliers")
                for tr in tool_results
            )
            if has_results:
                if candidates:
                    view_data = {"product_id": candidates[0]["id"], "path_hint": "product"}
                else:
                    view_data = {"path_hint": "marketplace"}
                actions.append(ActionButton(
                    label="View in Marketplace",
                    type="view_product",
                    data=view_data,
                ))

        return actions if actions else None
