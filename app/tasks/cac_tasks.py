from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import random
import re
import logging

logger = logging.getLogger(__name__)


def get_cac_business_info(rc_number: str) -> dict:
    URL = "https://icrp.cac.gov.ng/public-search"

    result = {
        "business_name": "",
        "rc_number": "",
        "date_of_registration": "",
        "nature_of_business": "",
        "status": "",
        "tax_id": "",
    }

    browser = None
    context = None

    try:
        ua = UserAgent()
        random_user_agent = ua.random

        viewport = random.choice(
            [
                {"width": 1366, "height": 768},
                {"width": 1440, "height": 900},
                {"width": 1536, "height": 864},
                {"width": 1920, "height": 1080},
            ]
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )

            context = browser.new_context(
                user_agent=random_user_agent,
                viewport=viewport,
                locale="en-US",
                timezone_id="Africa/Lagos",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "DNT": "1",
                },
            )

            page = context.new_page()

            page.goto(URL, wait_until="domcontentloaded", timeout=30000)

            page.fill(
                'input[placeholder="Entity name, RC number, or AV code..."]',
                rc_number,
            )

            page.click("button.search-btn")
            page.wait_for_selector("h3.fs-18.text-primary", timeout=15000)

            # Click "Get Tax ID" button if visible
            try:
                tax_btn = page.locator(
                    "button.btn.btn-primary", has_text="Get Tax ID"
                )

                if tax_btn.is_visible():
                    try:
                        tax_btn.click(timeout=3000)
                    except Exception:
                        tax_btn.click(force=True)

                    # Wait for the tax ID element to appear
                    try:
                        page.wait_for_selector(
                            'ul.list-unstyled li:has-text("Tax ID")',
                            timeout=5000,
                        )
                    except Exception:
                        page.wait_for_timeout(2000)

            except Exception:
                pass

            soup = BeautifulSoup(page.content(), "html.parser")

            name_el = soup.select_one("h3.fs-18.text-primary")
            if name_el:
                result["business_name"] = name_el.get_text(strip=True)

            rc_el = soup.select_one("p.text-secondary.pt-1.pb-2")
            if rc_el:
                rc_text = rc_el.get_text(strip=True)
                parts = rc_text.split("-")
                if len(parts) > 1:
                    extracted_rc = parts[1].strip()
                    if extracted_rc == str(rc_number):
                        result["rc_number"] = extracted_rc

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

                elif text.startswith("Status"):
                    # Only match lines that start with "Status" to avoid
                    # picking up "Registration Status", "Filing Status", etc.
                    badge = li.select_one("span.badge")
                    if badge:
                        result["status"] = badge.get_text(strip=True)

                elif "Tax ID -" in text:
                    match = re.search(r"Tax ID -\s*(\d+)", text)
                    if match:
                        result["tax_id"] = match.group(1)

    except Exception:
        logger.error(
            f"CAC lookup failed for RC={rc_number}",
            exc_info=True,
        )

    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    return result