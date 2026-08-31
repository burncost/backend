"""Chat Service - Conversational AI agent with tool execution."""
from typing import Dict, Any, Optional, List
import logging
import uuid
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.database import mongodb
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
                    "category": {"type": "string", "description": "Category name to filter by"},
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
                    "items": {"type": "array", "description": "List of {description, quantity, quoted_rate}"},
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
                    "items": {"type": "array", "description": "List of {description, quoted_rate}"},
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
                    "items": {"type": "array", "description": "List of {description, quantity}"},
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
                    "products": {"type": "array", "description": "List of product names"},
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
                    "items": {"type": "array", "description": "List of {description, quantity, rate}"},
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
]

# System prompt

SYSTEM_PROMPT = """You are Burncost AI — a Nigerian construction project manager with 15+ years experience in building materials procurement across Lagos, Abuja, and Port Harcourt. You work for Burncost, a Nigerian construction materials marketplace.

BREVITY (HIGHEST PRIORITY — overrides all other guidance):
- Reply in AT MOST ~120 words: one short paragraph, or 2-4 bullet points.
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
3. When showing products, include: name, price, brand, and why it's a good fit for their project.
4. After presenting a product, naturally nudge toward action: "Want me to add this to your cart?" or "I can help you place an order."
5. Use 90% proper English. Only use Pidgin once per conversation at the persuasion moment — e.g. "Oga, this one na the best price for 12mm iron rods for this week."
6. Prices are in Nigerian Naira (₦).
7. If the user asks to create a BOQ, generate a quote, or place an order without an account, tell them to sign up and include a signup action.
8. NON-NEGOTIABLE PRICE RULE: Never state a current market price unless it came from a tool result (compare_prices, get_price_range, get_price_history, or a verified DB price). Never invent or estimate a price from your own knowledge. If no verified price was returned, say exactly: "BurnCost does not currently have a verified price for this item in this location. I've notified our vendors — you'll get a quotation once available."
9. APPROVAL RULE: Never execute an order or place a purchase without the user's explicit, unambiguous confirmation. Always show the full itemized total and ask "Shall I proceed with this order?" before executing. You may only execute after the user confirms.
10. DOMAIN GUARD: You are exclusively a construction and building-materials assistant. If the user asks about anything unrelated to construction, building materials, pricing, or project advice, do not engage. Respond with a polite redirect: "I specialize in construction and building materials procurement. I can help you with materials, pricing, BOQs, suppliers, and project advice — how can I assist with your project?"

GREETING RULES:
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
- Example: If asked about roofing in Abuja, advise on roofing sheet types (stone-coated, aluminum, long-span), insulation needs for the harmattan, typical roof pitch, and current price ranges
- Example: If asked about foundation, advise on soil test importance, strip footing vs raft foundation, concrete mix ratios, and reinforcement requirements"""


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

    # Map colloquial user terms -> catalog naming so "iron rods" finds products
    # stored as "Reinforcement Rod" / "rebar". Used only as a fallback when the
    # literal ILIKE search returns nothing.
    _MATERIAL_SYNONYMS = {
        "iron rod": ["reinforcement rod", "reinforcement bar", "rebar", "tmt bar", "steel bar"],
        "steel bar": ["reinforcement bar", "rebar", "high yield rebar"],
        "rebar": ["reinforcement rod", "high yield rebar", "tmt bar"],
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

        # Colloquial -> catalog fallback (e.g. "iron rods" -> "Reinforcement Rod").
        # A literal ILIKE search for "iron rods" returns nothing because the
        # catalog stores the product as "12mm Reinforcement Rod"; without this
        # retry the agent emits the canned "not in catalog" answer.
        if not products and filters.search:
            for alt in self._synonym_terms(filters.search):
                alt_filters = filters.model_copy(update={"search": alt})
                alt_result = await self.product_service.list_products(
                    filters=alt_filters, page=page, page_size=page_size
                )
                if alt_result.get("products"):
                    result = alt_result
                    products = result.get("products", [])
                    break

        # Serialize for JSON
        serialized = []
        for p in products:
            serialized.append({
                "id": str(p.get("id", "")),
                "name": p.get("name", ""),
                "base_price": float(p.get("base_price", 0)),
                "discount_price": float(p.get("discount_price", 0)) if p.get("discount_price") else None,
                "category": p.get("category", ""),
                "brand_name": p.get("brand_name", ""),
                "quantity": p.get("quantity", 0),
                "unit_of_measure": p.get("unit_of_measure", "piece"),
                "status": p.get("status", ""),
                "rating": float(p.get("rating", 0)),
            })
        return {
            "products": serialized,
            "total": result.get("total", 0),
            "page": result.get("page", page),
        }

    async def _get_product(self, product_id: str) -> dict:
        from uuid import UUID
        product = await self.product_service.get_product_by_id(UUID(product_id))
        if not product:
            return {"error": "Product not found"}
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
            "status": product.get("status", ""),
            "rating": float(product.get("rating", 0)),
            "sku": product.get("sku", ""),
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
        await mongodb["vendor_requests"].insert_one(doc)
        logger.info(f"Vendor request created: {request_id} for {product_name}")
        return {
            "request_id": request_id,
            "product_name": product_name,
            "quantity": quantity or 1,
            "status": "pending",
            "message": "Our procurement team is sourcing this item from verified vendors. You'll receive quotations once available.",
        }

    async def _track_vendor_request(self, request_id: str) -> dict:
        doc = await mongodb["vendor_requests"].find_one({"_id": request_id})
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
            cursor = mongodb["boqs"].find({"projectId": ObjectId(project_id)})
            boqs = await cursor.to_list(length=20)
            return {"project_id": project_id, "boqs": [str(b.get("_id")) for b in boqs], "count": len(boqs)}
        except Exception as e:
            logger.warning(f"_get_project_boq failed: {e}")
            return {"error": str(e)}

    async def _get_remaining_materials(self, project_id: str) -> dict:
        from app.services.project_memory_service import ProjectMemoryService
        svc = ProjectMemoryService(mongo_db=mongodb, pg_db=self.pg_db)
        return await svc.get_project_materials(project_id)

    async def _get_procurement_recommendation(self, project_id: str) -> dict:
        from app.services.project_memory_service import ProjectMemoryService
        svc = ProjectMemoryService(mongo_db=mongodb, pg_db=self.pg_db)
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
        if not self.user_id:
            return {"error": "Authentication required to add to cart."}
        from app.models.cart import CartItem
        existing = await self.db.execute(
            select(CartItem).where(CartItem.user_id == self.user_id, CartItem.product_id == product_id)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.quantity += quantity
            await self.db.commit()
        else:
            self.db.add(CartItem(user_id=self.user_id, product_id=product_id, quantity=quantity, price_at_addition=0))
            await self.db.commit()
        return {"success": True, "product_id": product_id, "quantity": quantity}

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
        max_turns = 5
        tool_results_list = []
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
                assistant_msg = {"role": "assistant", "content": msg.content}
                messages.append(assistant_msg)
                await self._save_history(conversation_id, messages, user_id, token_usage)
                actions = self._build_actions(msg.content or "", tool_results_list, message)
                cards = self._build_cards(tool_results_list)
                return ChatResponse(
                    reply=msg.content or "",
                    conversation_id=conversation_id,
                    actions=actions,
                    cards=cards,
                    has_tool_results=len(tool_results_list) > 0,
                )

            # Process tool calls
            assistant_msg = {"role": "assistant", "content": msg.content, "tool_calls": []}
            for tc in msg.tool_calls:
                tc_dict = {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                assistant_msg["tool_calls"].append(tc_dict)

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
                    "content": json.dumps(result),
                })

            messages.append(assistant_msg)

        # If we exit the loop without a final text response
        return ChatResponse(
            reply="I've processed your request. Is there anything else I can help you with?",
            conversation_id=conversation_id,
            has_tool_results=len(tool_results_list) > 0,
        )

    async def _load_history(self, conversation_id: str) -> dict:
        try:
            doc = await mongodb["chat_conversations"].find_one({"_id": conversation_id})
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
            await mongodb["chat_conversations"].update_one(
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

            if tool in ("compare_prices", "get_price_range", "get_price_history"):
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
                        "rate": offer.get("rate"),
                        "price_source": offer.get("price_source") or result.get("source"),
                        "verified": offer.get("verified", result.get("verified", False)),
                        "confidence": offer.get("confidence"),
                        "last_verified_at": offer.get("last_verified_at") or offer.get("city"),
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

    def _build_actions(
        self,
        reply: str,
        tool_results: List[dict],
        user_message: str,
    ) -> Optional[List[ActionButton]]:
        """Generate action buttons based on AI response and tool results."""
        actions: List[ActionButton] = []
        lower_reply = reply.lower()
        lower_user = user_message.lower()

        # 1. Signup action for unauthenticated users
        if not self.is_authenticated:
            # Check if user wants to order, add to cart, or create BOQ
            purchase_intent = any(kw in lower_user for kw in [
                "yes", "add it", "add to cart", "buy", "order", "purchase",
                "checkout", "create boq", "generate boq", "i want"
            ])
            # Also show signup if AI suggests adding to cart or placing an order
            ai_suggests_action = any(kw in lower_reply for kw in [
                "add to cart", "add this to your cart", "place an order", "place order", "checkout"
            ])
            if purchase_intent or ai_suggests_action:
                actions.append(ActionButton(
                    label="Create Free Account",
                    type="signup",
                    data={"redirect": "/auth/choose-role"},
                ))
                return actions

        # 2. For authenticated users, check for product-related actions
        if self.is_authenticated:
            # Check if AI mentioned adding to cart
            if any(kw in lower_reply for kw in ["add to cart", "add this to your cart", "add to your cart"]):
                # Extract product IDs from tool results
                product_ids = []
                for tr in tool_results:
                    result = tr.get("result", {})
                    if "products" in result:
                        for p in result["products"]:
                            if p.get("id"):
                                product_ids.append(p["id"])
                    if "alternatives" in result:
                        for p in result["alternatives"]:
                            if p.get("id"):
                                product_ids.append(p["id"])
                    if "id" in result and tr.get("tool") == "get_product":
                        product_ids.append(result["id"])

                if product_ids:
                    actions.append(ActionButton(
                        label="Add to Cart",
                        type="add_to_cart",
                        data={"product_ids": product_ids},
                    ))

            # Check if user confirmed purchase
            if any(kw in lower_user for kw in ["yes", "add it", "go ahead", "sure"]) and \
               any(kw in lower_reply for kw in ["cart", "order", "checkout"]):
                actions.append(ActionButton(
                    label="Proceed to Checkout",
                    type="checkout",
                    data={},
                ))

            # Check if AI mentioned placing an order
            if any(kw in lower_reply for kw in ["place an order", "place order", "checkout"]):
                if not any(a.type == "checkout" for a in actions):
                    actions.append(ActionButton(
                        label="Checkout",
                        type="checkout",
                        data={},
                    ))

        # 3. View in marketplace if tool results exist
        if tool_results:
            has_products = any(
                tr.get("result", {}).get("products")
                or tr.get("result", {}).get("alternatives")
                for tr in tool_results
            )
            if has_products and not any(a.type == "add_to_cart" for a in actions):
                actions.append(ActionButton(
                    label="View in Marketplace",
                    type="view_product",
                    data={},
                ))

        return actions if actions else None
