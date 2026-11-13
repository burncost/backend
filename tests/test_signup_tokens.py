"""Role policy for the free tokens granted at signup."""
import pytest

from app.services.token_service import SIGNUP_FREE_TOKENS, signup_bonus_for_role


@pytest.mark.parametrize("role", ["customer", "vendor", "CUSTOMER", "Vendor"])
def test_builders_and_vendors_get_no_signup_tokens(role):
    assert signup_bonus_for_role(role) == 0


@pytest.mark.parametrize("role", ["driver", "DRIVER"])
def test_drivers_keep_the_signup_bonus(role):
    assert signup_bonus_for_role(role) == SIGNUP_FREE_TOKENS
