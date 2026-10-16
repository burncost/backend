"""Shared promo-code eligibility rules.

Single source of truth for "does this code cover these lines, and how much can
it take off". The cart's pre-checkout validation (`POST /promos/validate` with
cart context) and `orders.create`'s authoritative check both call into here, so
a code the cart shows as applicable can never be silently dropped at checkout.

Pure functions only - no DB and no I/O: the caller fetches the promo row, counts
per-buyer uses and persists `current_uses`.
"""

from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Tuple

# Plain-language reasons, safe to show verbatim in the cart UI.
REASON_INACTIVE = "This promo code is no longer active"
REASON_USAGE_LIMIT = "This promo code has reached its usage limit"
REASON_PER_USER_LIMIT = "You've already used this promo code the maximum number of times"
REASON_EXPIRED = "This promo code has expired"
REASON_NO_ELIGIBLE_ITEMS = "This promo code doesn't apply to any items in your cart"
REASON_MIN_ORDER = "Minimum order of ₦{amount:,.0f} required for this code"


def eligible_base(promo: Any, items: Iterable[Mapping[str, Any]]) -> float:
    """Value of the lines a promo covers.

    Scope coverage mirrors checkout:
      platform -> every line, vendor -> the code owner's lines,
      product -> that product, category -> lines in that category.
    A vendor/category code never bleeds onto another vendor's lines.

    Each item is a mapping with `product_id`, `vendor_id`, `category_id` and
    `total_price` (as built by `orders.create` and the validate endpoint).
    """
    scope = (promo.scope_type or "platform")
    owner_id = str(promo.vendor_id) if promo.vendor_id else None
    scope_target = str(promo.scope_id) if promo.scope_id else None

    total = 0.0
    for item in items:
        if scope == "platform":
            matched = True
        elif scope == "vendor":
            matched = owner_id is not None and str(item.get("vendor_id")) == owner_id
        elif scope == "product":
            matched = scope_target is not None and str(item.get("product_id")) == scope_target
        elif scope == "category":
            matched = (
                scope_target is not None
                and item.get("category_id") is not None
                and str(item.get("category_id")) == scope_target
            )
        else:
            matched = False
        # For a vendor/category promo, a code must not bleed onto items the owner
        # does not control.
        if matched and scope in ("vendor", "category") and owner_id is not None:
            matched = str(item.get("vendor_id")) == owner_id
        if matched:
            total += float(item.get("total_price") or 0.0)

    return round(total, 2)


def failure_reason(
    promo: Any,
    base: float,
    *,
    per_user_ok: bool = True,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Why the promo cannot be applied to `base`, or None when it can.

    `base` is the eligible item value (0 when nothing in the cart matches).
    Order matters: hard failures (inactive/expired/caps) are reported before the
    cart-specific ones so the message stays accurate.
    """
    if not promo.is_active:
        return REASON_INACTIVE
    if promo.max_uses and promo.current_uses >= promo.max_uses:
        return REASON_USAGE_LIMIT
    if not per_user_ok:
        return REASON_PER_USER_LIMIT
    now = now or datetime.utcnow()
    if promo.expires_at is not None and promo.expires_at <= now:
        return REASON_EXPIRED
    if base <= 0:
        return REASON_NO_ELIGIBLE_ITEMS
    min_order = float(promo.min_order_amount or 0)
    if min_order > 0 and base < min_order:
        return REASON_MIN_ORDER.format(amount=min_order)
    return None


def evaluate(
    promo: Any,
    items: Optional[Iterable[Mapping[str, Any]]] = None,
    *,
    subtotal: Optional[float] = None,
    per_user_ok: bool = True,
    now: Optional[datetime] = None,
) -> Tuple[float, Optional[str]]:
    """Return `(discount, reason)` for a promo against a cart.

    `items` are the cart lines (see `eligible_base`). When the caller has no
    per-line detail (a guest cart knows no vendor/category ids) pass `subtotal`
    instead: only the flat cap and the minimum order can be checked then.
    A non-None reason means the promo must not be applied.
    """
    base = float(subtotal or 0.0) if items is None else eligible_base(promo, items)
    reason = failure_reason(promo, base, per_user_ok=per_user_ok, now=now)
    if reason:
        return 0.0, reason
    # Flat-amount promo: cap it at the eligible subtotal so the discount can
    # never exceed the value of the items it applies to.
    return round(min(float(promo.discount_amount), base), 2), None
