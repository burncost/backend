"""Cancel unpaid duplicate drafts that shadow an already-paid order.

Usage (from Backend/):
    python -m app.scripts.cancel_duplicate_drafts
    python -m app.scripts.cancel_duplicate_drafts --apply
    python -m app.scripts.cancel_duplicate_drafts --apply --window-minutes 10

Before checkout became idempotent, a double-clicked "Proceed to Checkout"
could create two drafts for one basket. One gets paid, the other lingers as
"pending payment" and shows as a second Recent Order card. This reconciles
those rows: a draft is only cancelled when it has the exact same
(product, quantity) basket as a paid order created within the same short
checkout burst (``--window-minutes``), so a genuine later re-order is kept.

Dry-run by default; ``--apply`` performs the cancellation, releasing any promo
use exactly like deleting the draft from the dashboard would.
"""
import argparse
import asyncio
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.orders import _DRAFT_STATUSES, _cancel_draft, _cart_fingerprint
from app.core.database import AsyncSessionLocal
from app.models.order import Order, OrderStatus, PaymentStatus

DEFAULT_WINDOW_MINUTES = 10


def _find_duplicates(orders, window: timedelta):
    """Pair unpaid drafts with a paid order for the same basket + user.

    Returns ``[(draft, paid_order), ...]``. Pure function so the matching rule
    is testable without a database.
    """
    paid = defaultdict(list)
    drafts = defaultdict(list)
    for order in orders:
        fingerprint = _cart_fingerprint(order.items)
        if not fingerprint:
            continue
        key = (str(order.user_id), fingerprint)
        if order.payment_status == PaymentStatus.COMPLETED:
            if order.status != OrderStatus.CANCELLED:
                paid[key].append(order)
        elif order.status in _DRAFT_STATUSES:
            drafts[key].append(order)

    matches = []
    for key, unpaid in drafts.items():
        paid_rows = paid.get(key)
        if not paid_rows:
            continue
        for draft in unpaid:
            nearest = min(paid_rows, key=lambda p: abs(p.created_at - draft.created_at))
            if abs(nearest.created_at - draft.created_at) <= window:
                matches.append((draft, nearest))
    return matches


async def reconcile(window: timedelta, apply: bool) -> int:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Order)
            .options(selectinload(Order.items))
            .where(or_(
                Order.payment_status == PaymentStatus.COMPLETED,
                Order.status.in_([s.value for s in _DRAFT_STATUSES]),
            ))
            .order_by(Order.created_at)
        )).scalars().all()

        matches = _find_duplicates(rows, window)
        if not matches:
            print("No duplicate unpaid drafts found.")
            return 0

        for draft, paid_order in matches:
            print(
                f"{draft.order_number} [{draft.status.value if hasattr(draft.status, 'value') else draft.status}] "
                f"duplicates paid {paid_order.order_number} "
                f"(NGN {float(draft.total_amount or 0):,.2f}) - created "
                f"{draft.created_at:%Y-%m-%d %H:%M} vs {paid_order.created_at:%Y-%m-%d %H:%M}",
                flush=True,
            )
            if apply:
                await _cancel_draft(
                    session, draft,
                    f"Duplicate of paid order {paid_order.order_number}",
                )

        if apply:
            await session.commit()
            print(f"Cancelled {len(matches)} duplicate draft(s).")
        else:
            print(f"{len(matches)} duplicate draft(s) found. Re-run with --apply to cancel them.")
        return len(matches)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="cancel the duplicates (default: report only)")
    parser.add_argument(
        "--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES,
        help=f"only pair draft/paid orders created within this window (default {DEFAULT_WINDOW_MINUTES})",
    )
    args = parser.parse_args()
    asyncio.run(reconcile(timedelta(minutes=args.window_minutes), args.apply))


if __name__ == "__main__":
    main()
