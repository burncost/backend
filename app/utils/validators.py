import re


### Password policy (Admin → Settings → Security → Password Complexity)
# Baseline rules mirror what registration has always enforced (schema-level).
# The stricter set is only applied when the `password_complexity` toggle is ON.
BASELINE_MIN_LENGTH = 8
STRICT_MIN_LENGTH = 12
_SPECIAL_CHARS = r"""!@#$%^&*()_+\-=\[\]{};':"\\|,.<>/?~`"""


def password_policy_errors(password: str, *, strict: bool = False) -> list[str]:
    """Return the policy violations for `password` (empty list == acceptable).

    `strict=False` is the baseline every password write already satisfies;
    `strict=True` adds the extra length + special-character rule that the
    `password_complexity` toggle turns on.
    """
    errors: list[str] = []
    password = password or ""
    min_length = STRICT_MIN_LENGTH if strict else BASELINE_MIN_LENGTH

    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters long")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        errors.append("Password must contain at least one digit")
    if strict and not re.search(f"[{_SPECIAL_CHARS}]", password):
        errors.append("Password must contain at least one special character")
    return errors


def validate_password_policy(password: str, *, strict: bool = False) -> bool:
    """True when `password` satisfies the policy."""
    return not password_policy_errors(password, strict=strict)


### Validate Nigerian phone number format
def validate_nigerian_phone(phone: str) -> bool:
    pattern = r'^(\+234|0)[7-9][0-1]\d{8}$'
    return bool(re.match(pattern, phone))

### Validate email format
def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))