"""Central pricing engine (Phase 2) — platform-margin infusion, VAT and the
client/vendor margin split.

Design decisions (approved):

* **Basis = Option B — the EFFECTIVE selling price.** Margin is charged on the price
  the buyer actually pays (`discount_price` when one exists, else `base_price`),
  never on the struck-through market price.
* **`fee_model` is honoured per category.**
    - ``percentage`` -> ``total_price x platform_margin%``
      (``platform_margin`` is stored as a PERCENT, e.g. ``5.00`` = 5%)
    - ``fixed``      -> ``fee_fixed x quantity``
      (``fee_fixed`` is stored in Naira, e.g. ``200`` = ₦200/bag)
    - ``service``    -> treated exactly like ``percentage`` (services are quoted as
      a margin on the service value).
  Unknown/blank models degrade to ``percentage`` so pricing can never fail closed.
* **40/60 client/vendor split.** The platform margin computed above is the TOTAL
  margin. A configurable share is added to the buyer's invoice as the "platform
  fee" line (``platform_fee``), and the remainder is deducted from the vendor's
  settlement payout (``vendor_fee``). The two shares are normalised to sum to 1.
* **VAT** is charged on ``subtotal + platform_fee`` by default (the buyer's
  platform-fee line), and is admin-switchable plus admin-scoped via `vat_base`
  (``subtotal_plus_margin`` | ``subtotal``).

Money is handled with :class:`decimal.Decimal` and rounded half-up to 2dp via
:func:`q2`, matching the ``Numeric(15, 2)`` columns.

Nothing here writes to the database; callers pass in already-loaded items.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.services import runtime_settings
from app.services.settlement_service import margin_split

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
DEFAULT_MARGIN_PERCENT = Decimal("5.00")
VAT_BASES = ("subtotal_plus_margin", "subtotal")


def q2(value: Any) -> Decimal:
    """Round a money value to 2dp, half-up (matches Numeric(15, 2) storage)."""
    if isinstance(value, Decimal):
        dec = value
    else:
        dec = Decimal(str(value if value is not None else 0))
    return dec.quantize(CENT, rounding=ROUND_HALF_UP)


def f2(value: Any) -> float:
    """Decimal -> float for JSON responses (existing API shape stays float)."""
    return float(q2(value))


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PricingConfig:
    """Effective (admin-overridable) pricing configuration."""

    vat_enabled: bool
    vat_rate: Decimal          # percent
    vat_base: str              # subtotal_plus_margin | subtotal
    client_share: Decimal      # fraction of the margin charged to the buyer
    vendor_share: Decimal      # fraction of the margin borne by the vendor


def load_pricing_config() -> PricingConfig:
    """Read the effective pricing config (DB overrides already cached by now)."""
    client_share, vendor_share = margin_split()
    vat_base = (runtime_settings.get_str("vat_base", "subtotal_plus_margin") or "").strip().lower()
    if vat_base not in VAT_BASES:
        vat_base = "subtotal_plus_margin"
    return PricingConfig(
        vat_enabled=runtime_settings.get_bool("vat_enabled", True),
        vat_rate=runtime_settings.get_decimal("vat_rate", Decimal("7.5")),
        vat_base=vat_base,
        client_share=client_share,
        vendor_share=vendor_share,
    )


# ── Category fee resolution ─────────────────────────────────────────────────

@dataclass(frozen=True)
class CategoryFee:
    category_id: Optional[str]
    fee_model: str
    platform_margin: Decimal   # percent
    fee_fixed: Decimal         # naira per unit


def _category_fee_from_row(row: Optional[Category]) -> CategoryFee:
    if row is None:
        return CategoryFee(None, "percentage", DEFAULT_MARGIN_PERCENT, Decimal("0"))
    model = (row.fee_model or "percentage").strip().lower()
    if model not in ("percentage", "fixed", "service"):
        model = "percentage"
    return CategoryFee(
        category_id=str(row.id),
        fee_model=model,
        platform_margin=Decimal(str(row.platform_margin)) if row.platform_margin is not None else DEFAULT_MARGIN_PERCENT,
        fee_fixed=Decimal(str(row.fee_fixed)) if row.fee_fixed is not None else Decimal("0"),
    )


async def load_category_fees(
    db: Optional[AsyncSession], category_ids: Iterable[Optional[str]]
) -> dict[str, CategoryFee]:
    """Bulk-load fee data for the categories referenced by a basket.

    Returns a mapping of ``str(category_id) -> CategoryFee``. Categories that are
    missing (or a ``None`` db for pure unit tests) fall back to the default fee.
    """
    wanted = {str(c) for c in category_ids if c}
    if db is None or not wanted:
        return {}
    rows = (await db.execute(select(Category).where(Category.id.in_(wanted)))).scalars().all()
    return {str(row.id): _category_fee_from_row(row) for row in rows}


def fee_for(category_id: Optional[str], fees: Mapping[str, CategoryFee]) -> CategoryFee:
    """Fee for one item, defaulting when the category is unknown."""
    if category_id is not None:
        found = fees.get(str(category_id))
        if found is not None:
            return found
    return CategoryFee(None, "percentage", DEFAULT_MARGIN_PERCENT, Decimal("0"))


# ── Margin maths ────────────────────────────────────────────────────────────

def compute_item_margin(unit_price: Any, quantity: int, fee: CategoryFee) -> Decimal:
    """Total platform margin for one line, honouring the category's fee model."""
    qty = int(quantity or 0)
    if qty <= 0:
        return Decimal("0")
    if fee.fee_model == "fixed" and fee.fee_fixed > 0:
        return q2(fee.fee_fixed * qty)
    return q2(Decimal(str(unit_price or 0)) * qty * (fee.platform_margin / HUNDRED))


def split_margin(total_margin: Decimal, cfg: PricingConfig) -> tuple[Decimal, Decimal]:
    """(client_fee, vendor_fee) for a total margin, split by the configured shares."""
    total = q2(total_margin)
    client = q2(total * cfg.client_share)
    vendor = q2(total - client)   # remainder, so the two always reconcile to the total
    return client, vendor


# ── Result shapes ───────────────────────────────────────────────────────────

@dataclass
class PricingLine:
    product_id: Optional[str]
    product_name: str
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    fee_model: str
    platform_margin: Decimal   # percent (0 for fixed-fee lines)
    fee_fixed: Decimal
    margin: Decimal            # total margin for the line


@dataclass
class OrderPricing:
    subtotal: Decimal
    platform_fee: Decimal       # charged to the buyer
    vendor_fee: Decimal         # deducted from the vendor payout
    platform_margin_total: Decimal
    shipping_fee: Decimal
    vat_enabled: bool
    vat_rate: Decimal
    vat_base: str
    vat_base_amount: Decimal
    vat_amount: Decimal
    discount_amount: Decimal
    promo_discount: Decimal
    total_amount: Decimal
    lines: list[PricingLine] = field(default_factory=list)
    breakdown: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        """JSON-friendly view (floats), matching the existing API conventions."""
        return {
            "subtotal": f2(self.subtotal),
            "platform_fee": f2(self.platform_fee),
            "vendor_fee": f2(self.vendor_fee),
            "platform_margin_total": f2(self.platform_margin_total),
            "shipping_fee": f2(self.shipping_fee),
            "vat_enabled": self.vat_enabled,
            "vat_rate": f2(self.vat_rate),
            "vat_base": self.vat_base,
            "vat_base_amount": f2(self.vat_base_amount),
            "vat_amount": f2(self.vat_amount),
            "discount_amount": f2(self.discount_amount),
            "promo_discount": f2(self.promo_discount),
            "total_amount": f2(self.total_amount),
            "breakdown": self.breakdown,
        }


def compute_order_pricing(
    items: Sequence[Mapping[str, Any]],
    fees: Mapping[str, CategoryFee],
    cfg: Optional[PricingConfig] = None,
    *,
    shipping_fee: Any = 0,
    discount_amount: Any = 0,
    promo_discount: Any = 0,
) -> OrderPricing:
    """Price a basket.

    ``items`` entries need: ``product_id``, ``category_id``, ``product_name``,
    ``quantity``, ``unit_price`` (effective selling price) and ``total_price``.
    """
    cfg = cfg or load_pricing_config()
    shipping = q2(shipping_fee)
    discount = q2(discount_amount)
    promo = q2(promo_discount)

    lines: list[PricingLine] = []
    breakdown: list[dict] = []
    subtotal = Decimal("0")
    margin_total = Decimal("0")

    for item in items:
        qty = int(item.get("quantity") or 0)
        unit_price = q2(item.get("unit_price"))
        line_total = q2(item.get("total_price", unit_price * qty))
        fee = fee_for(item.get("category_id"), fees)
        margin = compute_item_margin(unit_price, qty, fee)

        subtotal += line_total
        margin_total += margin
        lines.append(PricingLine(
            product_id=str(item.get("product_id")) if item.get("product_id") else None,
            product_name=str(item.get("product_name") or ""),
            quantity=qty,
            unit_price=unit_price,
            total_price=line_total,
            fee_model=fee.fee_model,
            platform_margin=fee.platform_margin,
            fee_fixed=fee.fee_fixed,
            margin=margin,
        ))

    subtotal = q2(subtotal)
    margin_total = q2(margin_total)
    client_fee, vendor_fee = split_margin(margin_total, cfg)

    for line in lines:
        line_client, line_vendor = split_margin(line.margin, cfg)
        breakdown.append({
            "product_name": line.product_name,
            "fee_model": line.fee_model,
            "margin_pct": f2(line.platform_margin),
            "fee_fixed": f2(line.fee_fixed),
            "margin": f2(line.margin),
            "client_fee": f2(line_client),
            "vendor_fee": f2(line_vendor),
        })

    vat_base_amount = subtotal + client_fee if cfg.vat_base == "subtotal_plus_margin" else subtotal
    vat_amount = q2(vat_base_amount * (cfg.vat_rate / HUNDRED)) if cfg.vat_enabled else Decimal("0")

    total = q2(subtotal + client_fee + shipping + vat_amount - discount - promo)

    return OrderPricing(
        subtotal=subtotal,
        platform_fee=client_fee,
        vendor_fee=vendor_fee,
        platform_margin_total=margin_total,
        shipping_fee=shipping,
        vat_enabled=cfg.vat_enabled,
        vat_rate=cfg.vat_rate,
        vat_base=cfg.vat_base,
        vat_base_amount=q2(vat_base_amount),
        vat_amount=vat_amount,
        discount_amount=discount,
        promo_discount=promo,
        total_amount=total,
        lines=lines,
        breakdown=breakdown,
    )


async def vendor_margin_for_items(db: AsyncSession, items: Sequence[Any]) -> Decimal:
    """Vendor-borne portion of the platform margin for a set of OrderItems.

    At checkout the buyer is only charged the *client* share of the margin, so the
    remainder is deducted from the vendor's settlement payout. This recomputes that
    remainder at settlement time from the order's items (Product -> Category), so no
    extra columns are needed on `orders`.

    Best-effort: returns 0 when items cannot be resolved.
    """
    from app.models.product import Product

    product_ids = [getattr(i, "product_id", None) for i in items]
    product_ids = [pid for pid in product_ids if pid is not None]
    if not product_ids:
        return Decimal("0")

    rows = (await db.execute(
        select(Product.id, Product.category_id).where(Product.id.in_(product_ids))
    )).all()
    cat_by_product = {str(pid): (str(cid) if cid else None) for pid, cid in rows}
    fees = await load_category_fees(db, cat_by_product.values())
    cfg = load_pricing_config()

    total = Decimal("0")
    for item in items:
        category_id = cat_by_product.get(str(getattr(item, "product_id", "")))
        fee = fee_for(category_id, fees)
        total += compute_item_margin(getattr(item, "unit_price", 0), getattr(item, "quantity", 0), fee)

    _client_fee, vendor_fee = split_margin(total, cfg)
    return vendor_fee
