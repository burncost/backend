"""Gateway payment references must resolve back to the full order number.

Contract pinned here:
  * References are `BURNCOST-{order_number}-{YYYYMMDDHHMMSS}` and the order
    number itself contains hyphens (`BC-YYYYMMDD-XXXXXX`).
  * Recovering the order number by splitting the reference on "-" and taking
    index 1 yields just "BC", so no order is ever found and a PAID order stays
    `pending_payment` while the supplier shows "Awaiting Payment"
    (regression: order BC-20261014-JCEX8G / NGN 95,716.01).
  * Prefix + trailing-timestamp stripping is the fix, and the timestamp-less
    legacy form must still resolve unchanged.
  * Gateway channels map onto our PaymentMethod enum, defaulting to card.
"""

from app.api.v1.endpoints.payments import (
    _amounts_disagree,
    _gateway_amount,
    _order_number_from_reference,
    _order_number_from_metadata,
    _payment_method_from_channel,
)
from app.models.order import PaymentMethod

# The exact reference that left the order stuck in pending_payment.
STUCK_REFERENCE = "BURNCOST-BC-20261014-JCEX8G-20261014124430"


def test_real_reference_resolves_to_full_order_number():
    assert _order_number_from_reference(STUCK_REFERENCE) == "BC-20261014-JCEX8G"


def test_real_reference_is_not_truncated_to_first_segment():
    # The pre-fix `reference.split("-")[1]` returned "BC" and matched no order.
    assert _order_number_from_reference(STUCK_REFERENCE) != "BC"


def test_legacy_reference_without_timestamp_is_unchanged():
    assert _order_number_from_reference("BURNCOST-BC-20261014-JCEX8G") == "BC-20261014-JCEX8G"


def test_simple_order_number_resolves():
    assert _order_number_from_reference("BURNCOST-ORD123-20261014124430") == "ORD123"


def test_token_and_foreign_references_are_ignored():
    assert _order_number_from_reference("TKN-abc-123") is None
    assert _order_number_from_reference("") is None
    assert _order_number_from_reference(None) is None


def test_non_timestamp_tail_is_kept():
    assert _order_number_from_reference("BURNCOST-BC-20261014-ABCDEF") == "BC-20261014-ABCDEF"


def test_metadata_fallback_reads_order_number():
    assert _order_number_from_metadata({"metadata": {"order_number": "BC-1"}}) == "BC-1"
    assert _order_number_from_metadata({"meta": {"order_number": "BC-2"}}) == "BC-2"
    assert _order_number_from_metadata({"metaData": {"order_number": "BC-3"}}) == "BC-3"
    assert _order_number_from_metadata({"metadata": {}}) is None
    assert _order_number_from_metadata(None) is None


def test_channel_maps_to_payment_method():
    assert _payment_method_from_channel("card") == PaymentMethod.CARD
    assert _payment_method_from_channel("bank") == PaymentMethod.BANK_TRANSFER
    assert _payment_method_from_channel("bank_transfer") == PaymentMethod.BANK_TRANSFER
    assert _payment_method_from_channel("ussd") == PaymentMethod.USSD
    assert _payment_method_from_channel("wallet") == PaymentMethod.WALLET
    # Unknown/absent channels must never raise mid-settlement.
    assert _payment_method_from_channel("mobile_money") == PaymentMethod.CARD
    assert _payment_method_from_channel(None) == PaymentMethod.CARD


# ── gateway amount reconciliation on the client-return verify path ──────────

def test_verified_amount_must_match_the_order_total():
    # The 100x Monnify regression: NGN 6,429,703.00 vs a NGN 64,297.03 order.
    assert _amounts_disagree(6429703.0, 64297.03) is True
    assert _amounts_disagree(64297.03, 64297.03) is False
    assert _amounts_disagree(64297.035, 64297.03) is False  # sub-kobo noise
    assert _amounts_disagree(None, 64297.03) is False  # gateway reported none


def test_gateway_amount_coercion_never_raises():
    assert _gateway_amount("64297.03") == 64297.03
    assert _gateway_amount(64297.03) == 64297.03
    assert _gateway_amount(None) is None
    assert _gateway_amount("") is None
    assert _gateway_amount("not-a-number") is None
