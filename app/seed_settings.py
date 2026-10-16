"""Idempotent seeder for pricing + settlement system settings (Phase 0).

Creates the `system_settings` rows the admin Pricing/Settlement screens edit, using
the config defaults as the starting values. Existing rows are left untouched, so
this is safe to run repeatedly and never clobbers an admin's changes.

Run with: python -m app.seed_settings
"""
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.system_setting import SystemSetting
from app.services import runtime_settings

# key -> (section, description, default value)
SETTINGS: dict[str, tuple[str, str, str]] = {
    "vat_enabled": (
        "pricing",
        "Charge VAT at checkout (true/false).",
        "true",
    ),
    "vat_rate": (
        "pricing",
        "VAT rate as a percentage, e.g. 7.5 = 7.5%.",
        "7.5",
    ),
    "vat_base": (
        "pricing",
        "What VAT is charged on: 'subtotal_plus_margin' or 'subtotal'.",
        "subtotal_plus_margin",
    ),
    "client_margin_share": (
        "pricing",
        "Share of the platform margin charged to the buyer (0-1).",
        "0.40",
    ),
    "vendor_margin_share": (
        "pricing",
        "Share of the platform margin deducted from the vendor payout (0-1).",
        "0.60",
    ),
    "settlement_enabled": (
        "settlements",
        "Master switch for vendor settlement accrual and payouts (true/false).",
        "false",
    ),
    "settlement_commission_rate": (
        "settlements",
        "Extra platform commission taken at settlement as a fraction (0.05 = 5%).",
        "0.0",
    ),
    "payout_primary_gateway": (
        "settlements",
        "Preferred payout provider (paystack).",
        "paystack",
    ),
    "payout_backup_gateway": (
        "settlements",
        "Fallback payout provider used when the primary is unavailable (monnify).",
        "monnify",
    ),
    # ── notifications ───────────────────────────────────────────────────────
    "alert_fraud": (
        "notifications",
        "Send admins an in-app alert for fraud/risk events (true/false).",
        "true",
    ),
    "alert_supplier": (
        "notifications",
        "Notify admins when a new supplier registers or is auto-verified (true/false).",
        "true",
    ),
    "alert_price": (
        "notifications",
        "Send admins an in-app alert for price-integrity violations (true/false).",
        "true",
    ),
    "alert_escrow": (
        "notifications",
        "Notify admins about escrow/payment status changes (true/false).",
        "true",
    ),
    "alert_dispute": (
        "notifications",
        "Escalate high-priority disputes to the admin inbox (true/false).",
        "true",
    ),
    # ── security: password policy ──────────────────────────────────────────
    "password_complexity": (
        "security",
        "Enforce minimum password complexity on every password write (true/false).",
        "false",
    ),
    "password_expiration": (
        "security",
        "Require a password change once it is older than the configured age (true/false).",
        "false",
    ),
    "password_expiration_days": (
        "security",
        "Password lifetime in days before a change is required (default 90).",
        "90",
    ),
    "password_attempts": (
        "security",
        "Lock an account/IP after repeated failed logins (true/false). On by default.",
        "true",
    ),
    "password_attempts_threshold": (
        "security",
        "Failed logins allowed inside the window before lockout (default 5).",
        "5",
    ),
    "password_attempts_window_minutes": (
        "security",
        "How long a failed-login counter lives, in minutes (default 15).",
        "15",
    ),
    # ── security: authentication ───────────────────────────────────────────
    "security_2fa": (
        "security",
        "Require every admin-tier user to enrol a second factor (true/false). "
        "Enrol admins first - enabling this forces enrolment on their next login.",
        "false",
    ),
    "security_2fa_default_method": (
        "security",
        "Preferred second factor pre-selected during enrolment: 'email' or 'totp'.",
        "email",
    ),
    "security_2fa_remember_hours": (
        "security",
        "Hours a 'remember this device' 2FA trust lasts (default 48).",
        "48",
    ),
    "security_session": (
        "security",
        "Auto-logout admin sessions after a period of inactivity (true/false).",
        "false",
    ),
    "security_session_idle_minutes": (
        "security",
        "Idle minutes before an admin session is signed out (default 30).",
        "30",
    ),
    "security_ip": (
        "security",
        "Restrict admin console access to the configured IP allowlist (true/false).",
        "false",
    ),
    "security_ip_allowlist": (
        "security",
        "Comma-separated IPs/CIDRs allowed to reach the admin console.",
        "",
    ),
}


async def seed_settings() -> int:
    """Insert missing pricing/settlement settings rows. Returns rows created."""
    defaults = runtime_settings.snapshot()
    created = 0
    async with AsyncSessionLocal() as db:
        for key, (section, description, fallback) in SETTINGS.items():
            existing = (await db.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )).scalar_one_or_none()
            if existing:
                continue
            db.add(SystemSetting(
                key=key,
                value=defaults.get(key, fallback),
                section=section,
                description=description,
            ))
            created += 1
        await db.commit()
        runtime_settings.invalidate()

    print(f"Seeded {created} pricing/settlement settings (existing rows untouched).")
    return created


if __name__ == "__main__":
    asyncio.run(seed_settings())
