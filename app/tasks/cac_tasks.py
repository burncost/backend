"""CAC RC-number lookup via the CAC public-search JSON API + TIN service.

This module talks to CAC's JSON endpoints directly (no headless browser) and
returns the exact same dict shape it has always returned, so
`app/api/v1/endpoints/vendors.py` keeps working unchanged:

    business_name, rc_number, date_of_registration,
    nature_of_business, status, tax_id

`_is_cac_result_complete()` in vendors.py requires business_name + tax_id +
status, and auto-verification compares tax_id against the submitted TIN, so
both the search and the TIN call are attempted on every lookup.

Failures are non-fatal: the function always returns a dict (never raises, it is
invoked through `asyncio.to_thread`) and records anything it could not fetch in
`warnings` for logging/debugging.
"""

import logging
import random
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

CAC_SEARCH_URL = (
    "https://authapp.cac.gov.ng/name_similarity_app/api/public_search/search"
)
CAC_TIN_URL = (
    "https://icrp.cac.gov.ng/tin_service/api/v1/public/tin/generate-tax-id/{company_id}"
)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _clean(value) -> str:
    """Normalise CAC values (None-safe, strips padding/spaces)."""
    return "" if value is None else str(value).strip()


def get_cac_business_info(rc_number: str, max_retries: int = 3) -> dict:
    """Look up a CAC registration by RC number and return the legacy dict shape."""
    result = {
        # Legacy contract — read by vendors.py
        "business_name": "",
        "rc_number": _clean(rc_number),
        "date_of_registration": "",
        "nature_of_business": "",
        "status": "",
        "tax_id": "",
        # Additive extras (callers use .get() on known keys, so these are safe)
        "classificationName": "",
        "active_days": None,
        "warnings": [],
    }

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://icrp.cac.gov.ng/public-search/",
            "Origin": "https://icrp.cac.gov.ng",
            "Content-Type": "application/json",
        }
    )

    def make_request(method, url, **kwargs):
        """Request with retries + jittered backoff. Returns None on final failure."""
        for attempt in range(max_retries):
            try:
                response = session.request(method.upper(), url, **kwargs)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException:
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(2.0, 4.5))
                else:
                    logger.warning(
                        "CAC request failed (%s) after %s attempts",
                        url,
                        max_retries,
                    )
                    return None
        return None

    # ── Step 1: search for the company ────────────────────────────────────────
    company_info = {}
    try:
        search_response = make_request(
            "POST",
            CAC_SEARCH_URL,
            json={"searchTerm": str(rc_number)},
            timeout=15,
        )
        if search_response is None:
            result["warnings"].append("search_request_failed")
            return result

        search_data = search_response.json()
        rows = search_data.get("data") or []
        if not search_data.get("success") or not rows:
            result["warnings"].append("company_not_found")
            return result

        # This endpoint is a fuzzy *search* (a partial term returns unrelated
        # companies), so prefer the row whose RC matches exactly.
        wanted = str(rc_number).strip()
        company_info = next(
            (c for c in rows if _clean(c.get("rcNumber")) == wanted),
            rows[0],
        )

        result["business_name"] = _clean(company_info.get("approvedName"))
        result["rc_number"] = _clean(company_info.get("rcNumber")) or wanted
        result["nature_of_business"] = _clean(company_info.get("natureOfBusiness"))
        result["status"] = _clean(company_info.get("status"))
        result["classificationName"] = _clean(company_info.get("classificationName"))

        reg_date_str = company_info.get("companyRegistrationDate")
        if reg_date_str:
            result["date_of_registration"] = str(reg_date_str)
            try:
                # CAC returns e.g. "2024-02-08T08:50:59.204Z"; normalise the "Z"
                # so datetime.fromisoformat parses it.
                reg_date = datetime.fromisoformat(
                    str(reg_date_str).replace("Z", "+00:00")
                )
                result["active_days"] = max(
                    0, (datetime.now(timezone.utc) - reg_date).days
                )
            except Exception:
                logger.warning(
                    "Could not parse CAC registration date for RC=%s",
                    rc_number,
                    exc_info=True,
                )
    except Exception:
        logger.error("CAC lookup failed for RC=%s", rc_number, exc_info=True)
        result["warnings"].append("search_parse_failed")
        return result

    # ── Step 2: fetch the TIN (required for auto-verification) ────────────────
    company_id = company_info.get("companyId")
    if not company_id:
        result["warnings"].append("company_id_missing")
        return result

    tin_response = make_request(
        "GET",
        CAC_TIN_URL.format(company_id=company_id),
        params={
            "rc": rc_number,
            "type": company_info.get("classificationId") or 2,
        },
        timeout=15,
    )
    if tin_response is None:
        result["warnings"].append("tin_request_failed")
        return result

    try:
        tin_data = tin_response.json()
        # CAC reports status="OK" with success=false when a TIN already exists,
        # so key off the payload rather than the success flag.
        tax_id = _clean((tin_data.get("data") or {}).get("tax_id"))
        if tin_data.get("status") == "OK" and tax_id:
            result["tax_id"] = tax_id
        else:
            result["warnings"].append("tin_not_available")
    except Exception:
        logger.warning(
            "Could not parse CAC TIN response for RC=%s",
            rc_number,
            exc_info=True,
        )
        result["warnings"].append("tin_parse_failed")

    return result
