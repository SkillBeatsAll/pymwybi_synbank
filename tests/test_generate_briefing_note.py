import json
from types import SimpleNamespace

import pandas as pd

from src.syn_wallet.generate_briefing_note import (
    PILLAR_FIELDS,
    build_grounding_context,
    generate_briefing_note,
    load_system_prompt,
    render_user_message,
)


class FakeMessages:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_call: dict | None = None

    def create(self, *, model, max_tokens, temperature, system, messages):
        self.last_call = {"model": model, "system": system, "messages": messages}
        return SimpleNamespace(content=[SimpleNamespace(text=self.reply)])


class FakeClient:
    def __init__(self, reply: str) -> None:
        self.messages = FakeMessages(reply)


def _wallet_results() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"entity_id": "E08", "pillar": "fx", "addressable_p50": 1_000_000.0, "observed": 50_000.0,
             "share_p50": 0.05, "unaddressed_p50": 950_000.0, "confidence": 0.8, "opportunity_score": 0.7, "rank": 3,
             "internal_note_field_that_must_not_leak": "secret sauce"},
            {"entity_id": "E08", "pillar": "lending_dcm", "addressable_p50": 2_000_000.0, "observed": None,
             "share_p50": None, "unaddressed_p50": None, "confidence": 0.2, "opportunity_score": 0.4, "rank": 10,
             "internal_note_field_that_must_not_leak": "secret sauce"},
        ]
    )


def _competitor_evidence() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"entity_id": "E08", "bank_name": "Standard Bank", "is_confirmed_lender": True},
            {"entity_id": "E08", "bank_name": "A Bank Only Mentioned In Passing", "is_confirmed_lender": False},
        ]
    )


def test_context_only_whitelists_known_fields() -> None:
    context = build_grounding_context("E08", _wallet_results(), "Sanlam", "insurance", _competitor_evidence())
    assert set(context["pillars"]["fx"].keys()) == set(PILLAR_FIELDS)
    assert "internal_note_field_that_must_not_leak" not in json.dumps(context)


def test_unobservable_pillar_is_null_not_zero() -> None:
    context = build_grounding_context("E08", _wallet_results(), "Sanlam", "insurance", _competitor_evidence())
    assert context["pillars"]["lending_dcm"]["share_p50"] is None
    assert context["pillars"]["lending_dcm"]["observed"] is None


def test_only_confirmed_lenders_pass_through() -> None:
    context = build_grounding_context("E08", _wallet_results(), "Sanlam", "insurance", _competitor_evidence())
    assert context["confirmed_competitor_lenders"] == ["Standard Bank"]
    assert "A Bank Only Mentioned In Passing" not in context["confirmed_competitor_lenders"]


def test_generate_briefing_note_sends_grounded_context_and_returns_text() -> None:
    context = build_grounding_context("E08", _wallet_results(), "Sanlam", "insurance", _competitor_evidence())
    client = FakeClient(reply="Sanlam's largest opportunity sits in FX...")
    note = generate_briefing_note(context, client)

    assert note == "Sanlam's largest opportunity sits in FX..."
    sent_message = client.messages.last_call["messages"][0]["content"]
    assert "Standard Bank" in sent_message
    assert "Sanlam" in sent_message
    assert client.messages.last_call["system"] == load_system_prompt()


def test_render_user_message_embeds_full_context_json() -> None:
    context = {"entity_id": "E08", "entity_name": "Sanlam", "sector": "insurance", "pillars": {}, "confirmed_competitor_lenders": []}
    message = render_user_message(context)
    assert json.dumps(context, indent=2) in message
