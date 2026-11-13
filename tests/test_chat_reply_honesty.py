"""Chat honesty: real answers, capped fan-out, and text that matches the cards.

The contract this file locks down:

  1. A Gemini 3 ``thought_signature`` survives our OpenAI-shaped history: it is
     captured (base64) from the function_call / reasoning-text part and replayed
     verbatim on the next turn. Without it Gemini 3 refuses every second turn with
     400 INVALID_ARGUMENT, and that refusal was what surfaced to shoppers as a
     canned apology instead of an answer.
  2. History written before signatures were captured is repaired instead of
     failing the turn: the function turns are dropped and the call retried, and a
     missing-signature error is recognised as such (any other error is not).
  3. A turn may open at most ONE price lookup. The model's fan-out is capped
     before the extra calls run, so the answer cannot drift onto products the
     shopper never asked about.
  4. The fallback text renders the single most exact price lookup of the turn —
     the same one ``_build_cards`` keeps — never every fanned-out result.
  5. On the price paths a product answers a query on WHOLE WORDS in its name:
     "Tile Adhesive (25kg)" (a cement-based product at N7,500, cheaper than
     cement, which describes itself as "Cement-based tile adhesive") and
     "12mm Reinforcement Rod" ("reinfor-cement") used to win a cement question.
"""
import base64

import pytest

from app.services.ai_service import (
    ChatAIService,
    _encode_signature,
    _is_missing_signature_error,
)
from app.services.chat_service import (
    ChatService,
    _cap_price_lookups,
    _name_matches_query,
)

SIGNATURE_BYTES = b"\x01\x02-opaque-reasoning-blob"
SIGNATURE_B64 = base64.b64encode(SIGNATURE_BYTES).decode("ascii")

CEMENT_OFFER = {
    "product_id": "p-cement",
    "product_name": "Dangote Cement 50kg",
    "rate": 11500.0,
    "unit": "bag",
    "market": "Wuse",
}
ADHESIVE_OFFER = {
    "product_id": "p-adhesive",
    "product_name": "Tile Adhesive (25kg)",
    "rate": 7500.0,
    "unit": "bag",
    "market": "Wuse",
}


def _ai() -> ChatAIService:
    """The signature plumbing needs no API client, so skip the constructor."""
    return ChatAIService.__new__(ChatAIService)


def _assistant_turn(text_signature=None, call_signature=None) -> dict:
    """An assistant turn holding a get_cheapest_price call, as we persist it."""
    call = {
        "id": "get_cheapest_price",
        "type": "function",
        "function": {"name": "get_cheapest_price", "arguments": '{"search": "cement"}'},
    }
    if call_signature:
        call["thought_signature"] = call_signature
    message = {
        "role": "assistant",
        "content": "Checking the catalogue.",
        "tool_calls": [call],
    }
    if text_signature:
        message["text_signature"] = text_signature
    return message


def _tool_turn(name: str = "get_cheapest_price", content: str = '{"offers": []}') -> dict:
    return {"role": "tool", "tool_name": name, "content": content}


def _price_hit(query: str, matched: str, **over) -> dict:
    """A tool result shaped like ToolExecutor._get_cheapest_price output."""
    result = {
        "search": query,
        "matched_query": matched,
        "offers": [{
            "product_name": f"{matched.title()} Listing",
            "rate": 11500.0,
            "unit": "bag",
            "market": "Wuse",
        }],
        "explanation": f"Cheapest catalog listing for '{matched}'.",
    }
    result.update(over)
    return {"tool": "get_cheapest_price", "result": result}


class _FakeCall:
    """Minimal shape of an OpenAI tool call."""

    def __init__(self, name: str):
        self.id = name
        self.function = type("_Fn", (), {"name": name, "arguments": "{}"})()


# ── 1/2. Thought signatures survive the round-trip ──────────────────────────


def test_signature_is_replayed_on_the_function_call_and_its_text():
    contents = _ai()._messages_to_contents([
        {"role": "system", "content": "You are BurnCost."},
        {"role": "user", "content": "cheapest cement?"},
        _assistant_turn(SIGNATURE_B64, SIGNATURE_B64),
        _tool_turn(),
    ])

    model_turn = contents[1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0] == {
        "text": "Checking the catalogue.",
        "thought_signature": SIGNATURE_B64,
    }
    call_part = model_turn["parts"][1]
    assert call_part["function_call"]["name"] == "get_cheapest_price"
    assert call_part["function_call"]["args"] == {"search": "cement"}
    assert call_part["thought_signature"] == SIGNATURE_B64
    # The response still follows the call it answers (Gemini drops orphans).
    assert contents[2]["parts"][0]["function_response"]["name"] == "get_cheapest_price"


def test_history_without_a_signature_replays_none_and_reads_as_legacy():
    contents = _ai()._messages_to_contents([
        {"role": "user", "content": "cheapest cement?"},
        _assistant_turn(),
        _tool_turn(),
    ])

    assert "thought_signature" not in contents[1]["parts"][0]
    # Which is exactly the failure the retry path has to recognise.
    assert _is_missing_signature_error(Exception(
        "Function call is missing a thought_signature in functionCall parts."
    )) is True


def test_other_api_errors_are_not_mistaken_for_signature_failures():
    assert _is_missing_signature_error(Exception("429 RESOURCE_EXHAUSTED")) is False


def test_signature_failure_repairs_history_to_text_only():
    contents = [
        {"role": "user", "parts": [{"text": "cheapest cement?"}]},
        {"role": "model", "parts": [{
            "function_call": {"name": "get_cheapest_price", "args": {"search": "cement"}},
        }]},
        {"role": "user", "parts": [{
            "function_response": {"name": "get_cheapest_price", "response": {"offers": []}},
        }]},
        {"role": "model", "parts": [{"text": "Dangote Cement is 11,500/bag."}]},
    ]

    assert _ai()._drop_function_turns(contents) == [
        {"role": "user", "parts": [{"text": "cheapest cement?"}]},
        {"role": "model", "parts": [{"text": "Dangote Cement is 11,500/bag."}]},
    ]


def test_encode_signature_is_json_and_mongo_safe():
    assert _encode_signature(SIGNATURE_BYTES) == SIGNATURE_B64
    assert _encode_signature(SIGNATURE_B64) == SIGNATURE_B64  # already encoded
    assert _encode_signature(None) is None
    assert _encode_signature(b"") is None


def _fake_gemini_response(signature: bytes):
    """A Gemini response: one signed reasoning-text part plus one signed call."""
    part_cls = type("_Part", (), {})
    text_part = part_cls()
    text_part.text = "Let me check the catalogue."
    text_part.thought_signature = signature
    call_part = part_cls()
    call_part.text = None
    call_part.thought_signature = signature
    call_part.function_call = type(
        "_FC", (), {"name": "get_cheapest_price", "args": {"search": "cement"}}
    )()
    content = type("_Content", (), {"parts": [text_part, call_part]})()
    candidate = type("_Candidate", (), {"content": content})()
    return type("_Response", (), {"candidates": [candidate], "usage_metadata": None})()


def test_captured_signature_lands_on_the_tool_call_and_the_message():
    response = _ai()._gemini_to_openai_response(_fake_gemini_response(SIGNATURE_BYTES), [])

    message = response.choices[0].message
    assert message.tool_calls[0].function.name == "get_cheapest_price"
    assert message.tool_calls[0].thought_signature == SIGNATURE_B64
    assert message.text_signature == SIGNATURE_B64
    assert response.is_error is False


# ── 3. One price lookup per turn ────────────────────────────────────────────


def test_price_lookup_fanout_is_capped_to_one_per_turn():
    calls = [
        _FakeCall("get_cheapest_price"),
        _FakeCall("compare_prices"),
        _FakeCall("get_price_range"),
        _FakeCall("search_suppliers"),
    ]

    kept, dropped = _cap_price_lookups(calls)

    # The first lookup runs; the shopper still gets the supplier help.
    assert [c.function.name for c in kept] == ["get_cheapest_price", "search_suppliers"]
    assert dropped == ["compare_prices", "get_price_range"]


def test_single_or_non_price_calls_are_left_alone():
    only_one = [_FakeCall("get_cheapest_price")]
    assert _cap_price_lookups(only_one) == (only_one, [])

    non_price = [_FakeCall("search_products"), _FakeCall("add_to_cart")]
    assert _cap_price_lookups(non_price) == (non_price, [])

    assert _cap_price_lookups([]) == ([], [])


# ── 4. The fallback text agrees with the cards ──────────────────────────────


def test_fallback_renders_only_the_most_exact_lookup():
    reply = ChatService._prices_found_reply([
        # A broadened lookup Gemini fanned out: it must not appear, because the
        # card kept for the turn is the exact one.
        _price_hit("cement", "building materials", relaxed_from="cement"),
        _price_hit("cement", "cement"),
        {"tool": "add_to_cart", "result": {"message": "Added 2 bags of Dangote Cement 50kg."}},
    ])

    assert reply.count("Here are the prices I found") == 1
    assert "Here are the prices I found for 'cement':" in reply
    assert "Cement Listing" in reply
    assert "Building Materials Listing" not in reply
    assert "Added 2 bags of Dangote Cement 50kg." in reply


def test_reply_and_card_pick_the_same_lookup():
    hits = [
        _price_hit("cement", "building materials", relaxed_from="cement"),
        _price_hit("cement", "cement"),
    ]

    reply = ChatService._prices_found_reply(hits)
    cards = ChatService._build_cards(ChatService.__new__(ChatService), hits)
    cheapest_card = next(c for c in cards if c.type == "cheapest_price")

    assert "Cement Listing" in reply
    assert [o["product_name"] for o in cheapest_card.data["offers"]] == ["Cement Listing"]


def test_fallback_ranks_an_estimate_below_a_priced_lookup():
    reply = ChatService._prices_found_reply([
        _price_hit("cement", "cement", offers=[ADHESIVE_OFFER], insufficient_data=True),
        _price_hit("cement", "cement", offers=[CEMENT_OFFER]),
    ])

    # Identical queries: the real catalogue price is shown, not the estimate.
    assert "Dangote Cement 50kg" in reply
    assert "Tile Adhesive (25kg)" not in reply


def test_fallback_is_none_when_no_tool_found_anything():
    assert ChatService._prices_found_reply(
        [{"tool": "search_products", "result": {"products": []}}]
    ) is None


def test_exactness_ranks_real_data_over_estimates_and_broadened_lookups():
    exact = {"search": "cement", "matched_query": "cement"}
    broadened = {"search": "cement", "matched_query": "cement", "relaxed_from": "dangote opc cement"}
    estimate = {"search": "cement", "matched_query": "cement", "insufficient_data": True}
    near_miss = {"search": "cement", "matched_query": "cement bags"}

    assert ChatService._price_exactness(exact) > ChatService._price_exactness(broadened)
    assert ChatService._price_exactness(exact) > ChatService._price_exactness(estimate)
    assert ChatService._price_exactness(exact) > ChatService._price_exactness(near_miss)


# ── 5. A price query matches on whole words in the product NAME ─────────────


@pytest.mark.parametrize(
    "name,query,expected",
    [
        ("Dangote Cement 50kg", "cement", True),
        ("Dangote Cement 50kg", "dangote cement", True),
        ("Stone-Coated Roofing Sheet", "roofing sheets", True),
        ("12mm Reinforcement Rod", "reinforcement rod", True),
        # The false answers a "cement" question used to get: all cheaper than
        # real cement, none of them cement.
        ("Tile Adhesive (25kg)", "cement", False),
        ("12mm Reinforcement Rod", "cement", False),
        ("Binding Wire (1kg roll)", "cement", False),
        # Wrong brand / an extra word is not a name match (the catalogue can still
        # return the row as a broad match — just never first).
        ("BUA Cement 50kg", "dangote cement", False),
        ("Dangote Cement 50kg", "cement bags", False),
    ],
)
def test_price_name_match_is_whole_word_and_all_tokens(name, query, expected):
    assert _name_matches_query(name, query) is expected


def test_blank_or_tiny_queries_never_count_as_name_matches():
    assert _name_matches_query("Dangote Cement 50kg", "") is False
    assert _name_matches_query("Dangote Cement 50kg", "50 kg") is False


