"""Helper utilities for the Burncost API."""
from typing import Dict, Any, Optional
import re
import uuid
from datetime import datetime


def generate_order_number() -> str:
    """Generate a unique order number."""
    date_part = datetime.utcnow().strftime("%y%m")
    unique_part = str(uuid.uuid4())[:4].upper()
    return f"ORD-{date_part}-{unique_part}"


def generate_reference(prefix: str = "REF") -> str:
    """Generate a unique reference string."""
    date_part = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique_part = str(uuid.uuid4())[:6].upper()
    return f"{prefix}-{date_part}-{unique_part}"


def format_currency(amount: float, currency: str = "NGN") -> str:
    """Format an amount as currency string."""
    symbols = {"NGN": "₦", "USD": "$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(currency, "")
    return f"{symbol}{amount:,.2f}"


def parse_phone_number(phone: str) -> Optional[str]:
    """Parse and validate a Nigerian phone number."""
    cleaned = re.sub(r"[^\d+]", "", phone)
    if cleaned.startswith("+234") and len(cleaned) == 14:
        return cleaned
    if cleaned.startswith("0") and len(cleaned) == 11:
        return f"+234{cleaned[1:]}"
    if cleaned.startswith("234") and len(cleaned) == 13:
        return f"+{cleaned}"
    return None


def validate_email(email: str) -> bool:
    """Validate an email address format."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename for safe storage."""
    # Remove path separators
    filename = filename.replace("/", "_").replace("\\", "_")
    # Remove null bytes
    filename = filename.replace("\x00", "")
    # Keep only safe characters
    filename = re.sub(r"[^\w\.\-]", "_", filename)
    # Limit length
    if len(filename) > 200:
        name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        filename = f"{name[:190]}.{ext}"
    return filename


def paginate_results(
    items: list,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """Paginate a list of items."""
    total = len(items)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
        "hasNext": page < total_pages,
        "hasPrev": page > 1,
    }


def calculate_vat(amount: float, vat_rate: float = 0.075) -> float:
    """Calculate VAT for a given amount (Nigeria: 7.5%)."""
    return round(amount * vat_rate, 2)


def calculate_contingency(amount: float, rate: float = 0.05) -> float:
    """Calculate contingency allowance."""
    return round(amount * rate, 2)


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default instead of ZeroDivisionError."""
    if denominator == 0:
        return default
    return numerator / denominator
