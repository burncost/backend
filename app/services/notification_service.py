"""Notification Service - Real email/SMS notifications via Brevo and Termii."""
from typing import Optional, List, Dict, Any
import logging
import os
from app.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending email and SMS notifications."""

    def __init__(self):
        self.brevo_api_key = settings.BREVO_API_KEY
        self.termii_api_key = os.getenv("TERMII_API_KEY", "")
        self.termii_sender_id = os.getenv("TERMII_SENDER_ID", "Burncost")
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.emails_from = settings.EMAILS_FROM_EMAIL
        self.emails_from_name = settings.EMAILS_FROM_NAME

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        cc: Optional[List[str]] = None
    ) -> bool:
        """Send an email via Brevo API or SMTP fallback."""
        logger.info(f"Sending email to {to}: {subject}")

        if self.brevo_api_key:
            return await self._send_via_brevo(to, subject, html_content, cc)

        if self.smtp_host:
            return await self._send_via_smtp(to, subject, html_content, cc)

        logger.warning("No email provider configured, logging email")
        logger.info(f"EMAIL TO: {to} | SUBJECT: {subject}")
        return True

    async def send_sms(self, to: str, message: str) -> bool:
        """Send an SMS via Termii API."""
        logger.info(f"Sending SMS to {to}")

        if self.termii_api_key:
            return await self._send_via_termii(to, message)

        logger.warning("No SMS provider configured, logging SMS")
        logger.info(f"SMS TO: {to} | MESSAGE: {message}")
        return True

    async def send_order_confirmation(self, email: str, order_number: str, items: List[Dict[str, Any]], total: float) -> bool:
        """Send order confirmation email."""
        items_html = "".join(
            f"<tr><td>{i.get('name', 'Item')}</td><td>{i.get('quantity', 0)}</td><td>₦{i.get('price', 0):,.2f}</td></tr>"
            for i in items
        )
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
        <div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Order Confirmed!</h2>
        <p>Your order <strong>{order_number}</strong> has been placed successfully.</p>
        <table style="width:100%;border-collapse:collapse;">
        <tr style="background:#f8f8f8;"><th style="padding:8px;text-align:left;">Item</th><th style="padding:8px;">Qty</th><th style="padding:8px;">Price</th></tr>
        {items_html}
        </table>
        <p style="font-size:18px;font-weight:bold;margin-top:16px;">Total: ₦{total:,.2f}</p>
        <p>Your payment is secured in escrow until delivery confirmation.</p>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, f"Order Confirmed - {order_number}", html)

    async def send_delivery_update(self, email: str, order_number: str, status: str, location: str) -> bool:
        """Send delivery status update email."""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
        <div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Delivery Update</h2>
        <p>Order <strong>{order_number}</strong> status: <strong>{status}</strong></p>
        <p>Current location: {location}</p>
        <p>Track your delivery in your Burncost dashboard.</p>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, f"Delivery Update - {order_number}", html)

    async def send_welcome_email(self, email: str, full_name: str, role: str = "customer") -> bool:
        """Send welcome email after successful registration."""
        dashboard_link = f"{settings.FRONTEND_URL}/dashboard"
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
        <div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Welcome to Burncost, {full_name}! 🎉</h2>
        <p>Thank you for joining Burncost — Nigeria's trusted marketplace for construction materials.</p>
        <p>Here's what you can do:</p>
        <ul style="line-height:1.8;">
            <li>🔍 Browse verified suppliers and compare prices</li>
            <li>🛒 Order construction materials at competitive rates</li>
            <li>🔒 Pay securely with our escrow protection</li>
            <li>📦 Track your deliveries in real-time</li>
        </ul>
        <div style="text-align:center;margin:24px 0;">
            <a href="{dashboard_link}" style="background:#FF6B00;color:white;padding:12px 32px;border-radius:6px;text-decoration:none;font-weight:bold;">Go to Dashboard</a>
        </div>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, f"Welcome to Burncost, {full_name}!", html)

    async def send_verification_email(self, email: str, verification_token: str, full_name: str) -> bool:
        """Send email verification link."""
        verify_link = f"{settings.API_URL}/auth/verify-email?token={verification_token}"
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;"><div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Verify Your Email Address</h2>
        <p>Hi {full_name},</p>
        <p>Please verify your email address by clicking the button below:</p>
        <div style="text-align:center;margin:24px 0;">
            <a href="{verify_link}" style="background:#FF6B00;color:white;padding:12px 32px;border-radius:6px;text-decoration:none;font-weight:bold;">Verify Email</a>
        </div>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, "Verify your Burncost email address", html)

    async def send_password_reset_email(self, email: str, reset_token: str, full_name: str) -> bool:
        """Send password reset email."""
        reset_link = f"{settings.FRONTEND_URL}/auth/reset-password?token={reset_token}"
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
        <div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Reset Your Password</h2>
        <p>Hi {full_name},</p>
        <p>We received a request to reset your password. Click the button below to set a new password:</p>
        <div style="text-align:center;margin:24px 0;">
            <a href="{reset_link}" style="background:#FF6B00;color:white;padding:12px 32px;border-radius:6px;text-decoration:none;font-weight:bold;">Reset Password</a>
        </div>
        <p>If you didn't request this, you can safely ignore this email.</p>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, "Reset your Burncost password", html)

    async def send_vendor_approval_email(self, email: str, business_name: str) -> bool:
        """Send vendor approval notification."""
        dashboard_link = f"{settings.FRONTEND_URL}/supplier-dashboard"
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
        <div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Vendor Application Approved! ✅</h2>
        <p>Congratulations <strong>{business_name}</strong>,</p>
        <p>Your vendor application has been reviewed and approved. You can now start receiving orders on Burncost.</p>
        <div style="text-align:center;margin:24px 0;">
            <a href="{dashboard_link}" style="background:#FF6B00;color:white;padding:12px 32px;border-radius:6px;text-decoration:none;font-weight:bold;">Go to Vendor Dashboard</a>
        </div>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, f"{business_name} - Vendor Application Approved!", html)

    async def send_payment_receipt(self, email: str, amount: float, reference: str) -> bool:
        """Send payment receipt email."""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
        <div style="text-align:center;margin-bottom:20px;">
            <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
        </div>
        <h2 style="color:#FF6B00;">Payment Receipt</h2>
        <p>Payment of <strong>₦{amount:,.2f}</strong> has been received.</p>
        <p>Reference: {reference}</p>
        <p>Funds are held securely in escrow until delivery confirmation.</p>
        <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
        </div></body></html>"""
        return await self.send_email(email, f"Payment Receipt - {reference}", html)

    async def _send_via_brevo(self, to: str, subject: str, html_content: str, cc: Optional[List[str]] = None) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                payload = {
                    "sender": {"name": self.emails_from_name, "email": self.emails_from},
                    "to": [{"email": to}],
                    "subject": subject,
                    "htmlContent": html_content,
                }
                if cc:
                    payload["cc"] = [{"email": c} for c in cc]

                response = await client.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={
                        "api-key": self.brevo_api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=30.0,
                )
                if response.status_code in (200, 201):
                    logger.info(f"Email sent via Brevo to {to}")
                    return True
                logger.error(f"Brevo error {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Brevo send failed: {str(e)}")
            return False

    async def _send_via_smtp(self, to: str, subject: str, html_content: str, cc: Optional[List[str]] = None) -> bool:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.utils import formataddr

            msg = MIMEText(html_content, "html")
            msg["Subject"] = subject
            msg["From"] = formataddr((self.emails_from_name, self.emails_from))
            msg["To"] = to
            if cc:
                msg["Cc"] = ", ".join(cc)

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info(f"Email sent via SMTP to {to}")
            return True
        except Exception as e:
            logger.error(f"SMTP send failed: {str(e)}")
            return False

    async def _send_via_termii(self, to: str, message: str) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.termii.com/api/sms/send",
                    json={
                        "api_key": self.termii_api_key,
                        "to": to,
                        "from": self.termii_sender_id,
                        "sms": message,
                        "type": "plain",
                        "channel": "generic",
                    }
                )
                if response.status_code == 200:
                    logger.info(f"SMS sent via Termii to {to}")
                    return True
                logger.error(f"Termii error: {response.text}")
                return False
        except ImportError:
            logger.warning("httpx not installed for SMS")
            return False
        except Exception as e:
            logger.error(f"Termii send failed: {str(e)}")
            return False
