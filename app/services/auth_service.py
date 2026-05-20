from requests import options
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging

import os
import base64
from email.mime.text import MIMEText

import smtplib

from app.core.security import verify_email_token, create_email_verification_token, decode_token
from app.crud import user as user_crud
from app.config import settings

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import random
import re
import time

import resend

logger = logging.getLogger(__name__)

API_URL = settings.API_URL

resend.api_key = settings.RESEND_API_KEY

class AuthService:

    def send_email(self, recipient: str, verification_link: str):
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
                        <h2>Verify Your Email</h2>

                        <p>Please click below:</p>

                        <p style="text-align:center;">
                            <a href="{verification_link}" 
                            style="background:#0d6efd; color:#fff; padding:12px 20px; text-decoration:none; border-radius:5px;">
                            Verify Email
                            </a>
                        </p>

                        <p>{verification_link}</p>
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

            response = resend.Emails.send({
                "from":"Burncost <noreply@burncost.com>",
                "to":recipient,
                "subject": "Verify your email",
                "html": html_content   
                } 
            )
            logger.info(f"Email sent successfully to {recipient} with Resend API. Response: {response}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {recipient} with Resend API: {str(e)}")
            return False
        
    async def send_verification_email(self, email: str, redirect_dashboard: str):
        token = create_email_verification_token(email)
        verification_link = f"{settings.API_URL}/auth/verify-email?token={token}"

        # CALL the Gmail sender
        logger.info(f"\n\n\nSending verification email to {email} with link: {verification_link}\n\n")
        self.send_email(email, verification_link)

    async def verify_email(self, db: AsyncSession, token: str) -> bool:
        email = verify_email_token(token)
        if not email:
            return False

        user = await user_crud.get_by_email(db, email=email)
        if not user:
            return False
        role = user.role.value
        await user_crud.verify_email(db, user_id=user.id)
        # return True
        return role
    
    async def verify_business(self, rc_number: int) -> dict:
        URL = "https://icrp.cac.gov.ng/public-search"

        result = {
            "business_name": "",
            "rc_number": "",
            "date_of_registration": "",
            "nature_of_business": "",
            "status": "",
            "tax_id": ""
        }

        # Random realistic desktop user agents
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        ]

        # Random viewport sizes
        viewports = [
            (1366, 768),
            (1440, 900),
            (1536, 864),
            (1920, 1080),
        ]

        selected_user_agent = random.choice(user_agents)
        selected_viewport = random.choice(viewports)

        options = webdriver.ChromeOptions()

        # options.binary_location = ".//chrome_headless_shell//headless//chrome-headless-shell.exe"

        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--window-size={selected_viewport[0]},{selected_viewport[1]}")
        options.add_argument(f"user-agent={selected_user_agent}")

        # Reduce Chrome log verbosity
        options.add_argument("--log-level=3")
        options.add_argument("--silent")

        # Suppress webdriver console logs
        service = Service(
            service=Service(ChromeDriverManager().install()),
            log_path=os.devnull
        )

        driver = None

        try:
            driver = webdriver.Chrome(
                service=service,
                options=options
            )

            wait = WebDriverWait(driver, 15)

            # Hide webdriver flag
            driver.execute_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """)

            # Load page
            driver.get(URL)

            # Enter RC number
            search_input = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                    'input[placeholder="Entity name, RC number, or AV code..."]')
                )
            )

            search_input.clear()
            search_input.send_keys(rc_number)

            # Click search
            search_btn = driver.find_element(By.CSS_SELECTOR, "button.search-btn")
            search_btn.click()

            # Wait for results
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "h3.fs-18.text-primary")
                )
            )

            # Try Get Tax ID
            try:
                tax_btn = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, '//button[contains(text(),"Get Tax ID")]')
                    )
                )

                driver.execute_script("arguments[0].click();", tax_btn)
                time.sleep(1)

            except Exception:
                pass

            # Parse HTML
            soup = BeautifulSoup(driver.page_source, "html.parser")

            # Business Name
            name_el = soup.select_one("h3.fs-18.text-primary")
            if name_el:
                result["business_name"] = name_el.get_text(strip=True)

            # RC Number
            rc_el = soup.select_one("p.text-secondary.pt-1.pb-2")
            if rc_el:
                rc_text = rc_el.get_text(strip=True)
                parts = rc_text.split("-")
                if len(parts) > 1:
                    extracted_rc = parts[1].strip()
                    if extracted_rc == str(rc_number):
                        result["rc_number"] = extracted_rc

            # Parse details
            for li in soup.select("ul.list-unstyled li"):
                text = li.get_text(" ", strip=True)

                if "Date of Registration -" in text:
                    result["date_of_registration"] = text.split(
                        "Date of Registration -"
                    )[-1].strip()

                elif "Nature of Business -" in text:
                    result["nature_of_business"] = text.split(
                        "Nature of Business -"
                    )[-1].strip()

                elif "Status" in text:
                    badge = li.select_one("span.badge")
                    if badge:
                        result["status"] = badge.get_text(strip=True)

                elif "Tax ID -" in text:
                    match = re.search(r"Tax ID -\s*(\d+)", text)
                    if match:
                        result["tax_id"] = match.group(1)

        except Exception as e:
            print(f"Critical Error: {str(e)}")

        finally:
            if driver:
                driver.quit()

        return result

