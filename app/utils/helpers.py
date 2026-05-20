from datetime import datetime
from email.utils import formataddr
from typing import Any
from email.mime.text import MIMEText
import smtplib 
from app.config import settings
import logging

# from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
# from bs4 import BeautifulSoup
# from fake_useragent import UserAgent
import random

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

import os

# os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "./playwright_browsers"
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
                    </p>

                    <p>{_link}</p>
                    </td>
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

        logger.info(f"Email sent successfully to {recipient} with Resend API. Response: {response}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {recipient} with Resend API: {str(e)}")
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
                    </p>

                    <p>{_link}</p>
                    </td>
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
        logger.info(f"Email sent successfully to {recipient}")

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

# async def verify_business(rc_number: str) -> dict:
#     URL = "https://icrp.cac.gov.ng/public-search"

#     result = {
#         "business_name": "",
#         "rc_number": "",
#         "date_of_registration": "",
#         "nature_of_business": "",
#         "status": "",
#         "tax_id": ""
#     }

#     try:
#         ua = UserAgent()
#         random_user_agent = ua.random

#         viewport = random.choice([
#             {"width": 1366, "height": 768},
#             {"width": 1440, "height": 900},
#             {"width": 1536, "height": 864},
#             {"width": 1920, "height": 1080},
#         ])

#         async with async_playwright() as p:
#             browser = await p.chromium.launch(
#                 headless=True,
#                 args=["--disable-blink-features=AutomationControlled"]
#             )

#             try:
#                 context = await browser.new_context(
#                     user_agent=random_user_agent,
#                     viewport=viewport,
#                     locale="en-US",
#                     timezone_id="Africa/Lagos",
#                     extra_http_headers={
#                         "Accept-Language": "en-US,en;q=0.9",
#                         "DNT": "1"
#                     }
#                 )

#                 page = await context.new_page()

#                 await page.goto(URL, timeout=30000)

#                 await page.fill(
#                     'input[placeholder="Entity name, RC number, or AV code..."]',
#                     rc_number
#                 )

#                 await page.click("button.search-btn")

#                 # 👇 critical timeout protection
#                 await page.wait_for_selector(
#                     "h3.fs-18.text-primary",
#                     timeout=15000
#                 )

#                 # Optional Tax ID click
#                 try:
#                     tax_btn = page.locator(
#                         "button.btn.btn-primary",
#                         has_text="Get Tax ID"
#                     )

#                     if await tax_btn.is_visible():
#                         await tax_btn.click()
#                         await page.wait_for_timeout(1000)
#                 except:
#                     pass

#                 soup = BeautifulSoup(await page.content(), "html.parser")

#                 # --- parsing ---
#                 name_el = soup.select_one("h3.fs-18.text-primary")
#                 if name_el:
#                     result["business_name"] = name_el.get_text(strip=True)

#                 rc_el = soup.select_one("p.text-secondary.pt-1.pb-2")
#                 if rc_el:
#                     parts = rc_el.get_text(strip=True).split("-")
#                     if len(parts) > 1:
#                         extracted_rc = parts[1].strip()
#                         if extracted_rc == str(rc_number):
#                             result["rc_number"] = extracted_rc

#                 for li in soup.select("ul.list-unstyled li"):
#                     text = li.get_text(" ", strip=True)

#                     if "Date of Registration -" in text:
#                         result["date_of_registration"] = text.split(
#                             "Date of Registration -"
#                         )[-1].strip()

#                     elif "Nature of Business -" in text:
#                         result["nature_of_business"] = text.split(
#                             "Nature of Business -"
#                         )[-1].strip()

#                     elif "Status" in text:
#                         badge = li.select_one("span.badge")
#                         if badge:
#                             result["status"] = badge.get_text(strip=True)

#                     elif "Tax ID -" in text:
#                         match = re.search(r"Tax ID -\s*(\d+)", text)
#                         if match:
#                             result["tax_id"] = match.group(1)

#                 return result

#             finally:
#                 await browser.close()

#     except PlaywrightTimeoutError:
#         raise Exception("Timeout while fetching CAC data")

#     except Exception as e:
#         raise Exception(f"Scraper failed: {str(e)}")
