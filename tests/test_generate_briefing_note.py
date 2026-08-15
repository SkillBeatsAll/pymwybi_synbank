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
            {"entity_id": "E08", "product": "fx_global_markets", "estimate_zar": 1_000_000.0,
             "observed_zar": 50_000.0, "share": 0.05, "gap_zar": 950_000.0, "confidence": 0.8,
             "confidence_band": "MEDIUM", "opportunity_score": 0.7, "rank_overall": 3,
             "diagnostic_flags": "foreign_revenue_imputed", "explanation": "FX estimate from peer benchmark.",
             "internal_note_field_that_must_not_leak": "secret sauce"},
            {"entity_id": "E08", "product": "lending", "estimate_zar": 2_000_000.0,
             "observed_zar": None, "share": None, "gap_zar": 2_000_000.0, "confidence": 0.2,
             "confidence_band": "LOW", "opportunity_score": 0.4, "rank_overall": 10,
             "diagnostic_flags": "", "explanation": "Financing opportunity, no observed loan book.",
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
    assert set(context["pillars"]["fx_global_markets"].keys()) == set(PILLAR_FIELDS)
    assert "internal_note_field_that_must_not_leak" not in json.dumps(context)


def test_lending_share_is_null_not_zero() -> None:
    context = build_grounding_context("E08", _wallet_results(), "Sanlam", "insurance", _competitor_evidence())
    assert context["pillars"]["lending"]["share"] is None
    assert context["pillars"]["lending"]["observed_zar"] is None


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
