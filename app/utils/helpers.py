from datetime import datetime
from email.utils import formataddr
from typing import Any
from email.mime.text import MIMEText
import smtplib 
from app.config import settings
import logging

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

logger = logging.getLogger(__name__)

API_URL = settings.API_URL

configuration = sib_api_v3_sdk.Configuration()
configuration.api_key["api-key"] = settings.BREVO_API_KEY

api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

### Generate unique code with timestam
def generate_unique_code(prefix: str = "") -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{timestamp}" if prefix else timestamp

### Format currency amount
def format_currency(amount: float, currency: str = "NGN") -> str:
    return f"{currency} {amount:,.2f}"

async def send_mail_via_brevo(_link: str, recipient: str, subject: str):
    try:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Arial, sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
            <tr>
            <td align="center">

                <table width="600" style="background:#ffffff; border-radius:8px; overflow:hidden;">

                <tr>
                    <td align="center" style="padding:20px; background:#;">
                    <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png"
                        alt="Logo"
                        width="150"
                        style="display:block; max-width:150px; height:auto;"/>
                    </td>
                </tr>

                <tr>
                    <td style="padding:30px; color:#333;">
                    <h2>{subject}</h2>

                    <p>Please click below:</p>

                    <p style="text-align:center;">
                        <a href="{_link}" 
                        style="background:#0d6efd; color:#fff; padding:12px 20px; text-decoration:none; border-radius:5px;">
                        Verify Email
                        </a>
                </tr>

                <tr>
                    <td style="text-align:center; padding:20px; font-size:12px; color:#888;">
                    © 2026 BurnCost. All rights reserved.
                    </td>
                </tr>

                </table>

            </td>
            </tr>
        </table>
        </body>
        </html>
        """

        response = sib_api_v3_sdk.SendSmtpEmail(
            sender={"name": "Burncost", "email": settings.EMAILS_FROM_EMAIL},
            to=[{"email": recipient}],
            subject=subject,
            html_content=html_content,
        )        
        try:
            res = api_instance.send_transac_email(response)
            logger.info(f"Email sent successfully to {recipient} with Brevo API.")
            return True
        except ApiException as e:
            print(f"Error Sending email: {e}")
            raise
        
    except Exception as e:
        logger.error(f"Failed to send email to {recipient} with Brevo API: {str(e)}")
        return False

async def send_mail_via_spacemail(_link: str, recipient: str, subject: str):
    try:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Arial, sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
            <tr>
            <td align="center">

                <table width="600" style="background:#ffffff; border-radius:8px; overflow:hidden;">

                <tr>
                    <td align="center" style="padding:20px; background:#;">
                    <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png"
                        alt="Logo"
                        width="150"
                        style="display:block; max-width:150px; height:auto;"/>
                    </td>
                </tr>

                <tr>
                    <td style="padding:30px; color:#333;">
                    <h2>{subject}</h2>

                    <p>Please click below:</p>

                    <p style="text-align:center;">
                        <a href="{ _link}" 
                        style="background:#0d6efd; color:#fff; padding:12px 20px; text-decoration:none; border-radius:5px;">
                        Verify Email
                        </a>
                </tr>

                <tr>
                    <td style="text-align:center; padding:20px; font-size:12px; color:#888;">
                    © 2026 BurnCost. All rights reserved.
                    </td>
                </tr>

                </table>

            </td>
            </tr>
        </table>
        </body>
        </html>
        """

        message = MIMEText(html_content, 'html')
        message["Subject"] = subject
        message["From"] = formataddr(("Burncost", settings.EMAILS_FROM_EMAIL))
        message["To"] = recipient

        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(message)
        logger.info(f"Email sent successfully to {recipient} via SPacemail")

        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP auth failed for {recipient}: {str(e)}")
        return False

    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email to {recipient}: {str(e)}")
        return False

    except Exception as e:
        logger.exception(f"Unexpected error sending email to {recipient}")
        return False
