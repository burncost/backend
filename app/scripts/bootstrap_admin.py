"""Idempotent bootstrap: create the initial super_admin / admin user.

Usage (from Backend/):
    python -m app.scripts.bootstrap_admin --email admin@burncost.test --password 'change-me'
    python -m app.scripts.bootstrap_admin --email admin@burncost.test --reset-2fa

Reuses the app's password hashing (passlib/bcrypt) so the password is
never stored in plaintext or committed to the repo.
"""
import argparse
import asyncio
import getpass
import uuid
from datetime import datetime

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.user import User


async def bootstrap_admin(email: str, password: str, role: str = "super_admin") -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Admin already exists for {email} (id={existing.id}) - nothing to do.")
            return

        user = User(
            email=email,
            phone_number=f"999{uuid.uuid4().hex[:7]}",  # short unique placeholder (< VARCHAR(20))
            password_hash=get_password_hash(password),
            role=role,
            status="active",
            email_verified=True,
            phone_verified=True,
            password_changed_at=datetime.utcnow(),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        print(f"Created {role} user: {email} (id={user.id}). Change password on first login.")


async def reset_two_factor(email: str) -> None:
    """Break-glass: clear an admin's second factor so they can re-enrol.

    For when an admin has lost their authenticator AND their recovery codes, or
    the 2FA policy is on and they are locked out. Every trusted device is also
    dropped, so no stale "remember this device" survives the reset.
    """
    from app.services import two_factor

    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user:
            print(f"No user found for {email} - nothing to do.")
            return

        previous = user.two_factor_method
        user.two_factor_method = None
        user.two_factor_enabled_at = None
        user.totp_secret = None
        user.totp_recovery_codes = None
        session.add(user)
        await session.commit()

        await two_factor.revoke_all(str(user.id))
        print(f"Cleared 2FA for {email} (was: {previous or 'not enrolled'}). "
              f"They will be asked to enrol again on next sign-in.")


async def reset_password(email: str, password: str) -> None:
    """Break-glass: set a known password on an existing account.

    Separate from the idempotent create above, which deliberately never touches an
    existing row. Kept here so credential recovery does not need ad-hoc SQL.
    """
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user:
            print(f"No user found for {email} - nothing to do.")
            return

        user.password_hash = get_password_hash(password)
        user.password_changed_at = datetime.utcnow()
        session.add(user)
        await session.commit()

        # A new password invalidates every "remember this device" 2FA trust.
        from app.services import two_factor

        await two_factor.revoke_all(str(user.id))
        print(f"Password reset for {email} (role={user.role}). "
              f"Remembered 2FA devices were cleared.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an initial admin/super_admin user.")
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--password", help="Admin password (prompted if omitted)")
    parser.add_argument("--role", default="super_admin", choices=["admin", "super_admin"])
    parser.add_argument(
        "--reset-2fa",
        action="store_true",
        help="Break-glass: clear this account's 2FA enrolment and trusted devices, then exit.",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Break-glass: set --password on an EXISTING account, then exit.",
    )
    args = parser.parse_args()

    if args.reset_2fa:
        asyncio.run(reset_two_factor(args.email))
        return

    if args.reset_password:
        password = args.password or getpass.getpass("New password: ")
        asyncio.run(reset_password(args.email, password))
        return

    password = args.password or getpass.getpass("Password: ")
    asyncio.run(bootstrap_admin(args.email, password, args.role))


if __name__ == "__main__":
    main()