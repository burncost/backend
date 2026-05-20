import re


### Validate Nigerian phone number format
def validate_nigerian_phone(phone: str) -> bool:
    pattern = r'^(\+234|0)[7-9][0-1]\d{8}$'
    return bool(re.match(pattern, phone))

### Validate email format
def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))