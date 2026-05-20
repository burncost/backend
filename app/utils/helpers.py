from datetime import datetime
from typing import Any


### Generate unique code with timestam
def generate_unique_code(prefix: str = "") -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{timestamp}" if prefix else timestamp


### Format currency amount
def format_currency(amount: float, currency: str = "NGN") -> str:
    return f"{currency} {amount:,.2f}"