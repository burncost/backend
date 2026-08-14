"""Idempotent bootstrap: create the initial super_admin / admin user.

Usage (from Backend/):
    python -m app.scripts.bootstrap_admin --email admin@burncost.test --password 'change-me'

Reuses the app's password hashing (passlib/bcrypt) so the password is
never stored in plaintext or committed to the repo.
"""
import argparse
import asyncio
import getpass
import uuid

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.user import User


async def bootstrap_admin(email: str, password: str, role: str = "super_admin") -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Admin already exists for {email} (id={existing.id}) — nothing to do.")
            return

        user = User(
            email=email,
            phone_number=f"999{uuid.uuid4().hex[:7]}",  # short unique placeholder (< VARCHAR(20))
            password_hash=get_password_hash(password),
            role=role,
            status="active",
            email_verified=True,
            phone_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        print(f"Created {role} user: {email} (id={user.id}). Change password on first login.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an initial admin/super_admin user.")
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--password", help="Admin password (prompted if omitted)")
    parser.add_argument("--role", default="super_admin", choices=["admin", "super_admin"])
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    asyncio.run(bootstrap_admin(args.email, password, args.role))


if __name__ == "__main__":
    main()