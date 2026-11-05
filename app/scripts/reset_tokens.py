"""Reset token balances to zero for every non-demo user.

Usage (from Backend/):
    python -m app.scripts.reset_tokens          # dry run: show affected rows
    python -m app.scripts.reset_tokens --apply  # perform the reset

The three demo accounts keep their existing balances. Lifetime purchase /
consumption counters, monthly free-tier usage and transaction history are
preserved; each changed balance gets an `expiry` ledger row so the reset is
visible in the audit trail.
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.models.token_usage import TokenTransaction, TokenUsage, TransactionType
from app.models.user import User

DEMO_EMAILS = (
    "builder.demo@burncost.com",
    "driver.demo@burncost.com",
    "vendor.demo@burncost.com",
)


def _mask(db_url: str) -> str:
    """Host/database only - never echo credentials into logs."""
    tail = db_url.split("://", 1)[-1].split("@", 1)[-1]
    return tail.split("?", 1)[0]


async def reset_tokens(apply: bool) -> None:
    print(f"Target database: {_mask(str(engine.url))}")

    async with AsyncSessionLocal() as session:
        demo_rows = (await session.execute(
            select(User.email, TokenUsage.balance)
            .outerjoin(TokenUsage, TokenUsage.user_id == User.id)
            .where(User.email.in_(DEMO_EMAILS))
        )).all()
        demo_balances = {email: balance for email, balance in demo_rows}

        rows = (await session.execute(
            select(TokenUsage, User.email)
            .join(User, User.id == TokenUsage.user_id)
            .where(
                TokenUsage.balance != 0,
                User.email.notin_(DEMO_EMAILS),
            )
        )).all()

        total = sum(int(usage.balance or 0) for usage, _ in rows)
        print(f"Non-demo token rows with a non-zero balance: {len(rows)} "
              f"(total {total} tokens)")

        for email in DEMO_EMAILS:
            if email in demo_balances:
                print(f"[KEEP] {email}: {demo_balances[email]}")
            else:
                print(f"[KEEP] {email}: no token row")

        if not apply:
            print("Dry run only - re-run with --apply to reset these balances to 0.")
            return

        for usage, _ in rows:
            old_balance = int(usage.balance or 0)
            usage.balance = 0
            session.add(TokenTransaction(
                user_id=usage.user_id,
                transaction_type=TransactionType.EXPIRY.value,
                amount=-old_balance,
                balance_after=0,
                description="Admin reset: token balance cleared",
            ))

        await session.commit()
        print(f"Reset {len(rows)} token balance(s) to 0.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset non-demo token balances to zero."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the reset (default is a dry run).",
    )
    args = parser.parse_args()
    asyncio.run(reset_tokens(args.apply))


if __name__ == "__main__":
    main()
